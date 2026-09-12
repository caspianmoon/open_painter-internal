import bpy,json
from pathlib import Path
path=Path(__file__).resolve().parents[1]/'logs/ui-painted.blend'
bpy.ops.wm.open_mainfile(filepath=str(path))
mat=bpy.context.object.active_material
image=next(l.image for l in mat.painter_layers if l.kind=='PAINT')
assert image.packed_file
w,h=image.size
center=((h//2)*w+w//2)*4
assert image.pixels[center+3]>.9, list(image.pixels[center:center+4])
assert image.pixels[center+2]>.8
assert image.pixels[3]==0
import open_painter_native as painter,base64
for origin,direction in [([0,0,6],[0,0,-1]),([6,0,0],[-1,0,0]),([0,6,0],[0,-1,0])]:
    request=base64.b64encode(json.dumps(dict(action='ray',origin=origin,direction=direction)).encode()).decode()
    result=json.loads(painter.shell_dispatch(request))
    assert result['hit'],result
    assert all(0<=v<=1 for v in result['uv']),result
print('PAINT_PIXELS_AND_RAYCAST_PASS',flush=True)
