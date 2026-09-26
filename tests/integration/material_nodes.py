"""The texture set's material is three nodes: its Open Painter Texture Set node, a
Principled BSDF and a Material Output — in every mode.

Headless (``gpu.init``). A new set's material reuses Blender's own Principled BSDF and
Material Output, and the node feeds the BSDF directly (Base Color, Metallic, Roughness,
Normal, Emission, Alpha, Tangent). AO in the preview, Engine Preview's adapters, View
Mask's snapshot and a CPU-reference display's images all live inside the node's group, so
none of them adds a node to the material. A material laid out by an earlier version
(wrapper nodes, labels, Blender's default pair beside Open Painter's) is repaired to the
three nodes; a default BSDF the artist wired stays. A group saved before the Tangent
output existed gains it without losing the node's links.
"""
import sys
from pathlib import Path
from time import monotonic

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.constants import OWNER
from open_painter.core import layer_stack as stack, material_manager, texture_set_node
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, preview_policy, shader_preview
from open_painter.targets import preview
from open_painter.ui import mask_view
from open_painter.utils.ids import new_id

THREE = sorted([texture_set_node.NODE_ID, 'ShaderNodeBsdfPrincipled', 'ShaderNodeOutputMaterial'])
WIRED = {('Base Color', 'Base Color'), ('Metallic', 'Metallic'), ('Roughness', 'Roughness'), ('Normal', 'Normal'),
         ('Emission', 'Emission Color'), ('Alpha', 'Alpha'), ('Tangent', 'Tangent')}

open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=2)
ts = sets.create(scene, 'Painted_Steel_Cube', 512)
sets.assign(scene, ts, bpy.context.object)
layer_id = stack.add(ts, 'FILL', 'Base').id
stack.channel_for(next(item for item in ts.layers if item.id == layer_id), 'BASE_COLOR').color = (.6, .3, .2, 1)


def ready():
    deadline = monotonic() + 60
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts):
            return info
    raise AssertionError(shader_preview.diagnostics(scene, ts))


def three(label):
    """The material is the three nodes, wired directly; returns them by role."""
    tree = ts.material.node_tree
    kinds = sorted(node.bl_idname for node in tree.nodes)
    assert kinds == THREE, (label, kinds)
    roles = {node.get('role'): node for node in tree.nodes}
    source, bsdf, output = roles['Texture Set'], roles['Surface'], roles['Output']
    wired = {(link.from_socket.name, link.to_socket.name) for link in tree.links if link.to_node == bsdf}
    assert wired == WIRED and all(link.from_node == source for link in tree.links if link.to_node == bsdf), (label, wired)
    assert output.is_active_output and output.inputs['Surface'].links[0].from_node == bsdf, label
    assert not bsdf.label and not output.label and not source.label, (label, bsdf.label, output.label, source.label)
    print('MATERIAL_NODES', label, 'three nodes', flush=True)
    return roles


def group():
    return bpy.data.node_groups[texture_set_node.tree_name(ts)]


def inner(role):
    return next((node for node in group().nodes if node.get('role') == role), None)


# ---- a new set: Blender's own BSDF and output, the node wired straight in ----------------------------
material_manager.repair(ts)
ready()
roles = three('new set')
assert roles['Surface'].name == 'Principled BSDF' and roles['Output'].name == 'Material Output', \
    'a new material keeps the BSDF and output Blender made for it'
assert inner('AO Preview').inputs[0].default_value == 1.0
ts.preview_ao = False
assert inner('AO Preview').inputs[0].default_value == 0.0, 'AO in Preview is a value inside the node'
ts.preview_ao = True
three('AO in the preview')

# ---- Engine Preview: the adapters live in the group ----------------------------------------------------
scene.open_painter.engine_preview = True
ready()
three('engine preview')
assert {preview.PREFIX + name for name in ('Base Color', 'Metallic', 'Roughness', 'Normal')} <= \
    {node.get('role') for node in group().nodes}
scene.open_painter.engine_preview = False
ready()
three('engine preview off')
assert not any(str(node.get('role', '')).startswith(preview.PREFIX) for node in group().nodes)

# ---- View Mask: the node shows the snapshot ---------------------------------------------------------------
layer = next(item for item in ts.layers if item.id == layer_id)
mask = layer.masks.add()
mask.id, mask.source, mask.procedural = new_id(), 'PROCEDURAL', 'CHECKER'
stack.invalidate(ts)
ready()
mask_view.enter(bpy.context, ts, next(item for item in ts.layers if item.id == layer_id))
three('view mask')
assert texture_set_node.showing_mask(ts)
mask_view.leave(bpy.context, ts)
three('back to material')
assert not texture_set_node.showing_mask(ts) and inner(texture_set_node.MASK_VIEW) is None

# ---- the CPU reference: the same three nodes; a display's proxy is bound inside the node ---------------------
ts.preview_backend = 'CPU'
material_manager.repair(ts)
three('cpu reference')
proxy = preview_policy.publish(scene, ts, 'BASE_COLOR', 64, ts.edit_serial, np.full((64, 64, 4), .5, np.float32))
assert proxy is not None and texture_set_node.channel_nodes(ts)['BASE_COLOR'].image == proxy
three('cpu reference with a proxy')
proxy_name = proxy.name
preview_policy.clear()   # restores the set's own images and releases the proxy
shown = texture_set_node.channel_nodes(ts)['BASE_COLOR'].image
assert proxy_name not in bpy.data.images and (shown is None or not shown.get('open_painter_disposable_preview'))
ts.preview_backend = 'LIVE'
ready()
three('engine again')

# ---- a material laid out by an earlier version ----------------------------------------------------------------
tree = ts.material.node_tree
nodes, links = tree.nodes, tree.links
default_bsdf, default_output = nodes.new('ShaderNodeBsdfPrincipled'), nodes.new('ShaderNodeOutputMaterial')
links.new(default_bsdf.outputs['BSDF'], default_output.inputs['Surface'])
for role, kind in (('AO Preview', 'ShaderNodeMixRGB'), ('Normal', 'ShaderNodeNormalMap'), ('Height', 'ShaderNodeBump'),
                   ('UV Tangent', 'ShaderNodeTangent'), ('Engine · Roughness', 'ShaderNodeGroup'),
                   ('Mask View', 'ShaderNodeTexImage'), ('BASE_COLOR', 'ShaderNodeTexImage')):
    item = nodes.new(kind)
    item[OWNER], item['role'], item.label = True, role, 'Open Painter · ' + role
for item in nodes:
    if item.get('role') in {'Surface', 'Output', 'Texture Set'}:
        item.label = 'Open Painter · ' + item['role']
assert len(nodes) == 12
material_manager.repair(ts)
three('an earlier layout repaired')

# ---- a default BSDF the artist wired stays ---------------------------------------------------------------------------
tree = ts.material.node_tree
mine = tree.nodes.new('ShaderNodeBsdfPrincipled')
mine.name = 'Artist BSDF'
tree.links.new(tree.nodes.new('ShaderNodeRGB').outputs[0], mine.inputs['Base Color'])
material_manager.repair(ts)
assert 'Artist BSDF' in tree.nodes, 'a BSDF with links into it is the artist\'s'
tree.nodes.remove(next(node for node in tree.nodes if node.bl_idname == 'ShaderNodeRGB'))
tree.nodes.remove(tree.nodes['Artist BSDF'])
material_manager.repair(ts)
three('artist nodes removed by hand')

# ---- a group saved before the Tangent output keeps the node's links ---------------------------------------------------
tangent = next(item for item in group().interface.items_tree
               if item.item_type == 'SOCKET' and item.in_out == 'OUTPUT' and item.name == 'Tangent')
group().interface.remove(tangent)
source = next(node for node in ts.material.node_tree.nodes if node.bl_idname == texture_set_node.NODE_ID)
assert 'Tangent' not in source.outputs and source.outputs['Base Color'].links
texture_set_node.sync(ts)
assert 'Tangent' in source.outputs and source.outputs['Base Color'].links and source.outputs['Normal'].links, \
    'appending an output kept the existing links'
material_manager.repair(ts)
three('tangent output appended')
print('MATERIAL_NODES_PASS', flush=True)
open_painter.unregister()
