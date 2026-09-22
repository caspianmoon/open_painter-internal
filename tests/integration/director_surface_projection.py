"""Generic surface templates, projected color/scalars, editable frame and transaction safety."""
import base64,json,os,sys,tempfile,time,traceback
from pathlib import Path
import bpy,numpy as np
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
import open_painter
from open_painter.ai import candidates,generated_images,session
from open_painter.ai.viewport import png
from open_painter.authoring import document,regions,surface_projection,transactions
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import channel_eval,shader_preview
from open_painter.painting.projection import geometry
from open_painter.evaluation.snapshots import capture
from mathutils import Vector


def run():
    bpy.ops.mesh.primitive_cube_add()
    obj=bpy.context.object
    ts=sets.create(bpy.context.scene,'Projection regression',512);sets.assign(bpy.context.scene,ts,obj,0)
    bpy.context.view_layer.update()
    captured=session.capture(bpy.context,mode='REFINE',scope='SET',prompt='Synthetic projection test')
    objects=session.objects_for(captured)
    region=regions.capture(ts,objects)[0]['id']
    before=document.digest(ts); counts=(len(bpy.data.objects),len(bpy.data.images))
    result=surface_projection.inspect(ts,region,objects,(0,0,1),(0,1,0))
    assert counts==(len(bpy.data.objects),len(bpy.data.images)) and before==document.digest(ts)
    assert result['_images'][0]['url'].startswith('data:image/png;base64,')
    try:surface_projection.inspect(ts,region,objects,(0,0,1),(0,0,1));raise AssertionError('Invalid frame accepted')
    except ValueError:pass
    pixels=np.full((128,128,4),255,np.uint8)
    pixels[:64,:,:3]=(30,50,80);pixels[64:,:,:3]=(180,100,60)
    pixels[:,64:,:3]//=2
    with tempfile.TemporaryDirectory() as directory:
        entry=generated_images.store(directory,dict(result=base64.b64encode(png(128,128,pixels.tobytes())).decode()))
        entry['state_directory']=directory
        proposal=dict(version=1,summary='Generic projected surface',commands=[
            dict(op='add_layer',id='new:surface',name='Projected source',kind='FILL'),
            dict(op='assign_projected_surface',layer_id='new:surface',image_id=entry['image_id'],region_id=region,
                 frame=result['frame'],name='Synthetic surface',purpose='Projection and scalar registration regression',roughness_range=[.2,.7],height_amplitude=.01)])
        if os.environ.get('OP_TEST_AUTO_CACHE') == '1':
            proposal['commands'][0]['parent_id'] = 'new:cache'
            proposal['commands'][:0] = [dict(op='add_layer', id='new:cache', name='Editable cached group', kind='FOLDER'),
                dict(op='set_layer', layer_id='new:cache', values=dict(group_mode='ISOLATED', cache_mode='CACHED'))]
        original=document.snapshot(ts)
        transactions.apply(ts,proposal,operation_id='projection-regression',expected_digest=before,objects=objects,images={entry['image_id']:entry})
    scene=bpy.context.scene;scene.render.engine='BLENDER_EEVEE'
    if os.environ.get('OP_TEST_AUTO_CACHE') == '1':
        shader_preview.flush(scene, ts)  # Save/render must not expose placeholders.
    else:
        candidates._ready(scene,time.monotonic()+60)
    layer=ts.layers[-1];assert layer.decal_inline and layer.decal_object is None
    if os.environ.get('OP_TEST_AUTO_CACHE') == '1':
        from open_painter.authoring import resource_budget
        from open_painter.core import image_manager
        assert resource_budget.estimate(document.snapshot(ts)) == 4
        cached = next(row for row in ts.layers[0].cache if row.channel == 'ROUGHNESS')
        assert cached.image and cached.provenance
        old_pixels = image_manager.pixels(cached.image).copy()
        prop = next(p for p in layer.channels if p.channel == 'ROUGHNESS')
        old_value=prop.value; prop.value=.9
        candidates._ready(scene,time.monotonic()+60)
        assert np.max(np.abs(old_pixels-image_manager.pixels(cached.image))) > .1, 'Cached child edits did not refresh'
        prop.value=old_value
        candidates._ready(scene,time.monotonic()+60)
    assert counts[0]==len(bpy.data.objects),'Projection allocated an object'
    positions,normals,valid=geometry(ts,512,coverage=True)
    top=valid & (normals[...,2]>.9);back=valid & (normals[...,2]<-.9)
    def scalar(key,cache=None):
        prop=next(p for p in layer.channels if p.channel==key)
        return channel_eval.source(capture(prop),'FILL',512,0,512,{},ts,cache if cache is not None else {})
    rough=scalar('ROUGHNESS');color=scalar('BASE_COLOR');height=scalar('HEIGHT')
    assert rough[top,3].min()>.99 and rough[back,3].max()==0,'Facing coverage differs'
    assert color[top,:3].std()>.02 and rough[top,0].std()>.001 and height[top,0].std()>.00001
    # Frame edits invalidate derived scalar tiles even when a caller reuses geometry cache.
    cache={};initial=scalar('ROUGHNESS',cache).copy();saved_origin=tuple(layer.decal_origin);layer.decal_origin[1]+=.4
    moved=scalar('ROUGHNESS',cache);assert np.max(np.abs(initial-moved))>.01
    layer.decal_origin=saved_origin
    candidates._ready(scene,time.monotonic()+60)
    camera=scene.camera;camera.location=(0,0,6);camera.rotation_euler=(Vector((0,0,0))-camera.location).to_track_quat('-Z','Y').to_euler();camera.data.type='ORTHO';camera.data.ortho_scale=2.5
    out=ROOT/'.blender-test/director-surface-projection';out.mkdir(exist_ok=True)
    scene.render.resolution_x=512;scene.render.resolution_y=512;scene.render.resolution_percentage=100
    scene.render.filepath=str(out/'projection.png');bpy.ops.render.render(write_still=True)
    assert shader_preview.session_graph(scene,ts).bound_sampler_count()<=4
    bpy.ops.wm.save_as_mainfile(filepath=str(out/'projection.blend'))
    ident=ts.id;bpy.ops.wm.open_mainfile(filepath=str(out/'projection.blend'),use_scripts=False)
    ts=next(t for t in bpy.context.scene.open_painter.texture_sets if t.id==ident)
    assert ts.layers[-1].decal_inline
    assert next(p for p in ts.layers[-1].channels if p.channel=='BASE_COLOR').image.packed_file
    transactions.undo(ts)
    assert document.canonical(document.snapshot(ts))==document.canonical(original)
    print('DIRECTOR_SURFACE_PROJECTION_OK',flush=True)

open_painter.register()
def main():
    try:run()
    except BaseException:traceback.print_exc();os._exit(1)
    bpy.ops.wm.quit_blender()
bpy.app.timers.register(main,first_interval=1.)
