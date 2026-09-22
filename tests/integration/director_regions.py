"""Production region masks and face-accurate target captures (no provider).

Run in background for geometry/persistence; run visibly for the camera-mode
pixel regression. The latter writes its actual captured PNG to the temp folder.
"""
import base64
import json
from pathlib import Path
import sys
import tempfile
import traceback
from types import SimpleNamespace

import bpy
import numpy as np
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.ai import viewport
from open_painter.authoring import document, regions
from open_painter.core import layer_stack, texture_set_manager as sets
from open_painter.evaluation import cache, mesh_masks


def fixture():
    scene = bpy.data.scenes.new('Region isolation regression')
    bpy.context.window.scene = scene
    scene.render.engine = 'BLENDER_EEVEE'
    # Two disconnected target faces inside slot 0 and narrow slot 1 hardware.
    vertices, faces = [], []
    for x, z, width, height in ((-1., 0., .9, .9), (1., 0., .9, .9), (-.5, -.05, .035, .24)):
        start = len(vertices)
        vertices.extend(((x-width,-height,z),(x+width,-height,z),(x+width,height,z),(x-width,height,z)))
        faces.append(tuple(range(start, start+4)))
    mesh = bpy.data.meshes.new('Artist multi-slot mesh')
    mesh.from_pydata(vertices, [], faces)
    uv = mesh.uv_layers.new(name='ArtistUV')
    for polygon in mesh.polygons:
        column = polygon.index / 3
        for loop, point in zip(polygon.loop_indices, ((column,0),(column+.3,0),(column+.3,1),(column,1))):
            uv.data[loop].uv = point
        polygon.material_index = int(polygon.index == 2)
        polygon.select = polygon.index == 1
    obj = bpy.data.objects.new('Artist parts', mesh)
    scene.collection.objects.link(obj)
    for name in ('Body', 'Hardware'):
        mesh.materials.append(bpy.data.materials.new(name))
    a = sets.create(scene, 'Shared surface', 512)
    aid = a.id
    b = sets.create(scene, 'Hardware surface', 512)
    a = next(s for s in scene.open_painter.texture_sets if s.id == aid)
    sets.assign(scene, a, obj, 0)
    sets.assign(scene, b, obj, 1)
    bpy.context.view_layer.update()
    return scene, obj, a, b


def state(obj):
    return (obj.data.as_pointer(), tuple(tuple(v.co) for v in obj.data.vertices),
            tuple((tuple(p.vertices), p.material_index, p.select) for p in obj.data.polygons),
            tuple(tuple(item.uv) for item in obj.data.uv_layers.active.data),
            tuple(slot.material.as_pointer() for slot in obj.material_slots), obj.select_get(), obj.hide_get())


def geometry_checks():
    scene, obj, a, b = fixture()
    initial = state(obj)
    count = (len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.collections))
    objs = {'object:0': obj}
    catalogue = regions.capture(a, objs)
    assert catalogue == regions.capture(a, objs), 'region IDs unstable'
    assignment = next(item for item in catalogue if item['kind'] == 'ASSIGNMENT')
    assert assignment['face_count'] == 2 and assignment['triangle_count'] == 4
    assert assignment['material_space']['valid_surface_fraction'] == 1.
    components = [item for item in catalogue if item['kind'] == 'CONNECTED_COMPONENT']
    assert len(components) == 2 and all(item['face_count'] == 1 for item in components)
    selected = next(item for item in catalogue if item['kind'] == 'ARTIST_SELECTION')
    layer = layer_stack.add(a, 'FILL', 'Selected finish')
    mask = layer.masks.add(); mask.id = 'region-test'; mask.source = 'MESH'
    for key, value in regions.selector(a, selected['id'], objs).items():
        setattr(mask.mesh, key, value)
    before = document.snapshot(a)
    raster = mesh_masks.raster(a, mask, 128)
    assert raster[:, 0:35].max() == 0 and raster[:, 45:75].mean() > .99
    assert raster[:, 90:].max() == 0, 'mask escaped its material assignment'
    # Saved face regions are independent of subsequent artist selection.
    for polygon in obj.data.polygons: polygon.select = False
    np.testing.assert_array_equal(mesh_masks.raster(a, mask, 128), raster)
    for polygon, original in zip(obj.data.polygons, initial[2]): polygon.select = original[2]
    layer_stack.restore(before['layers'][0], layer)
    np.testing.assert_array_equal(mesh_masks.raster(a, layer.masks[0], 128), raster)
    for target, expected in ((a, 2), (b, 1)):
        with viewport.isolated(bpy.context, {obj}, target.assignments) as isolated:
            assert len(isolated) == 1 and len(isolated[0].data.polygons) == expected
            assert obj.hide_get()
            assert {p.material_index for p in isolated[0].data.polygons} == {target.assignments[0].slot_index}
        assert state(obj) == initial
        assert count == (len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.collections))
    with viewport.isolated(bpy.context, {obj}, a.assignments, region_faces={obj:{1}}) as isolated:
        assert len(isolated[0].data.polygons)==1
    assert state(obj)==initial
    try:
        with viewport.isolated(bpy.context, {obj}, a.assignments, region_faces={obj:{2}}):
            raise AssertionError('non-target material face leaked into region capture')
    except ValueError: pass
    assert state(obj)==initial
    # Error cleanup restores source selection/visibility and deletes all scratch IDs.
    try:
        with viewport.isolated(bpy.context, {obj}, b.assignments): raise RuntimeError('injected capture failure')
    except RuntimeError: pass
    assert state(obj) == initial
    assert count == (len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.collections))
    # Two objects in the same set each retain only their own assigned faces.
    other = obj.copy(); other.data = obj.data.copy(); scene.collection.objects.link(other)
    other.location.x = 3
    sets.assign(scene, a, other, 0)
    with viewport.isolated(bpy.context, {obj, other}, a.assignments) as isolated:
        assert len(isolated) == 2 and all(len(o.data.polygons) == 2 for o in isolated)
    with viewport.isolated(bpy.context, {obj, other}, a.assignments, region_faces={obj:{1}}) as isolated:
        assert len(isolated)==1 and len(isolated[0].data.polygons)==1
    # Persistence survives save and reopen; topology drift is rejected explicitly.
    path = Path(tempfile.gettempdir()) / 'open-painter-region-persistence.blend'
    set_id = a.id
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))
    a = next(s for s in bpy.context.scene.open_painter.texture_sets if s.id == set_id)
    mask = a.layers[0].masks[0]
    assert mask.mesh.region_faces and mask.mesh.region_provenance
    np.testing.assert_array_equal(mesh_masks.raster(a, mask, 128), raster)
    obj = mask.mesh.object
    old_index = obj.data.polygons[0].material_index
    obj.data.polygons[0].material_index = 1
    try:
        regions.face_indices(mask.mesh, obj.data)
    except ValueError as exc:
        assert 'topology changed' in str(exc)
    else: raise AssertionError('stale region did not reject reassigned faces')
    obj.data.polygons[0].material_index = old_index
    print('DIRECTOR_REGIONS_GEOMETRY_PERSISTENCE_OK', flush=True)


def pixel_checks():
    scene, obj, a, b = fixture()
    a.preview_backend = b.preview_backend = 'CPU'
    # Bright red body, green narrow hardware; colors permit decoded-pixel assertions.
    for ts, color in ((a,(1.,0.,0.,1.)), (b,(0.,1.,0.,1.))):
        mat = ts.material; mat.use_nodes = True; mat.node_tree.nodes.clear()
        out = mat.node_tree.nodes.new('ShaderNodeOutputMaterial')
        emission = mat.node_tree.nodes.new('ShaderNodeEmission')
        emission.inputs['Color'].default_value = color
        mat.node_tree.links.new(emission.outputs[0], out.inputs['Surface'])
    camera_data = bpy.data.cameras.new('Read-only perspective camera')
    camera = bpy.data.objects.new('Read-only perspective camera', camera_data)
    scene.collection.objects.link(camera); scene.camera = camera
    camera.location = (0.,0.,7.); camera.rotation_euler = (0.,0.,0.)
    area = next(area for area in bpy.context.screen.areas if area.type == 'VIEW_3D')
    space = area.spaces.active; space.shading.type = 'MATERIAL'
    space.region_3d.view_perspective = 'CAMERA'
    bpy.context.view_layer.update()
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(area=area, region=region):
        space.region_3d.update()
        saved = (camera.matrix_world.copy(), space.region_3d.view_matrix.copy(), state(obj))
        url = viewport.capture(bpy.context, b, max_side=512, isolate=True, frame_target=True,
            region_faces={obj:{2}}, region_topologies={obj:regions.topology(obj.data)})
    path = Path(tempfile.gettempdir()) / 'open-painter-isolated-camera-hardware.png'
    path.write_bytes(base64.b64decode(url.split(',',1)[1]))
    image = bpy.data.images.load(str(path)); width, height = image.size
    pixels = np.asarray(image.pixels[:]).reshape(height,width,4)
    green = (pixels[:,:,1] > .3) & (pixels[:,:,1] > pixels[:,:,0] * 1.3)
    red = (pixels[:,:,0] > .3) & (pixels[:,:,0] > pixels[:,:,1] * 1.3)
    rows, columns = np.nonzero(green)
    assert len(rows) > width*height*.025, ('small target unframed', len(rows),width,height)
    assert rows.max()-rows.min() > height*.35, ('hardware remains tiny', rows.max()-rows.min(),height)
    assert red.sum() == 0, 'unassigned body faces remain in isolated capture'
    assert camera.matrix_world == saved[0] and space.region_3d.view_matrix == saved[1]
    assert state(obj) == saved[2]
    bpy.data.images.remove(image)
    print('DIRECTOR_REGIONS_CAMERA_PIXELS_OK', width,height,int(green.sum()),str(path), flush=True)


open_painter.register()
if bpy.app.timers.is_registered(cache.tick): bpy.app.timers.unregister(cache.tick)
if bpy.app.background:
    geometry_checks()
else:
    def run():
        try:
            pixel_checks()
        except BaseException:
            traceback.print_exc()
            import os
            os._exit(1)
        bpy.ops.wm.quit_blender()
    bpy.app.timers.register(run, first_interval=2.)
