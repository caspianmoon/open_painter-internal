"""Chain fusion: a run of changed sibling units rendered as one pass, bit for bit.

Inside an isolated folder (a smart material), layers composite with straight alpha over a
transparent start. That is not affine in the backdrop, so suffix collapse cannot fold
them (suffix.py), nor a run of non-affine blend modes. Editing a layer there re-renders
every sibling above it, one pass each. Each pass writes a texture that the next one reads
back at the same texel.

A fused pass runs the members' own programs one after another in registers. Between two
members it rounds the value as the member's texture store would. ``store_rounding``
finds the device's float-to-half conversion once, by comparing real stores with the
emulations. The top's result is then the sequential result bit for bit. Each fused
shader is also checked once against sequential rendering before it is used; a mismatch
retires it.

C plans the passes (op_graph_execute). Like a collapsed suffix run, a fused chain
materializes its top before C plans the channel: the top then holds its desired stamp
and C treats it as rendered. The members below the top keep no texture; their stamps say
they hold their desired content, as an evicted result's would, and they are rendered
again only if something else needs them.

A chain's shader is compiled when the chain repeats on consecutive evaluations (a drag),
or while idle for the selected layer (``prearm``), never for one-off renders.
"""
import os
import re
from collections import OrderedDict
from dataclasses import dataclass

from .. import metrics
from .formats import scalar_unit

ENABLED = os.environ.get('OP_FUSE_CHAINS', '1') != '0'
MIN_LENGTH = 2
MAX_LENGTH = 8
SHADERS = 64                    # fused shaders kept per device, least recently used first
VERIFY_SIZE = 128               # the region each fused shader is checked on, once

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
  return roundEven(x * 16777216.0) * (1.0 / 16777216.0);
}
''',
}
# What reading a member's texture back gives, per format: R/RG reads fill (0, 1).
ROUND = {
    'RGBA16F': 'vec4({0}(v.x), {0}(v.y), {0}(v.z), {0}(v.w))',
    'RG16F': 'vec4({0}(v.x), {0}(v.y), 0.0, 1.0)',
    'RGBA32F': 'v',
    'RG32F': 'vec4(v.x, v.y, 0.0, 1.0)',
}


@dataclass
class Part:
    """A unit program split for fusion: its helpers, the body of main and its backdrop."""
    head: str
    body: str
    backdrop: str


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
    if len(backdrops) != 1 or any(s.kind in ('grouped', 'filter') or s.source is not None
                                  for s in program.samplers):
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
    return Part(head, body, backdrop)


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
    # Affine units are suffix collapse's: one armed pass for a run of any length.
    result = (program is not None and len(formats) == 1 and formats[0] in ROUND and bool(position)
              and position[0] is not None and position[1] is None and program.affine is None
              and parse(program) is not None)
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
    shader = dev._create(functions + _PATTERN + 'void main() { vec4 v = op_value(); op_out0 = ' + body + '; }\n', [])
    texture = dev.texture(n, fmt)
    dev.draw(shader, [texture], n)
    # Read through an RGBA32F copy, so R/RG formats expand as a fetch sees them.
    full = dev.texture(n, 'RGBA32F')
    dev.draw(dev.fixed('copy'), [full], n, [('op_source', texture)])
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


def _source(engine, chain, rounding):
    from .procedural_glsl import library
    parts = [parse(engine.states[key].program) for key in chain]
    head = _merged(part.head for part in parts)
    procedurals = sorted({kind for key in chain for kind in engine.states[key].program.procedurals})
    functions, names, samplers, offset = [], [], [], 0
    for index, (key, part) in enumerate(zip(chain, parts)):
        program = engine.states[key].program
        mapping = {s.name: f'op_f{index}_{s.name[3:]}' for s in program.samplers}
        body = part.body
        if index:
            body = body.replace(FETCH.format(part.backdrop), 'op_backdrop')
        body = SAMPLER.sub(lambda match: mapping['op_s' + match.group(1)], body)
        base = offset
        body = PARAM.sub(lambda match: f'op_params.p[{int(match.group(1)) + base}]', body)
        body = body.replace('op_out0 = ', 'return ')
        signature = f'vec4 op_fused{index}(vec4 op_backdrop)' if index else f'vec4 op_fused{index}()'
        functions.append(f'{signature} {{{body}}}\n')
        names.append(mapping)
        samplers.extend(mapping[s.name] for s in program.samplers if not (index and s.kind == 'backdrop'))
        offset += program.parameter_slots
    rounds = ''.join(f'vec4 op_fused_round{index}(vec4 v) {{ return '
                     f'{ROUND[engine.states[key].formats[0]].format(rounding)}; }}\n'
                     for index, key in enumerate(chain[:-1]))
    calls = ['  vec4 op_v = op_fused0();']
    calls += [f'  op_v = op_fused{index}(op_fused_round{index - 1}(op_v));' for index in range(1, len(chain))]
    source = (library(procedurals) if procedurals else '') + head + STORES[rounding] + rounds + ''.join(functions)
    source += MAIN + '\n' + '\n'.join(calls) + '\n  op_out0 = op_v;\n}\n'
    return source, names, samplers, offset


def _signature(engine, chain):
    """What a chain's fused source is made of: each member's program, layout and format."""
    signature = []
    for key in chain:
        state = engine.states[key]
        program = state.program
        signature.append((program.source, program.procedurals, program.parameter_slots,
                          tuple((s.name, s.kind) for s in program.samplers), state.formats[0]))
    return tuple(signature)


def _fused(dev, engine, chain, build):
    """The chain's fused shader: cached, or compiled now when ``build``; None otherwise."""
    cache = getattr(dev, '_fused', None)
    if cache is None:
        cache = dev._fused = OrderedDict()
    signature = _signature(engine, chain)
    entry = cache.get(signature)
    if entry is not None:
        cache.move_to_end(signature)
        return None if entry.failed else entry
    if not build:
        return None
    rounding = store_rounding(dev)
    built = _source(engine, chain, rounding) if rounding else None
    if built is None:
        cache[signature] = Fused(None, [], failed=True)
        return None
    source, names, samplers, slots = built
    if len(samplers) > dev.max_samplers:
        cache[signature] = Fused(None, [], failed=True)
        return None
    try:
        shader = dev._create(source, samplers, slots, 0, 1)
    except Exception:
        # The members render one pass each, as without fusion.
        cache[signature] = Fused(None, [], failed=True)
        metrics.count('engine_chain_build_failures')
        return None
    metrics.count('engine_chain_compiles')
    entry = cache[signature] = Fused(shader, names)
    while len(cache) > SHADERS:
        cache.popitem(last=False)
    return entry


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

def chains(engine, root):
    """Fusable chains in the stale composition tree under ``root``, bottom-up, in an order
    that renders every chain before the chains above it."""
    from ...native_bridge.graph import MASK
    graph = engine._native_graph
    stamps, states, positions = graph.stamps, engine.states, engine.positions
    consumers, keep = engine.graph.consumers, engine.keep
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
        if fusable(engine, key, state):
            if max_samplers is None:
                from .runtime import device
                max_samplers = device().max_samplers
            count = len(state.program.samplers)
            while len(chain) < MAX_LENGTH:
                ref = positions[chain[-1]][0]
                if ref[0] != 'unit':
                    break
                below = ref[1]
                member = states.get(below)
                if member is None or below in seen or below in keep or consumers.get(below):
                    break
                # A member below the top is only read by the next one, and its content
                # changes: an unchanged result is rendered once and kept (a checkpoint).
                current = member.stamp
                if (isinstance(current, int) and current & MASK == stamps.get(below)
                        or not fusable(engine, below, member)):
                    break
                count += len(member.program.samplers) - 1
                if count > max_samplers:
                    break
                chain.append(below)
        seen.update(chain)
        if len(chain) >= MIN_LENGTH:
            found.append(tuple(reversed(chain)))
        for ref in positions.get(chain[-1]) or ():
            if ref is not None and ref[0] == 'unit':
                stack.append(ref[1])
    found.reverse()
    return found


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
        return cached[1]
    found = chains(engine, root)
    plans[root] = (key, found)
    return found


def materialize(engine, root, n, memo, epochs):
    """Render each fusable chain under ``root`` as one pass; returns the chains rendered."""
    graph = engine._native_graph
    if not ENABLED or graph is None or engine.profiling or graph.stamps is None:
        return 0
    from ...native_bridge.graph import MASK, resident_stamp
    stamps, states = graph.stamps, engine.states

    def changes(key):
        state = states.get(key)
        return state is not None and not (isinstance(state.stamp, int) and state.stamp & MASK == stamps.get(key))

    planned = _planned(engine, root)
    if not all(changes(key) for chain in planned for key in chain[:-1]):
        engine._fusion_plans.pop(root, None)   # a member keeps its content now: plan again
        planned = _planned(engine, root)
    seen = engine.__dict__.setdefault('_fusion_seen', {})
    serial, edited = engine._suffix.serial, getattr(graph, 'last_changed', None)
    rendered = 0
    for chain in planned:
        if resident_stamp(states[chain[-1]]) == stamps.get(chain[-1]):
            continue   # already rendered (by another channel's batch, say)
        # A chain's shader is compiled once the same edit renders the chain again (a drag's
        # second tick), never for a one-off render: applying a material, opening a file, an
        # undo, or the evaluations that settle them.
        last = seen.get(chain)
        repeated = last is not None and last[0] == serial - 1 and last[1] == edited
        seen[chain] = (serial, edited)
        if _render(engine, chain, n, memo, epochs, build=repeated):
            rendered += 1
    if len(seen) > 4096:
        for chain in [chain for chain, last in seen.items() if last[0] < serial - 1]:
            del seen[chain]
    return rendered


def _above(engine):
    """Each unit's backdrop consumer: the next step of its chain (cached per layout)."""
    cached = engine.__dict__.get('_fusion_above')
    if cached is None or cached[0] is not engine.positions:
        above = {}
        for key, position in engine.positions.items():
            if position and position[0] is not None and position[0][0] == 'unit':
                above.setdefault(position[0][1], key)
        cached = engine.__dict__['_fusion_above'] = (engine.positions, above)
    return cached[1]


def prearm(engine, layer_id):
    """While idle: compile one fused shader an edit of ``layer_id`` would use, so the first
    ticks of its drag need no compilation. True when it compiled one (call again later)."""
    from ...constants import CHANNELS
    from .runtime import device
    if not ENABLED or engine._native_graph is None:
        return False
    dev = device()
    if not store_rounding(dev):
        return False
    above, consumers, keep = _above(engine), engine.graph.consumers, engine.keep
    cache = getattr(dev, '_fused', None) or {}
    for channel in CHANNELS:
        key = ('CHANNEL', layer_id, channel)
        if key not in engine.states or not fusable(engine, key):
            continue
        # The edited unit and the siblings above it change: the chain an edit renders.
        chain, count = [key], len(engine.states[key].program.samplers)
        while len(chain) < MAX_LENGTH:
            up = above.get(chain[-1])
            if up is None or chain[-1] in keep or consumers.get(chain[-1]) or not fusable(engine, up):
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
        if _signature(engine, chain) in cache:
            continue
        if _fused(dev, engine, chain, True) is not None:
            metrics.count('engine_chain_prearms')
            return True
    return False


def _render(engine, chain, n, memo, epochs, build=True):
    from .runtime import device
    dev = device()
    # Planning read the states as they were; each member's program is now brought up to
    # date, as rendering it alone would.
    for key in chain:
        if not fusable(engine, key, engine._state(key)):
            engine.__dict__.get('_fusion_plans', {}).clear()   # a program changed: plan again
            return False
    entry = _fused(dev, engine, chain, build)
    if entry is None:
        return False
    held = []

    def hold(key):
        engine._in_use[key] += 1
        held.append(key)

    try:
        bottom, top = chain[0], chain[-1]
        ref = engine.positions[bottom][0]
        if ref[0] == 'unit':
            backdrop = _produce(engine, ref[1], n, memo, epochs)[0]
            hold(ref[1])
        else:
            backdrop = engine._texture_of(ref, n, memo, epochs, scalar_unit(bottom))
        members = []    # per member: [(its sampler name, texture)]
        for index, key in enumerate(chain):
            bound = []
            for sampler in engine.states[key].program.samplers:
                if sampler.kind == 'backdrop':
                    texture = backdrop if index == 0 else None
                elif sampler.kind == 'import':
                    producer, target = sampler.producer
                    texture = _produce(engine, producer, n, memo, epochs)[target]
                    hold(producer)
                else:
                    texture = engine._bind(sampler, n, memo, epochs, None, None, key)
                bound.append((sampler.name, texture))
            members.append(bound)
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
        stamps = engine._native_graph.stamps
        state.stamp = stamps[top]
        # The members below the top now have their desired content, held by no texture: as C
        # sees an evicted result. A stale current stamp would make one of them the lowest
        # changed unit, and the checkpoints would keep the wrong results.
        for key in chain[:-1]:
            member = engine.states[key]
            engine._free(key, member, n)
            member.stamp = stamps[key]
            engine._note_reads(key, True, ())
        engine.rendered.append(top)
        engine._note_reads(top, True, held)
        engine.stages['passes']['CHAIN'] += 1
        metrics.count('engine_chain_passes')
        metrics.count('engine_chain_units', len(chain))
        if engine.graph.consumers.get(top):
            engine._retain(top, state, n)
        return True
    finally:
        for key in held:
            engine._in_use[key] -= 1
            if key[0] == 'CHANNEL':
                engine._settle(key, n)
            elif key[0] == 'FIELD':
                engine._drop_copy(key, n)


def _verify(dev, engine, chain, entry, members, samplers, ubo, n):
    """Render a corner of the chain both ways; True when they agree bit for bit. The corner's
    targets are small; the passes keep the set's texel scale (``n``) and read its inputs."""
    import gpu
    import numpy as np
    size = min(n, VERIFY_SIZE)
    region = (0, 0, size, size)
    owned = []
    try:
        below = None
        for index, (key, bound) in enumerate(zip(chain, members)):
            state = engine.states[key]
            target = dev.texture(size, state.formats[0])
            owned.append((target, state.formats[0]))
            inputs = [(name, below if texture is None else texture) for name, texture in bound]
            dev.draw(state.shader, [target], n, inputs, state.uniforms(), region=region)
            below = target
        fused = dev.texture(size, engine.states[chain[-1]].formats[0])
        owned.append((fused, engine.states[chain[-1]].formats[0]))
        dev.draw(entry.shader, [fused], n, samplers, ubo, region=region)
        fmt = engine.states[chain[-1]].formats[0]
        components = 2 if fmt.startswith('RG') and not fmt.startswith('RGBA') else 4
        results = []
        for texture in (below, fused):
            framebuffer = gpu.types.GPUFrameBuffer(color_slots=(texture,))
            with framebuffer.bind():
                buffer = framebuffer.read_color(0, 0, size, size, components, 0, 'FLOAT')
            buffer.dimensions = size * size * components
            results.append(np.array(buffer, np.float32).view(np.uint32))
        dev.settled = dev.passes
        return np.array_equal(*results)
    finally:
        for texture, fmt in owned:
            dev.release(texture, size, fmt)
