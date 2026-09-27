"""Legacy adoption identifies exact trees without dereferencing group-node pointers.

The current engine migrates legacy material groups to a Texture Set node. Both
standalone legacy graph adoption and that migration must tolerate treeless decoys.
"""
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.constants import OWNER
from open_painter.core import layer_stack as stack, material_manager, texture_set_manager as sets
from open_painter.evaluation.shader_graph import Graph, _adoptable_top, owned_tops, remove_orphan_tops

open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, 'Rehydrate', 512)
ts.preview_backend = 'CPU'
stack.add(ts, 'FILL', 'Base')
graph = Graph(scene, ts, ts.material)
graph.build()
assert _adoptable_top(ts.id, graph.instance) == graph.tree

# Same sockets and owner tags do not prove that an instance owns a particular tree.
decoy = ts.material.node_tree.nodes.new('ShaderNodeGroup')
decoy[OWNER], decoy['role'] = True, 'Procedural Graph'
assert _adoptable_top(ts.id, decoy) is None
class PointerTrap(dict):
    @property
    def node_tree(self):
        raise AssertionError('unsafe tree pointer read')
    outputs = graph.instance.outputs
proxy = PointerTrap(op_tree_identity=graph.tree['op_tree_identity'])
assert _adoptable_top(ts.id, proxy) == graph.tree
proxy['op_tree_identity'] = 'another graph'
assert _adoptable_top(ts.id, proxy) is None

adopted = Graph(scene, ts, ts.material)
assert adopted.rehydrate(), 'legacy graph can still be adopted by exact identities'
assert adopted.tree == graph.tree
# A stray tagged tree must not be adopted or survive the orphan sweep.
stray = graph.tree.copy()
stray_name = stray.name
assert _adoptable_top(ts.id, graph.instance) == graph.tree
remove_orphan_tops(ts.id)
assert stray_name not in bpy.data.node_groups

# The current engine's repair sweeps legacy groups without reading node.node_tree.
material_manager.repair(ts)
assert not any(node.get('role') == 'Procedural Graph' for node in ts.material.node_tree.nodes)
assert not owned_tops(ts.id)
assert any(node.bl_idname == 'OPShaderNodeTextureSet' for node in ts.material.node_tree.nodes)

from open_painter.graph.operators import _scene_kind_items
from open_painter.properties.influencer import OP_Influencer
assert {key for key, _ in _scene_kind_items()} == {
    item.identifier for item in OP_Influencer.bl_rna.properties['kind'].enum_items}
open_painter.unregister()
print('SHADER_REHYDRATE_SAFETY_PASS', flush=True)
