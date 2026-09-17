"""Panel/bolt fields react to explicit inputs, preserve maps and survive reload."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import bpy
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images
from open_painter.generators import scene_fields
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation import dependencies
from open_painter.evaluation.metrics import Metrics
from open_painter.utils.ids import new_id
open_painter.register()
scene=bpy.context.scene
bpy.ops.mesh.primitive_plane_add(size=2)
panel=bpy.context.object
ts=sets.create(scene,'Panel',512); sets.assign(scene,ts,panel)
layer=stack.add(ts,'FILL'); rough=stack.channel_for(layer,'ROUGHNESS'); rough.value=.1
mask=layer.masks.add(); mask.id=new_id(); mask.source='SCENE'
field=mask.influence; field.kind='CONTACT'; field.distance=.5; field.mode='LIVE'
bpy.ops.mesh.primitive_uv_sphere_add(segments=12,ring_count=8,radius=.15,location=(0,0,.1))
bolt=bpy.context.object; field.object=bolt
bpy.context.view_layer.update()
LayerEvaluator(scene).evaluate_texture_set(ts.id)
first=images.pixels(field.image).copy()
assert first[32,32,0]>.7 and first[0,0,0]==0
assert not ts.baked_maps
field.resolution='64'
left=panel.vertex_groups.new(name='Left')
left.add([v.index for v in panel.data.vertices if v.co.x < 0], 1., 'REPLACE')
field.kind='VERTEX_GROUP'; field.attribute='Left'
expected=np.broadcast_to(1-(np.arange(64)+.5)/64,(64,64))
np.testing.assert_allclose(scene_fields.compute(ts,field),expected,atol=1e-6)
for domain in ('POINT','CORNER','FACE'):
    attribute=panel.data.attributes.new('Weight '+domain,'FLOAT',domain)
    values=([float(v.co.x < 0) for v in panel.data.vertices] if domain=='POINT' else
            [float(panel.data.vertices[loop.vertex_index].co.x < 0) for loop in panel.data.loops]
            if domain=='CORNER' else [.3]*len(panel.data.polygons))
    attribute.data.foreach_set('value',values)
    field.kind='ATTRIBUTE'; field.attribute=attribute.name
    np.testing.assert_allclose(scene_fields.compute(ts,field),.3 if domain=='FACE' else expected,atol=1e-6)
# Painting weights or attribute values moves no vertex, so the mesh content hash stays;
# the mesh update the stroke reports re-identifies a LIVE field and it re-analyses (0.9.7).
pointer=panel.data.as_pointer()
def repaint(paint):
    paint()
    dependencies._mesh_epochs[pointer]=dependencies.mesh_epoch(panel.data)+1   # what the stroke's depsgraph update reports
    dependencies._candidates.add(scene.as_pointer())
field.kind='VERTEX_GROUP'; field.attribute='Left'
LayerEvaluator(scene).evaluate_texture_set(ts.id)
painted=field.provenance
assert painted==repr(scene_fields.signature(ts,field))
left.add([v.index for v in panel.data.vertices],1.,'REPLACE')
assert repr(scene_fields.signature(ts,field))==painted, 'weights are hashed on every signature'
repaint(lambda: None)
assert repr(scene_fields.signature(ts,field))!=painted, 'painted weights left the field signature alone'
dependencies.refresh(scene)
assert ts.dirty
LayerEvaluator(scene).evaluate_texture_set(ts.id)
assert field.provenance!=painted
np.testing.assert_allclose(images.pixels(field.image)[...,0],1,atol=1e-6)
field.kind='ATTRIBUTE'; field.attribute='Weight POINT'
LayerEvaluator(scene).evaluate_texture_set(ts.id)
painted=field.provenance
np.testing.assert_allclose(images.pixels(field.image)[...,0],expected,atol=1e-6)
repaint(lambda: panel.data.attributes['Weight POINT'].data.foreach_set('value',[1.]*len(panel.data.vertices)))
dependencies.refresh(scene)
assert ts.dirty and field.provenance==painted
LayerEvaluator(scene).evaluate_texture_set(ts.id)
assert field.provenance!=painted
np.testing.assert_allclose(images.pixels(field.image)[...,0],1,atol=1e-6)
# The persisted provenance digests the values: a reopen (epochs restart) keeps the identity.
dependencies.clear()
assert repr(scene_fields.signature(ts,field))==field.provenance, 'the epoch leaked into the provenance'
field.kind='CONTACT'
field.attribute=''
LayerEvaluator(scene).evaluate_texture_set(ts.id)
rough.value=.2
with Metrics() as counters:
    LayerEvaluator(scene).evaluate_texture_set(ts.id)
assert counters.phases.get('scene_analysis',{}).get('calls',0)==0
assert counters.counts['scene_query_samples']==0
bolt.location.x=.5; bpy.context.view_layer.update(); dependencies.refresh(scene)
assert ts.dirty
LayerEvaluator(scene).evaluate_texture_set(ts.id)
second=images.pixels(field.image)
assert not np.array_equal(first,second) and second[32,48,0]>.7
assert not ts.baked_maps
field.kind='UP'
LayerEvaluator(scene).evaluate_texture_set(ts.id)
np.testing.assert_allclose(images.pixels(field.image)[...,0],1,atol=1e-6)
panel.rotation_euler.x=np.pi; bpy.context.view_layer.update(); dependencies.refresh(scene)
LayerEvaluator(scene).evaluate_texture_set(ts.id)
np.testing.assert_allclose(images.pixels(field.image)[...,0],0,atol=1e-6)
# Missing references produce failure without destroying last good analysis.
field.kind='CONTACT'; field.object=bolt
LayerEvaluator(scene).evaluate_texture_set(ts.id)
last=field.image; last_pixels=images.pixels(last)
bpy.data.objects.remove(bolt,do_unlink=True); bpy.context.view_layer.update(); stack.invalidate(ts)
try:
    LayerEvaluator(scene).evaluate_texture_set(ts.id)
    raise AssertionError('Deleted reference silently accepted')
except ValueError as exc:
    assert 'existing external mesh' in str(exc)
assert field.image==last
np.testing.assert_array_equal(images.pixels(last),last_pixels)
field.mode='FROZEN'
LayerEvaluator(scene).evaluate_texture_set(ts.id)
# Every influencer datablock pointer is stripped from a portable material, not only a scene
# field's reference object: a Ground object and an Exclude collection on the frozen field, a
# reference object picked while a mask was a scene field and a node group picked while it was
# a custom graph (0.9.7: json.dumps used to fail on the datablock).
field.ground_object=panel
excluded=bpy.data.collections.new('Excluded'); scene.collection.children.link(excluded)
field.exclude_collection=excluded
stale=stack.add(ts,'FILL','Stale pointers')
was_scene=stale.masks.add(); was_scene.id=new_id(); was_scene.source='SCENE'
was_scene.influence.object=panel; was_scene.influence.collection=excluded
was_scene.source='GENERATOR'
was_graph=stale.masks.add(); was_graph.id=new_id(); was_graph.source='GRAPH'
was_graph.node_group=bpy.data.node_groups.new('Stale graph','ShaderNodeTree')
was_graph.source='GENERATOR'
assert was_scene.influence.object==panel and was_graph.node_group is not None, 'pointers no longer survive a source change'
with TemporaryDirectory(prefix='op-scene-field-') as directory:
    from open_painter.smart_materials.serializer import save
    from open_painter.smart_materials.loader import apply
    save(ts,Path(directory)/'frozen.openpaintmaterial')
    other=sets.create(scene,'Portable snapshot',512)
    apply(other,Path(directory)/'frozen.openpaintmaterial')
    imported=other.layers[0].masks[0].influence
    assert imported.mode=='FROZEN' and imported.object is None
    assert imported.ground_object is None and imported.exclude_collection is None
    np.testing.assert_allclose(images.pixels(imported.image),last_pixels,atol=1e-6)
    imported_scene,imported_graph=other.layers[1].masks
    assert imported_scene.influence.object is None and imported_scene.influence.collection is None
    assert imported_graph.node_group is None
    assert field.ground_object==panel and was_scene.influence.object==panel, 'saving changed the live set'
    identifier=ts.id
    bpy.ops.wm.save_as_mainfile(filepath=directory+'/scene.blend')
    bpy.ops.wm.open_mainfile(filepath=directory+'/scene.blend')
    ts=next(t for t in bpy.context.scene.open_painter.texture_sets if t.id==identifier)
    assert ts.layers[0].masks[0].influence.image.packed_file
open_painter.unregister()
print('SCENE_MASKS_PASS',flush=True)
