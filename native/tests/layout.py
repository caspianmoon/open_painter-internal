import bpy,json,time
from pathlib import Path
root=Path(__file__).resolve().parents[1]
started=time.monotonic()
def check():
    types=[a.type for a in sorted(bpy.context.screen.areas,key=lambda a:a.x)]
    if types!=['PAINTER_ASSETS','VIEW_3D','IMAGE_EDITOR','PAINTER_LAYERS']:
        if time.monotonic()-started<30:return 1
        (root/'logs/layout.json').write_text(json.dumps({'status':'FAIL','areas':types}))
    else:
        bpy.ops.painter.new_set();bpy.ops.painter.layer(kind='FILL');bpy.ops.painter.layer(kind='GROUP');bpy.ops.painter.layer(kind='FILL')
        mat=bpy.context.object.active_material
        image=bpy.data.images.new('Painter UV Grid',width=512,height=512)
        image.generated_type='COLOR_GRID';image.pack()
        mat.painter_layers[mat.painter_active].image=image
        bpy.app.timers.register(capture,first_interval=5)
        return None
    bpy.ops.wm.quit_blender()
def capture():
    bpy.ops.wm.save_as_mainfile(filepath=str(root/'logs/native-layout.blend'))
    bpy.ops.screen.screenshot(filepath=str(root/'logs/native-layout.png'))
    (root/'logs/layout.json').write_text(json.dumps({'status':'PASS','areas':[a.type for a in sorted(bpy.context.screen.areas,key=lambda a:a.x)]}))
    bpy.ops.wm.quit_blender()
bpy.app.timers.register(check,first_interval=5)
