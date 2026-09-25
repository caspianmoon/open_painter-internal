"""Rehydrate/adoption never dereferences a group node's tree pointer.

A material group node whose referenced tree is gone access-violates Blender
instead of raising (``rna_NodeTree_refine`` dereferences ``ntree->typeinfo``
with no validity check), and the scheduler read it on every adoption
(``Graph.rehydrate`` -> ``instance.node_tree``). Adoption now resolves owned
trees by tags over live datablocks; ambiguous states rebuild; the repair
sweep never deletes another live session's instance.
"""
import sys
from pathlib import Path
from time import monotonic

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.constants import OWNER
from open_painter.core import layer_stack as stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, shader_preview
from open_painter.evaluation.shader_graph import owned_tops, remove_orphan_tops


def ready(scene, ts):
    deadline = monotonic() + 60
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts):
            return info
    raise AssertionError(shader_preview.diagnostics(scene, ts))


def role_nodes(material):
    return [node for node in material.node_tree.nodes
            if node.bl_idname == 'ShaderNodeGroup'
            and node.get(OWNER) and node.get('role') == 'Procedural Graph']


def run():
    open_painter.register()
    if bpy.app.timers.is_registered(cache.tick):
        bpy.app.timers.unregister(cache.tick)
    scene = bpy.context.scene
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_plane_add(size=2)
    plane = bpy.context.object

    ts = sets.create(scene, 'Rehydrate', 512)
    sets.assign(scene, ts, plane)
    base = stack.add(ts, 'FILL', 'Base')
    stack.channel_for(base, 'BASE_COLOR').color = (.2, .4, .8, 1.)
    ready(scene, ts)
    material = ts.material
    assert len(role_nodes(material)) == 1, 'one procedural instance after build'
    graph = shader_preview.session_graph(scene, ts)
    assert graph is not None and graph.tree is not None
    assert graph.tree.users == 1, 'adopted top is singly referenced'
    assert len(owned_tops(ts.id)) == 1, 'tag scan finds the live top'

    # A detach keeps the material graph while sessions go away (disable /
    # reinstall). The next tick must adopt it through the tag scan.
    rehydrations = shader_preview.diagnostics(scene, ts)['graph_rehydrations']
    shader_preview.clear()
    assert shader_preview.session_graph(scene, ts) is None
    ready(scene, ts)
    assert shader_preview.diagnostics(scene, ts)['graph_rehydrations'] > rehydrations, \
        'material graph adopted after session reset'
    assert len(role_nodes(material)) == 1, 'adoption reuses the persisted instance'

    # A treeless decoy with the same tags must not break the session: the
    # repair sweep drops it without reading any tree pointer.
    decoy = material.node_tree.nodes.new('ShaderNodeGroup')
    decoy[OWNER], decoy['role'] = True, 'Procedural Graph'
    assert decoy.node_tree is None
    material_manager.repair(ts)
    assert len(role_nodes(material)) == 1, 'decoy instance swept, live one kept'
    assert shader_preview.session_graph(scene, ts) is not None

    # An unreferenced tree copy keeps its tags but has no users: it is not a
    # viable adoption candidate and the orphan-top sweep reaps it.
    top = shader_preview.session_graph(scene, ts).tree
    stray = top.copy()
    stray_name = stray.name
    assert stray.users == 0, 'copy starts unreferenced'
    assert len([t for t in owned_tops(ts.id) if t.users == 1]) == 1, \
        'stray copy is not an adoption candidate'
    material_manager.repair(ts)
    assert stray_name not in bpy.data.node_groups, 'orphan top swept after repair'

    # Two sets briefly sharing one material: repair separates them by copy,
    # and the sweep keeps each live session's instance on its own material.
    other = sets.create(scene, 'Shared', 512)
    other_base = stack.add(other, 'FILL', 'Base')
    stack.channel_for(other_base, 'BASE_COLOR').color = (.8, .2, .1, 1.)
    other.material = material
    ready(scene, other)
    ready(scene, ts)
    assert other.material.name != material.name, 'shared material separated by copy'
    assert len(role_nodes(material)) == 1, 'original keeps its single instance'
    assert len(role_nodes(other.material)) == 1, 'copy carries the rebuilt instance'
    live = shader_preview.live_instance_pointers()
    for owner in (ts, other):
        graph = shader_preview.session_graph(scene, owner)
        assert graph is not None and graph.tree.name in bpy.data.node_groups
        assert graph.instance.as_pointer() in live, 'live instances are guarded'
        assert graph.instance.name in owner.material.node_tree.nodes

    # Orphan-top sweep only reaps unreferenced tagged tops.
    live_top = shader_preview.session_graph(scene, ts).tree.name
    stray_top = bpy.data.node_groups.new('OP Stray Top', 'ShaderNodeTree')
    stray_top_name = stray_top.name
    stray_top[OWNER], stray_top['op_shader_graph'] = True, ts.id
    remove_orphan_tops(ts.id)
    assert live_top in bpy.data.node_groups, 'live top survives the orphan sweep'
    assert stray_top_name not in bpy.data.node_groups, 'unreferenced tops are swept'

    # The scene-field menu helper follows the registered enum, and stays
    # silent while the class is unregistered (reinstall windows).
    from open_painter.graph.operators import _scene_kind_items
    from open_painter.properties.influencer import OP_Influencer
    live = _scene_kind_items()
    assert live, 'menu lists scene kinds while registered'
    assert {identifier for identifier, _ in live} == \
        {item.identifier for item in OP_Influencer.bl_rna.properties['kind'].enum_items}, \
        'menu mirrors the registered enum'
    covered = False
    try:
        bpy.utils.unregister_class(OP_Influencer)
        try:
            assert _scene_kind_items() == (), 'menu draws empty while unregistered'
            covered = True
        finally:
            bpy.utils.register_class(OP_Influencer)
    except Exception:
        try:
            bpy.utils.register_class(OP_Influencer)
        except Exception:
            pass
    if covered:
        assert _scene_kind_items() == live, 'menu recovers after re-registration'

    print('SHADER_REHYDRATE_SAFETY_PASS', {'sets': len(scene.open_painter.texture_sets)}, flush=True)


run()
