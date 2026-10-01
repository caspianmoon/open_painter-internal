"""Refresh frozen C shader assets from the independently tested Python reference.

Developer-only synchronization: no Python reference code runs in C compilation.
Review the generated diff and run native shader/CPU numerical parity afterward.
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from open_painter.evaluation.engine import glsl, procedural_glsl as p

out = ['/* Frozen shader math and procedural catalog. See scripts/generate_native_catalog.py. */\n']
quote = json.dumps
out.append('static const char *op_preamble[] = {\n' + ',\n'.join(quote(glsl.PREAMBLE[i:i+1000]) for i in range(0, len(glsl.PREAMBLE), 1000)) + ',NULL};\n')
out.append('typedef struct { const char *name, *vector, *scalar; } math_entry;\n')
out.append('static const math_entry scalar_math[] = {\n' + ',\n'.join('{' + quote(k) + ',NULL,' + quote(v) + '}' for k, v in glsl.MATH.items()) + ',{NULL,NULL,NULL}};\n')
out.append('static const math_entry vector_math[] = {\n' + ',\n'.join('{' + quote(k) + ',' + ('NULL' if v[0] is None else quote(v[0])) + ',' + ('NULL' if v[1] is None else quote(v[1])) + '}' for k, v in glsl.VECTOR_MATH.items()) + ',{NULL,NULL,NULL}};\n')
ordered = []
def need(name):
    if name in ordered:
        return
    for d in p.SNIPPETS[name][0]:
        need(d)
    ordered.append(name)
for name in p.SNIPPETS:
    need(name)
assert len(ordered) < 64
out.append('static const char *primitive_source[] = {\n' + ',\n'.join(quote(p.SNIPPETS[k][1]) for k in ordered) + '};\n')
out.append('typedef struct {const char *name,*function,*source; uint64_t dependencies;} procedural_entry;\nstatic const procedural_entry catalog[] = {\n')
for k, (deps, body) in sorted(p.KINDS.items()):
    seen = set()
    def trans(name):
        seen.add(name)
        for d in p.SNIPPETS[name][0]:
            trans(d)
    for name in deps:
        trans(name)
    mask = sum(1 << ordered.index(name) for name in seen)
    fn = 'op_procedural_' + k.lower()
    source = f'float {fn}(vec2 uv, float scale, uint seed) {{\n  float u = uv.x, v = uv.y;\n  float x = u * scale, y = v * scale;\n  float res = 0.0;\n  {body}\n  if (isnan(res)) {{ res = 0.0; }}\n  if (isinf(res)) {{ res = (res > 0.0) ? 1.0 : 0.0; }}\n  return clamp(res, 0.0, 1.0);\n}}\n'
    out.append('{' + ','.join([quote(k), quote(fn), quote(source), f'UINT64_C({mask})']) + '},\n')
out.append('{NULL,NULL,NULL,0}};\n')
(ROOT / 'libopenpainter/src/shaders/catalog.inc').write_text(''.join(out), encoding="utf-8")
print(len(glsl.MATH), 'math;', len(glsl.VECTOR_MATH), 'vector;', len(p.KINDS), 'procedurals')

# Fixed paint/publication/filter shaders are frozen with the native compiler too.
# Parse literals without importing Blender, NumPy, or the running add-on.
import ast
fixed = {}
for relative, prefix in [('evaluation/engine/runtime.py', 'runtime'),
                         ('painting/gpu_strokes.py', 'paint'), ('painting/gpu_history.py', 'paint'), ('evaluation/engine/filters.py', 'filter')]:
    tree = ast.parse((ROOT / 'src/open_painter' / relative).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            name = node.targets[0]
            if isinstance(name, ast.Name) and 'void main()' in node.value.value:
                fixed[prefix + '.' + name.id.lower()] = node.value.value
fixed['paint.region'] = fixed.pop('paint.copy_region')
fixed['runtime.publish_pixels'] = fixed['runtime.publish'].replace('  op_out1 = value;   /* the source of its mip chain */\n', '')
from open_painter.evaluation.engine.publication_shaders import mip_batch_source
for count in (2, 3):
    fixed[f'runtime.mip_{count}'] = mip_batch_source(fixed['runtime.mip'], count)
# Resolve the reference's literal shader concatenations without importing its
# NumPy/GPU modules. These expressions are repository-owned build inputs.
filter_tree = ast.parse((ROOT / 'src/open_painter/evaluation/engine/filters.py').read_text(encoding="utf-8"))
env = {}
def value(node, scope):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return scope[node.id]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return value(node.left, scope) + value(node.right, scope)
    if isinstance(node, ast.Dict):
        return {value(k, scope): value(v, scope) for k, v in zip(node.keys, node.values)}
    if isinstance(node, ast.Subscript):
        return value(node.value, scope)[value(node.slice, scope)]
    raise ValueError('Not a shader literal')
for node in filter_tree.body:
    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
        try:
            env[node.targets[0].id] = value(node.value, env)
        except (ValueError, KeyError):
            pass
env['BODIES']['UV_DISTORTION'] = env['BODIES']['VECTOR_WARP']
for kind in ('DILATE', 'ERODE', 'MORPHOLOGICAL_EXPAND', 'MORPHOLOGICAL_CONTRACT'):
    env['BODIES'][kind] = env['BODIES']['BLUR']
for name in ('AXIS', 'MORPH', 'GATHER', 'MERGE'):
    fixed['filter.' + name.lower()] = env[name]
apply = next(n for n in filter_tree.body if isinstance(n, ast.FunctionDef) and n.name == '_apply_uv')
body = next(n.value for n in ast.walk(apply) if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id == 'body')
for kind in env['BODIES']:
    fixed['filter.' + kind] = value(body, dict(env, kind=kind))
text = ['/* Frozen GPU programs. Refresh with scripts/generate_native_catalog.py. */\n']
for index, (name, source) in enumerate(sorted(fixed.items())):
    text.append(f'static const char fixed_{index}[] =\n' + '\n'.join(quote(source[i:i+1000]) for i in range(0, len(source), 1000)) + ';\n')
text.append('static const struct { const char *name,*source; } fixed_shaders[] = {\n')
text.extend('{' + quote(name) + f',fixed_{i}' + '},\n' for i, name in enumerate(sorted(fixed)))
text.append('{NULL,NULL}};\n')
(ROOT / 'libopenpainter/src/shaders/fixed.inc').write_text(''.join(text), encoding="utf-8")
print(len(fixed), 'fixed GPU programs')
