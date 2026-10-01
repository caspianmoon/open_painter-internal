"""Native semantic blends vs independent legacy GPU and NumPy references on Metal."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import gpu
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.evaluation import blend_modes
from open_painter.evaluation.engine import glsl, headless, runtime
from open_painter.evaluation.shader_math import Math
from open_painter.native_bridge import compiler, lifecycle

MODES = ('NORMAL DISABLE MULTIPLY ADD SUBTRACT DARKEN LIGHTEN DIVIDE DIFFERENCE '
         'EXCLUSION SCREEN LINEAR_BURN COLOR_BURN COLOR_DODGE OVERLAY HARD_LIGHT '
         'SOFT_LIGHT VIVID_LIGHT LINEAR_LIGHT PIN_LIGHT HUE SATURATION COLOR LUMINOSITY '
         'NORMAL_COMBINE RNM NORMAL_ADD NORMAL_REPLACE').split()


def program(lib, mode, opaque, native):
    tree = headless.Tree('Blend parity')
    unit = SimpleNamespace(tree=tree, key=('CHANNEL', 'fixture', 'BASE_COLOR'))
    graph = SimpleNamespace(units={unit.key: unit})
    translator = compiler.Snapshot(lib, graph, unit) if native else glsl.Translator(graph, unit)
    math = Math(tree)
    node = math.node('OPBlend')
    node.operation, node.opaque = mode, opaque
    # Bind three packed input textures without host image/color management.
    if native:
        fetches = [translator.add(compiler.FETCH, headless.RGBA, aux=(i,)) for i in range(3)]
    for index, (texture, component, kind) in enumerate(((0, 'rgb', headless.VECTOR),
            (0, 'a', headless.VALUE), (1, 'rgb', headless.VECTOR),
            (1, 'a', headless.VALUE), (2, 'r', headless.VALUE))):
        socket = math.node('ShaderNodeRGB' if kind == headless.VECTOR else 'ShaderNodeValue').outputs[0]
        math.link(socket, node.inputs[index])
        if native:
            value = (translator.convert(fetches[texture], headless.RGBA, headless.VECTOR)
                     if component == 'rgb' else translator.add(compiler.COMPONENT, headless.VALUE,
                                      (fetches[texture],), (3 if component == 'a' else 0,)))
        else:
            value = f'texelFetch(op_s{texture}, op_texel, 0).{component}'
        translator._set(socket, value, kind)
    for i in range(3):
        translator._sampler(('fixture', i), 'fixture')
    color = translator.output(node.outputs['Color'])[0]
    alpha = translator.output(node.outputs['Alpha'])[0]
    if native:
        assert sum(n.opcode == compiler.BLEND for n in translator.ir) == 1
        assert len(translator.ir) == 11, len(translator.ir)
        return translator.compile([(color, alpha, 0)], [('Color', 'Alpha')])
    source = ('void main() { ivec2 op_texel=ivec2(gl_FragCoord.xy);\n'
              + '\n'.join(translator.lines) + f'\nop_out0=vec4({color},{alpha});\n}}')
    return glsl.Program(source, translator.samplers, [], [], [('Color', 'Alpha')], {})


def reference(base, source, coverage, mode, opaque):
    ba, sa = base[..., 3:], source[..., 3:]
    a = np.clip(sa * coverage[..., :1], 0, 1)
    if mode == 'DISABLE':
        return base.copy()
    if opaque:
        color = blend_modes.blend(base[..., :3], source[..., :3], a, mode)
        return np.concatenate((color, np.ones_like(ba)), axis=-1)
    straight = base[..., :3] * np.divide(1., ba, out=np.zeros_like(ba), where=np.abs(ba) > 1e-8)
    mixed = blend_modes.blend(straight, source[..., :3], 1., mode)
    contributing = source[..., :3] + (mixed-source[..., :3])*ba
    return np.concatenate((base[..., :3]*(1.-a)+contributing*a, a+ba*(1.-a)), axis=-1)


gpu.init()
open_painter.register()
try:
    lib = lifecycle.execution_library(gpu.platform.backend_type_get())
    assert lib is not None
    dev, n = runtime.device(), 32
    rng = np.random.default_rng(9841)
    source = rng.random((n, n, 4), dtype=np.float32)
    base = rng.random((n, n, 4), dtype=np.float32)
    coverage = rng.uniform(-.5, 1.5, (n, n, 4)).astype(np.float32)
    # Singular denominators, branch boundaries, gray colors and degenerate normals.
    edges = np.array([0, 1e-9, 1e-8, 1e-7, .25, .5, 1.-1e-7, 1.], np.float32)
    base[:8, :, :3] = edges[:, None, None]
    source[:, :8, :3] = edges[None, :, None]
    base[:, ::4, 3] = 0
    base[:, 1::4, 3] = 1
    worst = {}
    for opaque in (False, True):
        inputs = base.copy()
        if opaque:
            inputs[..., 3] = 1
        else:
            inputs[..., :3] *= inputs[..., 3:]
        textures = [dev.upload(a) for a in (inputs, source, coverage)]
        target = dev.texture(n, 'RGBA32F')
        try:
            for mode in MODES:
                results = []
                for native in (True, False):
                    p = program(lib, mode, opaque, native)
                    shader = dev.shader(p)
                    with dev.state():
                        dev.draw(shader, [target], n, list(zip(('op_s0', 'op_s1', 'op_s2'), textures)))
                    results.append(np.asarray(dev.readback(target, n)).reshape(n, n, 4).copy())
                expected = reference(inputs, source, coverage, mode, opaque)
                errors = [float(np.abs(results[0]-other).max()) for other in (results[1], expected)]
                assert all(np.isfinite(a).all() for a in results)
                assert max(errors) <= 2e-4, (mode, opaque, errors)
                worst[f'{mode}:{opaque}'] = errors
        finally:
            dev.discard(target)
            for texture in textures:
                dev.discard(texture)
    print('NATIVE_BLEND_PASS', json.dumps({'modes': len(MODES), 'pixels_per_mode': n*n*2,
          'max_legacy_error': max(v[0] for v in worst.values()),
          'max_cpu_error': max(v[1] for v in worst.values())}), flush=True)
finally:
    open_painter.unregister()
