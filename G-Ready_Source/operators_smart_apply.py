import bpy
from mathutils import Vector


# Ported from:
# C:\Users\Farid\Desktop\Texport\Texport\smart_transform_apply.py


def compensate_modifiers(obj, pre_matrix, post_matrix, props):
    delta = post_matrix.inverted() @ pre_matrix
    linear_delta = delta.to_3x3()
    rotation_delta = (
        post_matrix.to_quaternion().inverted()
        @ pre_matrix.to_quaternion()
    ).to_matrix()

    for mod in obj.modifiers:
        if mod.type == 'ARRAY':
            if mod.use_constant_offset:
                v = Vector(mod.constant_offset_displace)
                mod.constant_offset_displace = linear_delta @ v

            if mod.use_relative_offset:
                v = Vector(mod.relative_offset_displace)
                v = rotation_delta @ v
                mod.relative_offset_displace = v

            if mod.use_object_offset and mod.offset_object:
                off = mod.offset_object
                off.matrix_world = delta @ off.matrix_world

        if mod.type == 'MIRROR':
            axes = [mod.use_axis[0], mod.use_axis[1], mod.use_axis[2]]
            axis_vecs = [
                Vector((1, 0, 0)),
                Vector((0, 1, 0)),
                Vector((0, 0, 1)),
            ]

            new_axes = [False, False, False]

            for i, enabled in enumerate(axes):
                if enabled:
                    v = rotation_delta @ axis_vecs[i]
                    abs_v = [abs(v.x), abs(v.y), abs(v.z)]
                    idx = abs_v.index(max(abs_v))
                    new_axes[idx] = True

            mod.use_axis[0] = new_axes[0]
            mod.use_axis[1] = new_axes[1]
            mod.use_axis[2] = new_axes[2]


def build_apply_groups(selected_objects):
    mesh_objects = [obj for obj in selected_objects if obj and obj.type == 'MESH']

    data_groups = {}
    for obj in mesh_objects:
        key = obj.data if obj.data else obj
        data_groups.setdefault(key, []).append(obj)

    shared_groups = []
    single_groups = []

    for objs in data_groups.values():
        if len(objs) > 1:
            shared_groups.append(objs)
        else:
            single_groups.append(objs)

    return shared_groups + single_groups


class GREADY_OT_smart_apply_transforms(bpy.types.Operator):
    bl_idname = "gready.smart_apply_transforms"
    bl_label = "Smart Apply Transforms"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.gready_smart_apply_props
        selected = context.selected_objects
        mesh_selected = [obj for obj in selected if obj and obj.type == 'MESH']

        if not selected:
            self.report({'WARNING'}, "No objects selected")
            return {'CANCELLED'}

        if not mesh_selected:
            self.report({'WARNING'}, "No mesh objects selected")
            return {'CANCELLED'}

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        view_layer = context.view_layer
        original_active = view_layer.objects.active
        original_selection = list(selected)

        pre_matrices = {o: o.matrix_world.copy() for o in mesh_selected}

        for objs in build_apply_groups(mesh_selected):
            bpy.ops.object.select_all(action='DESELECT')

            for o in objs:
                o.select_set(True)

            view_layer.objects.active = objs[0]
            view_layer.update()

            try:
                bpy.ops.object.transform_apply(
                    location=props.apply_location,
                    rotation=props.apply_rotation,
                    scale=props.apply_scale,
                )
            except RuntimeError as exc:
                self.report({'WARNING'}, str(exc))
                continue

            view_layer.update()

            if props.compensate_modifiers:
                for o in objs:
                    compensate_modifiers(
                        o,
                        pre_matrices[o],
                        o.matrix_world.copy(),
                        props,
                    )

        bpy.ops.object.select_all(action='DESELECT')
        for obj in original_selection:
            obj.select_set(True)

        view_layer.objects.active = original_active

        self.report({'INFO'}, "Smart Apply finished")
        return {'FINISHED'}
