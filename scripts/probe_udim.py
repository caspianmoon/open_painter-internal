"""Target-build tile-addressing gate; creating tiled images alone is insufficient."""
import bpy,json
import numpy as np
from pathlib import Path

report=dict(blender=bpy.app.version_string,build=bpy.app.build_hash.decode(),native_paint='NOT_RUN',bake='NOT_RUN')
image=bpy.data.images.new('UDIM addressing probe',width=16,height=16,tiled=True,float_buffer=True)
area=next(area for area in bpy.context.screen.areas if area.type=='VIEW_3D')
old=area.type
try:
    area.type='IMAGE_EDITOR'; area.spaces.active.image=image
    region=next(region for region in area.regions if region.type=='WINDOW')
    with bpy.context.temp_override(area=area,region=region):
        bpy.ops.image.tile_add(number=1002,fill=True,color=(0,1,0,1),width=16,height=16,**{'float':True})
    values=[]
    for index,color in [(0,(1,0,0,1)),(1,(0,1,0,1))]:
        image.tiles.active_index=index
        data=np.tile(np.asarray(color,np.float32),(16,16,1))
        image.pixels.foreach_set(data.ravel()); image.update()
    for index in (0,1):
        image.tiles.active_index=index
        data=np.empty(16*16*4,np.float32); image.pixels.foreach_get(data)
        values.append(data.reshape(16,16,4)[0,0].tolist())
    report['tile_pixels']=values
    report['tile_addressing']='PASS' if np.allclose(values,[(1,0,0,1),(0,1,0,1)]) else 'FAILED'
    if report['tile_addressing']!='PASS':
        report['reason']='Image.pixels did not address independent tiles using tiles.active_index. Native paint/bake integration remains gated; no UDIM UI advertised.'
except Exception as exc:
    report.update(tile_addressing='UNAVAILABLE',reason=str(exc))
finally:
    area.type=old; bpy.data.images.remove(image)
Path('/private/tmp/op-udim-probe.json').write_text(json.dumps(report,indent=2))
print('UDIM_PROBE',json.dumps(report),flush=True)
