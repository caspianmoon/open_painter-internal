"""Semantic aging is reproducible and preserves strokes/local overrides."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import bpy
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
import open_painter
from open_painter.core import texture_set_manager as sets,layer_stack as stack,image_manager as images,material_states as states
from open_painter.core.environment_profiles import responses
open_painter.register()
scene=bpy.context.scene
textures=[]
for index in range(3):
    ts=sets.create(scene,f'Asset {index}',512); textures.append(ts)
    for role in ('DIRT','FADE','SCRATCHES','OXIDATION','COATING_LOSS'):
        layer=stack.add(ts,'FILL',role); layer.effect_semantic=role
    paint=stack.add(ts,'PAINT','Artist strokes')
    prop=stack.channel_for(paint,'ROUGHNESS'); prop.image=images.create('Authored',512,(.4,.4,.4,1)); prop.enabled=True
    states.apply(ts,'USED',profile='COASTAL',seed=index)
    before={l.id:l.opacity for l in ts.layers}
    states.apply(ts,'USED',profile='COASTAL',seed=index)
    assert before=={l.id:l.opacity for l in ts.layers}
    np.testing.assert_allclose(images.pixels(prop.image)[...,0],.4,atol=1e-7)
assert len({ts.layers[0].opacity for ts in textures})==3
assert len(set(responses(.5,.5,.5).values()))>3
first=textures[0]; unrelated={l.id:l.opacity for l in textures[1].layers}
first.layers[0].opacity=.123
states.apply(first,'DAMAGED',profile='COASTAL',seed=0)
assert abs(first.layers[0].opacity-.123)<1e-6
assert unrelated=={l.id:l.opacity for l in textures[1].layers}
paint=next(l for l in first.layers if l.type=='PAINT')
assert paint.opacity==1 and stack.channel_for(paint,'ROUGHNESS').image
with TemporaryDirectory(prefix='op-states-') as directory:
    from open_painter.smart_materials.serializer import save
    from open_painter.smart_materials.loader import apply
    path=save(first,Path(directory)/'states.openpaintmaterial')
    imported=sets.create(scene,'Imported states',512); apply(imported,path)
    assert imported.material_state_data
    states.apply(imported,'CLEAN')
    assert abs(imported.layers[0].opacity-.123)<1e-6
open_painter.unregister()
print('MATERIAL_STATES_PASS',flush=True)
