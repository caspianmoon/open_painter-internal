"""Engine Preview: the display material shows what the target engine computes from the
exported textures — 8-bit sRGB colour, smoothness/DirectX round trips, BC5 Z reconstruction,
AO indirect only, Standard view — and toggling it back restores the plain preview. The
adapters live in the Texture Set node's group: the material stays that node, a Principled
BSDF and a Material Output throughout."""
import sys
from pathlib import Path
from time import monotonic

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.constants import OWNER
from open_painter.core import image_manager as images, layer_stack as stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.core import texture_set_node
from open_painter.evaluation import cache, shader_preview
from open_painter.targets import preview

N = 16
open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
project = scene.open_painter
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=2)
plane = bpy.context.object
ts = sets.create(scene, 'Engine preview', 512); sets.assign(scene, ts, plane)
base = stack.add(ts, 'FILL', 'Base')
stack.channel_for(base, 'BASE_COLOR').color = (.2, .5, .7, 1.)
stack.channel_for(base, 'ROUGHNESS').value = .37
stack.channel_for(base, 'METALLIC').value = .61
stack.channel_for(base, 'NORMAL').enabled = True
stack.channel_for(base, 'NORMAL').color = (.6, .3, .9, 1.)
stack.channel_for(base, 'AO').enabled = True
stack.channel_for(base, 'AO').value = .5
ts.preview_ao = True
material_manager.repair(ts)


def ready():
    deadline = monotonic() + 60
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts):
            return info
    raise AssertionError(shader_preview.diagnostics(scene, ts))


def group():
    return bpy.data.node_groups[texture_set_node.tree_name(ts)]


def roles():
    """What decides what the surface shows: the nodes of the Texture Set node's group."""
    kinds = sorted(node.bl_idname for node in ts.material.node_tree.nodes)
    assert kinds == sorted([texture_set_node.NODE_ID, 'ShaderNodeBsdfPrincipled', 'ShaderNodeOutputMaterial']), kinds
    return {node.get('role'): node for node in group().nodes if node.get('role')}


def group_output():
    return next(node for node in group().nodes if node.bl_idname == 'NodeGroupOutput')


def bake(inner):
    """Bake ``inner`` (a socket inside the group) through a temporary output of the group."""
    # A scripted bake fires no bake handler: copy the engine's results into the images' pixels
    # (Cycles reads those), as the add-on's own bakes do.
    shader_preview.sync_pixels()
    scene.render.engine = 'CYCLES'; scene.cycles.device = 'CPU'; scene.cycles.samples = 1
    scene.render.bake.margin = 0; scene.render.bake.use_clear = True
    probe = group().interface.new_socket(name='Probe', in_out='OUTPUT', socket_type='NodeSocketColor')
    group().links.new(inner, group_output().inputs['Probe'])
    tree = ts.material.node_tree
    socket = next(n for n in tree.nodes if n.bl_idname == texture_set_node.NODE_ID).outputs['Probe']
    target = bpy.data.images.new('Engine readback', width=N, height=N, float_buffer=True)
    target.colorspace_settings.name = 'Non-Color'
    output = next(n for n in tree.nodes if n.bl_idname == 'ShaderNodeOutputMaterial')
    emission = tree.nodes.new('ShaderNodeEmission')
    node = tree.nodes.new('ShaderNodeTexImage'); node.image = target; tree.nodes.active = node
    surface = output.inputs['Surface'].links[0].from_socket
    tree.links.new(emission.outputs[0], output.inputs['Surface'])
    tree.links.new(socket, emission.inputs['Color'])
    bpy.ops.object.select_all(action='DESELECT'); plane.select_set(True); bpy.context.view_layer.objects.active = plane
    bpy.ops.object.bake(type='EMIT')
    value = images.pixels(target)[N // 2, N // 2, :3].copy()
    tree.links.new(surface, output.inputs['Surface'])
    tree.nodes.remove(node); tree.nodes.remove(emission)
    bpy.data.images.remove(target)
    group().interface.remove(probe)
    return value


q = lambda x: np.round(np.asarray(x) * 255) / 255
srgb = lambda x: np.where(x <= .0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - .055)
linear = lambda y: np.where(y <= .04045, y / 12.92, np.power((y + .055) / 1.055, 2.4))

ready()
graph = shader_preview.ensure(scene, ts)
identities = graph.node_identities()
builds = shader_preview.diagnostics(scene, ts)['graph_builds']
before = roles()
assert before['AO Preview'].inputs[0].default_value == 1
saved_view = scene.view_settings.view_transform
scene.view_settings.view_transform = 'AgX'

# Unreal: DirectX normals, roughness, AO indirect, quantised.
project.target = 'UNREAL'
assert ts.export.preset == 'UNREAL' and ts.export.follow_target
project.engine_preview = True
ready()
assert shader_preview.ensure(scene, ts) is graph and graph.node_identities() == identities, 'engine preview rebuilt the graph'
assert shader_preview.diagnostics(scene, ts)['graph_builds'] == builds
after = roles()
assert all(preview.PREFIX + name in after for name in ('Base Color', 'Metallic', 'Roughness', 'Normal')), list(after)
assert group_output().inputs['Roughness'].links[0].from_node == after[preview.PREFIX + 'Roughness']
assert after['Normal Map'].inputs['Color'].links[0].from_node == after[preview.PREFIX + 'Normal']
assert after['AO Preview'].inputs[0].default_value == 0, 'AO must be indirect only'
assert scene.view_settings.view_transform == 'Standard' and project.saved_view_transform == 'AgX'
normal = bake(after[preview.PREFIX + 'Normal'].outputs['Color'])
# The graph's final normal is normalised before the adapter sees it.
vector = np.array((.6, .3, .9)) * 2 - 1
unit = vector / np.linalg.norm(vector) * .5 + .5
r, g = q(unit[0]), 1 - q(1 - unit[1])
x, y = 2 * r - 1, 2 * g - 1
z = np.sqrt(max(0.0, 1 - x * x - y * y))
np.testing.assert_allclose(normal, (r, g, (z + 1) / 2), atol=2e-3)
rough = bake(after[preview.PREFIX + 'Roughness'].outputs['Value'])[0]
np.testing.assert_allclose(rough, q(.37), atol=2e-3)
color = bake(after[preview.PREFIX + 'Base Color'].outputs['Color'])
np.testing.assert_allclose(color, linear(q(srgb(np.array((.2, .5, .7))))), atol=3e-3)
metal = bake(after[preview.PREFIX + 'Metallic'].outputs['Value'])[0]
np.testing.assert_allclose(metal, q(.61), atol=2e-3)
print('ENGINE_PREVIEW unreal: PASS', flush=True)

# Unity HDRP: smoothness round trip, OpenGL normals (green untouched).
project.target = 'UNITY_HDRP'
ready()
after = roles()
rough = bake(after[preview.PREFIX + 'Roughness'].outputs['Value'])[0]
np.testing.assert_allclose(rough, 1 - q(1 - .37), atol=2e-3)
normal = bake(after[preview.PREFIX + 'Normal'].outputs['Color'])
np.testing.assert_allclose(normal[1], q(unit[1]), atol=2e-3)
project.engine_preview_normal_reconstruct = False
ready()
normal = bake(roles()[preview.PREFIX + 'Normal'].outputs['Color'])
np.testing.assert_allclose(normal[2], q(unit[2]), atol=2e-3)
project.engine_preview_normal_reconstruct = True
print('ENGINE_PREVIEW unity: PASS', flush=True)

# Off: adapters removed, plain wiring back, AO and view transform restored; graph untouched throughout.
project.engine_preview = False
ready()
final = roles()
assert not any(str(role).startswith(preview.PREFIX) for role in final), [r for r in final if str(r).startswith(preview.PREFIX)]
assert group_output().inputs['Roughness'].links[0].from_node == final['ROUGHNESS Value']
assert final['Normal Map'].inputs['Color'].links[0].from_node == final['NORMAL']
assert final['AO Preview'].inputs[0].default_value == 1
assert scene.view_settings.view_transform == 'AgX' and not project.saved_view_transform
scene.view_settings.view_transform = saved_view
assert shader_preview.ensure(scene, ts) is graph and shader_preview.diagnostics(scene, ts)['graph_builds'] == builds
# Blender target: no conventions to adapt beyond quantisation; CUSTOM leaves export settings alone.
project.target = 'CUSTOM'
ts.export.preset = 'GLTF'
project.target = 'BLENDER'
assert ts.export.preset == 'BLENDER'
ts.export.follow_target = False
project.target = 'UNREAL'
assert ts.export.preset == 'BLENDER', 'a set that does not follow the target kept its preset'
print('ENGINE_PREVIEW_PASS', flush=True)
open_painter.unregister()
