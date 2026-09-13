import sys,json
from pathlib import Path
import bpy,numpy as np
root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'src'))
import open_painter
from open_painter.core import texture_set_manager as sets,layer_stack as stack,image_manager as images,surface_presets
from open_painter.painting import paint_session as paint
from open_painter.painting.channel_paint import target
from open_painter.evaluation import cache
from open_painter.evaluation.evaluator import LayerEvaluator
open_painter.register();scene=bpy.context.scene;ts=sets.create(scene,'Scan',512)
layer=stack.add(ts,'PAINT');image=target(ts,layer,'BASE_COLOR')
evaluator=LayerEvaluator(scene);evaluator.evaluate_texture_set(ts.id)
project=scene.open_painter;project.paint_image=image;project.paint_set_id=ts.id;project.paint_layer_id=layer.id;project.paint_target_channel='BASE_COLOR';project.paint_channel='BASE_COLOR'
paint._rasters[scene.as_pointer()]=(image.as_pointer(),images.pixels(image))
for row in [50,100,200]:
    data=images.pixels(image);data[row:row+2,100:120]=(.2,.4,.6,1)
    # Native Blender brush writes bypass the managed Python writer.
    image.pixels.foreach_set(data.ravel()); image.update()
    assert paint.detect(scene,force=True)
    region=cache._regions[cache.key_for(scene, ts)];assert region[2:4]==(row,row+2),region[2:4]
    job=evaluator.channel_steps(ts.id,'BASE_COLOR',region)
    for _ in job:pass
    regional=images.pixels(images.output(ts,'BASE_COLOR').image)
    evaluator.evaluate_texture_set(ts.id,force=True)
    np.testing.assert_array_equal(regional,images.pixels(images.output(ts,'BASE_COLOR').image))
    cache._regions.pop(cache.key_for(scene, ts),None)
    assert not paint.detect(scene,force=True)
project.paint_image=None
for name in ['Fine Wood','Weathered Paint']:
    ts=sets.create(scene,name,512);layer=surface_presets.apply(ts,name)
    assert all(c.image.packed_file for c in layer.channels if c.image)
    assert stack.channel_for(layer,'BASE_COLOR').image.colorspace_settings.name=='sRGB'
    assert stack.channel_for(layer,'NORMAL').image.colorspace_settings.name=='Non-Color'
    evaluator.evaluate_texture_set(ts.id)
open_painter.unregister();print('SCAN_AND_LIBRARY_PASS',flush=True)
