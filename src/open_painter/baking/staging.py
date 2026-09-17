"""Owned evaluated bake scene; source objects, materials and UI stay intact."""
from contextlib import contextmanager
import bpy
import numpy as np
from ..constants import OWNER


class TopologyMismatch(ValueError):
    """A face selection was made on a different evaluation of the target than the stage holds."""


class BakeStage:
    def __init__(self, context, ts, groups, image, scratch):
        self.context, self.ts, self.groups = context, ts, groups
        self.image, self.scratch = image, scratch
        self.scene = None
        self.world = None
        self.objects, self.meshes, self.materials = [], [], []
        self.node_groups = []   # the realise trees generated helpers are staged with
        self.copies = {}
        self.cages = {}
        self.own_sources = {}

    def prepare(self):
        if self.context.scene != self.ts.id_data:
            raise ValueError('Bake context must belong to the texture set scene')
        self.scene = bpy.data.scenes.new('Open Painter Bake Staging')
        self.scene[OWNER] = True
        self.scene.render.engine = 'CYCLES'
        self.scene.cycles.device = self.context.scene.cycles.device if self.ts.bake.device == 'SCENE' else 'CPU'
        self.scene.cycles.samples = self.ts.bake.samples
        self.scene.cycles.use_adaptive_sampling = False
        self.scene.cycles.seed = 0
        # A scene created here has no World, so Cycles used its implicit AO
        # distance. An owned World makes that distance an explicit bake setting
        # (the node-stack add-on sets world.light_settings.distance the same way).
        self.world = bpy.data.worlds.new('Open Painter Bake World')
        self.world[OWNER] = True
        self.world.light_settings.distance = self.ts.bake.ao_distance
        self.scene.world = self.world
        originals = list(dict.fromkeys(obj for group in self.groups
                                      for obj in (group.low, *group.sources, group.cage) if obj))
        from .render_geometry import objects
        from .cages import topology_signature
        self.copies = objects(self.context, originals, self.scene, self.objects, self.meshes, self.node_groups)
        for group in self.groups:
            target = self.copies[group.low]
            if group.cage and topology_signature(target.data) != topology_signature(self.copies[group.cage].data):
                raise ValueError(f'Custom cage topology must match the render-evaluated low-poly mesh {group.low.name}')
            assigned = {ref.slot_index for ref in self.ts.assignments if ref.object == group.low}
            if not target.data.uv_layers:
                raise ValueError(f'Evaluated target {group.low.name} has no UV map')
            if any(index < 0 or index >= len(target.material_slots) for index in assigned):
                raise ValueError(f'Evaluated target {group.low.name} lost an assigned material slot')
            for index in range(len(target.material_slots)):
                material = bpy.data.materials.new('OP Bake Target')
                self.materials.append(material)
                material[OWNER] = True
                material.use_nodes = True
                node = material.node_tree.nodes.new('ShaderNodeTexImage')
                node.image = self.image if index in assigned else self.scratch
                material.node_tree.nodes.active = node
                target.data.materials[index] = material
        from ..geometry_effects.manager import TAG
        for group in self.groups:
            if group.sources and all(obj.get(TAG) == 'INSTANCE_HELPER' for obj in group.sources):
                # Generated helpers decorate a low that has no high-poly of its own (Bake Back on a
                # set that baked from itself): the low is the active target, never a hit, so every
                # texel outside a helper would keep the neutral default and the low's own
                # occlusion would be lost. A copy of the target is projected alongside them.
                target = self.copies[group.low]
                own = target.copy()
                own.data = target.data.copy()
                own.data.materials.clear()
                self.scene.collection.objects.link(own)
                self.objects.append(own)
                self.meshes.append(own.data)
                self.own_sources[group.low] = own
        self.scene.view_layers[0].update()
        return self

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
    def target(self, group, kind, sources=None, ao_scope=None):
        layer = self.scene.view_layers[0]
        target = self.copies[group.low]
        sources = [self.copies[obj] for obj in (group.sources if sources is None else sources)]
        if group.low in self.own_sources:
            sources.append(self.own_sources[group.low])
        selected = [target, *sources]
        visible = set(selected)
        if kind == 'AO' and (ao_scope or self.ts.bake.ao_scope) == 'SET':
            # Assembly contact uses original positions and excludes unrelated
            # scene objects. High-poly parts occlude high-to-low bakes.
            visible.update(self.copies[obj] for part in self.groups
                           for obj in (part.sources if self.ts.bake.selected_to_active else (part.low,)))
            visible.update(self.own_sources.values())
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
def prepared(context, ts, groups, image, scratch):
    stage = BakeStage(context, ts, groups, image, scratch)
    try:
        yield stage.prepare()
    finally:
        stage.close()
