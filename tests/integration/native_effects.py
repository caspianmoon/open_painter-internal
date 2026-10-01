"""Native semantic effects/mask folds vs legacy GPU and independent NumPy filters."""
import json
from contextlib import nullcontext
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
import gpu
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.evaluation.engine import headless, glsl, runtime
from open_painter.evaluation.engine.compiler import EngineGraph
from open_painter.evaluation.shader_math import Math, Signal
from open_painter.evaluation.shader_effects import POINTWISE, EFFECT_FIELDS, apply_effect
from open_painter.evaluation.channel_eval import apply_effect as reference
from open_painter.native_bridge import compiler, lifecycle


def program(lib, settings, native, mask=False, first=False):
    tree = headless.Tree('Semantic parity')
    unit = SimpleNamespace(tree=tree, key=('CHANNEL', 'semantic', 'BASE_COLOR'))
    graph = SimpleNamespace(units={unit.key: unit})
    tr = compiler.Snapshot(lib, graph, unit) if native else glsl.Translator(graph, unit)
    b = Math(tree)
    sockets = []
    for index, (texture, component, kind) in enumerate(((0, 'rgb', headless.VECTOR),
            (0, 'a', headless.VALUE), (1, 'rgb', headless.VECTOR),
            (0, 'r', headless.VALUE), (1, 'r', headless.VALUE))):
        socket = b.node('ShaderNodeRGB' if kind == headless.VECTOR else 'ShaderNodeValue').outputs[0]
        if native:
            raw = tr.add(compiler.FETCH, headless.RGBA, aux=(texture,))
            value = (tr.convert(raw, headless.RGBA, headless.VECTOR) if component == 'rgb' else
                     tr.add(compiler.COMPONENT, headless.VALUE, (raw,), (3 if component == 'a' else 0,)))
        else:
            value = f'texelFetch(op_s{texture},op_texel,0).{component}'
        tr._set(socket, value, kind)
        sockets.append(socket)
    for i in range(2):
        tr._sampler(('fixture', i), 'fixture')
    def param(_owner, field, index=None):
        value = getattr(settings, field)
        return value[index] if index is not None else value
    b.param, b.role = param, lambda *_: nullcontext()
    b.effect_image = lambda *_: sockets[2]
    if mask:
        result = EngineGraph._mask_fold(b, SimpleNamespace(id='fixture'), settings,
                                       sockets[3], None if first else sockets[4])
        color = b.combine(result, result, result)
    else:
        color = EngineGraph.pointwise_effect(b, Signal(sockets[0], sockets[1]), settings).color
    color, alpha = tr.output(color)[0], tr.output(sockets[1])[0]
    if native:
        assert sum(n.opcode == (compiler.MASK_FOLD if mask else compiler.EFFECT) for n in tr.ir) == 1
        return tr.compile([(color, alpha, 0)], [('Color', 'Alpha')])
    source = 'void main(){ivec2 op_texel=ivec2(gl_FragCoord.xy);\n'+'\n'.join(tr.lines)+f'\nop_out0=vec4({color},{alpha});\n}}'
    return glsl.Program(source, tr.samplers, [], [], [('Color', 'Alpha')], {})


def settings(kind, variant):
    values = dict(kind=kind, enabled=True, strength=.63, black=.15, white=.87, gamma=.73,
                  output_black=.07, output_white=.93, amount=.43, contrast=1.7, softness=.08,
                  steps=5, hue=-.27, saturation=1.4, lightness=-.13, color=(.7,.2,.4),
                  color2=(.1,.6,.9), matrix=(.7,.2,-.1,.3,.9,.2,.4,-.2,.8), bias=(.1,-.1,.07), image=True,
                  id='fixture', source='FILL', invert=.7)
    if variant == 1:
        values.update(strength=1., gamma=1., hue=1., steps=3, amount=0., contrast=0., softness=0.,
                      black=0., white=1., saturation=0., lightness=0.)
    elif variant == 2:
        values.update(enabled=False, black=.5, white=.5, gamma=0., image=None)
    return SimpleNamespace(**values)


gpu.init()
open_painter.register()
try:
    assert set(EFFECT_FIELDS) == POINTWISE
    lib = lifecycle.execution_library(gpu.platform.backend_type_get())
    assert lib is not None, lifecycle.diagnostics()
    dev, n = runtime.device(), 32
    rng = np.random.default_rng(78043)
    source = rng.random((n,n,4), dtype=np.float32)
    detail = rng.random((n,n,4), dtype=np.float32)
    for i, edge in enumerate((0., 1e-9, 1e-8, 1e-6, .125, .25, .5, .75, .875, 1.)):
        source[i,:,:3] = edge
    source[10:12,:,:3] = (.5,.5,.5)
    detail[10:12,:,:3] = (.5,.5,.5)
    target = dev.texture(n, 'RGBA32F')
    textures = [dev.upload(a) for a in (source,detail)]
    worst = [0.,0.]
    def render(config, mask=False, first=False):
        results=[]
        for native in (True,False):
            p = program(lib, config, native, mask, first)
            with dev.state():
                dev.draw(dev.shader(p), [target], n, list(zip(('op_s0','op_s1'),textures)))
            results.append(np.asarray(dev.readback(target,n)).reshape(n,n,4).copy())
        return results
    try:
        for variant in range(3):
            for kind in sorted(POINTWISE):
                config = settings(kind,variant)
                results = render(config)
                with patch('open_painter.evaluation.image_snapshots.read', return_value=detail):
                    expected = reference(source,config)
                errors = [float(abs(results[0]-other).max()) for other in (results[1],expected)]
                assert all(np.isfinite(a).all() for a in results)
                assert max(errors) <= 2e-4, (kind,variant,errors)
                worst = [max(a,b) for a,b in zip(worst,errors)]
        for first in (False,True):
            for operation in ('NORMAL','MULTIPLY','MAX','MIN','ADD'):
                for variant in range(3):
                    config = settings('INVERT',variant)
                    config.operation = operation
                    results = render(config,True,first)
                    v=np.clip((source[...,0]-config.black)/max(config.white-config.black,1e-6),0,1)
                    v=v+((1-v)-v)*config.invert
                    prev=detail[...,0]
                    if first: expected=1+(v-1)*config.strength
                    elif operation=='NORMAL': expected=prev+(v-prev)*config.strength
                    elif operation=='MULTIPLY': expected=prev*(1+(v-1)*config.strength)
                    elif operation=='MAX': expected=prev+(np.maximum(prev,v)-prev)*config.strength
                    elif operation=='MIN': expected=prev+(np.minimum(prev,v)-prev)*config.strength
                    else: expected=np.clip(prev+v*config.strength,0,1)
                    expected=np.concatenate((np.repeat(expected[...,None],3,axis=-1),source[...,3:]),axis=-1)
                    errors=[float(abs(results[0]-other).max()) for other in (results[1],expected)]
                    assert max(errors)<=2e-5, (operation,first,variant,errors)
        print('NATIVE_EFFECTS_PASS',json.dumps(dict(effects=30,effect_cases=90,mask_cases=30,pixels_per_case=n*n,
              max_legacy_error=worst[0],max_cpu_error=worst[1])),flush=True)
    finally:
        dev.discard(target)
        for texture in textures: dev.discard(texture)
    # Production RNA bindings: packed matrix/color parameters, scalar uniforms
    # and mask controls must update without rebuilding the graph or shaders.
    import bpy
    import os
    from open_painter.core import layer_stack as stack, texture_set_manager as sets, material_manager
    from open_painter.evaluation import shader_preview
    from open_painter.utils.ids import new_id
    scene = bpy.context.scene
    ts = sets.create(scene, 'Native semantic bindings', 512)
    sets.assign(scene, ts, bpy.context.object)
    base = stack.add(ts, 'FILL', 'Base')
    coat = stack.add(ts, 'FILL', 'Coat')
    prop = stack.channel_for(coat, 'BASE_COLOR')
    prop.color = (.3,.5,.8,1.)
    mixer = prop.effects.add()
    mixer.id, mixer.kind = new_id(), 'CHANNEL_MIXER'
    gamma = prop.effects.add()
    gamma.id, gamma.kind = new_id(), 'GAMMA'
    mask = coat.masks.add()
    mask.id, mask.source, mask.procedural = new_id(), 'PROCEDURAL', 'FBM'
    material_manager.repair(ts)
    def ready():
        for _ in range(200):
            shader_preview.tick(scene,ts)
            info=shader_preview.diagnostics(scene,ts)
            assert not info['error'],info
            if shader_preview.current(scene,ts):
                return shader_preview.session_engine(scene,ts)
        raise AssertionError(info)
    engine=ready()
    assert any(node.bl_idname=='OPEffect' for unit in engine.graph.units.values() for node in unit.tree.nodes)
    assert any(node.bl_idname=='OPMaskFold' for unit in engine.graph.units.values() for node in unit.tree.nodes)
    rebuilds=engine._native_graph.rebuilds
    for owner,field,value in ((mixer,'matrix',(.8,.1,.2,.2,.7,.1,.1,.2,.9)),
            (mixer,'bias',(.1,-.1,.05)),(gamma,'gamma',.7),(gamma,'strength',.4),
            (gamma,'enabled',False),(mask,'black',.2),(mask,'invert',True),(mask,'strength',.6)):
        compiles=dev.compiles
        setattr(owner,field,value)
        engine=ready()
        assert dev.compiles==compiles and engine._native_graph.rebuilds==rebuilds, (field,engine.report)
    actual = {o.channel:np.array(gpu.texture.from_image(o.image).read(),np.float32).copy() for o in ts.outputs}
    try:
        os.environ['OP_NATIVE_ENGINE']='0'
        engine.published.clear()
        engine.evaluate(budget=False)
        for o in ts.outputs:
            np.testing.assert_allclose(np.array(gpu.texture.from_image(o.image).read(),np.float32),actual[o.channel],atol=2e-3,rtol=0)
    finally:
        os.environ.pop('OP_NATIVE_ENGINE',None)
    print('NATIVE_EFFECT_BINDINGS_PASS 8 live edits; no topology rebuilds or shader compilation',flush=True)
finally:
    open_painter.unregister()
