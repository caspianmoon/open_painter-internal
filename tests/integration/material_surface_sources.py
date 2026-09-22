"""Actual image-content recoloring and shared imported PBR source transforms.

Images here are deterministic test inputs, never claimed to be AI generation.
"""
from pathlib import Path
import json
import sys

import bpy
import numpy as np
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.authoring import sources, surfaces, physical_space, document, transactions, commands
from open_painter.authoring.schema import AuthoringError
from open_painter.core import texture_set_manager as sets, image_manager
from open_painter.evaluation import cache, channel_eval

open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
scene.render.engine = 'BLENDER_EEVEE'
bpy.ops.mesh.primitive_plane_add(size=.016)
obj = bpy.context.object
ts = sets.create(scene, 'Image surface contracts', 512)
ts.preview_backend = 'CPU'
sets.assign(scene, ts, obj)
physical_space.declare(ts, 1, '16mm diagnostic test geometry')


def apply(commands, operation):
    return transactions.apply(ts, dict(version=1, summary='Source contract test', commands=commands),
                              operation_id=operation, expected_digest=document.digest(ts))


sid = apply([dict(op='create_surface', id='new:cloth', name='Cloth', family='WOVEN_FABRIC',
                 values=dict(period_m=.001))], 'create')['aliases']['new:cloth']
layer = next(l for l in ts.layers if l.surface_id == sid and l.type == 'FILL')
base = next(p for p in layer.channels if p.channel == 'BASE_COLOR')
rough = next(p for p in layer.channels if p.channel == 'ROUGHNESS')
y, x = np.indices((32,32))
color_pixels = np.ones((32,32,4), np.float32)
color_pixels[..., :3] = (.2 + .6 * (x / 31))[..., None] * np.array([1,.25,.08])
rough_pixels = np.ones_like(color_pixels)
rough_pixels[..., :3] = (.2 + .6 * (y / 31))[..., None]
for label, prop, pixels in [('Color', base, color_pixels), ('Rough', rough, rough_pixels)]:
    image = bpy.data.images.new(label, width=32, height=32, float_buffer=True)
    image.colorspace_settings.name = 'Non-Color'
    image.pixels.foreach_set(pixels.ravel())
    image.pack()
    prop.image = image
    prop.procedural = ''
ids = {item['name']:item['id'] for item in sources.describe(ts)}
original_image = base.image
original_colorspace = original_image.colorspace_settings.name
apply([dict(op='assign_bundle', id='new:bundle', layer_id=layer.id,
            sources=dict(BASE_COLOR=ids['Color'], ROUGHNESS=ids['Rough']),
            scale=[2,3], offset=[.1,-.2], rotation=.1)], 'bundle')
assert base.image != original_image and original_image.colorspace_settings.name == original_colorspace
assert list(base.scale) == list(rough.scale) == [2,3]
assert list(base.offset) == list(rough.offset)
metadata = [json.loads(prop.source_metadata) for prop in (base,rough)]
assert metadata[0]['bundle_id'] == metadata[1]['bundle_id']
assert all(not m['calibrated_bundle'] for m in metadata)
assert metadata[1]['interpretation'] == 'Linear data'
image_ids = [prop.material_source_id for prop in (base,rough)]
before = document.snapshot(ts)
raw = channel_eval.source(base, 'FILL', 512, 0, 512, {}, ts, {})
apply([dict(op='patch_surface', surface_id=sid, mode='COLOR_ONLY', values=dict(color=[.03,.15,.045,1]))], 'recolor')
colored = channel_eval.source(base, 'FILL', 512, 0, 512, {}, ts, {})
assert np.mean(colored[...,1]) > 3 * np.mean(colored[...,0]), 'Actual pixels must become green'
assert colored[..., :3].min() > .02, 'Bounded palette transfer must not clip source structure to black'
assert np.ptp(colored[..., 1]) > .005, 'Source structure remains visible after recolor'
assert [prop.material_source_id for prop in (base,rough)] == image_ids
changed = document.changes(before, document.snapshot(ts))
assert all(path[2:4] == ('channels','BASE_COLOR') for path,_,_ in changed), changed
report = next(item for item in surfaces.describe(ts) if item['id'] == sid)
np.testing.assert_allclose(report['controls']['color'], [.03,.15,.045,1], atol=1e-6)
print('IMAGE_CONTENT_RECOLOR_AND_BUNDLE_PASS', flush=True)

# Metric image sampling respects non-unit object transforms and declared tile
# dimensions, through the same material-space frame as native structural fields.
base.material_mapping_version = 1
base.material_meters_per_unit = 1
base.material_image_extent_m = (.002,.002)
base.material_frame = 'WORLD'
metric = channel_eval.source(base, 'FILL', 512, 0, 512, {}, ts, {})
obj.scale = (10,10,10)
bpy.context.view_layer.update()
base.material_meters_per_unit = .1
rescaled = channel_eval.source(base, 'FILL', 512, 0, 512, {}, ts, {})
np.testing.assert_allclose(metric, rescaled, atol=2e-6)
print('METRIC_IMAGE_FRAME_EQUIVALENCE_PASS', flush=True)

# Bundle installation must preserve an existing member frame and synchronize
# every declared map's sampling, not silently rotate wood away from its relief.
obj.scale = (1,1,1)
bpy.context.view_layer.update()
base.material_meters_per_unit = 1
base.material_frame = 'COMPONENT'
base.material_origin = (.001,.002,.003)
base.material_axis_u, base.material_axis_v = (0,1,0), (0,0,1)
base.filtering, base.tiling, base.triplanar_hardness = 'NEAREST', 'NONE', 8
rough.filtering, rough.tiling, rough.triplanar_hardness = 'BILINEAR', 'REPEAT', 2
before_height = next(
    p for p in document.snapshot(ts)['layers'][-1]['channels'] if p['channel']=='HEIGHT')
bound = sources.bindings(ts)
color_id = next(key for key,value in bound.items() if value==base.image)
rough_id = next(key for key,value in bound.items() if value==rough.image)
apply([dict(op='assign_bundle',id='new:metricbundle',layer_id=layer.id,
            sources=dict(BASE_COLOR=color_id,ROUGHNESS=rough_id),physical_extent_m=[.002,.003])], 'metric-bundle')
for member in (base,rough):
    assert member.material_mapping_version == 1 and member.material_frame == 'COMPONENT'
    np.testing.assert_allclose(member.material_origin,[.001,.002,.003])
    np.testing.assert_allclose(member.material_axis_u,[0,1,0])
    np.testing.assert_allclose(member.material_axis_v,[0,0,1])
    assert member.filtering=='NEAREST' and member.tiling=='NONE' and member.triplanar_hardness==8
    metadata=json.loads(member.source_metadata)
    assert metadata['mapping_frame']['material_frame']=='COMPONENT'
height_after=next(p for p in document.snapshot(ts)['layers'][-1]['channels'] if p['channel']=='HEIGHT')
assert document.canonical(before_height)==document.canonical(height_after)
print('METRIC_BUNDLE_PRESERVES_MEMBER_FRAME_PASS', flush=True)

# An unsupported metric extent on an image mask must be refused, never silently
# interpreted as legacy mapping. Rejection precedes source loading or mutation.
before = document.canonical(document.snapshot(ts))
try:
    commands.compile_proposal(ts,dict(version=1,summary='Unsupported metric mask extent',commands=[
        dict(op='add_mask',id='new:mask',layer_id=layer.id,name='Image mask',source='FILL'),
        dict(op='assign_texture',layer_id=layer.id,mask_id='new:mask',image_id='image:unused',physical_extent_m=[.01,.01])]),'unsupported-mask-extent')
except AuthoringError as error:
    assert 'channel images only' in str(error), str(error)
else:
    raise AssertionError('Unsupported metric image-mask extent was silently accepted')
assert document.canonical(document.snapshot(ts)) == before
print('METRIC_IMAGE_MASK_EXTENT_REFUSAL_PASS', flush=True)

# Two perpendicular member faces with equal radial/along coordinates. Rotate
# their object90degrees and supply the corresponding explicit WORLD frame;
# native wood and an assigned image must both retain grain along that axis.
import base64
import math
import struct
import tempfile
import zlib
from open_painter.ai import generated_images
width, length, radial = .002, .008, .003
vertices=[(-width,-length,radial),(width,-length,radial),(width,length,radial),(-width,length,radial),
          (radial,-length,-width),(radial,-length,width),(radial,length,width),(radial,length,-width)]
mesh=bpy.data.meshes.new('Directional side-face diagnostic')
mesh.from_pydata(vertices,[],[(0,1,2,3),(4,5,6,7)])
uv=mesh.uv_layers.new(name='UVMap')
for polygon,coords in zip(mesh.polygons,[[(0,0),(.5,0),(.5,1),(0,1)],[(.5,0),(1,0),(1,1),(.5,1)]]):
    for loop,coordinate in zip(polygon.loop_indices,coords):uv.data[loop].uv=coordinate
member=bpy.data.objects.new('Rotated member faces',mesh)
scene.collection.objects.link(member)
member.rotation_euler.z=math.pi/2
bpy.context.view_layer.update()
wood=sets.create(scene,'Explicit directional wood',512)
wood.preview_backend='CPU'
sets.assign(scene,wood,member)
physical_space.declare(wood,1,'16mm long diagnostic member faces in metre coordinates')
created=transactions.apply(wood,dict(version=1,summary='Explicit wood axis',commands=[dict(
    op='create_surface',id='new:wood',name='Wood',family='WOOD',values=dict(
        frame='WORLD',axis_u=[0,1,0],axis_v=[-1,0,0],period_m=.002))]),
    operation_id='directional-wood',expected_digest=document.digest(wood))
wood_fill=next(l for l in wood.layers if l.id==created['aliases']['new:wood:finish'])
wood_color=next(p for p in wood_fill.channels if p.channel=='BASE_COLOR')
native=channel_eval._procedural_tile(wood_color,512,0,512,wood,{})
np.testing.assert_allclose(native[:,:256],native[:,256:],atol=2e-5)
assert np.ptp(native)>.05
assert wood_color.material_directional
unchanged={p.channel:document.canonical(next(item for item in document.snapshot(wood)['layers'][-1]['channels']
    if item['channel']==p.channel)) for p in wood_fill.channels if p.channel!='BASE_COLOR'}
grid=np.indices((64,64),dtype=np.float32)
stripe=(.3+.25*np.sin(grid[1]/64*2*np.pi)+.03*np.cos(grid[0]/64*2*np.pi))
rgb=np.clip(stripe[...,None]*np.array([1,.5,.15])*255,0,255).astype(np.uint8)
def chunk(kind,data):
    return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',64,64,8,2,0,0,0))+chunk(
    b'IDAT',zlib.compress(b''.join(b'\0'+row.tobytes() for row in rgb)))+chunk(b'IEND',b'')
with tempfile.TemporaryDirectory(prefix='painter-directional-source-') as directory:
    # Deliberately synthetic bytes exercising the actual generated-artifact
    # assignment boundary; not a provider call or an AI appearance claim.
    meta=generated_images.store(directory,dict(result=base64.b64encode(png).decode(),revisedPrompt='Synthetic directional mapping test'))
    images={meta['image_id']:dict(meta,state_directory=directory)}
    transactions.apply(wood,dict(version=1,summary='Assign directional source',commands=[dict(
        op='assign_texture',layer_id=wood_fill.id,channel='BASE_COLOR',image_id=meta['image_id'],physical_extent_m=[.002,.003])]),
        operation_id='directional-source',expected_digest=document.digest(wood),images=images)
sampled=channel_eval.source(wood_color,'FILL',512,0,512,{},wood,{})
assert not wood_color.procedural and wood_color.material_directional and wood_color.material_frame=='WORLD'
np.testing.assert_allclose(sampled[:,:256],sampled[:,256:],atol=2e-5)
assert np.ptp(sampled[...,:3])>.05
for channel,saved in unchanged.items():
    current=next(p for p in document.snapshot(wood)['layers'][-1]['channels'] if p['channel']==channel)
    assert document.canonical(current)==saved
print('WORLD_ALONG_AXIS_WOOD_AND_GENERATED_SOURCE_PASS', flush=True)
