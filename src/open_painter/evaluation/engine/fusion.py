"""Chain fusion: a run of changed sibling units rendered as one pass, bit for bit.

Inside an isolated folder (a smart material), layers composite with straight alpha over a
transparent start. That is not affine in the backdrop, so suffix collapse cannot fold
them (suffix.py), nor a run of non-affine blend modes. Editing a layer there re-renders
every sibling above it, one pass each. Each pass writes a texture that the next one reads
back at the same texel.

A fused pass runs the members' own programs one after another in registers. A member hands
its result to the next through that one's backdrop or, at a folder step, through its group
input: the step fuses with the chain inside its folder (``_link``). Between two members the
value is rounded as the member's texture store would. ``store_rounding``
finds the device's float-to-half conversion once, by comparing real stores with the
emulations. A value handed over at full float passes through an identity the compiler
cannot see through (``op_opaque``): Blender compiles Metal shaders with fast math, which
would otherwise fuse one member's arithmetic with the next one's. The top's result is then
the sequential result bit for bit. Each fused shader is also checked once against
sequential rendering before it is used; a mismatch retires it. On Metal that still happens
to some chains at full float (``engine.precise``), where fast math can compile a member
differently inside the fused shader; at display precision none was seen.

C plans the passes (op_graph_execute). Like a collapsed suffix run, a fused chain
materializes its top before C plans the channel: the top then holds its desired stamp
and C treats it as rendered. The members below the top keep no texture; their stamps say
they hold their desired content, as an evicted result's would, and they are rendered
again only if something else needs them.

A chain's shader is compiled when the chain repeats on consecutive evaluations (a drag),
or while idle for the selected layer (``prearm``), never for one-off renders. With a UI, a
shader this machine has not compiled before waits for an idle tick (``compile_when_idle``):
a first compile takes 100-330 ms on Vulkan, and Blender's and the driver's disk caches make
the next ones 1-4 ms.

A channel's final can also render inside its publication pass, with images written in place
(``publish``, ``publish_suffix``): a fused chain ending at it, or the collapsed run it tops,
together with the fused chain that is that run's input.
The shader evaluates the final at the texel publication reads, rounds it as the final's
texture would hold it and finishes it as the publication pass does. The final's pass, its
texture and the read of it are saved.
"""
import hashlib
import os
import re
from collections import OrderedDict
from dataclasses import dataclass

from .. import metrics
from .formats import scalar_unit

ENABLED = os.environ.get('OP_FUSE_CHAINS', '1') != '0'
# A chain ending at a channel's final renders in the publication pass (``publish``).
PUBLISH = os.environ.get('OP_FUSE_PUBLISH', '1') != '0'
# A folder step fuses with the chain inside its folder (its group input in a register, ``_link``).
GROUPS = os.environ.get('OP_FUSE_GROUPS', '1') != '0'
# The fused chain that is a collapsed run's input evaluates in that run's publication pass.
RUN_INPUTS = os.environ.get('OP_FUSE_RUN_INPUTS', '1') != '0'
MIN_LENGTH = 2
MAX_LENGTH = 8
SHADERS = 64                    # fused shaders kept per device, least recently used first
VERIFY_SIZE = 128               # the region each fused shader is checked on, once
IDLE_COMPILE = None             # None: with a UI (``compile_when_idle``); tests may force it
REGISTRY_ENTRIES = 4096         # fused shaders remembered as compiled on this machine

MAIN = 'void main() {'
FETCH = 'texelFetch({0}, min(op_texel, textureSize({0}, 0) - 1), 0)'
PARAM = re.compile(r'op_params\.p\[(\d+)\]')
SAMPLER = re.compile(r'\bop_s(\d+)\b')

# Float to half as a store converts it, toward zero or to nearest even. NaN and infinity keep
# their bits (a NaN's payload only survives where no arithmetic reads it); finite values past
# the half range become the largest half (toward zero) or infinity (to nearest).
STORES = {
    'op_store_rtz': '''
float op_store_rtz(float x) {
  uint u = floatBitsToUint(x);
  uint e = (u >> 23) & 0xffu;
  if (e == 255u) return x;
  if (e >= 143u) return uintBitsToFloat((u & 0x80000000u) | 0x477fe000u);
  if (e >= 113u) return uintBitsToFloat(u & 0xffffe000u);
  return trunc(x * 16777216.0) * (1.0 / 16777216.0);
}
''',
    'op_store_rne': '''
float op_store_rne(float x) {
  uint u = floatBitsToUint(x);
  uint e = (u >> 23) & 0xffu;
  if (e == 255u) return x;
  if (e >= 113u) {
    uint r = (u + 0xfffu + ((u >> 13) & 1u)) & 0xffffe000u;
    if (((r >> 23) & 0xffu) >= 143u) return uintBitsToFloat((u & 0x80000000u) | 0x7f800000u);
    return uintBitsToFloat(r);
  }
  /* roundEven, which Blender's Metal shading language lacks: |y| < 2^10 here, so trunc, the
     fraction and the parity are exact. */
  float y = x * 16777216.0;
  float t = trunc(y);
  float f = abs(y - t);
  if (f > 0.5 || (f == 0.5 && mod(t, 2.0) != 0.0)) t += sign(y);
  return t * (1.0 / 16777216.0);
}
''',
}
# An identity the shader compiler cannot see through, for values handed over at full float.
# Blender compiles Metal shaders with fast math, which may contract one member's last multiply
# with the next member's add, or reassociate across them: the register then differs from the
# texture in its last bits. The bits pass through an XOR with a value known only at run time
# (always 0). The half-float emulations above already work on the bits.
OPAQUE = '''
vec4 op_opaque(vec4 v) { return uintBitsToFloat(floatBitsToUint(v) ^ uvec4(gl_FragCoord.x < -1.0 ? 1u : 0u)); }
'''
STORES = {name: OPAQUE + source for name, source in STORES.items()}
# What reading a member's texture back gives, per format: R/RG reads fill (0, 1).
ROUND = {
    'RGBA16F': 'vec4({0}(v.x), {0}(v.y), {0}(v.z), {0}(v.w))',
    'RG16F': 'vec4({0}(v.x), {0}(v.y), 0.0, 1.0)',
    'RGBA32F': 'op_opaque(v)',
    'RG32F': 'vec4(op_opaque(v).xy, 0.0, 1.0)',
}


@dataclass
class Part:
    """A unit program split for fusion: its helpers, the body of main, its backdrop and, when it
    reads it only at its own texel, its group input (a folder step's children)."""
    head: str
    body: str
    backdrop: str
    grouped: str = None


@dataclass
class Fused:
    shader: object
    names: list                     # per member: {its sampler name: the fused shader's}
    verified: bool = False          # checked once against sequential rendering
    failed: bool = False            # never fused again (mismatch, layout or compilation)


def parse(program):
    """``program`` split for fusion, or None when it cannot be fused (cached on the program)."""
    cached = program.__dict__.get('_fusion_part', False)
    if cached is not False:
        return cached
    part = _parse(program)
    program.__dict__['_fusion_part'] = part
    return part


def _parse(program):
    if not program.native or program.objects or len(program.targets) != 1:
        return None
    backdrops = [s.name for s in program.samplers if s.kind == 'backdrop']
    # A folder step's group input is one more texture read at its texel; a spatial filter
    # reads its input elsewhere.
    if (len(backdrops) != 1 or sum(1 for s in program.samplers if s.kind == 'grouped') > 1
            or any(s.kind == 'filter' or s.source is not None for s in program.samplers)):
        return None
    if not all(re.fullmatch(r'op_s\d+', s.name) for s in program.samplers):
        return None
    source = program.source
    if source.count(MAIN) != 1:
        return None
    head, _, rest = source.partition(MAIN)
    rest = rest.rstrip()
    if not rest.endswith('}'):
        return None
    body = rest[:-1]
    backdrop = backdrops[0]
    fetch = FETCH.format(backdrop)
    reads = body.count(fetch)
    # The backdrop must be read only at the unit's own texel: a member above the first
    # takes its predecessor's value from a register instead.
    if not reads or len(re.findall(r'\b' + backdrop + r'\b', body)) != 2 * reads:
        return None
    if body.count('op_out') != 1 or body.count('op_out0 = ') != 1 or 'return' in body or 'discard' in body:
        return None
    if body[body.index('op_out0 = '):].count(';') != 1:
        return None
    if body.count('op_params') != len(PARAM.findall(body)):
        return None
    names = {s.name for s in program.samplers}
    if any('op_s' + index not in names for index in SAMPLER.findall(body)):
        return None
    # A folder step reads its children's composite at its texel too: the chain inside the
    # folder can then hand it over in a register (``_link``).
    grouped = next((s.name for s in program.samplers if s.kind == 'grouped'), None)
    if grouped is not None:
        reads = body.count(FETCH.format(grouped))
        if not reads or len(re.findall(r'\b' + grouped + r'\b', body)) != 2 * reads:
            grouped = None
    return Part(head, body, backdrop, grouped)


def _group_links(engine, chain):
    return sum(1 for lower, upper in zip(chain, chain[1:]) if _link(engine, lower, upper) == 'grouped')


def _link(engine, lower, upper):
    """Which input of ``upper`` takes ``lower``'s result in a fused chain: 'backdrop' (the
    layer below) or 'grouped' (a folder step over the chain inside it); None if neither."""
    position = engine.positions.get(upper) or ()
    if position and position[0] == ('unit', lower):
        return 'backdrop'
    if len(position) > 1 and position[1] == ('unit', lower):
        return 'grouped'
    return None


def fusable(engine, key, state=None):
    """Whether ``key``'s unit can be a member of a fused chain, memoized per program, formats
    and position (planning must not re-derive a state per stale unit)."""
    if key[0] != 'CHANNEL':
        return False
    if state is None:
        state = engine.states.get(key)
        if state is None:
            return False
    program, formats, position = state.program, state.formats, engine.positions.get(key)
    flags = engine.__dict__.setdefault('_fusion_flags', {})
    cached = flags.get(key)
    if cached is not None and cached[0] is program and cached[1] is formats and cached[2] is position:
        return cached[3]
    result = (program is not None and len(formats) == 1 and formats[0] in ROUND and bool(position)
              and position[0] is not None and parse(program) is not None)
    if len(flags) > 2 * len(engine.states) + 64:
        for stale in [k for k in flags if k not in engine.states]:
            del flags[stale]
    flags[key] = (program, formats, position, result)
    return result


# ---- device rounding ------------------------------------------------------------------

_PATTERN = '''
uint op_hash(uint x) { x ^= x >> 16; x *= 0x7feb352du; x ^= x >> 15; x *= 0x846ca68bu; x ^= x >> 16; return x; }
float op_case(uint h, uint kind) {
  uint m = op_hash(h) & 0x7fffffu;
  uint s = (op_hash(h + 9u) & 1u) << 31;
  uint e;
  if (kind == 0u) e = 113u + op_hash(h + 3u) % 30u;            /* normal halves */
  else if (kind == 1u) e = 103u + op_hash(h + 3u) % 10u;       /* half denormals */
  else if (kind == 2u) e = 1u + op_hash(h + 3u) % 102u;        /* below the smallest half */
  else if (kind == 3u) e = 143u + op_hash(h + 3u) % 20u;       /* past the half range */
  else if (kind == 4u) { e = 255u; m = 0u; }                    /* infinity */
  else if (kind == 5u) e = 0u;                                  /* float denormals */
  else if (kind == 6u) { e = 113u + op_hash(h + 3u) % 30u; m = (m & ~0x1fffu) | 0x1000u; }  /* ties */
  else { e = 142u; m |= 0x7fe000u; }                            /* the largest halves and beyond */
  return uintBitsToFloat(s | (e << 23) | m);
}
vec4 op_value() {
  uvec2 p = uvec2(gl_FragCoord.xy);
  uint h = op_hash(p.x * 7919u + p.y * 104729u);
  uint kind = p.y / 8u;
  return vec4(op_case(h, kind), op_case(h + 101u, kind), op_case(h + 202u, kind), op_case(h + 303u, kind));
}
'''
_PATTERN_SIZE = 64


def store_rounding(dev):
    """The emulation of this device's float-to-half store ('op_store_rtz' or 'op_store_rne'),
    or '' when neither matches every case bit for bit (fusion is then off). Found once."""
    found = getattr(dev, '_fusion_rounding', None)
    if found is None:
        try:
            found = _find_rounding(dev)
        except Exception as error:   # a device that cannot run the check renders sequentially
            print(f'Open Painter: chain fusion disabled ({error})')
            found = ''
        dev._fusion_rounding = found
        metrics.count('engine_chain_rounding_' + (found or 'none'))
    return found


def _pattern(dev, fmt, body, functions=''):
    import gpu
    n = _PATTERN_SIZE
    shader = dev._create(functions + _PATTERN + 'void main() { vec4 v = op_value(); op_out0 = ' + body + '; }\n', [],
                         counted=False)
    texture = dev.texture(n, fmt)
    dev.draw(shader, [texture], n)
    # Read through an RGBA32F copy, so R/RG formats expand as a fetch sees them.
    full = dev.texture(n, 'RGBA32F')
    dev.draw(dev.fixed('copy', counted=False), [full], n, [('op_source', texture)])
    framebuffer = gpu.types.GPUFrameBuffer(color_slots=(full,))
    with framebuffer.bind():
        buffer = framebuffer.read_color(0, 0, n, n, 4, 0, 'FLOAT')
    dev.release(texture, n, fmt)
    dev.release(full, n, 'RGBA32F')
    buffer.dimensions = n * n * 4
    import numpy as np
    return np.array(buffer, np.float32).view(np.uint32)


def _find_rounding(dev):
    import numpy as np
    with dev.state():
        stores = {fmt: _pattern(dev, fmt, 'v') for fmt in ('RGBA16F', 'RG16F', 'RGBA32F', 'RG32F')}
        for name, function in STORES.items():
            if all(np.array_equal(stores[fmt], _pattern(dev, 'RGBA32F', ROUND[fmt].format(name), function))
                   for fmt in stores):
                return name
    return ''


# ---- fused shaders ----------------------------------------------------------------------

def _definitions(head):
    """A program's helpers as top-level items: functions and comment blocks."""
    items, block, depth, comment = [], [], 0, False
    for line in head.splitlines(keepends=True):
        block.append(line)
        if comment:
            comment = '*/' not in line
        elif line.lstrip().startswith('/*') and '*/' not in line:
            comment = True
        else:
            depth += line.count('{') - line.count('}')
        if depth == 0 and not comment:
            items.append(''.join(block))
            block = []
    if block:
        items.append(''.join(block))
    return items


def _merged(heads):
    """Every member's helpers, each once, in first-seen order. Each program defines a helper
    after the helpers it uses, so the union does too."""
    seen, merged = set(), []
    for head in dict.fromkeys(heads):
        for item in _definitions(head):
            if item not in seen:
                seen.add(item)
                merged.append(item)
    return ''.join(merged)


PROLOGUE = '  ivec2 op_texel = ivec2(gl_FragCoord.xy);\n'
# The publication pass's own epilogue (runtime.publish), for a chain that publishes its result:
# Blender's safe normalize of a normal map, re-encoded; then clamped, to the image and its copy.
NORMAL_EPILOGUE = (
    '  vec3 v = rgb * 2.0 - 1.0;\n'
    '  float l = dot(v, v);\n'
    '  v = (l > 0.0) ? v * inversesqrt(l) : v;\n'
    '  rgb = (v + 1.0) * 0.5;\n')


def _publication_main(publish, samplers, calls, value):
    """``main`` of a shader that publishes ``value`` (a vec4 expression, rounded as the final's
    texture holds it) as the publication pass does (``runtime.PUBLISH``): evaluated at the texel
    the pass reads (``op_publish_texel``, a padding texel's donor), then written, finished, to
    the image's level 0 and the copy its mips reduce. ``calls`` compute it; the donor's sampler
    is appended to ``samplers``."""
    normal, padded, scalar = publish
    lines = ['  op_publish_texel = ivec2(gl_FragCoord.xy);']
    if padded:
        samplers.append('op_publish_donor')
        lines += ['  vec2 op_donor_texel = texelFetch(op_publish_donor, op_publish_texel, 0).xy;',
                  '  if (op_donor_texel.x >= 0.0) { op_publish_texel = ivec2(op_donor_texel); }']
    lines += calls
    lines.append(f'  vec3 rgb = {value}.rgb;')
    if scalar:
        lines.append('  rgb = vec3(rgb.r);')
    main = MAIN + '\n' + '\n'.join(lines) + '\n'
    if normal:
        main += NORMAL_EPILOGUE
    return main + ('  vec4 value = vec4(clamp(rgb, 0.0, 1.0), 1.0);\n'
                   '  op_out0 = value;\n  op_out1 = value;\n}\n')


def _source(engine, chain, rounding, publish=None, run_format=None):
    """The fused source, each member's {sampler: fused sampler}, the fused samplers, the
    parameter slots and the targets. ``publish`` ((normal, padded, scalar) of the channel):
    the chain is evaluated at the texel the publication reads (a padding texel's donor), its
    top rounded as its texture would hold it, and written as the publication pass writes its
    result: the image's level 0 and the copy its mips reduce."""
    from .procedural_glsl import library
    parts = [parse(engine.states[key].program) for key in chain]
    head = _merged(part.head for part in parts)
    procedurals = sorted({kind for key in chain for kind in engine.states[key].program.procedurals})
    functions, names, samplers, offset = [], [], [], 0
    for index, (key, part) in enumerate(zip(chain, parts)):
        program = engine.states[key].program
        mapping = {s.name: f'op_f{index}_{s.name[3:]}' for s in program.samplers}
        body = part.body
        if publish is not None:
            # Every coordinate the member reads derives from op_texel: the published texel.
            if body.count(PROLOGUE) != 1 or body.count('gl_FragCoord') != 1:
                return None
            body = body.replace(PROLOGUE, '  ivec2 op_texel = op_publish_texel;\n', 1)
        linked = None
        if index:
            # The member below hands its result over in a register: through this member's
            # backdrop, or its group input (a folder step over the chain inside it).
            link = _link(engine, chain[index - 1], key)
            linked = part.backdrop if link == 'backdrop' else part.grouped if link == 'grouped' else None
            if linked is None:
                return None
            body = body.replace(FETCH.format(linked), 'op_backdrop')
        body = SAMPLER.sub(lambda match: mapping['op_s' + match.group(1)], body)
        base = offset
        body = PARAM.sub(lambda match: f'op_params.p[{int(match.group(1)) + base}]', body)
        body = body.replace('op_out0 = ', 'return ')
        signature = f'vec4 op_fused{index}(vec4 op_backdrop)' if index else f'vec4 op_fused{index}()'
        functions.append(f'{signature} {{{body}}}\n')
        names.append(mapping)
        samplers.extend(mapping[s.name] for s in program.samplers if s.name != linked)
        offset += program.parameter_slots
    rounded = chain if publish is not None else chain[:-1]
    rounds = ''.join(f'vec4 op_fused_round{index}(vec4 v) {{ return '
                     f'{ROUND[engine.states[key].formats[0]].format(rounding)}; }}\n'
                     for index, key in enumerate(rounded))
    calls = ['  vec4 op_v = op_fused0();']
    calls += [f'  op_v = op_fused{index}(op_fused_round{index - 1}(op_v));' for index in range(1, len(chain))]
    source = (library(procedurals) if procedurals else '') + head + STORES[rounding] + rounds + ''.join(functions)
    if publish is None:
        source += MAIN + '\n' + '\n'.join(calls) + '\n  op_out0 = op_v;\n}\n'
        return source, names, samplers, offset, 1
    value = f'op_fused_round{len(chain) - 1}(op_v)'
    if run_format is not None:
        # The chain is a collapsed run's input: the run's transform applies to its result (as
        # its texture holds it), rounded as the run's top holds it, then published.
        apply = _suffix_apply(handed=True)
        if apply is None:
            return None
        helpers, function = apply
        source += (helpers + f'vec4 op_suffix_round(vec4 v) {{ return {ROUND[run_format].format(rounding)}; }}\n'
                   + function)
        samplers += ['op_a', 'op_b']
        value = f'op_suffix_round(op_suffix_value(op_publish_texel, {value}))'
    source = 'ivec2 op_publish_texel;\n' + source + _publication_main(publish, samplers, calls, value)
    return source, names, samplers, offset, 2


def _signature(engine, chain, publish=None):
    """What a chain's fused source is made of: each member's program, layout and format, and
    how it publishes (``_source``)."""
    signature = [('publish', publish)] if publish is not None else []
    for index, key in enumerate(chain):
        state = engine.states[key]
        program = state.program
        signature.append((program.source, program.procedurals, program.parameter_slots,
                          tuple((s.name, s.kind) for s in program.samplers), state.formats[0],
                          _link(engine, chain[index - 1], key) if index else None))
    return tuple(signature)


def _fused(dev, engine, chain, build, publish=None, interactive=True):
    """The chain's fused shader (``_shader``)."""
    def make():
        built = _source(engine, chain, store_rounding(dev), publish)
        return None if built is None else (*built, ())

    def again():
        return (all(key in engine.states for key in chain) and _current(engine, chain)
                and _fused(dev, engine, chain, True, publish, interactive=False) is not None)

    return _shader(dev, engine, _signature(engine, chain, publish), build, make, interactive, again)


def _shader(dev, engine, signature, build, make, interactive, again):
    """The fused shader of ``signature``: cached, or compiled now from ``make()`` ((source, names,
    samplers, parameter slots, targets, int uniforms), or None when it cannot be built) when
    ``build``; None otherwise. During an edit (``interactive``) with a UI, one this machine has
    not compiled before is queued for an idle tick instead (``compile_when_idle``), where
    ``prearm`` calls ``again()``."""
    cache = getattr(dev, '_fused', None)
    if cache is None:
        cache = dev._fused = OrderedDict()
    entry = cache.get(signature)
    if entry is not None:
        cache.move_to_end(signature)
        return None if entry.failed else entry
    if not build:
        return None
    built = make() if store_rounding(dev) else None
    if built is None:
        cache[signature] = Fused(None, [], failed=True)
        return None
    source, names, samplers, slots, targets, ints = built
    if len(samplers) > dev.max_samplers:
        cache[signature] = Fused(None, [], failed=True)
        return None
    digest = hashlib.sha256(f'{source}|{"|".join(samplers)}|{slots}|{targets}|{"|".join(ints)}'
                            .encode()).hexdigest()[:32]
    if interactive and compile_when_idle() and digest not in _known(dev):
        engine.__dict__.setdefault('_fusion_pending', OrderedDict())[signature] = again
        metrics.count('engine_chain_compiles_deferred')
        return None
    try:
        shader = dev._create(source, samplers, slots, 0, targets, ints=ints, counted=False)
    except Exception:
        # Rendered and published as without fusion.
        cache[signature] = Fused(None, [], failed=True)
        metrics.count('engine_chain_build_failures')
        return None
    metrics.count('engine_chain_compiles')
    _register(dev, digest)
    entry = cache[signature] = Fused(shader, names)
    while len(cache) > SHADERS:
        cache.popitem(last=False)
    return entry


def compile_when_idle():
    """Whether a fused shader this machine has not compiled before waits for an idle tick:
    with a UI, so that no edit waits for a first compile. Background Blender compiles when a
    chain repeats."""
    if IDLE_COMPILE is not None:
        return IDLE_COMPILE
    import bpy
    return not bpy.app.background


_registry = None   # (identity, OrderedDict of digests): the fused shaders compiled on this machine


def _identity(dev):
    """What a compiled shader's disk caches depend on: Blender, the backend and the GPU driver."""
    import bpy
    import gpu
    try:
        return [bpy.app.version_string, dev.backend, gpu.platform.renderer_get(), gpu.platform.version_get()]
    except Exception:
        return [bpy.app.version_string, dev.backend]


def _registry_file():
    """In the extension's user directory (the add-on's config directory when not installed as
    an extension), as the shelf and the material director keep theirs."""
    import bpy
    from pathlib import Path
    package = __package__.split('.evaluation')[0]
    if package.startswith('bl_ext.'):
        directory = bpy.utils.extension_path_user(package, path='shaders', create=True)
    else:
        directory = bpy.utils.user_resource('CONFIG', path='open_painter', create=True)
    return Path(directory) / 'fused_shaders.json'


def _known(dev):
    global _registry
    identity = _identity(dev)
    if _registry is None or _registry[0] != identity:
        digests = OrderedDict()
        try:
            import json
            data = json.loads(_registry_file().read_text(encoding='utf-8'))
            if data.get('identity') == identity:
                digests = OrderedDict.fromkeys(data.get('digests', ()))
        except Exception:
            pass   # none yet, or unreadable: everything is compiled while idle once
        _registry = (identity, digests)
    return _registry[1]


def _register(dev, digest):
    digests = _known(dev)
    digests.pop(digest, None)
    digests[digest] = None
    while len(digests) > REGISTRY_ENTRIES:
        digests.popitem(last=False)
    try:
        import json
        path = _registry_file()
        partial = path.with_suffix('.partial')
        partial.write_text(json.dumps({'identity': _registry[0], 'digests': list(digests)}), encoding='utf-8')
        os.replace(partial, path)
    except Exception:
        pass   # only the next session's first compiles wait for an idle tick again


def _uniforms(engine, chain):
    """The chain's parameters, every member's in turn, in a buffer of the chain's own (a
    signature's shader is shared between texture sets; their parameters are not)."""
    import gpu
    import numpy as np
    parts = []
    for key in chain:
        program = engine.states[key].program
        slots = program.parameter_slots
        if not slots:
            continue
        native = program.native_program
        native.update(program)
        data = bytes(native.data())
        if len(data) != slots * 16:
            raise ValueError(f'{key}: {len(data)} parameter bytes for {slots} slots')
        parts.append(data)
    data = b''.join(parts)
    if not data:
        return None
    buffers = engine.__dict__.setdefault('_fusion_buffers', {})
    ubo, previous = buffers.pop(chain, (None, None))   # re-inserted last: most recent use
    if ubo is None or previous is None or len(previous) != len(data):
        values = np.frombuffer(data, np.float32)
        ubo = gpu.types.GPUUniformBuf(gpu.types.Buffer('FLOAT', len(values), values))
    elif previous != data:
        values = np.frombuffer(data, np.float32)
        ubo.update(gpu.types.Buffer('FLOAT', len(values), values))
    buffers[chain] = (ubo, data)
    if len(buffers) > SHADERS:
        del buffers[next(iter(buffers))]
    return ubo


# ---- planning and execution -------------------------------------------------------------

def _collapsed(engine, root):
    """The members of the affine runs suffix collapse renders under ``root`` from their input
    (one armed pass for a run of any length): not fused; and each such run's input by its top."""
    from . import suffix
    if not suffix.ENABLED:
        return frozenset(), {}
    runs = [(top, source, members) for top, source, _, members in engine._native_graph.runs(engine, root)
            if len(members) >= suffix.MIN_LENGTH]
    return (frozenset(member for _, _, members in runs for member in members),
            {top: source for top, source, _ in runs if source is not None})


def chains(engine, root):
    """Fusable chains in the stale composition tree under ``root``, bottom-up, in an order
    that renders every chain before the chains above it."""
    return _chains(engine, root)[0]


def _chains(engine, root):
    """``chains``, the members of the runs collapse renders under ``root`` and their inputs."""
    from ...native_bridge.graph import MASK
    graph = engine._native_graph
    stamps, states, positions = graph.stamps, engine.states, engine.positions
    consumers, keep = engine.graph.consumers, engine.keep
    collapsed, inputs = _collapsed(engine, root)
    max_samplers = None
    found, stack, seen = [], [root], set()
    while stack:
        key = stack.pop()
        if key in seen:
            continue
        seen.add(key)
        state, desired = states.get(key), stamps.get(key)
        if state is None or desired is None:
            continue
        stamp = state.stamp
        if state.textures is not None and isinstance(stamp, int) and stamp & MASK == desired:
            continue   # resident with its content: nothing under it is needed
        chain = [key]
        if key not in collapsed and fusable(engine, key, state):
            if max_samplers is None:
                from .runtime import device
                max_samplers = device().max_samplers
            count = len(state.program.samplers)
            while len(chain) < MAX_LENGTH:
                upper = chain[-1]
                below = None
                # Down the backdrop; else, at a folder step, into the chain inside the folder.
                for index, ref in enumerate((positions[upper] or ())[:2]):
                    if ref is None or ref[0] != 'unit':
                        continue
                    if index == 1 and (not GROUPS or parse(states[upper].program).grouped is None):
                        continue
                    candidate = ref[1]
                    member = states.get(candidate)
                    if (member is None or candidate in seen or candidate in keep or candidate in collapsed
                            or consumers.get(candidate)):
                        continue
                    # A member below the top is only read by the next one, and its content
                    # changes: an unchanged result is rendered once and kept (a checkpoint).
                    current = member.stamp
                    if (isinstance(current, int) and current & MASK == stamps.get(candidate)
                            or not fusable(engine, candidate, member)):
                        continue
                    below = candidate
                    break
                if below is None:
                    break
                count += len(states[below].program.samplers) - 1
                if count > max_samplers:
                    break
                chain.append(below)
        seen.update(chain)
        if len(chain) >= MIN_LENGTH:
            found.append(tuple(reversed(chain)))
        # What the chain's members read besides each other: planned below it.
        for upper, lower in zip(chain, chain[1:] + [None]):
            for ref in positions.get(upper) or ():
                if ref is not None and ref[0] == 'unit' and ref[1] != lower:
                    stack.append(ref[1])
    found.reverse()
    return found, collapsed, inputs


def _produce(engine, key, n, memo, epochs):
    """``key``'s textures with their desired content: as they are when resident, else rendered
    (natively when the scheduler owns the unit)."""
    from ...native_bridge.graph import resident_stamp
    graph = engine._native_graph
    state = engine.states.get(key)
    if state is not None and resident_stamp(state) == graph.stamps.get(key, -1):
        if key in engine.retained:
            engine.retained.move_to_end(key)
            if engine._native_cache is not None:
                engine._native_cache.touch(key)
        return state.textures
    if (engine._native_stamps is not None and not graph.dispatching
            and n == engine.graph.n and key in engine._native_stamps):
        return graph.render(engine, key, n, memo, epochs)
    return engine._render(key, n, memo, epochs)


def _planned(engine, root):
    """``chains(engine, root)``, kept while the composition, the units whose own content
    changed and the kept results stay the same: then so do the chains (each member's content
    changes, so residency does not decide them). A drag plans once."""
    graph = engine._native_graph
    plans = engine.__dict__.setdefault('_fusion_plans', {})
    key = (graph.composition, graph.topology, engine.positions, getattr(graph, 'last_changed', None),
           frozenset(engine.keep))
    cached = plans.get(root)
    if cached is not None and len(cached[0]) == len(key) and all(a is b or a == b for a, b in zip(cached[0], key)):
        return cached[1:]
    found, collapsed, inputs = _chains(engine, root)
    plans[root] = (key, found, collapsed, inputs)
    return found, collapsed, inputs


def materialize(engine, root, n, memo, epochs, defer=False, inputs=None):
    """Render each fusable chain under ``root`` as one pass. ``defer``: the chain whose top is
    ``root`` (or ``root`` alone, when fusable), which the channel's publication pass renders
    (``publish``), is returned instead of rendered, with whether the same edit planned it on
    the previous evaluation too (``repeated``): (chain, repeated) or None. ``inputs`` (a dict):
    a chain that is the input of the collapsed run topped by ``root`` is left for that run's
    publication pass too (``publish_suffix``): ``inputs[root] = (chain, repeated)``."""
    graph = engine._native_graph
    if not ENABLED or graph is None or engine.profiling or graph.stamps is None:
        return None
    from ...native_bridge.graph import MASK, resident_stamp
    stamps, states = graph.stamps, engine.states

    def changes(key):
        state = states.get(key)
        return state is not None and not (isinstance(state.stamp, int) and state.stamp & MASK == stamps.get(key))

    planned, collapsed, run_inputs = _planned(engine, root)
    if not all(changes(key) for chain in planned for key in chain[:-1]):
        engine._fusion_plans.pop(root, None)   # a member keeps its content now: plan again
        planned, collapsed, run_inputs = _planned(engine, root)
    deferred = None
    if defer and root not in collapsed and resident_stamp(states[root]) != stamps.get(root):
        top = next((chain for chain in planned if chain[-1] == root), None)
        if top is None and fusable(engine, root, states[root]):
            top = (root,)
        if top is not None:
            deferred = (top, repeated(engine, ('publish', top)))
    handed = None
    if RUN_INPUTS and defer and inputs is not None and root in collapsed and root in run_inputs:
        source = run_inputs[root]
        handed = next((chain for chain in planned if chain[-1] == source), None)
        if (handed is None and source in states and source not in collapsed and source not in engine.keep
                and not engine.graph.consumers.get(source) and fusable(engine, source, states[source])):
            handed = (source,)   # a single changed unit (a folder step whose opacity is dragged)
        if handed is not None and resident_stamp(states[source]) != stamps.get(source):
            inputs[root] = (handed, repeated(engine, handed))
        else:
            handed = None
    for chain in planned:
        if (deferred is not None and chain is deferred[0]) or chain is handed:
            continue
        if resident_stamp(states[chain[-1]]) == stamps.get(chain[-1]):
            continue   # already rendered (by another channel's batch, say)
        # A chain's shader is compiled once the same edit renders the chain again (a drag's
        # second tick), never for a one-off render (applying a material, opening a file, an
        # undo, or the evaluations that settle them) nor after a structural edit (``_fresh``):
        # a blend mode toggled back and forth repeats too. While it waits for an idle compile,
        # the longest part of it below whose shader is compiled renders fused.
        if not _render(engine, chain, n, memo, epochs, build=repeated(engine, chain) and _fresh(engine)):
            for length in range(len(chain) - 1, MIN_LENGTH - 1, -1):
                if _render(engine, chain[:length], n, memo, epochs, build=False):
                    break
    seen, serial = engine.__dict__.get('_fusion_seen', {}), engine._suffix.serial
    if len(seen) > 4096:
        for item in [item for item, last in seen.items() if last[0] < serial - 1]:
            del seen[item]
    return deferred


def repeated(engine, item):
    """Whether the same edit planned ``item`` on the previous evaluation too (a drag's later
    ticks), recording it for the next one. Asked once per evaluation and item."""
    seen = engine.__dict__.setdefault('_fusion_seen', {})
    serial, edited = engine._suffix.serial, getattr(engine._native_graph, 'last_changed', None)
    last = seen.get(item)
    seen[item] = (serial, edited)
    return last is not None and last[0] == serial - 1 and last[1] == edited


def _above(engine):
    """Each unit's consumer through its backdrop or, for the top of a folder's chain, the folder
    step over it: the next step of its chain (cached per layout)."""
    cached = engine.__dict__.get('_fusion_above')
    if cached is None or cached[0] is not engine.positions:
        above = {}
        for key, position in engine.positions.items():
            for ref in (position or ())[:2]:
                if ref is not None and ref[0] == 'unit':
                    above.setdefault(ref[1], key)
        cached = engine.__dict__['_fusion_above'] = (engine.positions, above)
    return cached[1]


def prearm(engine, layer_id, n, memo, epochs):
    """While idle: compile and verify one fused shader an edit of ``layer_id`` would use, so
    the first tick of its drag needs neither. True when it did (call again later)."""
    from ...constants import CHANNELS
    from .runtime import device
    if not ENABLED or engine._native_graph is None:
        return False
    dev = device()
    if not store_rounding(dev):
        return False
    # First the shaders edits asked for that this machine had not compiled before.
    pending = engine.__dict__.get('_fusion_pending')
    while pending:
        _, again = pending.popitem(last=False)
        if again():
            metrics.count('engine_chain_idle_compiles')
            return True
    from . import suffix
    above, consumers, keep = _above(engine), engine.graph.consumers, engine.keep
    cache = getattr(dev, '_fused', None) or {}
    for channel in CHANNELS:
        key = ('CHANNEL', layer_id, channel)
        if key not in engine.states or not fusable(engine, key):
            continue
        # The edited unit and what changes above it: the chain an edit renders, up to the run
        # suffix collapse would fold over it (``_collapsed``), through folder steps (``_link``).
        final, collapsed = engine.finals.get(channel), set()
        if suffix.ENABLED and final is not None and final[0] == 'unit':
            for _, _, _, members in engine._native_graph.edit_runs(final[1], key):
                if len(members) >= suffix.MIN_LENGTH:
                    collapsed.update(members)
        chain, count = [key], len(engine.states[key].program.samplers)
        while len(chain) < MAX_LENGTH:
            up = above.get(chain[-1])
            if (up is None or chain[-1] in keep or consumers.get(chain[-1]) or up in collapsed
                    or not fusable(engine, up)):
                break
            if _link(engine, chain[-1], up) == 'grouped' and (
                    not GROUPS or parse(engine.states[up].program).grouped is None):
                break
            count += len(engine.states[up].program.samplers) - 1
            if count > dev.max_samplers:
                break
            chain.append(up)
        if len(chain) < MIN_LENGTH:
            continue
        chain = tuple(chain)
        for member in chain:
            engine._state(member)
        entry = cache.get(_signature(engine, chain))
        if entry is not None and (entry.verified or entry.failed):
            continue
        if _render(engine, chain, n, memo, epochs, build=True, draw=False, interactive=False):
            metrics.count('engine_chain_prearms')
            return True
    return False


def _current(engine, chain):
    """Bring each member's program up to date, as rendering it alone would (planning read the
    states as they were); False, and a new plan next time, when one is no longer fusable."""
    for key in chain:
        if not fusable(engine, key, engine._state(key)):
            engine.__dict__.get('_fusion_plans', {}).clear()
            return False
    return True


def _bind(engine, chain, n, memo, epochs, hold):
    """Per member, [(its sampler name, texture)]: every backdrop, group, import and image, but
    the input a member above the bottom reads from a register, its predecessor's result (None)."""
    def composite(ref, key):
        if ref is not None and ref[0] == 'unit':
            hold(ref[1])
            return _produce(engine, ref[1], n, memo, epochs)[0]
        return engine._texture_of(ref or ('const', (0.0, 0.0, 0.0, 0.0)), n, memo, epochs, scalar_unit(key))

    members = []
    for index, key in enumerate(chain):
        link = _link(engine, chain[index - 1], key) if index else None
        position = engine.positions[key]
        bound = []
        for sampler in engine.states[key].program.samplers:
            if sampler.kind == link:
                texture = None
            elif sampler.kind == 'backdrop':
                texture = composite(position[0], key)
            elif sampler.kind == 'grouped':
                texture = composite(position[1], key)
            elif sampler.kind == 'import':
                producer, target = sampler.producer
                texture = _produce(engine, producer, n, memo, epochs)[target]
                hold(producer)
            else:
                texture = engine._bind(sampler, n, memo, epochs, None, None, key)
            bound.append((sampler.name, texture))
        members.append(bound)
    return members


def _release(engine, held, n):
    for key in held:
        engine._in_use[key] -= 1
        if key[0] == 'CHANNEL':
            engine._settle(key, n)
        elif key[0] == 'FIELD':
            engine._drop_copy(key, n)


def _settled(engine, chain, held, n, keep_top):
    """After a fused pass: C records the members as rendered with their local stamps (they stay
    run members); each member holds its desired content, the top in its texture when
    ``keep_top``, the others in none, as C sees an evicted result (a stale current stamp would
    make one the lowest changed unit, and the checkpoints would keep the wrong results)."""
    graph = engine._native_graph
    stamps = graph.stamps
    graph.mark_rendered(chain)
    for key in chain if not keep_top else chain[:-1]:
        member = engine.states[key]
        engine._free(key, member, n)
        member.stamp = stamps[key]
        engine._note_reads(key, True, ())
    engine.rendered.append(chain[-1])
    engine._note_reads(chain[-1], True, held)


def _render(engine, chain, n, memo, epochs, build=True, draw=True, interactive=True):
    """Render ``chain`` as one pass (``draw``), or only compile and verify its shader; True
    when that succeeded."""
    from .runtime import device
    dev = device()
    if not _current(engine, chain):
        return False
    entry = _fused(dev, engine, chain, build, interactive=interactive)
    if entry is None:
        return False
    held = []

    def hold(key):
        engine._in_use[key] += 1
        held.append(key)

    try:
        top = chain[-1]
        members = _bind(engine, chain, n, memo, epochs, hold)
        samplers = [(entry.names[index][name], texture) for index, bound in enumerate(members)
                    for name, texture in bound if texture is not None]
        try:
            ubo = _uniforms(engine, chain)
        except ValueError:
            entry.failed = True
            metrics.count('engine_chain_layout_failures')
            return False
        if not entry.verified:
            if not _verify(dev, engine, chain, entry, members, samplers, ubo, n):
                entry.failed = True
                metrics.count('engine_chain_mismatches')
                return False
            entry.verified = True
        if not draw:
            return True
        state = engine.states[top]
        if state.textures is None:
            allocated = []
            try:
                for fmt in state.formats:
                    allocated.append(dev.texture(n, fmt))
            except Exception:
                for texture, fmt in zip(allocated, state.formats):
                    dev.release(texture, n, fmt)
                raise
            state.textures = allocated
        # A failed submission may have overwritten the target: never a cache hit then.
        state.stamp = None
        dev.draw(entry.shader, state.textures, n, samplers, ubo)
        state.stamp = engine._native_graph.stamps[top]
        _settled(engine, chain, held, n, keep_top=True)
        engine.stages['passes']['CHAIN'] += 1
        metrics.count('engine_chain_passes')
        metrics.count('engine_chain_units', len(chain))
        metrics.count('engine_chain_group_links', _group_links(engine, chain))
        if engine.graph.consumers.get(top):
            engine._retain(top, state, n)
        return True
    finally:
        _release(engine, held, n)


def render_deferred(engine, deferred, n, memo, epochs):
    """A chain ``materialize`` deferred to the publication pass that could not publish: render
    it as one pass as ``materialize`` would have (a chain of one renders as it is)."""
    chain, build = deferred
    if len(chain) >= MIN_LENGTH:
        _render(engine, chain, n, memo, epochs, build=build and _fresh(engine))


def publishable(engine, chain, dev):
    """Whether ``chain``'s top, a channel's final, can be rendered in its publication pass: the
    images are written in place (no pixel route, no revision staging them), and nothing else
    reads the final or keeps it. Such a final holds no texture until an idle tick keeps it
    (``Engine._keep_fused_finals``), which runs where idle arming does."""
    from .suffix import prearm_enabled
    top = chain[-1]
    return (ENABLED and PUBLISH and prearm_enabled() and not dev.pixel_publish and not engine.profiling
            and not (engine._native_revisions is not None and engine._native_revisions.submitting)
            and not engine.graph.consumers.get(top) and top not in engine.keep)


def _fresh(engine):
    """Whether this evaluation changed parameters only (no program was replaced): a publication
    shader is compiled and verified then, on a drag's ticks. After a structural edit only a shader
    already verified publishes: each new one costs a compile and a full-size check per channel."""
    return getattr(engine._native_graph, 'path', None) == 'valid'


def publish(engine, chain, image, channel, n, memo, epochs, build=True):
    """Render ``chain``, whose top is ``channel``'s final, in the channel's publication pass:
    the final's own pass and the read of its texture are saved, and the final keeps no texture.
    True when it published; False, and the caller renders and publishes as usual, otherwise."""
    from .runtime import _publication_format, device
    dev = device()
    if not publishable(engine, chain, dev) or not _current(engine, chain):
        return False
    scalar = engine.states[chain[-1]].formats[0] in ('RG16F', 'RG32F')
    donor = engine._donor(n) if engine.pad else None
    key = (channel == 'NORMAL', donor is not None, scalar)
    fresh = _fresh(engine)
    entry = _fused(dev, engine, chain, build and fresh, key)
    if entry is None or not (entry.verified or fresh):
        return False
    held = []

    def hold(item):
        engine._in_use[item] += 1
        held.append(item)

    try:
        members = _bind(engine, chain, n, memo, epochs, hold)
        samplers = [(entry.names[index][name], texture) for index, bound in enumerate(members)
                    for name, texture in bound if texture is not None]
        if donor is not None:
            samplers.append(('op_publish_donor', donor))
        try:
            ubo = _uniforms(engine, chain)
        except ValueError:
            entry.failed = True
            metrics.count('engine_chain_layout_failures')
            return False
        if not entry.verified:
            if not _verify_publish(dev, engine, chain, entry, members, samplers, ubo, n, key, donor, channel):
                entry.failed = True
                metrics.count('engine_chain_mismatches')
                return False
            entry.verified = True
        if engine._presentation is not None:
            engine._presentation.forget(channel)
        engine._begin_publication(image, channel, n)
        engine._publish_in_place(image, channel, n, _publication_format(channel, engine.precise, False),
                                 lambda slots: dev.draw(entry.shader, slots, n, samplers, ubo))
        _settled(engine, chain, held, n, keep_top=False)
        engine._note_fused_final(chain[-1], None, [key for key in held if key[0] == 'CHANNEL'], n)
        engine.stages['passes']['CHAIN publish'] += 1
        metrics.count('engine_chain_publications')
        metrics.count('engine_chain_units', len(chain))
        metrics.count('engine_chain_group_links', _group_links(engine, chain))
        return True
    finally:
        _release(engine, held, n)


def _suffix_apply(handed=False):
    """The library's ``suffix.apply`` as (its helpers, ``op_suffix_value(ivec2 p)``), evaluated at
    any texel; ``handed``: ``op_suffix_value(ivec2 p, vec4 op_suffix_input)``, the run's input
    handed over in a register (a fused chain's result) instead of fetched. None when that source
    cannot be read so."""
    from ...native_bridge.compiler import fixed_source
    apply = fixed_source('suffix.apply', None)
    texel = '  ivec2 p = ivec2(gl_FragCoord.xy);\n'
    fetch = 'op_suffix_fetch(op_source, p)'
    if (not apply or apply.count(MAIN) != 1 or apply.count(texel) != 1 or apply.count('gl_FragCoord') != 1
            or apply.count('op_out0 = ') != 1 or apply.count(fetch) != 1):
        return None
    head, body = apply.split(MAIN)
    body = body.replace(texel, '').replace('op_out0 = ', 'return ')
    if handed:
        return head, 'vec4 op_suffix_value(ivec2 p, vec4 op_suffix_input) {' + body.replace(fetch, 'op_suffix_input')
    return head, 'vec4 op_suffix_value(ivec2 p) {' + body


def _suffix_source(rounding, fmt, publish):
    """The publication of a collapsed run's top (``suffix.apply``, the library's own source,
    evaluated at the published texel and rounded as the top's ``fmt`` texture holds it), or None
    when that source cannot be read so: (source, samplers)."""
    apply = _suffix_apply()
    if apply is None:
        return None
    head, function = apply
    samplers = ['op_source', 'op_a', 'op_b']
    source = ('ivec2 op_publish_texel;\n' + head + STORES[rounding]
              + f'vec4 op_suffix_round(vec4 v) {{ return {ROUND[fmt].format(rounding)}; }}\n' + function
              + _publication_main(publish, samplers, [], 'op_suffix_round(op_suffix_value(op_publish_texel))'))
    return source, samplers


def _suffix_fused(dev, engine, fmt, publish, build, interactive=True):
    """The shader a collapsed run's top publishes with (``_shader``): one per top format and
    publication variant."""
    def make():
        built = _suffix_source(store_rounding(dev), fmt, publish)
        return None if built is None else (built[0], [], built[1], 0, 2, ('op_swizzle',))

    def again():
        return _suffix_fused(dev, engine, fmt, publish, True, interactive=False) is not None

    return _shader(dev, engine, ('suffix', fmt, publish), build, make, interactive, again)


def publish_suffix(engine, run, image, channel, n, memo, epochs, chain=None):
    """Apply ``run`` ((top, its input key or None, its bottom, its transform), collapsed and
    topped by ``channel``'s final) in the channel's publication pass: the top's own pass, its
    texture and the publication's read of it are saved. ``chain``: the run's input is that fused
    chain's top, evaluated in the same pass (its pass and texture are saved too). True when it
    published; False, and the caller renders and publishes as usual, otherwise."""
    from .runtime import device
    dev = device()
    top, source, bottom, entry = run
    if not publishable(engine, (top,), dev):
        return False
    state = engine.states[top]
    fmt = state.formats[0]
    donor = engine._donor(n) if engine.pad else None
    key = (channel == 'NORMAL', donor is not None, fmt in ('RG16F', 'RG32F'))
    if chain is not None:
        if chain[-1] != source or not _current(engine, chain):
            return False
        engine._suffix.active = entry   # never reclaimed while the chain's inputs render
        try:
            return _publish_chain_run(engine, dev, run, chain, image, channel, n, memo, epochs, key, donor)
        finally:
            engine._suffix.active = None
    fresh = _fresh(engine)
    fused = _suffix_fused(dev, engine, fmt, key, fresh)
    if fused is None or not (fused.verified or fresh):
        return False
    engine._suffix.active = entry   # never reclaimed while its input renders
    if source is not None:
        engine._in_use[source] += 1
    try:
        published = _publish_run(engine, dev, run, fused, image, channel, n, memo, epochs, key, donor)
        if published:
            engine._note_fused_final(run[0], run, (source,) if source is not None else (), n)
    finally:
        engine._suffix.active = None
        if source is not None:
            engine._in_use[source] -= 1
    if published and source is not None:
        engine._settle(source, n)
    return published


def _publish_run(engine, dev, run, fused, image, channel, n, memo, epochs, key, donor):
    from .runtime import _publication_format
    from .suffix import pack
    top, source, bottom, entry = run
    state = engine.states[top]
    fmt = state.formats[0]
    ref = ('unit', source) if source is not None else engine.positions[bottom][0]
    x = engine._texture_of(ref, n, memo, epochs, scalar_unit(bottom))
    samplers = [('op_source', x), ('op_a', entry.a), ('op_b', entry.b)]
    if donor is not None:
        samplers.append(('op_publish_donor', donor))
    ints = {'op_swizzle': pack(entry.source)}
    if not fused.verified:
        def sequential(target):
            dev.draw(dev.fixed('suffix.apply'), [target], n, samplers[:3], ints=ints)

        def draw(slots, region):
            dev.draw(fused.shader, slots, n, samplers, ints=ints, region=region)

        if not _verify_publication(dev, engine, fmt, sequential, draw, n, key, donor, channel):
            fused.failed = True
            metrics.count('engine_chain_mismatches')
            return False
        fused.verified = True
    if engine._presentation is not None:
        engine._presentation.forget(channel)
    engine._begin_publication(image, channel, n)
    engine._publish_in_place(image, channel, n, _publication_format(channel, engine.precise, False),
                             lambda slots: dev.draw(fused.shader, slots, n, samplers, ints=ints))
    # The top holds its desired content in no texture, as C sees an evicted result.
    previous = state.stamp
    engine._free(top, state, n)
    state.stamp = engine._native_graph.stamps[top]
    engine.rendered.append(top)
    engine._note_reads(top, previous != state.stamp, (source,) if source is not None else ())
    engine.stages['passes']['SUFFIX publish'] += 1
    metrics.count('engine_suffix_applications')
    metrics.count('engine_suffix_publications')
    return True


def _chain_run_fused(dev, engine, chain, fmt, publish, build, interactive=True):
    """The shader a chain publishes with through the collapsed run above it (``_shader``)."""
    def make():
        built = _source(engine, chain, store_rounding(dev), publish, run_format=fmt)
        return None if built is None else (*built, ('op_swizzle',))

    def again():
        return (all(key in engine.states for key in chain) and _current(engine, chain)
                and _chain_run_fused(dev, engine, chain, fmt, publish, True, interactive=False) is not None)

    return _shader(dev, engine, ('run', fmt, _signature(engine, chain, publish)), build, make, interactive, again)


def _publish_chain_run(engine, dev, run, chain, image, channel, n, memo, epochs, key, donor):
    from .runtime import _publication_format
    from .suffix import pack
    top, _, _, entry = run
    state = engine.states[top]
    fmt = state.formats[0]
    fresh = _fresh(engine)
    fused = _chain_run_fused(dev, engine, chain, fmt, key, fresh)
    if fused is None or not (fused.verified or fresh):
        return False
    held = []

    def hold(item):
        engine._in_use[item] += 1
        held.append(item)

    try:
        members = _bind(engine, chain, n, memo, epochs, hold)
        samplers = [(fused.names[index][name], texture) for index, bound in enumerate(members)
                    for name, texture in bound if texture is not None]
        samplers += [('op_a', entry.a), ('op_b', entry.b)]
        if donor is not None:
            samplers.append(('op_publish_donor', donor))
        try:
            ubo = _uniforms(engine, chain)
        except ValueError:
            fused.failed = True
            metrics.count('engine_chain_layout_failures')
            return False
        ints = {'op_swizzle': pack(entry.source)}
        if not fused.verified:
            def sequential(target):
                below, owned = None, []
                try:
                    for member, bound in zip(chain, members):
                        member_state = engine.states[member]
                        output = dev.texture(n, member_state.formats[0])
                        owned.append((output, member_state.formats[0]))
                        inputs = [(name, below if texture is None else texture) for name, texture in bound]
                        dev.draw(member_state.shader, [output], n, inputs, member_state.uniforms())
                        below = output
                    dev.draw(dev.fixed('suffix.apply'), [target], n,
                             [('op_source', below), ('op_a', entry.a), ('op_b', entry.b)], ints=ints)
                finally:
                    for texture, member_fmt in owned:
                        dev.release(texture, n, member_fmt)

            def draw(slots, region):
                dev.draw(fused.shader, slots, n, samplers, ubo, ints=ints, region=region)

            if not _verify_publication(dev, engine, fmt, sequential, draw, n, key, donor, channel):
                fused.failed = True
                metrics.count('engine_chain_mismatches')
                return False
            fused.verified = True
        if engine._presentation is not None:
            engine._presentation.forget(channel)
        engine._begin_publication(image, channel, n)
        engine._publish_in_place(image, channel, n, _publication_format(channel, engine.precise, False),
                                 lambda slots: dev.draw(fused.shader, slots, n, samplers, ubo, ints=ints))
        # The chain's members and the run's top hold their desired content in no texture.
        _settled(engine, chain, held, n, keep_top=False)
        previous = state.stamp
        engine._free(top, state, n)
        state.stamp = engine._native_graph.stamps[top]
        engine.rendered.append(top)
        engine._note_reads(top, previous != state.stamp, ())
        engine._note_fused_final(top, run, [item for item in held if item[0] == 'CHANNEL'], n)
        engine.stages['passes']['SUFFIX publish'] += 1
        metrics.count('engine_suffix_applications')
        metrics.count('engine_suffix_publications')
        metrics.count('engine_chain_run_publications')
        metrics.count('engine_chain_units', len(chain))
        metrics.count('engine_chain_group_links', _group_links(engine, chain))
        return True
    finally:
        _release(engine, held, n)


def _read(texture, size, components):
    import gpu
    import numpy as np
    framebuffer = gpu.types.GPUFrameBuffer(color_slots=(texture,))
    with framebuffer.bind():
        buffer = framebuffer.read_color(0, 0, size, size, components, 0, 'FLOAT')
    buffer.dimensions = size * size * components
    return np.array(buffer, np.float32).view(np.uint32)


def _verify(dev, engine, chain, entry, members, samplers, ubo, n):
    """Render a corner of the chain both ways; True when they agree bit for bit. The corner's
    targets are small; the passes keep the set's texel scale (``n``) and read its inputs."""
    import numpy as np
    size = min(n, VERIFY_SIZE)
    region = (0, 0, size, size)
    owned = []
    try:
        below = None
        for key, bound in zip(chain, members):
            state = engine.states[key]
            target = dev.texture(size, state.formats[0])
            owned.append((target, size, state.formats[0]))
            inputs = [(name, below if texture is None else texture) for name, texture in bound]
            dev.draw(state.shader, [target], n, inputs, state.uniforms(), region=region)
            below = target
        fmt = engine.states[chain[-1]].formats[0]
        fused = dev.texture(size, fmt)
        owned.append((fused, size, fmt))
        dev.draw(entry.shader, [fused], n, samplers, ubo, region=region)
        components = 2 if fmt in ('RG16F', 'RG32F') else 4
        same = np.array_equal(_read(below, size, components), _read(fused, size, components))
        dev.settled = dev.passes
        return same
    finally:
        for texture, side, fmt in owned:
            dev.release(texture, side, fmt)


def _verify_publish(dev, engine, chain, entry, members, samplers, ubo, n, key, donor, channel):
    """``_verify_publication`` of a fused chain: its members render one pass each."""
    def sequential(target):
        below, owned = None, []
        try:
            for index, (member, bound) in enumerate(zip(chain, members)):
                state = engine.states[member]
                if index == len(chain) - 1:
                    output = target
                else:
                    output = dev.texture(n, state.formats[0])
                    owned.append((output, state.formats[0]))
                inputs = [(name, below if texture is None else texture) for name, texture in bound]
                dev.draw(state.shader, [output], n, inputs, state.uniforms())
                below = output
        finally:
            for texture, fmt in owned:
                dev.release(texture, n, fmt)

    def draw(slots, region):
        dev.draw(entry.shader, slots, n, samplers, ubo, region=region)

    return _verify_publication(dev, engine, engine.states[chain[-1]].formats[0], sequential, draw, n, key,
                               donor, channel)


def _verify_publication(dev, engine, fmt, sequential, draw, n, key, donor, channel):
    """A corner published both ways: the final rendered as usual into a texture of ``fmt`` over
    the whole set (``sequential(target)``; a padding texel's donor may lie anywhere) and the
    publication pass, against the fused pass (``draw(slots, region)``); True when the images and
    their mip copies agree bit for bit."""
    import numpy as np
    from .runtime import RGBA, _publication_format
    normal, padded, scalar = key
    size = min(n, VERIFY_SIZE)
    region = (0, 0, size, size)
    copy_fmt = _publication_format(channel, engine.precise, False)
    owned = []
    try:
        below = dev.texture(n, fmt)
        owned.append((below, n, fmt))
        sequential(below)
        outputs = []
        for fused in (False, True):
            image, copy = dev.texture(size, RGBA), dev.texture(size, copy_fmt)
            owned += [(image, size, RGBA), (copy, size, copy_fmt)]
            if fused:
                draw([image, copy], region)
            else:
                dev.draw(dev.fixed('publish'), [image, copy], n,
                         [('op_source', below), ('op_donor', donor if donor is not None else below)],
                         ints={'op_mode': int(normal), 'op_pad': int(padded), 'op_scalar': int(scalar)},
                         region=region)
            outputs.append((_read(image, size, 4), _read(copy, size, 4)))
        dev.settled = dev.passes
        return all(np.array_equal(a, b) for a, b in zip(*outputs))
    finally:
        for texture, side, fmt_ in owned:
            dev.release(texture, side, fmt_)
