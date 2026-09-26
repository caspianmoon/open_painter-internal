"""Physical height survives export, transformed UVs, authored normals and assets."""

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import open_painter
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.export.exporter import export
from open_painter.export.surface_normal import combined
from open_painter.smart_materials import composer, loader, serializer
from open_painter.smart_materials.catalog import layer


def rgba(value):
    result = np.ones((*value.shape, 4), np.float32)
    result[..., :3] = value[..., None]
    return result


def plane(ts, name, coordinates):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(coordinates, [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new(name="Render UV")
    for item, coord in zip(uv.data, ((0, 0), (1, 0), (1, 1), (0, 1))):
        item.uv = coord
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    sets.assign(bpy.context.scene, ts, obj)
    return obj


open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, "Relief export", 512)
ts.preview_backend = 'CPU'  # Compare export against the raster adapter and shared baked images.
ts.height_distance = .5
obj = plane(ts, "Skewed relief plane", [(0, 0, 0), (2, 0, 0), (2.5, 1.5, 0), (.5, 1.5, 0)])
fill = stack.add(ts, "FILL", "Sloped height")
prop = stack.channel_for(fill, "HEIGHT")
prop.enabled = True
n = 512
v, u = np.indices((n, n), dtype=np.float32)
u, v = (u + .5) / n, (v + .5) / n
height = rgba(.25 + .2 * u + .1 * v)
prop.image = images.create("Height slope", n)
images.write(prop.image, height)
neutral = np.empty((n, n, 4), np.float32)
neutral[...] = (.5, .5, 1, 1)
actual = combined(ts, neutral, height)
expected = np.array([-.05, -1 / 60, 1], np.float32)
expected /= np.linalg.norm(expected)
np.testing.assert_allclose(actual[256, 128, :3] * 2 - 1, expected, atol=3e-6)
# The quad's triangulation diagonal belongs to one continuous UV chart.
np.testing.assert_allclose(actual[..., :3] * 2 - 1,
                           np.broadcast_to(expected, (n, n, 3)), atol=2e-5)

# A world transform changes the relief slope, including anisotropic scaling.
obj.scale = (2, 3, 1)
scene.view_layers[0].update()
scaled = combined(ts, neutral, height)
expected_scaled = np.array([-.025, -1 / 180, 1], np.float32)
expected_scaled /= np.linalg.norm(expected_scaled)
np.testing.assert_allclose(scaled[256, 128, :3] * 2 - 1, expected_scaled, atol=3e-6)
obj.scale = (1, 1, 1)
scene.view_layers[0].update()

# Resolution independence and correct +V/OpenGL convention. Use analytic
# ramps so derivative errors cannot be hidden by random image content.
small_v, small_u = np.indices((128, 128), dtype=np.float32)
small_height = .25 + .2 * (small_u + .5) / 128 + .1 * (small_v + .5) / 128
small_normal = np.tile(np.array([.5, .5, 1, 1], np.float32), (128, 128, 1))
np.testing.assert_allclose(combined(ts, small_normal, small_height)[64, 32, :3],
                           actual[256, 128, :3], atol=2e-6)
authored = neutral.copy()
authored[..., :3] = np.array([.2, .1, np.sqrt(.95)]) * .5 + .5
with_authored = combined(ts, authored, height)
assert np.max(abs(with_authored[..., :3] - actual[..., :3])) > .09
np.testing.assert_allclose(combined(ts, authored, rgba(np.full((n, n), .7, np.float32))),
                           authored, atol=1e-7)
ts.height_distance = 0
np.testing.assert_allclose(combined(ts, authored, height), authored, atol=1e-7)
ts.height_distance = .5

# Unrelated UV islands may touch in the atlas. Their individually constant
# height fields must not turn the shared atlas boundary into a physical bevel.
island_ts = sets.create(scene, "Touching UV islands", 512)
island_ts.height_distance = .5
island_mesh = bpy.data.meshes.new("Disconnected surfaces sharing an atlas boundary")
island_mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
                         (3, 0, 0), (4, 0, 0), (4, 1, 0), (3, 1, 0)], [],
                        [(0, 1, 2, 3), (4, 5, 6, 7)])
island_uv = island_mesh.uv_layers.new(name="Touching islands")
for item, coordinate in zip(island_uv.data, ((0, 0), (.5, 0), (.5, 1), (0, 1),
                                            (.5, 0), (1, 0), (1, 1), (.5, 1))):
    item.uv = coordinate
island_obj = bpy.data.objects.new("Disconnected touching islands", island_mesh)
scene.collection.objects.link(island_obj)
sets.assign(scene, island_ts, island_obj)
scene.view_layers[0].update()
island_height = rgba(np.where(u < .5, .3, .7).astype(np.float32))
np.testing.assert_allclose(combined(island_ts, neutral, island_height), neutral, atol=1e-7,
                           err_msg="Disconnected touching UV islands leaked height across their border")

# A dense evaluated mesh with mirrored, disconnected and degenerate triangles
# must produce the same metric and chart partition as the per-triangle
# reference, without ever building the per-triangle RNA list (which stalled
# exports of large assets for tens of seconds).
from unittest.mock import patch
from open_painter.export.surface_normal import _uv_metric
from open_painter.geometry import snapshots
from open_painter.geometry.uv_rasterizer import tiles


def reference_metric(ts, n):
    metric = np.zeros((n, n, 3), np.float32)
    charts = np.zeros((n, n), np.int32)
    chart_offset = 1
    with snapshots.targets(ts) as entries:
        for entry in entries:
            vertices, loops = entry["tri_vertices"], entry["tri_loops"]
            parents = list(range(len(vertices)))

            def root(index):
                while parents[index] != index:
                    parents[index] = parents[parents[index]]
                    index = parents[index]
                return index

            edges = {}
            for index in range(len(vertices)):
                uv = entry["uv"][loops[index]]
                du, dv = uv[1] - uv[0], uv[2] - uv[0]
                orientation = 1 if du[0] * dv[1] - du[1] * dv[0] >= 0 else -1
                for a, b in ((0, 1), (1, 2), (2, 0)):
                    if vertices[index][a] > vertices[index][b]:
                        a, b = b, a
                    key = (vertices[index][a], vertices[index][b], tuple(uv[a]), tuple(uv[b]), orientation)
                    parents[root(index)] = root(edges.setdefault(key, index))
            chart_ids = [root(index) for index in range(len(vertices))]
            for index, chart in enumerate(chart_ids):
                xyz = entry["positions"][vertices[index]]
                uv = entry["uv"][loops[index]]
                duv1, duv2 = uv[1] - uv[0], uv[2] - uv[0]
                determinant = duv1[0] * duv2[1] - duv1[1] * duv2[0]
                if abs(determinant) < 1e-12:
                    continue
                edge1, edge2 = xyz[1] - xyz[0], xyz[2] - xyz[0]
                dpdu = (edge1 * duv2[1] - edge2 * duv1[1]) / determinant
                dpdv = (edge2 * duv1[0] - edge1 * duv2[0]) / determinant
                length_u = np.linalg.norm(dpdu)
                if length_u < 1e-12:
                    continue
                shear = np.dot(dpdu, dpdv) / length_u
                length_v = np.linalg.norm(dpdv - dpdu * (shear / length_u))
                if length_v < 1e-12:
                    continue
                for region, inside, *_ in tiles(uv, n):
                    metric[region][inside] = (1 / length_u, -shear / (length_u * length_v), 1 / length_v)
                    charts[region][inside] = chart_offset + chart
            chart_offset += len(chart_ids)
    return metric, charts


dense_ts = sets.create(scene, "Dense relief mesh", 512)
dense_ts.height_distance = .5
dense_mesh = bpy.data.meshes.new("Dense relief islands")
# Two quads sharing an edge but with the second one's UVs mirrored back over
# the first (a seam the charts must not cross, later triangles winning the
# overlap), a quad whose UV corners exactly touch the first one's
# (disconnected), a quad overlapping the first elsewhere in the atlas and a
# UV-degenerate sliver that must not overwrite covered texels.
dense_mesh.from_pydata(
    [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (2, 0, 0), (2, 1, 0),
     (3, 0, 0), (4, 0, 0), (4, 1, 0), (3, 1, 0),
     (5, 0, 0), (6, 0, .5), (6, 1, .5), (5, 1, 0),
     (7, 0, 0), (7, 1, 0), (7, 2, 0)],
    [], [(0, 1, 2, 3), (1, 4, 5, 2), (6, 7, 8, 9), (10, 11, 12, 13), (14, 15, 16)])
dense_uv = dense_mesh.uv_layers.new(name="Render UV")
for item, coordinate in zip(dense_uv.data, (
        (0, 0), (.4, 0), (.4, .4), (0, .4),
        (.4, 0), (.2, 0), (.2, .4), (.4, .4),
        (.4, 0), (.8, 0), (.8, .4), (.4, .4),
        (.1, .1), (.3, .1), (.3, .3), (.1, .3),
        (.5, .5), (.5, .5), (.5, .5))):
    item.uv = coordinate
dense_obj = bpy.data.objects.new("Dense relief islands", dense_mesh)
scene.collection.objects.link(dense_obj)
dense_obj.scale = (1.5, .75, 1)
subdivision = dense_obj.modifiers.new("Dense", "SUBSURF")
subdivision.levels = 4
subdivision.subdivision_type = "SIMPLE"
sets.assign(scene, dense_ts, dense_obj)
scene.view_layers[0].update()
expected_metric, expected_charts = reference_metric(dense_ts, 256)
assert len(np.unique(expected_charts)) >= 5, np.unique(expected_charts)


def no_rna_triangles(self, key):
    assert key != "triangles", "the export metric materialised the per-triangle RNA list"
    raise KeyError(key)


with patch.object(snapshots.Target, "__missing__", no_rna_triangles):
    actual_metric, actual_charts = _uv_metric(dense_ts, 256)
np.testing.assert_allclose(actual_metric, expected_metric, rtol=1e-6, atol=1e-6)
# Chart labels are arbitrary; the partition of texels into charts is not.
_, expected_partition = np.unique(expected_charts, return_inverse=True)
_, actual_partition = np.unique(actual_charts, return_inverse=True)
np.testing.assert_array_equal(actual_partition, expected_partition,
                              err_msg="Vectorised UV charts partition the atlas differently")
np.testing.assert_array_equal((actual_charts > 0), (expected_charts > 0))

# Export both engine conventions and ensure the authoring image and the
# preview's single Bump connection are unchanged by exporting.
LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
np.testing.assert_allclose(images.pixels(images.output(ts, "NORMAL").image), neutral, atol=1e-7)
from open_painter.core import texture_set_node
roles = {node.get("role"): node for node in ts.material.node_tree.nodes}
assert roles["Surface"].inputs["Normal"].links[0].from_socket.name == "Normal"
assert roles["Surface"].inputs["Normal"].links[0].from_node == roles["Texture Set"]
inner = {node.get("role"): node for node in bpy.data.node_groups[texture_set_node.tree_name(ts)].nodes}
assert inner["Height"].inputs["Normal"].links[0].from_node == inner["Normal Map"]
assert inner["Height"].inputs["Distance"].default_value == ts.height_distance
with TemporaryDirectory(prefix="op-relief-") as directory:
    root = Path(directory)
    ts.export.bit_depth = "16"
    for preset, suffix, flip_y in (("BLENDER", "Normal", False), ("UNREAL", "Normal", True)):
        ts.export.preset = preset
        ts.export.directory = str(root / preset)
        files = export(bpy.context, ts, ts.export)
        path = next(path for path in files if path.stem.endswith("_" + suffix))
        loaded = bpy.data.images.load(str(path), check_existing=False)
        loaded.colorspace_settings.name = "Non-Color"
        pixel = images.pixels(loaded)[256, 128]
        expected_pixel = actual[256, 128].copy()
        if flip_y:
            expected_pixel[1] = 1 - expected_pixel[1]
        np.testing.assert_allclose(pixel, expected_pixel, atol=1 / 65535 + 1e-6)
        bpy.data.images.remove(loaded)
    np.testing.assert_allclose(images.pixels(images.output(ts, "NORMAL").image), neutral, atol=1e-7)

    # A real Cycles shader-normal render confirms that export slopes match the
    # visible Bump node, rather than merely matching another array function.
    for other in scene.objects:
        other.hide_render = other != obj
    camera_data = bpy.data.cameras.new("Relief validation camera")
    camera = bpy.data.objects.new("Relief validation camera", camera_data)
    scene.collection.objects.link(camera)
    camera.location = (1.25, .75, 5)
    camera_data.type, camera_data.ortho_scale = "ORTHO", 4
    scene.camera = camera
    scene.render.engine = "CYCLES"
    scene.cycles.device, scene.cycles.samples = "CPU", 16
    scene.cycles.use_denoising = False
    scene.render.resolution_x = scene.render.resolution_y = 64
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "OPEN_EXR"
    scene.render.image_settings.color_depth = "32"
    nodes, links = ts.material.node_tree.nodes, ts.material.node_tree.links
    encode = nodes.new("ShaderNodeVectorMath")
    encode.operation = "MULTIPLY_ADD"
    encode.inputs[1].default_value = (.5, .5, .5)
    encode.inputs[2].default_value = (.5, .5, .5)
    emission = nodes.new("ShaderNodeEmission")
    links.new(roles["Texture Set"].outputs["Normal"], encode.inputs[0])   # the bumped shading normal
    links.new(encode.outputs["Vector"], emission.inputs["Color"])
    links.new(emission.outputs["Emission"], roles["Output"].inputs["Surface"])
    # Render-pre flushes repair the managed material; inspect an independent
    # copy of its complete height graph without interfering with that handler.
    render_material = ts.material.copy()
    obj.material_slots[0].material = render_material
    bpy.ops.render.render()
    render_path = root / "shader-normal.exr"
    bpy.data.images["Render Result"].save_render(str(render_path), scene=scene)
    rendered = bpy.data.images.load(str(render_path))
    rendered.colorspace_settings.name = "Non-Color"
    np.testing.assert_allclose(images.pixels(rendered)[32, 32, :3], actual[256, 128, :3], atol=2e-4)
    bpy.data.images.remove(rendered)

    # Bump perturbs the connected authored Normal. It is not equivalent to
    # rotating a detail normal with RNM when that authored normal is tilted.
    tilted = neutral.copy()
    tilted[..., :3] = np.array([.6, .2, np.sqrt(.6)], np.float32) * .5 + .5
    normal_prop = stack.channel_for(fill, "NORMAL")
    normal_prop.enabled = True
    normal_prop.image = images.create("Tilted authored normal", n)
    images.write(normal_prop.image, tilted)
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
    bpy.ops.render.render()
    bpy.data.images["Render Result"].save_render(str(render_path), scene=scene)
    rendered = bpy.data.images.load(str(render_path), check_existing=False)
    rendered.colorspace_settings.name = "Non-Color"
    expected_tilted = combined(ts, tilted, height)
    np.testing.assert_allclose(images.pixels(rendered)[32, 32, :3], expected_tilted[256, 128, :3],
                               atol=2e-4, err_msg="Export disagrees with Bump on an authored normal")
    bpy.data.images.remove(rendered)

    # Mirrored U reverses tangent X in world space, while encoded tangent
    # normals still follow +U/+V. Validate handedness with the actual shader.
    normal_prop.enabled = False
    for uv in obj.data.uv_layers.active.data:
        uv.uv.x = 1 - uv.uv.x
    scene.view_layers[0].update()
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
    mirrored = combined(ts, neutral, height)
    expected_world = mirrored[256, 128, :3].copy()
    expected_world[0] = 1 - expected_world[0]
    bpy.ops.render.render()
    bpy.data.images["Render Result"].save_render(str(render_path), scene=scene)
    rendered = bpy.data.images.load(str(render_path), check_existing=False)
    rendered.colorspace_settings.name = "Non-Color"
    np.testing.assert_allclose(images.pixels(rendered)[32, 32, :3], expected_world, atol=2e-4,
                               err_msg="Mirrored UV tangent handedness disagrees with Blender")
    bpy.data.images.remove(rendered)
    obj.material_slots[0].material = ts.material
    bpy.data.materials.remove(render_material)
    nodes.remove(encode)
    nodes.remove(emission)
    links.new(roles["Surface"].outputs["BSDF"], roles["Output"].inputs["Surface"])

    # Offset coating heights use the native effect stack and survive sharing.
    material_ts = sets.create(scene, "Elevated coating", 512)
    spec = layer("Raised grain", (.2, .3, .4), .6, procedural="FBM", scale=8, height=.01)
    spec.update(height_range=(.55, .63), projection="TRIPLANAR", triplanar_hardness=6)
    document = dict(layers=[spec], surface={"height_distance": 1.25})
    composer.apply_document(material_ts, document)
    child = next(item for item in material_ts.layers if item.type == "FILL")
    channel = stack.channel_for(child, "HEIGHT")
    assert channel.projection == "TRIPLANAR" and channel.triplanar_hardness == 6
    assert channel.procedural_height == 1 and channel.effects[0].kind == "LEVELS"
    from open_painter.evaluation.channel_eval import source
    channel.projection = "UV"
    values = source(channel, "FILL", 512, 0, 512, {}, material_ts, {})
    assert .55 <= values[..., 0].min() < values[..., 0].max() <= .63
    assert values[..., 0].std() > .003
    asset = serializer.save(material_ts, root / "raised.openpaintmaterial")
    imported = sets.create(scene, "Reopened coating", 512)
    loader.apply(imported, asset)
    assert imported.height_distance == 1.25
    imported.anisotropy, imported.coat_weight, imported.coat_roughness = .3, .4, .2
    imported_id = imported.id
    expected_surface = {key: getattr(imported, key) for key in
                        ("height_distance", "anisotropy", "coat_weight", "coat_roughness")}
    imported_index = next(i for i, item in enumerate(scene.open_painter.texture_sets)
                          if item.id == imported_id)
    duplicated = sets.duplicate(scene, imported_index)
    # Adding a collection entry can invalidate an earlier RNA item reference.
    imported = next(item for item in scene.open_painter.texture_sets if item.id == imported_id)
    for key, expected_value in expected_surface.items():
        assert getattr(duplicated, key) == expected_value, key
    imported_height = stack.channel_for(next(item for item in imported.layers if item.type == "FILL"), "HEIGHT")
    np.testing.assert_allclose(source(imported_height, "FILL", 512, 0, 512, {}, imported, {}), values, atol=1e-7)
    legacy = root / "legacy.json"
    legacy.write_text(json.dumps(dict(format_version=2, name="Legacy", layers=[], surface={"coat_weight": .2})))
    imported.height_distance = .23
    loader.apply(imported, legacy)
    assert abs(imported.height_distance - .23) < 1e-7

open_painter.unregister()
print("SURFACE_RELIEF_EXPORT_PASS: world-scale/skew/resolution/UV-islands/mirrored-UVs/authored-normal-bump/DX/16-bit/asset-roundtrip", flush=True)
