"""Crease baking rejects triangulation, UV seams and smooth tessellation."""

import sys
from pathlib import Path
import bpy
import bmesh
import numpy as np
from mathutils import Matrix

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.baking.maps.curvature import crease_edges, rasterize, bake_curvature
from open_painter.core import texture_set_manager as sets, image_manager as images
from open_painter.generators.edge_wear import edge_wear

open_painter.register()
# The default cube has 12 physical convex creases. Tessellation must add none.
obj = bpy.context.object
assert len(crease_edges(obj.data, Matrix.Identity(4), 20)) == 12
mesh = obj.data.copy()
bm = bmesh.new()
bm.from_mesh(mesh)
bmesh.ops.triangulate(bm, faces=list(bm.faces))
bm.to_mesh(mesh)
bm.free()
assert len(crease_edges(mesh, Matrix.Identity(4), 20)) == 12
# Deliberately unrelated UV coordinates do not make triangle boundaries edges.
for polygon in mesh.polygons:
    for j, index in enumerate(polygon.loop_indices):
        mesh.uv_layers.active.data[index].uv = (
            (j == 1) * 0.4 + (polygon.index % 2) * 0.5,
            (j == 2) * 0.4,
        )
assert len(crease_edges(mesh, Matrix.Identity(4), 20)) == 12
bpy.data.meshes.remove(mesh)
# A dense smooth surface must not turn into a wireframe at maximum intensity.
bpy.ops.mesh.primitive_uv_sphere_add(segments=96, ring_count=64)
sphere = bpy.context.object
assert not crease_edges(sphere.data, Matrix.Identity(4), 20)
coverage = rasterize(sphere.data, Matrix.Identity(4), {0}, 512, 20, 4)
assert not coverage.any()
assert not edge_wear(coverage, None, np.ones_like(coverage), 1, 20, 1).any()
# A planar triangulated quad contains no convex creases, regardless of UV seams.
flat = bpy.data.meshes.new("Flat triangles")
flat.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2), (0, 2, 3)])
flat.update()
flat.uv_layers.new()
assert not crease_edges(flat, Matrix.Identity(4), 1)
bpy.data.meshes.remove(flat)
# A concave fold must not be classified as exposed edge wear.
fold = bpy.data.meshes.new("Concave fold")
fold.from_pydata(
    [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1)],
    [],
    [(0, 1, 2, 3), (1, 0, 4, 5)],
)
fold.update()
assert not crease_edges(fold, Matrix.Identity(4), 20)
bpy.data.meshes.remove(fold)
# Actual baked image has edge pixels AND clean face interiors; no normal map needed.
ts = sets.create(bpy.context.scene, "Geometry edges", 512)
sets.assign(bpy.context.scene, ts, obj)
image = bake_curvature(ts)
pixels = images.pixels(image)[..., 0]
assert pixels.max() > 0.5 and np.count_nonzero(pixels) < pixels.size * 0.15
# Baked maps come from the Cycles Bevel shader (0c31328); the geometric helpers above serve the
# live edge field and the crease overlay.
assert image.packed_file and image["open_painter_curvature_method"] == "CYCLES_BEVEL"
assert not ts.baked_maps
assert bpy.ops.open_painter.bake(action="CURVATURE") == {"FINISHED"}
assert ts.baked_maps[0].image.get("open_painter_curvature_method") == "CYCLES_BEVEL"
# A rounded edge below the crease angle is less curved than a sharp one: the Bevel shader
# gives it a weaker band, still on the edges, while a dense sphere and the flat faces stay black.
bevel = obj.modifiers.new("Bevel", "BEVEL")
bevel.width, bevel.segments = .04, 6  # about 13 degrees per edge, under the 20 degree crease minimum; tighter than the 0.02 radius
bpy.context.view_layer.update()
ts.baked_maps.clear()
assert not crease_edges(obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data, obj.matrix_world, 20)
smooth = images.pixels(bake_curvature(ts))[..., 0]
assert np.isfinite(smooth).all() and smooth.max() < pixels.max(), (smooth.max(), pixels.max())
assert np.count_nonzero(smooth > .01) < smooth.size * .2, np.count_nonzero(smooth > .01) / smooth.size
obj.modifiers.remove(bevel)
# A crease-only map left from an older bake is replaced when an edge-driven
# recipe is added; a user-supplied curvature image is never touched.
from open_painter.baking.bake_manager import refresh_stale_curvature, register_map
legacy = images.create("Geometry edges_BakedCurvature", 512, (0, 0, 0, 1))
legacy["open_painter_curvature_method"] = "GEOMETRIC_CREASES_V1"
register_map(ts, "CURVATURE", legacy)
assert refresh_stale_curvature(ts, {"AO"}) is False
assert refresh_stale_curvature(ts, {"CURVATURE"}) is True
current = next(m.image for m in ts.baked_maps if m.kind == "CURVATURE")
assert current.get("open_painter_curvature_method") == "CYCLES_BEVEL", current.get("open_painter_curvature_method")
assert images.pixels(current)[..., 0].max() > .5, (current.name, images.pixels(current)[..., 0].max())
assert refresh_stale_curvature(ts, {"CURVATURE"}) is False
custom = images.create("Artist curvature", 512, (.2, .2, .2, 1))
register_map(ts, "CURVATURE", custom)
assert refresh_stale_curvature(ts, {"CURVATURE"}) is False and next(m.image for m in ts.baked_maps if m.kind == "CURVATURE") == custom
sphere_ts = sets.create(bpy.context.scene, "Dense sphere", 512)
sets.assign(bpy.context.scene, sphere_ts, sphere)
# Uniform curvature stays black to within the Bevel shader's sampling noise (as curvature_cycles.py).
assert images.pixels(bake_curvature(sphere_ts))[..., 0].max() < .01, "Uniform curvature must stay flat"
open_painter.unregister()
print(
    "GEOMETRIC_CURVATURE_PASS: 12 cube creases before/after triangulation; smooth sphere and flat triangulation stay black at maximum intensity; UV seams add no edges; packed Cycles Bevel bake; a rounded edge stays below a sharp crease; dense sphere stays black"
)
