"""Owned evaluated bake scene; source objects, materials and UI stay intact."""
from contextlib import contextmanager
import bpy
import numpy as np
from ..constants import OWNER
from ..utils.logging import logger
from . import devices


class TopologyMismatch(ValueError):
    """A face selection was made on a different evaluation of the target than the stage holds."""


class BakeStage:
    def __init__(self, context, ts, groups, image, scratch, flush=False):
        self.context, self.ts, self.groups = context, ts, groups
        self.image, self.scratch = image, scratch
        # Whether the originals' edit meshes are flushed before staging: only a bake a user
        # action started (``render_geometry.flush_edit_meshes``), never the scheduler's.
        self.flush = flush
        self.scene = None
        self.world = None
        self.objects, self.meshes, self.materials = [], [], []
        self.node_groups = []   # the realise trees generated helpers are staged with
        self.copies = {}
        self.cages = {}
        # Occluder stand-ins for lows Cycles hides while helpers project onto them (``standin``).
        self.standins = {}

    def prepare(self):
        if self.context.scene != self.ts.id_data:
            raise ValueError('Bake context must belong to the texture set scene')
        self.scene = bpy.data.scenes.new('Open Painter Bake Staging')
        self.scene[OWNER] = True
        self.scene.render.engine = 'CYCLES'
        device, _ = devices.resolve(self.context.scene, self.ts.bake.device)
        self.scene.cycles.device = device
        logger.info('Bake staging device: %s', device)
        self.scene.cycles.samples = self.ts.bake.samples
        self.scene.cycles.use_adaptive_sampling = False
        self.scene.cycles.seed = 0
        # A scene created here has no World, so Cycles used its implicit AO
        # distance. An owned World makes that distance an explicit bake setting
        # (Ucupaint sets world.light_settings.distance the same way).
        self.world = bpy.data.worlds.new('Open Painter Bake World')
        self.world[OWNER] = True
        self.world.light_settings.distance = self.ts.bake.ao_distance
        self.scene.world = self.world
        originals = list(dict.fromkeys(obj for group in self.groups
                                      for obj in (group.low, *group.sources, group.cage) if obj))
        from .render_geometry import objects
        from .cages import topology_signature
        self.copies = objects(self.context, originals, self.scene, self.objects, self.meshes, self.node_groups,
                              flush=self.flush)
        for group in self.groups:
            target = self.copies[group.low]
            if group.cage and topology_signature(target.data) != topology_signature(self.copies[group.cage].data):
                raise ValueError(f'Custom cage topology must match the render-evaluated low-poly mesh {group.low.name}')
            assigned = {ref.slot_index for ref in self.ts.assignments if ref.object == group.low}
            if not target.data.uv_layers:
                raise ValueError(f'Evaluated target {group.low.name} has no UV map')
            if any(index < 0 or index >= len(target.material_slots) for index in assigned):
                raise ValueError(f'Evaluated target {group.low.name} lost an assigned material slot')
            if self.ts.udim_tile:
                from ..core.udim import source_uv, local_uv
                faces = [p.index for p in target.data.polygons if p.material_index in assigned]
                values = local_uv(target.data, self.ts, faces)
                uv = source_uv(target.data, self.ts)
                uv.data.foreach_set('uv', values.ravel())
                uv.active_render = True
                target.data.uv_layers.active = uv
            for index in range(len(target.material_slots)):
                material = bpy.data.materials.new('OP Bake Target')
                self.materials.append(material)
                material[OWNER] = True
                material.use_nodes = True
                node = material.node_tree.nodes.new('ShaderNodeTexImage')
                node.image = self.image if index in assigned else self.scratch
                material.node_tree.nodes.active = node
                target.data.materials[index] = material
        self.scene.view_layers[0].update()
        return self

    @staticmethod
    def self_baked(group, sources=None):
        """Whether the low bakes its own surface: no high-poly, or only generated helpers.

        Generated helpers (Bake Back) decorate a low that has no high-poly of its own; they
        are projected on top of the low's direct bake rather than standing in for it.
        """
        from ..geometry_effects.manager import TAG
        sources = group.sources if sources is None else sources
        return all(obj.get(TAG) == 'INSTANCE_HELPER' for obj in sources)

    def standin(self, group):
        """An unselected copy of the target that occludes while helpers project onto it.

        Cycles hides the active object from rays during a selected-to-active bake, so the
        occlusion a bolt or weld receives from the surface it sits on would be lost. The copy
        is never a projection source (no hit at the ray-distance boundary) and is shown only
        for that pass, so the target never self-occludes against it.
        """
        if group.low not in self.standins:
            target = self.copies[group.low]
            own = target.copy()
            own.data = target.data.copy()
            own.data.materials.clear()
            self.scene.collection.objects.link(own)
            self.objects.append(own)
            self.meshes.append(own.data)
            self.standins[group.low] = own
        return self.standins[group.low]

    def restrict(self, group, faces, polygons=None):
        """Route every face of the target copy outside ``faces`` to the scratch image.

        ``polygons`` is the face count the selection was made against; a copy
        with another count (the evaluation changed underneath) is refused
        rather than keeping faces the ids do not name.
        """
        target = self.copies[group.low]
        if polygons is not None and polygons != len(target.data.polygons):
            raise TopologyMismatch(f'{group.low.name} evaluates to {len(target.data.polygons)} faces, '
                                   f'the region was selected on {polygons}')
        material = bpy.data.materials.new('OP Bake Scratch')
        self.materials.append(material)
        material[OWNER] = True
        material.use_nodes = True
        node = material.node_tree.nodes.new('ShaderNodeTexImage')
        node.image = self.scratch
        material.node_tree.nodes.active = node
        target.data.materials.append(material)
        index = len(target.data.materials) - 1
        keep = np.zeros(len(target.data.polygons), bool)
        keep[list(faces)] = True
        current = np.empty(len(target.data.polygons), np.int32)
        target.data.polygons.foreach_get('material_index', current)
        target.data.polygons.foreach_set('material_index', np.where(keep, current, index).astype(np.int32))
        target.data.update()

    def add_cage(self, group, cage):
        """Adopt an owned corrected cage for a part (removed with the stage)."""
        self.scene.collection.objects.link(cage)
        self.objects.append(cage)
        self.meshes.append(cage.data)
        self.cages[group.low] = cage

    @contextmanager
    def target(self, group, kind, sources=None, ao_scope=None, project=None):
        """Select and show the objects of one bake pass of ``group``.

        ``project`` is whether this pass is selected-to-active (default: whenever there are
        sources); a direct pass selects the target alone. A projecting pass whose sources
        are only generated helpers also shows the target's occluder stand-in.
        """
        layer = self.scene.view_layers[0]
        target = self.copies[group.low]
        sources = group.sources if sources is None else sources
        project = bool(sources) if project is None else project
        selected = [target, *(self.copies[obj] for obj in sources)] if project else [target]
        visible = set(selected)
        if kind == 'AO' and project and sources and self.self_baked(group, sources):
            visible.add(self.standin(group))
        if kind == 'AO' and (ao_scope or self.ts.bake.ao_scope) == 'SET':
            # Assembly contact uses original positions and excludes unrelated scene objects.
            # High-poly parts occlude high-to-low bakes; a part baked from itself (no high-poly,
            # or helpers only) is its own low, with its helpers.
            for part in self.groups:
                visible.update(self.copies[obj] for obj in part.sources)
                if self.self_baked(part):
                    visible.add(self.copies[part.low])
        for obj in self.objects:
            obj.hide_render = obj not in visible
            obj.select_set(obj in selected, view_layer=layer)
        layer.objects.active = target
        layer.update()
        with self.context.temp_override(scene=self.scene, view_layer=layer,
                                        active_object=target, object=target,
                                        selected_objects=selected, selected_editable_objects=selected):
            yield target

    def close(self):
        for obj in reversed(self.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in reversed(self.meshes):
            bpy.data.meshes.remove(mesh)
        for group in reversed(self.node_groups):
            # The copies' modifiers were cleared once evaluated, so the trees are
            # orphans here; they would otherwise clutter the Geometry Nodes picker.
            try:
                bpy.data.node_groups.remove(group)
            except ReferenceError:
                pass
        for material in reversed(self.materials):
            bpy.data.materials.remove(material)
        if self.scene:
            bpy.data.scenes.remove(self.scene)
        if self.world:
            bpy.data.worlds.remove(self.world)


@contextmanager
def prepared(context, ts, groups, image, scratch, flush=False):
    stage = BakeStage(context, ts, groups, image, scratch, flush)
    try:
        yield stage.prepare()
    finally:
        stage.close()
