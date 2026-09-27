"""The texture set's material: its Open Painter Texture Set node, a Principled BSDF and a
Material Output, nothing else. What the surface shows of the channels (AO in the preview,
normal and height, Engine Preview, View Mask, a CPU-reference display's images) lives
inside the Texture Set node's group (``texture_set_node``)."""

import bpy

from ..constants import CHANNELS, OWNER
from .surface_settings import inputs as surface_inputs

# Nodes earlier versions placed around the surface; the Texture Set node carries all of it.
LEGACY_ROLES = frozenset({'UV Tangent', 'AO Preview', 'Normal', 'Height', 'UDIM Source UV', 'UDIM Local UV',
                          'Mask View', 'Procedural Graph', *CHANNELS})
# Texture Set node output -> Principled BSDF input.
SURFACE = (('Base Color', 'Base Color'), ('Metallic', 'Metallic'), ('Roughness', 'Roughness'),
           ('Normal', 'Normal'), ('Emission', 'Emission Color'), ('Alpha', 'Alpha'), ('Tangent', 'Tangent'))
LAYOUT = {'Texture Set': (-620, 0), 'Surface': (-260, 0), 'Output': (140, 0)}


def _surface_values(ts, bsdf):
    from ..targets.preview import specular
    for socket, value in (*surface_inputs(ts),
                          ("Specular IOR Level", specular(getattr(ts.id_data, 'open_painter', None)))):
        current = bsdf.inputs[socket].default_value
        if (tuple(current) if isinstance(value, tuple) else current) != value:
            bsdf.inputs[socket].default_value = value


def update_surface(ts):
    """Update surface uniforms without compiling layers or touching images."""
    if not ts.material or not ts.material.use_nodes:
        return
    bsdf = next((node for node in ts.material.node_tree.nodes
                 if node.get(OWNER) and node.get('role') == 'Surface'), None)
    if bsdf is not None:
        _surface_values(ts, bsdf)
    from .texture_set_node import sync
    sync(ts, images=False)   # height distance and AO in the preview live in the node; a display's images stay


def _adopt_defaults(nodes):
    """A new material's own Principled BSDF and Material Output become the set's."""
    for kind, role in (('ShaderNodeBsdfPrincipled', 'Surface'), ('ShaderNodeOutputMaterial', 'Output')):
        found = next((node for node in nodes if node.bl_idname == kind and not node.get(OWNER)), None)
        if found is not None:
            found[OWNER], found['role'] = True, role


def _drop_legacy(nodes):
    """Remove the nodes earlier versions placed around the surface; True if any was removed."""
    from ..targets.preview import PREFIX
    removed = False
    for node in list(nodes):
        role = node.get('role')
        if not node.get(OWNER) or role is None or not (role in LEGACY_ROLES or str(role).startswith(PREFIX)):
            continue
        nodes.remove(node)
        removed = True
    return removed


def _drop_untouched_defaults(nodes, keep):
    """The Principled BSDF and Material Output a new material starts with, left beside the set's
    own by earlier versions: removed while nothing is linked into them (an edited one stays).
    Everything is decided before anything is removed: a removed node's wrapper reads freed memory."""
    doomed = {}
    for node in nodes:
        if node.get(OWNER) or node.as_pointer() in keep or node.bl_idname != 'ShaderNodeBsdfPrincipled':
            continue
        targets = [link.to_node for socket in node.outputs for link in socket.links]
        if any(socket.is_linked for socket in node.inputs) or any(
                target.get(OWNER) or target.bl_idname != 'ShaderNodeOutputMaterial' for target in targets):
            continue
        doomed[node.as_pointer()] = node
        for target in targets:
            if all(link.from_node.as_pointer() == node.as_pointer() for socket in target.inputs for link in socket.links):
                doomed[target.as_pointer()] = target
    for node in nodes:
        if (node.bl_idname == 'ShaderNodeOutputMaterial' and not node.get(OWNER) and node.as_pointer() not in keep
                and not any(socket.is_linked for socket in node.inputs)):
            doomed[node.as_pointer()] = node   # an unlinked default output would compete with the set's
    for node in doomed.values():
        nodes.remove(node)


def _ensure_output(ts, channel):
    """The set's channel image the engine writes into: half float on the GPU, full float for
    Height (bump detail is finer than a half-float step)."""
    from . import image_manager
    from ..evaluation.shader_resources import new_leaves_frozen
    prop = next((o for o in ts.outputs if o.channel == channel), None)
    if (prop is None or prop.image is None) and new_leaves_frozen():
        # Between undo steps no datablock may be created for the material to reference.
        return None
    image = image_manager.output(ts, channel).image
    half = channel != 'HEIGHT'
    if image.use_half_precision != half:
        image.use_half_precision = half
    if not image.get('op_engine_output'):
        image['op_engine_output'] = True
    return image


def relink_slots(ts):
    """Put the set's material back on the assigned slots another material was dropped on.

    Explicit (the Repair Material button): render and save handlers also repair the
    material's graph, and a slot the artist swapped for a render of their own must stay
    theirs. Returns the objects whose slots changed.
    """
    material = ts.material
    changed = []
    if material is None:
        return changed
    for ref in ts.assignments:
        obj = ref.object
        if obj is not None and ref.slot_index < len(obj.material_slots) \
                and obj.material_slots[ref.slot_index].material != material:
            obj.material_slots[ref.slot_index].material = material
            changed.append(obj)
    return changed


def repair(ts, image_overrides=None):
    material = ts.material
    # Independent recipes cannot share one mutable material shader. Preserve
    # shared source images while separating the material for distinct slots.
    others = [other for scene in bpy.data.scenes if hasattr(scene, 'open_painter')
              for other in scene.open_painter.texture_sets
              if other.as_pointer() != ts.as_pointer() and material and other.material == material]
    if others:
        slots = {(ref.object.as_pointer(), ref.slot_index) for ref in ts.assignments if ref.object}
        if any((ref.object.as_pointer(), ref.slot_index) in slots
               for other in others for ref in other.assignments if ref.object):
            raise ValueError('Independent texture sets share an object material slot. Use separate objects or material slots.')
        previous = material
        material = material.copy()
        material['texture_set_id'] = ts.id
        ts.material = material
        # A scene copy shares the set id: its channel images and its texture set node's tree
        # are separated too, or two engines would write one image and one tree would show both.
        from .texture_set_node import TREE_PREFIX
        shared = {out.image.as_pointer() for other in others for out in other.outputs if out.image}
        for out in ts.outputs:
            if out.image is not None and out.image.as_pointer() in shared:
                out.image = None
        ts['op_node_tree'] = f'{TREE_PREFIX}{ts.id} · {ts.id_data.name}'
        for ref in ts.assignments:
            if ref.object and ref.slot_index < len(ref.object.material_slots):
                slot = ref.object.material_slots[ref.slot_index]
                if slot.material == previous:
                    slot.material = material
    created = False
    if not material or not material.get(OWNER):
        material = bpy.data.materials.new(f"OP · {ts.name}")
        material[OWNER] = True
        material["texture_set_id"] = ts.id
        ts.material = material
        created = True
    if not material.use_nodes:
        material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    if created:
        _adopt_defaults(nodes)
    relayout = _drop_legacy(nodes) or created

    def node(role, kind):
        found = next(
            (n for n in nodes if n.get(OWNER) and n.get("role") == role and n.bl_idname == kind),
            None,
        )
        if found is None:
            found = nodes.new(kind)
            found[OWNER], found["role"] = True, role
            found.location = LAYOUT[role]
        elif relayout and tuple(found.location) != LAYOUT[role]:
            found.location = LAYOUT[role]
        if found.label:
            found.label = ""   # the BSDF and output read as themselves; the set's node names its set
        return found

    def connect(source, target):
        if not any(link.from_socket == source for link in target.links):
            links.new(source, target)

    from ..evaluation import shader_preview
    from . import texture_set_node
    # The engine renders the set's images; the CPU reference writes the same images, or a
    # display (``image_overrides``) binds its own in the node's group.
    engine = image_overrides is None and shader_preview.requested(ts)
    if engine:
        for channel in CHANNELS:
            _ensure_output(ts, channel)
    with texture_set_node.creating():
        source = node("Texture Set", texture_set_node.NODE_ID)
    texture_set_node.bind(source, ts, overrides=image_overrides)
    bsdf = node("Surface", "ShaderNodeBsdfPrincipled")
    output = node("Output", "ShaderNodeOutputMaterial")
    _drop_untouched_defaults(nodes, {source.as_pointer(), bsdf.as_pointer(), output.as_pointer()})
    if not output.is_active_output:
        output.is_active_output = True
    for name, socket in SURFACE:
        connect(source.outputs[name], bsdf.inputs[socket])
    connect(bsdf.outputs["BSDF"], output.inputs["Surface"])
    _surface_values(ts, bsdf)
    for ref in ts.assignments:
        if ref.object and ref.slot_index < len(ref.object.material_slots):
            slot = ref.object.material_slots[ref.slot_index]
            if slot.material is None:
                slot.material = material
    from ..evaluation.shader_graph import remove_orphan_tops, remove_orphan_units
    remove_orphan_tops(ts.id)
    remove_orphan_units(ts.id)
    shader_preview.mark_attached(ts.id_data, ts, engine)
    return material
