"""Export → Target: one operator writes the target's maps with its naming, the mesh in its
format and a manifest; the set's own export settings are restored afterwards."""
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images

open_painter.register()
scene = bpy.context.scene
project = scene.open_painter
cube = bpy.context.active_object
ts = sets.create(scene, 'Hull', 512); sets.assign(scene, ts, cube)
ts.preview_backend = 'CPU'
layer = stack.add(ts, 'FILL')
stack.channel_for(layer, 'ROUGHNESS').value = .25
stack.channel_for(layer, 'METALLIC').value = .75
ao = stack.channel_for(layer, 'AO'); ao.enabled = True; ao.value = .5
stack.channel_for(layer, 'NORMAL').enabled = True
stack.channel_for(layer, 'NORMAL').color = (.5, .75, 1., 1.)


def load(path):
    image = bpy.data.images.load(str(path)); image.colorspace_settings.name = 'Non-Color'
    value = images.pixels(image)[8, 8].copy()
    bpy.data.images.remove(image)
    return value


with TemporaryDirectory(prefix='op-target-') as directory:
    root = Path(directory)
    # Unreal: T_ naming, ORM, DirectX normal, FBX, manifest; settings restored.
    ts.export.follow_target = False
    ts.export.file_format, ts.export.bit_depth = 'TIFF', '16'
    project.target = 'UNREAL'
    assert ts.export.preset == 'BLENDER', 'a set that does not follow the target keeps its preset'
    project.export_directory = str(root / 'unreal')
    bpy.ops.open_painter.export_target()
    files = sorted(p.name for p in (root / 'unreal').iterdir())
    assert files == ['Hull_manifest.json', 'Scene.fbx', 'T_Hull_BC.png', 'T_Hull_N.png', 'T_Hull_ORM.png'], files
    orm = load(root / 'unreal' / 'T_Hull_ORM.png')
    np.testing.assert_allclose(orm, (.5, .25, .75, 1), atol=1 / 255 + 1e-5)
    normal = load(root / 'unreal' / 'T_Hull_N.png')
    assert normal[1] < .5, 'DirectX normal must flip green'
    info = json.loads((root / 'unreal' / 'Hull_manifest.json').read_text())
    assert info['normal_map']['convention'] == 'DIRECTX' and info['files'][2]['map'] == 'ORM'
    assert (ts.export.file_format, ts.export.bit_depth, ts.export.preset) == ('TIFF', '16', 'BLENDER'), 'settings not restored'
    print('EXPORT_TARGET unreal: PASS', flush=True)
    # Godot: glTF and albedo/orm/normal naming; a second export into the same folder is refused.
    project.target = 'GODOT'
    project.export_directory = str(root / 'godot')
    bpy.ops.open_painter.export_target()
    files = sorted(p.name for p in (root / 'godot').iterdir())
    assert files == ['Hull_albedo.png', 'Hull_emission.png', 'Hull_manifest.json', 'Hull_normal.png', 'Hull_orm.png', 'Scene.glb'], files
    normal = load(root / 'godot' / 'Hull_normal.png')
    assert normal[1] > .5, 'OpenGL normal keeps green'
    try:
        bpy.ops.open_painter.export_target()
        raise AssertionError('overwrote an existing export')
    except RuntimeError as error:
        assert 'exist' in str(error)
    # No mesh when disabled; CUSTOM keeps the set's own settings and naming.
    project.export_mesh = False
    project.target = 'CUSTOM'
    ts.export.preset, ts.export.prefix = 'UNITY_URP', 'Prop_'
    project.export_directory = str(root / 'custom')
    bpy.ops.open_painter.export_target()
    files = sorted(p.name for p in (root / 'custom').iterdir())
    assert files == ['Prop_Hull_BaseMap.tif', 'Prop_Hull_EmissionMap.tif', 'Prop_Hull_MetallicGlossMap.tif',
                     'Prop_Hull_Normal.tif', 'Prop_Hull_OcclusionMap.tif'], files   # the set's own TIFF/16 settings
    print('EXPORT_TARGET godot and custom: PASS', flush=True)
    # The plain button sets no property, so it must not inherit all_sets=True from an earlier
    # "Selected Objects" click (undo=True takes the button's last-properties path).
    project.export_mesh, project.target, ts.export.prefix = True, 'UNREAL', ''
    deck = sets.create(scene, 'Deck', 512); deck.preview_backend = 'CPU'
    cube.select_set(True)
    project.export_directory = str(root / 'selected')
    bpy.ops.open_painter.export_target(True, all_sets=True)
    files = sorted(p.name for p in (root / 'selected').iterdir())
    assert not any(f.startswith('Deck') for f in files) and 'T_Hull_BC.png' in files, files
    project.active_texture_set = [ts.name for ts in project.texture_sets].index('Deck')
    cube.select_set(False)
    project.export_directory = str(root / 'plain')
    bpy.ops.open_painter.export_target(True)
    files = sorted(p.name for p in (root / 'plain').iterdir())
    assert not any(f.startswith('T_Hull') or f == 'Hull_manifest.json' for f in files), \
        f'plain export inherited all_sets from the previous run: {files}'
    assert 'T_Deck_BC.png' in files and 'Deck_manifest.json' in files, files
    print('EXPORT_TARGET plain button after selected-objects export: PASS', flush=True)
print('EXPORT_TARGET_PASS', flush=True)
open_painter.unregister()
