"""Every catalog source on the Open Painter engine against its NumPy definition.

Headless (``gpu.init``). Each kind is rendered by the engine's GLSL port over a
256² grid of coordinates spanning negative and positive values (triplanar
positions are world coordinates) for several scales and seeds, and compared with
``texture_engine.procedurals.evaluate`` on the same float32 coordinates. The
integer lattice hash is exact; transcendental functions may differ in their last
bits, which isolated threshold edges amplify on a few texels, so the check is on
the 99.9th percentile of the error plus a bound on the share of large errors.
"""
import json
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

REPORT = Path(os.environ.get('OP_ENGINE_PROCEDURALS_REPORT', '/private/tmp/op-engine-procedurals.json'))
N = 256
CASES = ((8.0, 1), (3.5, 17), (21.0, 402), (75.0, 1238065627))


def main():
    import gpu
    from open_painter.evaluation.engine import glsl
    from open_painter.evaluation.engine.procedural_glsl import KINDS, function_name, library
    from open_painter.evaluation.engine.runtime import Device
    from open_painter.texture_engine.procedurals import evaluate
    device = Device()
    centers = (np.arange(N, dtype=np.float32) + np.float32(.5)) / np.float32(N)
    u, v = np.meshgrid(centers, centers)
    # Coordinates as a projection hands them over: offset and scaled, partly negative.
    cu = (u * np.float32(3.0) - np.float32(1.5)).astype(np.float32)
    cv = (v * np.float32(3.0) - np.float32(1.25)).astype(np.float32)
    results, failures = {}, []
    for kind in sorted(KINDS):
        source = library([kind]) + (
            'void main() {\n'
            '  ivec2 texel = ivec2(gl_FragCoord.xy);\n'
            '  vec2 uv = (vec2(texel) + 0.5) * op_inv_size;\n'
            '  vec2 c = uv * 3.0 - vec2(1.5, 1.25);\n'
            '  float scale = op_params.p[0].x;\n'
            '  uint seed = uint(op_params.p[0].y) * 65536u + uint(op_params.p[0].z);\n'
            f'  float value = {function_name(kind)}(c, scale, seed);\n'
            '  op_out0 = vec4(value, value, value, 1.0);\n'
            '}\n')
        shader = device._create(source, [], parameter_slots=1, preamble=True)
        worst = []
        for scale, seed in CASES:
            target = device.texture(N)
            halves = [float((seed >> 16) & 0xFFFF), float(seed & 0xFFFF)]
            ubo = gpu.types.GPUUniformBuf(gpu.types.Buffer('FLOAT', 4, [scale, *halves, 0.0]))
            with device.state():
                device.draw(shader, [target], N, (), ubo)
            gpu_values = np.array(target.read(), np.float32).reshape(N, N, 4)[..., 0]
            device.release(target, N)
            cpu_values = evaluate(kind, cu, cv, seed=seed, scale=scale)
            diff = np.abs(gpu_values - cpu_values)
            worst.append(dict(scale=scale, seed=seed, max=float(diff.max()), p999=float(np.quantile(diff, .999)),
                              large_share=float((diff > .05).mean())))
        results[kind] = worst
        p999 = max(item['p999'] for item in worst)
        share = max(item['large_share'] for item in worst)
        if p999 > 2e-3 or share > 2e-3:
            failures.append((kind, p999, share))
    report = dict(status='FAIL' if failures else 'PASS', failures=failures, kinds=results)
    REPORT.write_text(json.dumps(report, indent=1))
    print('ENGINE_PROCEDURALS_' + report['status'], json.dumps(failures), flush=True)
    assert not failures, failures


main()
