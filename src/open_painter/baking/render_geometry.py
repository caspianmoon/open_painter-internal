"""Evaluate owned copies with render modifier visibility and subdivision levels.

Blender exposes a viewport depsgraph to Python. Explicitly mirror render switches
on disposable copies before evaluating, rather than silently baking viewport LOD.
"""
import bpy
from contextlib import contextmanager


def flush_edit_meshes(objects):
    """Load every Edit Mode object's edit BMesh into its datablock (the artist's geometry).

    Edit Mode keeps the geometry in the edit BMesh; the datablock lags behind until the
    mode ends. Every reader here copies or evaluates the datablock — ``ID.copy`` and
    ``to_mesh``/``new_from_object`` with ``preserve_all_data_layers`` both bypass the edit
    mesh — so without the flush a bake started from Edit Mode (a region rebake, Bake Back)
    staged the mesh as it was before the edit while the doctor's checks read the edited one.
    Each bake stage passes its originals through ``objects`` below, which is why the flush
    is wired there (``flush=True``) rather than in one entry point.

    Only the paths a user action starts ask for it. ``update_from_editmode`` tags the mesh
    as changed, so a stage the scheduler runs from its timer — a custom-graph raster on
    the evaluation tick, a layer-cache job — would flush the whole mesh on every tick for
    as long as the artist stays in Edit Mode and fight the edit session; those stage the
    datablock as it is (``evaluation.mesh_masks`` reads its selection the same way).
    """
    for obj in dict.fromkeys(objects):
        if obj is not None and obj.mode == 'EDIT':
            obj.update_from_editmode()


@contextmanager
def scope(context, originals, required=None, flush=False):
    scene = bpy.data.scenes.new('OP Bake Geometry Check')
    owned_objects, owned_meshes, owned_groups = [], [], []
    try:
        yield objects(context, originals, scene, owned_objects, owned_meshes, owned_groups, required, flush)
    finally:
        for obj in reversed(owned_objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in reversed(owned_meshes):
            bpy.data.meshes.remove(mesh)
        for group in reversed(owned_groups):
            try:
                bpy.data.node_groups.remove(group)
            except ReferenceError:
                pass
        bpy.data.scenes.remove(scene)


def objects(context, originals, scene, owned_objects, owned_meshes, owned_groups=None, required=None, flush=False):
    from ..geometry.snapshots import depsgraph_for, evaluable
    owned_groups = [] if owned_groups is None else owned_groups
    originals = list(originals)
    if flush:
        # Before the depsgraph and the copies: both read the datablock, not the edit BMesh.
        # Operator-driven stages only (``flush_edit_meshes``); the scheduler's never ask.
        flush_edit_meshes(originals)
    # The same layer choice as the CPU reference (``snapshots.targets``): an original the
    # context's layer excludes would otherwise hand over its base transform and modifier
    # values, and the staged faces would not be the ones the rasters were made against.
    # ``required`` (the targets, when the caller knows them) must be evaluated; by default
    # every original some layer can evaluate is. The rest — a high-poly or a generated helper
    # disabled in viewports to keep the scene responsive — keep their base transform and
    # modifier values, and their copies are evaluated in the owned scene as before.
    graph = depsgraph_for(context.scene, evaluable(context.scene, originals) if required is None else required)
    scene.render.fps = context.scene.render.fps
    scene.render.fps_base = context.scene.render.fps_base
    scene.frame_set(context.scene.frame_current, subframe=context.scene.frame_subframe)
    scene.unit_settings.system = context.scene.unit_settings.system
    scene.unit_settings.scale_length = context.scene.unit_settings.scale_length
    copies = {}
    for original in originals:
        evaluated = original.evaluated_get(graph)
        obj = original.copy()
        owned_objects.append(obj)
        obj.data = original.data.copy()
        owned_meshes.append(obj.data)
        # Freeze animated parameters/transforms at this frame. Constraints and
        # drivers must not run a second time in a different scene context.
        obj.animation_data_clear()
        obj.parent = None
        obj.constraints.clear()
        obj.matrix_world = evaluated.matrix_world.copy()
        obj.hide_render = obj.hide_viewport = obj.hide_select = False
        for modifier in obj.modifiers:
            value = evaluated.modifiers.get(modifier.name)
            if value:
                for prop in modifier.bl_rna.properties:
                    if prop.is_readonly or prop.identifier in {'rna_type','name','type'}:
                        continue
                    if prop.type in {'BOOLEAN','INT','FLOAT','ENUM','STRING'}:
                        try:
                            setattr(modifier, prop.identifier, getattr(value, prop.identifier))
                        except (AttributeError, TypeError):
                            pass
                if modifier.type == 'NODES':
                    try:
                        items = {key: value[key] for key in value.keys()}
                    except (TypeError, AttributeError):
                        # Evaluated modifiers may not expose ID properties: copy the original's.
                        items = {}
                    for key, item in items.items():
                        if key != '_RNA_UI':
                            modifier[key] = item.original if isinstance(item, bpy.types.ID) else item
            modifier.show_viewport = modifier.show_render
            if modifier.type in {'SUBSURF','MULTIRES'}:
                modifier.levels = modifier.render_levels
        if original.get('open_painter_geometry_effect') == 'INSTANCE_HELPER':
            # Generated geometry is instances: realise them so Cycles bakes real triangles.
            realize = bpy.data.node_groups.new('OP Realize Instances', 'GeometryNodeTree')
            realize.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
            realize.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
            group_in, group_out = realize.nodes.new('NodeGroupInput'), realize.nodes.new('NodeGroupOutput')
            node = realize.nodes.new('GeometryNodeRealizeInstances')
            realize.links.new(group_in.outputs['Geometry'], node.inputs['Geometry'])
            realize.links.new(node.outputs['Geometry'], group_out.inputs['Geometry'])
            obj.modifiers.new('Open Painter Realize', 'NODES').node_group = realize
            owned_groups.append(realize)
        scene.collection.objects.link(obj)
        copies[original] = obj
    for obj in copies.values():
        for modifier in obj.modifiers:
            for prop in modifier.bl_rna.properties:
                if prop.type == 'POINTER' and not prop.is_readonly:
                    value = getattr(modifier, prop.identifier, None)
                    if isinstance(value, bpy.types.Object) and value in copies:
                        setattr(modifier, prop.identifier, copies[value])
    # The owned scene has the one view layer it was created with; every copy is linked to
    # its master collection and enabled, so ``depsgraph_for`` would pick the same layer.
    with context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
        graph = context.evaluated_depsgraph_get()
        frozen = []
        for obj in copies.values():
            evaluated = obj.evaluated_get(graph)
            mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=graph)
            owned_meshes.append(mesh)
            for i, slot in enumerate(evaluated.material_slots):
                if i < len(mesh.materials):
                    # Evaluated IDs belong to the depsgraph, not Main. Keeping
                    # one in a persistent mesh can crash the next graph update.
                    mesh.materials[i] = slot.material.original if slot.material else None
            frozen.append((obj, mesh, evaluated.matrix_world.copy()))
        # Evaluate every dependency before replacing any of its inputs.
        for obj, mesh, matrix in frozen:
            obj.modifiers.clear()
            obj.data = mesh
            # Effective OBJECT overrides were captured above. Staged targets
            # must resolve through their owned mesh slots, never artist nodes.
            for slot in obj.material_slots:
                slot.link = 'DATA'
            obj.matrix_world = matrix
    return copies
