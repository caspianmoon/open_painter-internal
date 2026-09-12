"""Compare identical uncached/cached rectangle draws in Blender's GPU context."""
import sys,time,json,statistics
from pathlib import Path
import bpy,gpu
root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'src'))
from open_painter.ui.dock import drawing as d
results={'uncached':[],'cached':[]};handler=None

def draw():
    if min(map(len,results.values()))>=20:return
    gpu.state.blend_set('ALPHA')
    for mode in results:
        start=time.perf_counter()
        for i in range(100):
            batch=(d.rectangle_batch.__wrapped__ if mode=='uncached' else d.rectangle_batch)(10+(i%10)*12,10+(i//10)*12,10,10)
            d._shader.bind();d._shader.uniform_float('color',(.2,.2,.2,1));batch.draw(d._shader)
        results[mode].append((time.perf_counter()-start)*1000)
    gpu.state.blend_set('NONE')

def tick():
    if min(map(len,results.values()))>=20:
        report={k:statistics.median(v[3:]) for k,v in results.items()}
        (root/'.blender-test/draw-cache-benchmark.json').write_text(json.dumps(report,indent=2))
        bpy.types.SpaceView3D.draw_handler_remove(handler,'WINDOW');bpy.ops.wm.quit_blender();return
    for a in bpy.context.screen.areas:a.tag_redraw()
    return .05
handler=bpy.types.SpaceView3D.draw_handler_add(draw,(),'WINDOW','POST_PIXEL')
bpy.app.timers.register(tick,first_interval=1)
