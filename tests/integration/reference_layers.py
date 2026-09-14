"""One editable mask drives consumers; cycles, duplicate IDs and IO are safe."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import bpy
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.references import issues
from open_painter.utils.ids import new_id
open_painter.register()
scene=bpy.context.scene; ts=sets.create(scene,'Damage references',512)
source=stack.add(ts,'PAINT','Damage authoring'); source_id=source.id
paint=source.masks.add(); paint.id=new_id(); paint.source='PAINT'
paint.image=images.create('Damage',512,(.25,.25,.25,1)); paint_id=paint.id
for name, channel, value in [('Rust','ROUGHNESS',.9),('Metal exposure','METALLIC',1.)]:
    layer=stack.add(ts,'FILL',name)
    for prop in layer.channels: prop.enabled=prop.channel==channel
    stack.channel_for(layer,channel).value=value
    ref=layer.masks.add(); ref.id=new_id(); ref.source='REFERENCE'; ref.reference_layer=source_id
    ref.reference_mask=paint_id
assert not issues(ts)
LayerEvaluator(scene).evaluate_texture_set(ts.id)
np.testing.assert_allclose(images.pixels(images.output(ts,'ROUGHNESS').image)[...,0],.6,atol=1e-6)
np.testing.assert_allclose(images.pixels(images.output(ts,'METALLIC').image)[...,0],.25,atol=1e-6)
source=next(l for l in ts.layers if l.id==source_id)
images.write(source.masks[0].image,np.tile(np.array([.75,.75,.75,1],np.float32),(512,512,1)),pack=False)
LayerEvaluator(scene).evaluate_texture_set(ts.id)
np.testing.assert_allclose(images.pixels(images.output(ts,'ROUGHNESS').image)[...,0],.8,atol=1e-6)
np.testing.assert_allclose(images.pixels(images.output(ts,'METALLIC').image)[...,0],.75,atol=1e-6)
# Source-channel edits also dirty consumers of another destination channel.
source=next(l for l in ts.layers if l.id==source_id)
source.type='FILL'
prop=stack.channel_for(source,'HEIGHT'); prop.enabled=True; prop.value=.3
consumer=next(l for l in ts.layers if l.name=='Rust')
consumer.masks[0].reference_channel='HEIGHT'
LayerEvaluator(scene).evaluate_texture_set(ts.id)
prop.value=.6
assert images.output(ts,'ROUGHNESS').dirty
LayerEvaluator(scene).evaluate_texture_set(ts.id)
# Cycles are item-granular: the source mask stack reading the consumer's mask
# stack is fine while the consumer reads the source's HEIGHT channel or only the
# painted prefix, and becomes a cycle once the consumer reads the whole stack.
source=next(l for l in ts.layers if l.id==source_id)
cycle=source.masks.add(); cycle.id=new_id(); cycle.source='REFERENCE'; cycle.reference_layer=consumer.id
assert not issues(ts), issues(ts)
LayerEvaluator(scene).evaluate_texture_set(ts.id)
consumer.masks[0].reference_channel='MASK'
assert not issues(ts), issues(ts)
consumer.masks[0].reference_mask=''
assert any('cycle' in text for text in issues(ts))
revision=images.output(ts,'ROUGHNESS').revision
try:
    LayerEvaluator(scene).evaluate_texture_set(ts.id)
    raise AssertionError('Cycle accepted')
except ValueError: pass
assert images.output(ts,'ROUGHNESS').revision==revision
source.masks.remove(len(source.masks)-1)
consumer.masks[0].reference_channel='HEIGHT'; consumer.masks[0].reference_mask=paint_id
# Group duplication remaps internal references and copies editable paint.
folder=stack.group(ts,[l.id for l in ts.layers],'Bundle')
folder_id=folder.id
copy=stack.duplicate(ts,next(i for i,l in enumerate(ts.layers) if l.id==folder_id))
copied=[l for l in ts.layers if l.parent_id==copy.id]
new_source=next(l for l in copied if l.name.startswith('Damage'))
source=next(l for l in ts.layers if l.id==source_id)
assert new_source.masks[0].image != source.masks[0].image
assert all(m.reference_layer==new_source.id for l in copied for m in l.masks if m.source=='REFERENCE')
assert not issues(ts)
with TemporaryDirectory(prefix='op-reference-') as directory:
    original_id=ts.id
    copied_set=sets.duplicate(scene,next(i for i,t in enumerate(scene.open_painter.texture_sets) if t.id==original_id))
    assert not issues(copied_set), issues(copied_set)
    LayerEvaluator(scene).evaluate_texture_set(copied_set.id)
    ts=next(t for t in scene.open_painter.texture_sets if t.id==original_id)
    from open_painter.smart_materials.serializer import save
    from open_painter.smart_materials.loader import apply
    path=save(ts,Path(directory)/'damage.openpaintmaterial')
    other=sets.create(scene,'Imported',512); apply(other,path)
    assert not issues(other)
    LayerEvaluator(scene).evaluate_texture_set(other.id)
    identifier=other.id
    bpy.ops.wm.save_as_mainfile(filepath=directory+'/anchors.blend')
    bpy.ops.wm.open_mainfile(filepath=directory+'/anchors.blend')
    other=next(t for t in bpy.context.scene.open_painter.texture_sets if t.id==identifier)
    assert not issues(other)
open_painter.unregister()
print('REFERENCE_LAYERS_PASS',flush=True)
