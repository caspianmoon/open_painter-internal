# --- START OF FILE operators_bevel_shader.py ---

# bakeaddon/operators_bevel_shader.py

import random

import bmesh
import bpy
from bpy.props import EnumProperty, FloatProperty, IntProperty
from bpy.types import Operator

from .properties import BEVEL_UNIT_ITEMS


ADDON_LABEL = "Bake Addon 2.1"

RADIUS_ATTR = "BakeAddon_BevelRadius"
USER_ATTR = "BakeAddon_BevelUserDefined"
OBJ_RADIUS_PROP = "BakeAddon_IntactBevelRadius"
OBJ_DISPLAY_UNITS_PROP = "BakeAddon_IntactBevelDisplayUnits"
OBJ_DISPLAY_RADIUS_PROP = "BakeAddon_IntactBevelDisplayRadius"
OBJ_UNITS_MULT_PROP = "BakeAddon_NodeUnitsMult"

NORMAL_NODE_NAME = "BakeAddon_BevelNormal"
NORMAL_NODE_TREE = "BakeAddon_BevelNormalNodeTree"
PREVIEW_NODE_NAME = "BakeAddon_BevelPreview"
PREVIEW_NODE_TREE = "BakeAddon_BevelPreviewNodeTree"
DEFAULT_MATERIAL = "BakeAddon_DefaultBevelMaterial"
LINEAR_NODE_TREE = "BakeAddon_FromSRGBToLinear"

NODE_TREE_VERSION = "1.3"
PREVIEW_NODE_TREE_VERSION = "1.1"
DEFAULT_MATERIAL_COLOR = (196.0 / 255.0, 196.0 / 255.0, 196.0 / 255.0, 1.0)
MEDIUM_BEVEL_NODE_SAMPLES = 16




def _safe_link(node_tree, from_socket, to_socket):
    if from_socket is None or to_socket is None:
        return None
    try:
        return node_tree.links.new(from_socket, to_socket)
    except TypeError:
        return node_tree.links.new(to_socket, from_socket)


def _first_socket(sockets, name):
    if name in sockets:
        return sockets[name]
    for socket in sockets:
        if socket.name == name:
            return socket
    return sockets[0] if sockets else None


def _socket_by_names(sockets, names, fallback_index=None, socket_type=None):
    normalized_names = {name.lower().replace(" ", "_") for name in names}
    for name in names:
        if name in sockets:
            return sockets[name]
    for socket in sockets:
        if socket_type and getattr(socket, "type", None) != socket_type:
            continue
        socket_names = {
            str(getattr(socket, "name", "")).lower().replace(" ", "_"),
            str(getattr(socket, "identifier", "")).lower().replace(" ", "_"),
        }
        if socket_names & normalized_names:
            return socket
    if socket_type:
        for socket in sockets:
            if getattr(socket, "type", None) == socket_type:
                return socket
    if fallback_index is not None and len(sockets) > fallback_index:
        return sockets[fallback_index]
    return sockets[0] if sockets else None


def _attribute_factor_socket(attribute_node):
    return _socket_by_names(attribute_node.outputs, ("Fac", "Factor", "Value", "Float"), fallback_index=2, socket_type='VALUE')


def _mix_factor_socket(mix_node):
    return _socket_by_names(mix_node.inputs, ("Factor", "Factor_Float"), fallback_index=0)


def _mix_a_socket(mix_node):
    return _socket_by_names(mix_node.inputs, ("A", "A_Float"), fallback_index=2)


def _mix_b_socket(mix_node):
    return _socket_by_names(mix_node.inputs, ("B", "B_Float"), fallback_index=3)


def _mix_result_socket(mix_node):
    return _socket_by_names(mix_node.outputs, ("Result", "Result_Float"), fallback_index=0)


def _clear_node_tree(node_group):
    for node in list(node_group.nodes):
        node_group.nodes.remove(node)

    if hasattr(node_group, "interface"):
        for item in reversed(list(node_group.interface.items_tree)):
            try:
                node_group.interface.remove(item)
            except Exception:
                pass
        return

    for sockets in (getattr(node_group, "inputs", None), getattr(node_group, "outputs", None)):
        if not sockets:
            continue
        for socket in reversed(list(sockets)):
            try:
                sockets.remove(socket)
            except Exception:
                pass


def _add_group_socket(node_group, name, socket_type, in_out):
    if hasattr(node_group, "interface"):
        try:
            return node_group.interface.new_socket(
                name=name,
                socket_type=socket_type,
                in_out=in_out,
            )
        except Exception:
            pass

    sockets = node_group.inputs if in_out == 'INPUT' else node_group.outputs
    return sockets.new(socket_type, name)


def _unit_multiplier(units):
    if units == 'UNITS':
        return 1.0

    base = {
        'MILLIMETERS': 0.001,
        'CENTIMETERS': 0.01,
        'METERS': 1.0,
        'INCHES': 0.0254,
        'FEET': 0.3048,
    }.get(units, 1.0)

    scale_length = getattr(bpy.context.scene.unit_settings, "scale_length", 1.0) or 1.0
    return base / scale_length


def display_radius_to_scene(radius, units):
    return max(radius, 0.0) * _unit_multiplier(units)


def scene_radius_to_display(radius, units):
    multiplier = _unit_multiplier(units)
    if multiplier <= 0.0:
        return 0.0
    return max(radius, 0.0) / multiplier


def _object_display_radius_set(self, value):
    scene_radius = display_radius_to_scene(value, getattr(self, OBJ_DISPLAY_UNITS_PROP, "CENTIMETERS"))
    self[OBJ_RADIUS_PROP] = scene_radius


def _object_display_radius_get(self):
    return scene_radius_to_display(self.get(OBJ_RADIUS_PROP, 0.0), getattr(self, OBJ_DISPLAY_UNITS_PROP, "CENTIMETERS"))


def _object_units_multiplier_get(self):
    return _unit_multiplier(getattr(self, OBJ_DISPLAY_UNITS_PROP, "CENTIMETERS"))


def register_object_properties():
    if not hasattr(bpy.types.Object, OBJ_DISPLAY_UNITS_PROP):
        setattr(
            bpy.types.Object,
            OBJ_DISPLAY_UNITS_PROP,
            EnumProperty(
                name="Display Units",
                description="Units used to display this object's Bevel Shader radius",
                items=BEVEL_UNIT_ITEMS,
                default='CENTIMETERS',
            ),
        )

    if not hasattr(bpy.types.Object, OBJ_DISPLAY_RADIUS_PROP):
        setattr(
            bpy.types.Object,
            OBJ_DISPLAY_RADIUS_PROP,
            FloatProperty(
                name="Display Radius",
                description="Object-wide Bevel Shader radius in the chosen display units",
                min=0.0,
                precision=4,
                get=_object_display_radius_get,
                set=_object_display_radius_set,
            ),
        )

    if not hasattr(bpy.types.Object, OBJ_UNITS_MULT_PROP):
        setattr(
            bpy.types.Object,
            OBJ_UNITS_MULT_PROP,
            FloatProperty(
                name="Units Multiplier",
                description="Internal multiplier used by the Bevel Shader real-time modifier",
                get=_object_units_multiplier_get,
                options={'HIDDEN'},
            ),
        )


def unregister_object_properties():
    for prop_name in (OBJ_UNITS_MULT_PROP, OBJ_DISPLAY_RADIUS_PROP, OBJ_DISPLAY_UNITS_PROP):
        if hasattr(bpy.types.Object, prop_name):
            try:
                delattr(bpy.types.Object, prop_name)
            except Exception as exc:
                print(f"{ADDON_LABEL} Warning: Could not remove object property {prop_name}: {exc}")


def mesh_objects_from_context(context):
    if context.mode == 'EDIT_MESH':
        return [obj for obj in getattr(context, "objects_in_mode", []) if obj and obj.type == 'MESH']
    return [obj for obj in context.selected_objects if obj and obj.type == 'MESH']


def object_is_convenient(obj):
    return bool(obj and obj.type == 'MESH' and obj.data)


def _ensure_float_attribute(mesh, name, default_value=0.0):
    attr = mesh.attributes.get(name)
    if attr and (attr.data_type != 'FLOAT' or attr.domain != 'POINT'):
        mesh.attributes.remove(attr)
        attr = None

    if attr is None:
        attr = mesh.attributes.new(name=name, type='FLOAT', domain='POINT')
        for item in attr.data:
            item.value = default_value
    return attr


def _ensure_bmesh_float_layer(bm, name):
    layer = bm.verts.layers.float.get(name)
    if layer is None:
        layer = bm.verts.layers.float.new(name)
    return layer


def _ensure_color_attribute(mesh, name):
    attr = mesh.attributes.get(name)
    if attr and attr.data_type != 'FLOAT_COLOR':
        mesh.attributes.remove(attr)
        attr = None

    if attr is None:
        attr = mesh.attributes.new(name=name, type='FLOAT_COLOR', domain='CORNER')
        for item in attr.data:
            item.color = (0.0, 0.0, 0.0, 1.0)
    return attr


def _set_attr_values(attr, value):
    for item in attr.data:
        item.value = value


def _radius_matches(value, target):
    return abs(float(value) - float(target)) <= max(0.000001, abs(float(target)) * 0.00001)


def _selected_bmesh_vertices(bm):
    verts = set(v for v in bm.verts if v.select)
    for edge in bm.edges:
        if edge.select:
            verts.update(edge.verts)
    for face in bm.faces:
        if face.select:
            verts.update(face.verts)
    return verts


def build_normal_node_tree(node_group):
    node_group.name = NORMAL_NODE_TREE

    _add_group_socket(node_group, "Normal", 'NodeSocketVector', 'INPUT')
    _add_group_socket(node_group, "Normal", 'NodeSocketVector', 'OUTPUT')

    nodes = node_group.nodes
    links = node_group.links

    input_node = nodes.new('NodeGroupInput')
    input_node.location = (-460, -120)

    output_node = nodes.new('NodeGroupOutput')
    output_node.location = (460, 0)
    output_node.name = "ver: " + NODE_TREE_VERSION

    user_attr = nodes.new('ShaderNodeAttribute')
    user_attr.attribute_name = USER_ATTR
    user_attr.location = (-440, 260)

    object_radius_attr = nodes.new('ShaderNodeAttribute')
    object_radius_attr.attribute_type = 'OBJECT'
    object_radius_attr.attribute_name = OBJ_RADIUS_PROP
    object_radius_attr.location = (-440, 80)

    vertex_radius_attr = nodes.new('ShaderNodeAttribute')
    vertex_radius_attr.attribute_name = RADIUS_ATTR
    vertex_radius_attr.location = (-440, -100)

    mix_node = nodes.new('ShaderNodeMix')
    mix_node.location = (-110, 90)
    if hasattr(mix_node, "data_type"):
        mix_node.data_type = 'FLOAT'
    if hasattr(mix_node, "factor_mode"):
        mix_node.factor_mode = 'UNIFORM'

    bevel_node = nodes.new('ShaderNodeBevel')
    bevel_node.samples = MEDIUM_BEVEL_NODE_SAMPLES
    bevel_node.location = (170, 0)

    _safe_link(node_group, _attribute_factor_socket(user_attr), _mix_factor_socket(mix_node))
    _safe_link(node_group, _attribute_factor_socket(object_radius_attr), _mix_a_socket(mix_node))
    _safe_link(node_group, _attribute_factor_socket(vertex_radius_attr), _mix_b_socket(mix_node))
    _safe_link(node_group, _mix_result_socket(mix_node), _first_socket(bevel_node.inputs, "Radius"))
    _safe_link(node_group, bevel_node.outputs[0], output_node.inputs[0])

    return node_group


def create_normal_node_tree():
    node_group = bpy.data.node_groups.new(NORMAL_NODE_TREE, 'ShaderNodeTree')
    return build_normal_node_tree(node_group)


def build_preview_node_tree(node_group):
    node_group.name = PREVIEW_NODE_TREE

    _add_group_socket(node_group, "Surface", 'NodeSocketShader', 'INPUT')
    _add_group_socket(node_group, "Surface", 'NodeSocketShader', 'OUTPUT')

    nodes = node_group.nodes
    links = node_group.links

    input_node = nodes.new('NodeGroupInput')
    input_node.location = (-220, -10)

    shader_node = nodes.new('ShaderNodeBsdfPrincipled')
    shader_node.location = (120, 0)
    if "Base Color" in shader_node.inputs:
        shader_node.inputs["Base Color"].default_value = DEFAULT_MATERIAL_COLOR
    if "Metallic" in shader_node.inputs:
        shader_node.inputs["Metallic"].default_value = 1.0
    if "Roughness" in shader_node.inputs:
        shader_node.inputs["Roughness"].default_value = 0.35

    output_node = nodes.new('NodeGroupOutput')
    output_node.location = (450, 0)
    output_node.name = "ver: " + PREVIEW_NODE_TREE_VERSION

    normal_group = nodes.new('ShaderNodeGroup')
    normal_group.name = NORMAL_NODE_NAME
    normal_group.node_tree = get_or_create_node_tree(NORMAL_NODE_TREE)
    normal_group.location = (-70, -300)

    _safe_link(node_group, _first_socket(normal_group.outputs, "Normal"), _first_socket(shader_node.inputs, "Normal"))
    _safe_link(node_group, _first_socket(shader_node.outputs, "BSDF"), _first_socket(output_node.inputs, "Surface"))

    return node_group


def create_preview_node_tree():
    node_group = bpy.data.node_groups.new(PREVIEW_NODE_TREE, 'ShaderNodeTree')
    return build_preview_node_tree(node_group)


def create_srgb_to_linear_node_tree():
    node_group = bpy.data.node_groups.new(LINEAR_NODE_TREE, 'GeometryNodeTree')
    node_group.name = LINEAR_NODE_TREE

    _add_group_socket(node_group, "Float", 'NodeSocketFloat', 'INPUT')
    _add_group_socket(node_group, "Float", 'NodeSocketFloat', 'OUTPUT')

    nodes = node_group.nodes

    input_node = nodes.new('NodeGroupInput')
    input_node.location = (-760, 0)

    output_node = nodes.new('NodeGroupOutput')
    output_node.location = (420, 0)

    threshold = nodes.new('FunctionNodeCompare')
    threshold.name = "Threshold"
    threshold.data_type = 'FLOAT'
    threshold.operation = 'LESS_THAN'
    threshold.location = (-540, 150)
    threshold.inputs[1].default_value = 0.04045

    add_node = nodes.new('ShaderNodeMath')
    add_node.name = "Offset"
    add_node.operation = 'ADD'
    add_node.inputs[1].default_value = 0.055
    add_node.location = (-540, 10)

    divide_high = nodes.new('ShaderNodeMath')
    divide_high.name = "Normalize"
    divide_high.operation = 'DIVIDE'
    divide_high.inputs[1].default_value = 1.055
    divide_high.location = (-340, 10)

    power_node = nodes.new('ShaderNodeMath')
    power_node.name = "Gamma"
    power_node.operation = 'POWER'
    power_node.inputs[1].default_value = 2.4
    power_node.location = (-140, 10)

    divide_low = nodes.new('ShaderNodeMath')
    divide_low.name = "Linear Low"
    divide_low.operation = 'DIVIDE'
    divide_low.inputs[1].default_value = 12.92
    divide_low.location = (-140, -160)

    mix_node = nodes.new('ShaderNodeMix')
    mix_node.name = "Mix"
    mix_node.data_type = 'FLOAT'
    mix_node.factor_mode = 'UNIFORM'
    mix_node.location = (160, 0)

    _safe_link(node_group, input_node.outputs[0], threshold.inputs[0])
    _safe_link(node_group, input_node.outputs[0], add_node.inputs[0])
    _safe_link(node_group, add_node.outputs[0], divide_high.inputs[0])
    _safe_link(node_group, divide_high.outputs[0], power_node.inputs[0])
    _safe_link(node_group, input_node.outputs[0], divide_low.inputs[0])
    _safe_link(node_group, threshold.outputs[0], mix_node.inputs[0])
    _safe_link(node_group, power_node.outputs[0], mix_node.inputs[2])
    _safe_link(node_group, divide_low.outputs[0], mix_node.inputs[3])
    _safe_link(node_group, mix_node.outputs[0], output_node.inputs[0])

    return node_group


def get_or_create_linear_node_tree():
    node_tree = bpy.data.node_groups.get(LINEAR_NODE_TREE)
    if node_tree:
        return node_tree
    return create_srgb_to_linear_node_tree()


def create_realtime_node_tree():
    raise NotImplementedError("Real-Time Boolean functionality has been removed from this addon.")


def get_or_create_node_tree(node_tree_name):
    node_tree = bpy.data.node_groups.get(node_tree_name)
    if node_tree:
        if node_tree_name == NORMAL_NODE_TREE and node_tree_normal_get_version(node_tree) != NODE_TREE_VERSION:
            _clear_node_tree(node_tree)
            return build_normal_node_tree(node_tree)
        if node_tree_name == PREVIEW_NODE_TREE and node_tree_get_version(node_tree) != PREVIEW_NODE_TREE_VERSION:
            _clear_node_tree(node_tree)
            return build_preview_node_tree(node_tree)
        return node_tree

    if node_tree_name == NORMAL_NODE_TREE:
        return create_normal_node_tree()
    if node_tree_name == PREVIEW_NODE_TREE:
        return create_preview_node_tree()

    raise ValueError(f"Unknown {ADDON_LABEL} node tree: {node_tree_name}")


def node_tree_get_version(node_tree):
    if not node_tree:
        return ""
    for node in node_tree.nodes:
        if getattr(node, "type", "") == 'GROUP_OUTPUT' and node.name.startswith("ver: "):
            return node.name.split("ver: ", 1)[1]
    return ""


def node_tree_normal_get_version(node_tree=None):
    node_tree = node_tree or bpy.data.node_groups.get(NORMAL_NODE_TREE)
    return node_tree_get_version(node_tree)


def node_tree_normal_set_samples(samples):
    node_tree = get_or_create_node_tree(NORMAL_NODE_TREE)
    for node in node_tree.nodes:
        if node.type == 'BEVEL':
            node.samples = samples


def node_tree_normal_get_samples():
    node_tree = bpy.data.node_groups.get(NORMAL_NODE_TREE)
    if not node_tree:
        return MEDIUM_BEVEL_NODE_SAMPLES
    for node in node_tree.nodes:
        if node.type == 'BEVEL':
            return node.samples
    return MEDIUM_BEVEL_NODE_SAMPLES


def _ensure_principled_material_tree(mat):
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    output = next((node for node in nodes if node.type == 'OUTPUT_MATERIAL'), None)
    shader = next((node for node in nodes if node.type == 'BSDF_PRINCIPLED'), None)

    if output is None:
        output = nodes.new('ShaderNodeOutputMaterial')
        output.location = (300, 0)

    if shader is None:
        shader = nodes.new('ShaderNodeBsdfPrincipled')
        shader.location = (0, 0)
        if "Base Color" in shader.inputs:
            shader.inputs["Base Color"].default_value = DEFAULT_MATERIAL_COLOR
        if "Metallic" in shader.inputs:
            shader.inputs["Metallic"].default_value = 1.0
        if "Surface" in output.inputs and not output.inputs["Surface"].links:
            _safe_link(mat.node_tree, shader.outputs["BSDF"], output.inputs["Surface"])

    return output, shader


def create_default_material(mat):
    mat.diffuse_color = DEFAULT_MATERIAL_COLOR
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    for node in list(nodes):
        nodes.remove(node)

    output_node = nodes.new('ShaderNodeOutputMaterial')
    output_node.location = (300, 200)

    preview_node = nodes.new('ShaderNodeGroup')
    preview_node.name = PREVIEW_NODE_NAME
    preview_node.node_tree = get_or_create_node_tree(PREVIEW_NODE_TREE)
    preview_node.location = (60, 200)

    _safe_link(mat.node_tree, _first_socket(preview_node.outputs, "Surface"), output_node.inputs["Surface"])


def default_material_needs_rebuild(mat):
    if not mat or not mat.use_nodes or not mat.node_tree:
        return True
    return not material_get_group_nodes(mat, PREVIEW_NODE_TREE, 'OVERRIDE')


def get_or_create_default_material():
    mat = bpy.data.materials.get(DEFAULT_MATERIAL)
    if mat is None:
        mat = bpy.data.materials.new(DEFAULT_MATERIAL)
        create_default_material(mat)
    elif default_material_needs_rebuild(mat):
        create_default_material(mat)
    else:
        mat.diffuse_color = DEFAULT_MATERIAL_COLOR
    return mat


def object_has_materials_filled(obj):
    return bool(obj.data.materials) and all(slot.material for slot in obj.material_slots)


def object_fill_missing_materials(obj):
    mat = get_or_create_default_material()
    if len(obj.material_slots) == 0:
        obj.data.materials.append(mat)
        return

    for slot in obj.material_slots:
        if slot.material is None:
            slot.material = mat


def material_get_group_nodes(mat, node_tree_name, usage_check='ANY'):
    if not mat or not mat.use_nodes or not mat.node_tree:
        return []

    nodes = []
    for node in mat.node_tree.nodes:
        if node.type == 'GROUP' and node.node_tree and node.node_tree.name == node_tree_name:
            if usage_check == 'OVERRIDE':
                if any(link.to_node.type == 'OUTPUT_MATERIAL' for output in node.outputs for link in output.links):
                    nodes.append(node)
            else:
                nodes.append(node)
    return nodes


def material_has_normal_nodes(mat):
    return material_get_group_nodes(mat, NORMAL_NODE_TREE, 'ANY')


def material_add_normal_node(mat):
    if material_has_normal_nodes(mat):
        return

    output, shader = _ensure_principled_material_tree(mat)
    links = mat.node_tree.links
    nodes = mat.node_tree.nodes

    normal_inputs = []
    for node in nodes:
        has_shader_output = any(socket.type == 'SHADER' for socket in node.outputs)
        if has_shader_output and "Normal" in node.inputs:
            normal_inputs.append(node.inputs["Normal"])

    for normal_input in normal_inputs:
        original_from = normal_input.links[0].from_socket if normal_input.links else None
        if normal_input.links:
            links.remove(normal_input.links[0])

        normal_node = nodes.new('ShaderNodeGroup')
        normal_node.name = NORMAL_NODE_NAME
        normal_node.node_tree = get_or_create_node_tree(NORMAL_NODE_TREE)
        normal_node.location = (normal_input.node.location.x - 220, normal_input.node.location.y - 40)

        _safe_link(mat.node_tree, _first_socket(normal_node.outputs, "Normal"), normal_input)
        if original_from is not None:
            _safe_link(mat.node_tree, original_from, _first_socket(normal_node.inputs, "Normal"))

    if "Surface" in output.inputs and not output.inputs["Surface"].links and "BSDF" in shader.outputs:
        _safe_link(mat.node_tree, shader.outputs["BSDF"], output.inputs["Surface"])


def material_remove_normal_node(mat):
    if not mat or not mat.use_nodes or not mat.node_tree:
        return

    links = mat.node_tree.links
    nodes = mat.node_tree.nodes
    for node in list(material_has_normal_nodes(mat)):
        original_from = None
        if "Normal" in node.inputs and node.inputs["Normal"].links:
            original_from = node.inputs["Normal"].links[0].from_socket

        output_socket = _first_socket(node.outputs, "Normal")
        target_sockets = [link.to_socket for link in output_socket.links] if output_socket else []
        nodes.remove(node)

        if original_from:
            for target_socket in target_sockets:
                _safe_link(mat.node_tree, original_from, target_socket)


def material_add_preview_node(mat):
    if not mat:
        return
    if mat.name == DEFAULT_MATERIAL:
        return

    _ensure_principled_material_tree(mat)
    if material_get_group_nodes(mat, PREVIEW_NODE_TREE, 'OVERRIDE'):
        return

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    preview_tree = get_or_create_node_tree(PREVIEW_NODE_TREE)

    for output_node in [node for node in nodes if node.type == 'OUTPUT_MATERIAL']:
        surface_input = output_node.inputs.get("Surface")
        if surface_input is None:
            continue

        original_from = surface_input.links[0].from_socket if surface_input.links else None
        if surface_input.links:
            links.remove(surface_input.links[0])

        preview_node = nodes.new('ShaderNodeGroup')
        preview_node.name = PREVIEW_NODE_NAME
        preview_node.node_tree = preview_tree
        preview_node.location = (output_node.location.x - 180, output_node.location.y + 130)

        if original_from is not None:
            _safe_link(mat.node_tree, original_from, _first_socket(preview_node.inputs, "Surface"))
        _safe_link(mat.node_tree, _first_socket(preview_node.outputs, "Surface"), surface_input)


def material_remove_preview_node(mat):
    if not mat or not mat.use_nodes or not mat.node_tree:
        return
    if mat.name == DEFAULT_MATERIAL:
        return

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    preview_nodes = list(material_get_group_nodes(mat, PREVIEW_NODE_TREE, 'ANY'))

    for node in preview_nodes:
        original_from = None
        if "Surface" in node.inputs and node.inputs["Surface"].links:
            original_from = node.inputs["Surface"].links[0].from_socket

        output_socket = _first_socket(node.outputs, "Surface")
        target_sockets = [link.to_socket for link in output_socket.links] if output_socket else []
        nodes.remove(node)

        if original_from:
            for target_socket in target_sockets:
                _safe_link(mat.node_tree, original_from, target_socket)


def object_has_normal_nodes(obj):
    if not object_has_materials_filled(obj):
        return False
    return all(mat.name == DEFAULT_MATERIAL or material_has_normal_nodes(mat) for mat in obj.data.materials)


def object_add_normal_nodes(obj):
    for mat in obj.data.materials:
        material_add_normal_node(mat)


def object_add_preview_nodes(obj):
    for mat in obj.data.materials:
        material_add_preview_node(mat)


def object_remove_preview_nodes(obj):
    for mat in obj.data.materials:
        material_remove_preview_node(mat)


def ensure_object_ready(obj, preview_when_new=True):
    result = {'success': False, 'data_layer_was_added': False}
    if not object_is_convenient(obj):
        return result

    if obj.data.is_editmode:
        bm = bmesh.from_edit_mesh(obj.data)
        if bm.verts.layers.float.get(RADIUS_ATTR) is None:
            _ensure_bmesh_float_layer(bm, RADIUS_ATTR)
            result['data_layer_was_added'] = True
        _ensure_bmesh_float_layer(bm, USER_ATTR)
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    else:
        if RADIUS_ATTR not in obj.data.attributes:
            result['data_layer_was_added'] = True
        _ensure_float_attribute(obj.data, RADIUS_ATTR, obj.get(OBJ_RADIUS_PROP, 0.0))
        _ensure_float_attribute(obj.data, USER_ATTR, 0.0)

    if OBJ_RADIUS_PROP not in obj:
        obj[OBJ_RADIUS_PROP] = 0.0

    object_fill_missing_materials(obj)
    if not object_has_normal_nodes(obj):
        object_add_normal_nodes(obj)

    if preview_when_new and result['data_layer_was_added']:
        object_add_preview_nodes(obj)

    result['success'] = True
    return result


def set_radius_to_objects(objects, radius_scene_value, units='CENTIMETERS'):
    changed = 0
    for obj in objects:
        if not object_is_convenient(obj):
            continue

        ready = ensure_object_ready(obj, preview_when_new=False)
        if not ready['success']:
            continue

        setattr(obj, OBJ_DISPLAY_UNITS_PROP, units)
        obj[OBJ_RADIUS_PROP] = radius_scene_value

        if obj.data.is_editmode:
            bm = bmesh.from_edit_mesh(obj.data)
            radius_layer = _ensure_bmesh_float_layer(bm, RADIUS_ATTR)
            user_layer = _ensure_bmesh_float_layer(bm, USER_ATTR)
            for vert in bm.verts:
                vert[radius_layer] = radius_scene_value
                vert[user_layer] = 1.0
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        else:
            radius_attr = _ensure_float_attribute(obj.data, RADIUS_ATTR, radius_scene_value)
            user_attr = _ensure_float_attribute(obj.data, USER_ATTR, 1.0)
            _set_attr_values(radius_attr, radius_scene_value)
            _set_attr_values(user_attr, 1.0)
            obj.data.update()

        changed += 1
    return changed


def set_radius_to_selected_geometry(objects, radius_scene_value, units='CENTIMETERS'):
    changed = 0
    for obj in objects:
        if not object_is_convenient(obj) or not obj.data.is_editmode:
            continue

        ready = ensure_object_ready(obj, preview_when_new=False)
        if not ready['success']:
            continue

        setattr(obj, OBJ_DISPLAY_UNITS_PROP, units)
        obj[OBJ_RADIUS_PROP] = radius_scene_value

        bm = bmesh.from_edit_mesh(obj.data)
        radius_layer = _ensure_bmesh_float_layer(bm, RADIUS_ATTR)
        user_layer = _ensure_bmesh_float_layer(bm, USER_ATTR)
        selected_verts = _selected_bmesh_vertices(bm)

        if not selected_verts:
            continue

        for vert in selected_verts:
            vert[radius_layer] = radius_scene_value
            vert[user_layer] = 1.0

        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        changed += 1
    return changed


def set_radius_for_context(context, radius, units='CENTIMETERS'):
    radius_scene_value = display_radius_to_scene(radius, units)
    objects = mesh_objects_from_context(context)
    if context.mode == 'EDIT_MESH':
        changed = set_radius_to_selected_geometry(objects, radius_scene_value, units)
        if changed == 0:
            changed = set_radius_to_objects(objects, radius_scene_value, units)
    else:
        changed = set_radius_to_objects(objects, radius_scene_value, units)
    update_scene_max_bevel(context.scene)
    return changed


def set_zero_radius_for_context(context):
    return set_radius_for_context(context, 0.0, 'UNITS')


def cleanup_object(obj, remove_material_slots=False):
    if not object_is_convenient(obj):
        return False

    if OBJ_RADIUS_PROP in obj:
        del obj[OBJ_RADIUS_PROP]

    for attr_name in (RADIUS_ATTR, USER_ATTR):
        attr = obj.data.attributes.get(attr_name)
        if attr:
            obj.data.attributes.remove(attr)

    for mat in list(obj.data.materials):
        material_remove_preview_node(mat)
        material_remove_normal_node(mat)

    if remove_material_slots:
        for index in range(len(obj.material_slots) - 1, -1, -1):
            if obj.material_slots[index].material and obj.material_slots[index].material.name == DEFAULT_MATERIAL:
                obj.active_material_index = index
                bpy.ops.object.material_slot_remove()

    obj.data.update()
    return True


def cleanup_scene_data(remove_node_groups=True):
    for obj in bpy.data.objects:
        if object_is_convenient(obj):
            cleanup_object(obj)

    mat = bpy.data.materials.get(DEFAULT_MATERIAL)
    if mat:
        bpy.data.materials.remove(mat)

    if remove_node_groups:
        for node_tree_name in (NORMAL_NODE_TREE, PREVIEW_NODE_TREE, LINEAR_NODE_TREE):
            node_tree = bpy.data.node_groups.get(node_tree_name)
            if node_tree:
                bpy.data.node_groups.remove(node_tree)


def repair_materials(context):
    get_or_create_node_tree(NORMAL_NODE_TREE)
    get_or_create_node_tree(PREVIEW_NODE_TREE)

    changed = 0
    objects = mesh_objects_from_context(context)
    if not objects:
        objects = [obj for obj in bpy.data.objects if object_is_convenient(obj) and OBJ_RADIUS_PROP in obj]

    for obj in objects:
        if ensure_object_ready(obj, preview_when_new=False)['success']:
            changed += 1
    return changed


def toggle_preview_material(context):
    objects = mesh_objects_from_context(context)
    if not objects:
        objects = [context.object] if object_is_convenient(context.object) else []

    should_enable = False
    for obj in objects:
        if not object_is_convenient(obj):
            continue
        object_fill_missing_materials(obj)
        target_mats = [mat for mat in obj.data.materials if mat and mat.name != DEFAULT_MATERIAL]
        if any(not material_get_group_nodes(mat, PREVIEW_NODE_TREE, 'OVERRIDE') for mat in target_mats):
            should_enable = True
            break

    changed = 0
    for obj in objects:
        if not object_is_convenient(obj):
            continue
        ensure_object_ready(obj, preview_when_new=False)
        target_mats = [mat for mat in obj.data.materials if mat and mat.name != DEFAULT_MATERIAL]
        if not target_mats:
            continue
        if should_enable:
            for mat in target_mats:
                material_add_preview_node(mat)
        else:
            for mat in target_mats:
                material_remove_preview_node(mat)
        changed += 1

    return should_enable, changed


def _cycles_devices_available(context):
    try:
        cycles_prefs = context.preferences.addons["cycles"].preferences
        cycles_prefs.get_devices()
        found_gpu = False
        for devices in (getattr(cycles_prefs, "devices", []),):
            for device in devices:
                if getattr(device, "type", "CPU") != 'CPU':
                    device.use = True
                    found_gpu = True
        return found_gpu
    except Exception:
        return False


def _select_best_cycles_device(context):
    context.scene.render.engine = 'CYCLES'
    if _cycles_devices_available(context):
        try:
            context.scene.cycles.device = 'GPU'
            return 'GPU'
        except Exception:
            pass
    try:
        context.scene.cycles.device = 'CPU'
    except Exception:
        pass
    return 'CPU'


def _store_preview_config(context):
    props = context.scene.bevel_shading_props
    cycles = context.scene.cycles

    if props.preview_config_stored:
        return

    props.preview_saved_engine = context.scene.render.engine
    props.preview_saved_device = getattr(cycles, "device", "")
    props.preview_saved_samples = getattr(cycles, "preview_samples", 0)
    props.preview_saved_adaptive = getattr(cycles, "use_preview_adaptive_sampling", False)
    props.preview_saved_adaptive_threshold = getattr(cycles, "preview_adaptive_threshold", 0.1)
    props.preview_saved_min_samples = getattr(cycles, "preview_adaptive_min_samples", 0)
    props.preview_saved_denoise = getattr(cycles, "use_preview_denoising", False)
    props.preview_config_stored = True


def _restore_preview_config(context):
    props = context.scene.bevel_shading_props
    if not props.preview_config_stored:
        return

    cycles = context.scene.cycles
    if props.preview_saved_engine:
        context.scene.render.engine = props.preview_saved_engine

    for attr_name, value in (
        ("device", props.preview_saved_device),
        ("preview_samples", props.preview_saved_samples),
        ("use_preview_adaptive_sampling", props.preview_saved_adaptive),
        ("preview_adaptive_threshold", props.preview_saved_adaptive_threshold),
        ("preview_adaptive_min_samples", props.preview_saved_min_samples),
        ("use_preview_denoising", props.preview_saved_denoise),
    ):
        if value != "" and hasattr(cycles, attr_name):
            try:
                setattr(cycles, attr_name, value)
            except Exception:
                pass

    props.preview_config_stored = False


def apply_medium_preview_settings(context):
    _select_best_cycles_device(context)
    cycles = context.scene.cycles

    for attr_name, value in (
        ("use_preview_adaptive_sampling", True),
        ("preview_adaptive_threshold", 0.1),
        ("preview_samples", 16),
        ("preview_adaptive_min_samples", 1),
        ("use_preview_denoising", True),
        ("preview_denoiser", 'AUTO'),
        ("preview_denoising_prefilter", 'FAST'),
        ("preview_denoising_input_passes", 'RGB_ALBEDO'),
        ("preview_denoising_start_sample", 16),
    ):
        if hasattr(cycles, attr_name):
            try:
                setattr(cycles, attr_name, value)
            except Exception:
                pass

    node_tree_normal_set_samples(MEDIUM_BEVEL_NODE_SAMPLES)

    space = getattr(context, "space_data", None)
    if space and space.type == 'VIEW_3D':
        shading = space.shading
        if hasattr(shading, "use_scene_world_render"):
            shading.use_scene_world_render = False
        if hasattr(shading, "studiolight_rotate_z"):
            shading.studiolight_rotate_z = 0.0
        if hasattr(shading, "studiolight_intensity"):
            shading.studiolight_intensity = 1.0
        if hasattr(shading, "studiolight_background_alpha"):
            shading.studiolight_background_alpha = 0.0


def is_render_preview_active(context):
    space = getattr(context, "space_data", None)
    return bool(space and space.type == 'VIEW_3D' and space.shading.type == 'RENDERED')


def update_scene_max_bevel(scene):
    if not hasattr(scene, "bevel_shading_props"):
        return 0.0

    max_value = 0.0
    for obj in bpy.data.objects:
        if object_is_convenient(obj):
            max_value = max(max_value, float(obj.get(OBJ_RADIUS_PROP, 0.0)))
            attr = obj.data.attributes.get(RADIUS_ATTR)
            if attr:
                max_value = max(max_value, *(item.value for item in attr.data), 0.0)

    scene.bevel_shading_props.max_bevel_value = max_value
    return max_value


def update_preset_assignments(context, preset):
    old_scene_radius = getattr(preset, "last_scene_radius", -1.0)
    new_scene_radius = display_radius_to_scene(preset.radius, preset.units)

    if old_scene_radius < 0.0:
        preset.last_scene_radius = new_scene_radius
        return 0

    if _radius_matches(old_scene_radius, new_scene_radius):
        preset.last_scene_radius = new_scene_radius
        return 0

    changed = 0
    for obj in bpy.data.objects:
        if not object_is_convenient(obj):
            continue

        if OBJ_RADIUS_PROP in obj and _radius_matches(obj.get(OBJ_RADIUS_PROP, 0.0), old_scene_radius):
            obj[OBJ_RADIUS_PROP] = new_scene_radius
            setattr(obj, OBJ_DISPLAY_UNITS_PROP, preset.units)
            changed += 1

        if obj.data.is_editmode:
            bm = bmesh.from_edit_mesh(obj.data)
            radius_layer = bm.verts.layers.float.get(RADIUS_ATTR)
            if radius_layer is not None:
                edited = False
                for vert in bm.verts:
                    if _radius_matches(vert[radius_layer], old_scene_radius):
                        vert[radius_layer] = new_scene_radius
                        edited = True
                if edited:
                    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
                    changed += 1
            continue

        radius_attr = obj.data.attributes.get(RADIUS_ATTR)
        if radius_attr is not None:
            edited = False
            for item in radius_attr.data:
                if _radius_matches(item.value, old_scene_radius):
                    item.value = new_scene_radius
                    edited = True
            if edited:
                obj.data.update()
                changed += 1

    preset.last_scene_radius = new_scene_radius
    if context and getattr(context, "scene", None):
        update_scene_max_bevel(context.scene)
    return changed


def _random_preset_color():
    return (
        random.uniform(0.25, 0.95),
        random.uniform(0.25, 0.95),
        random.uniform(0.25, 0.95),
    )


def _active_preset(context):
    props = context.scene.bevel_shading_props
    if not props.presets:
        return None
    index = min(max(props.presets_index, 0), len(props.presets) - 1)
    return props.presets[index]


def _set_modifier_input(modifier, node_group, socket_name, value, driver_target=None, data_path=None):
    socket = None
    if hasattr(node_group, "interface"):
        for item in node_group.interface.items_tree:
            if getattr(item, "item_type", "") == 'SOCKET' and getattr(item, "in_out", "") == 'INPUT' and item.name == socket_name:
                socket = item
                break

    if not socket:
        return

    identifier = socket.identifier
    try:
        modifier[identifier] = value
    except Exception:
        return

    if driver_target and data_path:
        try:
            fcurve = modifier.driver_add(f'["{identifier}"]')
            driver = fcurve.driver
            driver.type = 'SCRIPTED'
            driver.expression = "value"
            variable = driver.variables.new()
            variable.name = "value"
            variable.targets[0].id = driver_target
            variable.targets[0].data_path = data_path
        except Exception:
            pass


class BAKEADDON_OT_bevel_set_radius(Operator):
    bl_idname = "object.bakeaddon_bevel_set_radius"
    bl_label = "Assign Bevel Radius"
    bl_description = "Assign the chosen Bevel Shader radius to selected objects or selected mesh elements"
    bl_options = {'REGISTER', 'UNDO'}

    radius: FloatProperty(name="Radius", min=0.0, default=1.0, precision=4)
    units: EnumProperty(name="Units", items=BEVEL_UNIT_ITEMS, default='CENTIMETERS')

    @classmethod
    def poll(cls, context):
        return bool(mesh_objects_from_context(context))

    def execute(self, context):
        changed = set_radius_for_context(context, self.radius, self.units)
        if changed == 0:
            self.report({'WARNING'}, "No mesh geometry was available for Bevel Shader assignment.")
            return {'CANCELLED'}

        self.report({'INFO'}, f"{ADDON_LABEL}: Assigned bevel radius to {changed} object(s).")
        return {'FINISHED'}


class BAKEADDON_OT_bevel_set_zero_radius(Operator):
    bl_idname = "object.bakeaddon_bevel_set_zero_radius"
    bl_label = "Clear Bevel Radius"
    bl_description = "Set the selected Bevel Shader radius to zero"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(mesh_objects_from_context(context))

    def execute(self, context):
        changed = set_zero_radius_for_context(context)
        if changed == 0:
            self.report({'WARNING'}, "No mesh geometry was available for Bevel Shader cleanup.")
            return {'CANCELLED'}
        self.report({'INFO'}, f"{ADDON_LABEL}: Cleared bevel radius on {changed} object(s).")
        return {'FINISHED'}


class BAKEADDON_OT_bevel_preset_add(Operator):
    bl_idname = "object.bakeaddon_bevel_preset_add"
    bl_label = "Add Bevel Radius"
    bl_description = "Add a new colored Bevel Shader radius preset"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.bevel_shading_props
        source = props.presets[-1] if props.presets else None

        props.suspend_preset_updates = True
        preset = props.presets.add()
        try:
            preset.units = source.units if source else 'CENTIMETERS'
            preset.radius = (source.radius + 1.0) if source else 1.0
            preset.color = _random_preset_color()
            preset.last_scene_radius = display_radius_to_scene(preset.radius, preset.units)
        finally:
            props.suspend_preset_updates = False
        props.presets_index = len(props.presets) - 1
        update_scene_max_bevel(context.scene)
        return {'FINISHED'}


class BAKEADDON_OT_bevel_preset_remove(Operator):
    bl_idname = "object.bakeaddon_bevel_preset_remove"
    bl_label = "Remove Bevel Radius"
    bl_description = "Remove the active Bevel Shader radius preset"
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty(name="Index", default=-1)

    def execute(self, context):
        props = context.scene.bevel_shading_props
        if not props.presets:
            return {'CANCELLED'}
        index = self.index if self.index >= 0 else props.presets_index
        index = min(max(index, 0), len(props.presets) - 1)
        props.presets.remove(index)
        props.presets_index = min(index, max(0, len(props.presets) - 1))
        update_scene_max_bevel(context.scene)
        return {'FINISHED'}


class BAKEADDON_OT_bevel_preset_move(Operator):
    bl_idname = "object.bakeaddon_bevel_preset_move"
    bl_label = "Move Bevel Radius"
    bl_description = "Move the active Bevel Shader radius preset"
    bl_options = {'REGISTER', 'UNDO'}

    direction: EnumProperty(
        name="Direction",
        items=(('UP', "Up", ""), ('DOWN', "Down", "")),
        default='UP',
    )

    def execute(self, context):
        props = context.scene.bevel_shading_props
        if not props.presets:
            return {'CANCELLED'}

        index = min(max(props.presets_index, 0), len(props.presets) - 1)
        new_index = index - 1 if self.direction == 'UP' else index + 1
        if new_index < 0 or new_index >= len(props.presets):
            return {'CANCELLED'}

        props.presets.move(index, new_index)
        props.presets_index = new_index
        return {'FINISHED'}


class BAKEADDON_OT_bevel_preset_select(Operator):
    bl_idname = "object.bakeaddon_bevel_preset_select"
    bl_label = "Select Bevel Radius"
    bl_description = "Select the active Bevel Shader radius preset"
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty(name="Index", default=0, min=0)

    def execute(self, context):
        props = context.scene.bevel_shading_props
        if not props.presets:
            return {'CANCELLED'}
        props.presets_index = min(max(self.index, 0), len(props.presets) - 1)
        return {'FINISHED'}


class BAKEADDON_OT_bevel_preview_toggle(Operator):
    bl_idname = "object.bakeaddon_bevel_preview_toggle"
    bl_label = "Toggle Render Preview"
    bl_description = "Toggle Bevel Shader render preview using Cycles medium preview settings"
    bl_options = {'REGISTER'}

    def execute(self, context):
        if not hasattr(context.scene, "bevel_shading_props"):
            return {'CANCELLED'}

        props = context.scene.bevel_shading_props
        space = getattr(context, "space_data", None)

        if is_render_preview_active(context):
            if space and space.type == 'VIEW_3D':
                space.shading.type = props.preview_backup_shading
            _restore_preview_config(context)
            if props.auto_preview_material:
                for obj in mesh_objects_from_context(context):
                    object_remove_preview_nodes(obj)
            return {'FINISHED'}

        _store_preview_config(context)
        if space and space.type == 'VIEW_3D':
            props.preview_backup_shading = space.shading.type
            space.shading.type = 'RENDERED'

        apply_medium_preview_settings(context)
        if props.auto_preview_material:
            for obj in mesh_objects_from_context(context):
                ensure_object_ready(obj, preview_when_new=False)
                object_add_preview_nodes(obj)

        return {'FINISHED'}


class BAKEADDON_OT_bevel_material_preview_toggle(Operator):
    bl_idname = "object.bakeaddon_bevel_material_preview_toggle"
    bl_label = "Toggle Preview Material"
    bl_description = "Toggle the temporary Bevel Shader material preview on selected mesh materials"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(mesh_objects_from_context(context))

    def execute(self, context):
        enabled, count = toggle_preview_material(context)
        state = "enabled" if enabled else "disabled"
        self.report({'INFO'}, f"{ADDON_LABEL}: Preview material {state} for {count} object(s).")
        return {'FINISHED'}


class BAKEADDON_OT_bevel_materials_repair(Operator):
    bl_idname = "object.bakeaddon_bevel_materials_repair"
    bl_label = "Repair Bevel Materials"
    bl_description = "Rebuild missing Bevel Shader node groups and material node links"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        changed = repair_materials(context)
        self.report({'INFO'}, f"{ADDON_LABEL}: Repaired Bevel Shader setup on {changed} object(s).")
        return {'FINISHED'}


class BAKEADDON_OT_bevel_object_cleanup(Operator):
    bl_idname = "object.bakeaddon_bevel_object_cleanup"
    bl_label = "Clean Selected Bevel Data"
    bl_description = "Remove Bevel Shader data from selected mesh objects"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(mesh_objects_from_context(context))

    def execute(self, context):
        changed = 0
        for obj in mesh_objects_from_context(context):
            if cleanup_object(obj):
                changed += 1
        update_scene_max_bevel(context.scene)
        self.report({'INFO'}, f"{ADDON_LABEL}: Cleaned Bevel Shader data from {changed} object(s).")
        return {'FINISHED'}


class BAKEADDON_OT_bevel_global_cleanup(Operator):
    bl_idname = "global.bakeaddon_bevel_global_cleanup"
    bl_label = "Clean All Bevel Data"
    bl_description = "Remove all Bevel Shader data, generated materials, node groups, and modifiers from the scene"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        cleanup_scene_data(remove_node_groups=True)
        update_scene_max_bevel(context.scene)
        self.report({'INFO'}, f"{ADDON_LABEL}: Removed all Bevel Shader data.")
        return {'FINISHED'}





class OBJECT_OT_apply_bevel_shading(Operator):
    bl_idname = "object.apply_bevel_shading"
    bl_label = "Apply Bevel Shading"
    bl_description = "Compatibility operator that assigns the active Bevel Shader radius"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(mesh_objects_from_context(context))

    def execute(self, context):
        preset = _active_preset(context)
        if preset:
            radius = preset.radius
            units = preset.units
        else:
            props = context.scene.bevel_shading_props
            radius = props.bevel_radius
            units = 'UNITS'

        changed = set_radius_for_context(context, radius, units)
        if changed == 0:
            self.report({'WARNING'}, "No selected mesh object was available.")
            return {'CANCELLED'}
        return {'FINISHED'}


class OBJECT_OT_remove_bevel_shading(Operator):
    bl_idname = "object.remove_bevel_shading"
    bl_label = "Remove Bevel Shading"
    bl_description = "Compatibility operator that removes Bevel Shader data from selected objects"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(mesh_objects_from_context(context))

    def execute(self, context):
        changed = 0
        for obj in mesh_objects_from_context(context):
            if cleanup_object(obj):
                changed += 1
        update_scene_max_bevel(context.scene)
        if changed == 0:
            self.report({'WARNING'}, "No selected mesh object had Bevel Shader data.")
            return {'CANCELLED'}
        return {'FINISHED'}


# --- END OF FILE operators_bevel_shader.py ---
