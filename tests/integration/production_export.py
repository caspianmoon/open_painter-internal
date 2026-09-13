"""Engine channel mappings, evaluated bundles and atlas source preservation."""
import json,sys
from pathlib import Path
from tempfile import TemporaryDirectory
import bpy
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
import open_painter
from open_painter.core import texture_set_manager as sets,layer_stack as stack,image_manager as images
from open_painter.export.exporter import export
from open_painter.export.mesh_bundle import export_bundle
from open_painter.export.atlas import build
from open_painter.geometry.texel_density import measure
open_painter.register()
scene=bpy.context.scene; cube=bpy.context.active_object
ts=sets.create(scene,'Export source',512); sets.assign(scene,ts,cube)
layer=stack.add(ts,'FILL'); stack.channel_for(layer,'ROUGHNESS').value=.25; stack.channel_for(layer,'METALLIC').value=.75
ao=stack.channel_for(layer,'AO'); ao.enabled=True; ao.value=.5
opacity=stack.channel_for(layer,'OPACITY'); opacity.enabled=True; opacity.value=0
original_uv=[tuple(item.uv) for item in cube.data.uv_layers.active.data]
original_vertices=[tuple(v.co) for v in cube.data.vertices]
assert measure(ts)['world_area']==24
with TemporaryDirectory(prefix='op-production-') as directory:
    root=Path(directory)
    for profile,label,expected in [('GODOT','ORM',(.5,.25,.75,1)),('GLTF','OcclusionRoughnessMetallic',(.5,.25,.75,1)),
                                    ('UNITY_URP','MetallicGlossMap',(.75,0,0,.75)),('UNITY_HDRP','MaskMap',(.75,.5,1,.75))]:
        ts.export.preset=profile; ts.export.directory=str(root/profile)
        files=export(bpy.context,ts,ts.export)
        path=next(path for path in files if path.stem.endswith('_'+label))
        image=bpy.data.images.load(str(path)); image.colorspace_settings.name='Non-Color'
        np.testing.assert_allclose(images.pixels(image)[0,0],expected,atol=1/255+.00001)
        bpy.data.images.remove(image)
    manifest=export_bundle(bpy.context,[ts],root/'bundle',geometry='OMIT')
    record=json.loads(manifest.read_text())
    assert record['generated_geometry']=='OMIT' and record['triangles']==12
    assert (manifest.parent/'scene.blend').exists()
    assert [tuple(v.co) for v in cube.data.vertices]==original_vertices
    identifier=ts.id
    atlas,copies=build(bpy.context,[ts],512,8)
    source=next(item for item in scene.open_painter.texture_sets if item.id==identifier)
    assert [tuple(item.uv) for item in cube.data.uv_layers.active.data]==original_uv
    assert copies[0].data!=cube.data and copies[0].data.uv_layers.active.name=='Open Painter Atlas UV'
    actual=images.pixels(images.output(atlas,'ROUGHNESS').image)
    np.testing.assert_allclose(actual[10:-10,10:-10,0],.25,atol=1e-6)
    assert all(stack.channel_for(atlas.layers[0],key).image for key in ('BASE_COLOR','ROUGHNESS','NORMAL','HEIGHT','AO','OPACITY','METALLIC','EMISSION'))
    from unittest.mock import patch
    before=(len(bpy.data.images),len(bpy.data.materials),len(bpy.data.objects),len(scene.open_painter.texture_sets))
    with patch('open_painter.export.atlas.sample_uv',side_effect=RuntimeError('Injected atlas sampling failure')):
        try:
            build(bpy.context,[source],512,8)
            raise AssertionError('Failed atlas was accepted')
        except RuntimeError as exc: assert 'Injected' in str(exc)
    assert before==(len(bpy.data.images),len(bpy.data.materials),len(bpy.data.objects),len(scene.open_painter.texture_sets))
open_painter.unregister()
print('PRODUCTION_EXPORT_PASS',flush=True)
