"""Real Blender operator undo restores pre-existing paint after a material stamp."""
import sys,json,traceback
from pathlib import Path
import bpy
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
result_path=Path('/private/tmp/op-material-stamp-ui.json')


def run():
    import open_painter
    from open_painter.core import texture_set_manager as sets,layer_stack as stack,image_manager as images
    open_painter.register()
    scene=bpy.context.scene; obj=bpy.context.active_object
    ts=sets.create(scene,'Stamp undo',512); sets.assign(scene,ts,obj)
    layer=stack.add(ts,'PAINT')
    identifier=ts.id
    old=images.create('Existing painted source',512,(.2,.2,.2,1))
    prop=stack.channel_for(layer,'ROUGHNESS'); prop.image=old; prop.enabled=True
    before=images.pixels(old)
    bpy.ops.ed.undo_push(message='Before material stamp')
    result=bpy.ops.open_painter.material_stamp('EXEC_DEFAULT',center=(0,0,1),normal=(0,0,1),radius=.2,roughness=.9)
    assert result=={'FINISHED'}
    after=images.pixels(stack.channel_for(layer,'ROUGHNESS').image)
    assert not np.array_equal(after,before)
    bpy.ops.ed.undo_push(message='Material stamp')
    assert bpy.ops.ed.undo()=={'FINISHED'}
    ts=next(t for t in bpy.context.scene.open_painter.texture_sets if t.id==identifier)
    restored=stack.channel_for(ts.layers[0],'ROUGHNESS').image
    np.testing.assert_array_equal(images.pixels(restored),before)
    assert bpy.ops.ed.redo()=={'FINISHED'}
    ts=next(t for t in bpy.context.scene.open_painter.texture_sets if t.id==identifier)
    np.testing.assert_array_equal(images.pixels(stack.channel_for(ts.layers[0],'ROUGHNESS').image),after)
    result_path.write_text(json.dumps(dict(material_stamp_undo='PASS',material_stamp_redo='PASS')))
    open_painter.unregister()


def deferred():
    try: run()
    except Exception:
        result_path.write_text(json.dumps(dict(error=traceback.format_exc())))
        traceback.print_exc()
    finally: bpy.ops.wm.quit_blender()
bpy.app.timers.register(deferred,first_interval=2)
