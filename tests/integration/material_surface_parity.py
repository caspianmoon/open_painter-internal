"""Read actual Eevee shader channels and compare native CPU/export results.

512px diagnostic plane spans16mm (32samples per1mm weave). Exported maps
are reimported and rendered under the same camera. No Cycles or AI calls.
"""
import json
from pathlib import Path
import sys
import time

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import open_painter
from open_painter.authoring import transactions, document, physical_space
from open_painter.core import texture_set_manager as sets, image_manager
from open_painter.evaluation import cache, shader_preview
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.shader_graph import Graph
from open_painter.export import exporter

OUT = ROOT / ".blender-test" / "material-remedy-parity"
OUT.mkdir(parents=True, exist_ok=True)
open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x = scene.render.resolution_y = 512
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "OPEN_EXR"
scene.render.image_settings.color_depth = "32"
scene.render.film_transparent = False
scene.world.color = (0,0,0)
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"
scene.view_settings.exposure = 0
scene.view_settings.gamma = 1
bpy.ops.mesh.primitive_plane_add(size=.016)
obj = bpy.context.object
ts = sets.create(scene, "Physical weave", 512)
ts.preview_backend = "CPU"
sets.assign(scene, ts, obj)
physical_space.declare(ts, 1, "16mm diagnostic plane defined in metres")
proposal = dict(version=1, summary="Physical weave parity", commands=[dict(op="create_surface", id="new:weave", name="Weave", family="WOVEN_FABRIC", values=dict(period_m=.001, height_m=.00003))])
transactions.apply(ts, proposal, operation_id="parity", expected_digest=document.digest(ts))
LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
expected = {out.channel: image_manager.pixels(out.image)[..., :3].copy() for out in ts.outputs}
ts.export.follow_target = False
ts.export.preset, ts.export.file_format, ts.export.bit_depth = "BLENDER", "PNG", "16"
ts.export.padding_mode = "NONE"
paths = exporter.export(bpy.context, ts, ts.export, directory=str(OUT / "maps"), overwrite=True)
# Construct native shader graph exactly as shader preview does, then route one
# output through emission solely to read shader values without lighting bias.
material = ts.material
material.node_tree.nodes.clear()
graph = Graph(scene, ts, material).build()
while graph.pending_resources:
    graph.step_resources(.1)
output = material.node_tree.nodes.new("ShaderNodeOutputMaterial")
emission = material.node_tree.nodes.new("ShaderNodeEmission")
material.node_tree.links.new(emission.outputs[0], output.inputs["Surface"])
bpy.ops.object.camera_add(location=(0,0,.04))
camera = bpy.context.object
camera.data.type = "ORTHO"
camera.data.ortho_scale = .016
camera.data.clip_start = .001
camera.rotation_euler = (0,0,0)
scene.camera = camera


def render(name, socket):
    material.node_tree.links.new(socket, emission.inputs["Color"])
    path = OUT / (name + ".exr")
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(str(path), check_existing=False)
    result = image_manager.pixels(image)[..., :3].copy()
    bpy.data.images.remove(image)
    return result


report = dict(engine=scene.render.engine, physical_period_m=.001, resolution=512,
              samples_per_period=32, channels={}, source="actual_Eevee_renders_and_production_export")
for channel in ("BASE_COLOR", "ROUGHNESS", "HEIGHT"):
    native = render(channel.lower() + "_native", graph.outputs[channel])
    label = {"BASE_COLOR":"BaseColor", "ROUGHNESS":"Roughness", "HEIGHT":"Height"}[channel]
    path = next(path for path in paths if label.lower() in path.stem.lower().replace("_", ""))
    image = bpy.data.images.load(str(path), check_existing=False)
    image.colorspace_settings.name = "sRGB" if channel == "BASE_COLOR" else "Non-Color"
    texture = material.node_tree.nodes.new("ShaderNodeTexImage")
    texture.image = image
    texture.interpolation = "Linear"
    exported = render(channel.lower() + "_exported", texture.outputs["Color"])
    raw_delta = np.abs(native[2:-2,2:-2] - expected[channel][2:-2,2:-2])
    # Pixel integration/antialiasing changes sharp weave peaks. Compare CPU
    # channels through the same Eevee image sampler, and report raw texel error
    # separately rather than weakening parity tolerance to fit filtered pixels.
    cpu_image = bpy.data.images.new("CPU reference", width=512, height=512, float_buffer=True)
    cpu_image.colorspace_settings.name = "Non-Color"
    rgba = np.concatenate((expected[channel], np.ones((512,512,1), np.float32)), axis=-1)
    cpu_image.pixels.foreach_set(rgba.ravel())
    cpu_image.update()
    texture.image = cpu_image
    cpu_render = render(channel.lower() + "_cpu", texture.outputs["Color"])
    delta = np.abs(native[2:-2,2:-2] - cpu_render[2:-2,2:-2])
    export_delta = np.abs(native[2:-2,2:-2] - exported[2:-2,2:-2])
    report["channels"][channel] = dict(cpu_shader_mae=float(delta.mean()), cpu_shader_max=float(delta.max()), raw_texel_mae=float(raw_delta.mean()),
                                        native_export_mae=float(export_delta.mean()), native_export_max=float(export_delta.max()),
                                        exported_file=str(path))
    assert delta.mean() < .001, report
    assert export_delta.mean() < .001, report
    material.node_tree.nodes.remove(texture)
    bpy.data.images.remove(image)
    bpy.data.images.remove(cpu_image)
# Height relief normal uses the same world amplitude in native Eevee and
# production-exported tangent normals. Compare evaluated normals, not a claim
# based on a matching amplitude parameter.
bump = material.node_tree.nodes.new("ShaderNodeBump")
bump.inputs["Distance"].default_value = ts.height_distance
material.node_tree.links.new(graph.outputs["HEIGHT"], bump.inputs["Height"])
scale = material.node_tree.nodes.new("ShaderNodeVectorMath")
scale.operation = "SCALE"
scale.inputs[3].default_value = .5
add = material.node_tree.nodes.new("ShaderNodeVectorMath")
add.operation = "ADD"
add.inputs[1].default_value = (.5,.5,.5)
material.node_tree.links.new(bump.outputs["Normal"], scale.inputs[0])
material.node_tree.links.new(scale.outputs[0], add.inputs[0])
native_normal = render("normal_relief_native", add.outputs[0])
normal_path = next(path for path in paths if "normal" in path.stem.lower())
normal_image = bpy.data.images.load(str(normal_path), check_existing=False)
normal_image.colorspace_settings.name = "Non-Color"
texture = material.node_tree.nodes.new("ShaderNodeTexImage")
texture.image = normal_image
normal_map = material.node_tree.nodes.new("ShaderNodeNormalMap")
material.node_tree.links.new(texture.outputs["Color"], normal_map.inputs["Color"])
material.node_tree.links.new(normal_map.outputs[0], scale.inputs[0])
exported_normal = render("normal_relief_exported", add.outputs[0])
normal_delta = np.abs(native_normal[4:-4,4:-4] - exported_normal[4:-4,4:-4])
report["normal_relief"] = dict(native_export_mae=float(normal_delta.mean()),
    native_export_max=float(normal_delta.max()), tolerance_mae=.02,
    note="Eevee screen derivatives and texture-space export derivatives differ at thread peaks")
assert normal_delta.mean() < .02, report
(OUT / "result.json").write_text(json.dumps(report, indent=2))
print("MATERIAL_SURFACE_EEVEE_CPU_EXPORT_PARITY_PASS", json.dumps(report), flush=True)
