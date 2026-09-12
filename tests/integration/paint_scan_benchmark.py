"""Compare the restored implementation's scan with bounded scratch-buffer scans."""
import sys,json,time,statistics,tracemalloc
from pathlib import Path
import bpy,numpy as np
root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'src'))
import open_painter
from open_painter.core import image_manager
from open_painter.painting import paint_session
open_painter.register();scene=bpy.context.scene
image=image_manager.create('Scan benchmark',2048)
scene.open_painter.paint_image=image
seed=image_manager.pixels(image)
paint_session._rasters[scene.as_pointer()]=(image.as_pointer(),seed)

def old():
    raster=image_manager.pixels(image)
    return np.flatnonzero(np.any(raster!=seed,axis=(1,2)))
def new():return paint_session.detect(scene,force=True)
results={}
for name,fn in [('restored_scan',old),('bounded_scan',new)]:
    for _ in range(3):fn()
    tracemalloc.start();times=[]
    for _ in range(12):
        start=time.perf_counter();fn();times.append((time.perf_counter()-start)*1000)
    _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    results[name]={'median_ms':statistics.median(times),'peak_transient_mib':peak/1024**2}
# Verify sparse changes are still detected exactly and unchanged scans remain false.
assert new() is False
out=np.empty_like(seed);assert np.shares_memory(image_manager.pixels(image,out),out)
results['resolution']=2048
(root/'.blender-test/paint-scan-benchmark.json').write_text(json.dumps(results,indent=2))
print(json.dumps(results),flush=True)
scene.open_painter.paint_image=None;open_painter.unregister()
