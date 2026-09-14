"""Render actual Blender shader arithmetic, then update existing bound sockets.

Emission baking is used only as a test readback mechanism. Production parameter
updates never invoke bake. Run in an isolated background Blender process.
"""
from pathlib import Path
import sys

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "unit"))

from open_painter.constants import BLEND_ITEMS
from open_painter.evaluation import blend_modes
from open_painter.evaluation.shader_math import Math, Signal, blend
from open_painter.evaluation.shader_effects import POINTWISE, apply_effect
from test_shader_arithmetic import effect, expected_effect


class BoundMath(Math):
    def __init__(self, tree):
        super().__init__(tree)
        self.bindings = {}

    def param(self, owner, field, index=None):
        key = id(owner), field, index
        if key not in self.bindings:
            node = self.node("ShaderNodeValue")
            self.bindings[key] = owner, field, index, node.outputs[0]
        owner, field, index, socket = self.bindings[key]
        value = getattr(owner, field)
        socket.default_value = float(value if index is None else value[index])
        return socket

    def sync(self):
        for owner, field, index, socket in self.bindings.values():
            value = getattr(owner, field)
            socket.default_value = float(value if index is None else value[index])

    def effect_image(self, owner):
        return owner.image


def run():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 1
    scene.render.bake.margin = 0
    scene.render.bake.use_clear = True
    source, base = np.array((.83, .27, .41), np.float32), np.array((.13, .64, .91), np.float32)
    props = [effect(kind) for kind in sorted(POINTWISE)]
    # Bypassed invalid ranges must still compile dynamic sockets. A subsequent
    # parameter revision below restores a valid range and nonzero strength.
    props += [effect("LEVELS", strength=0., black=.8, white=.2),
              effect("LEVELS", enabled=False, black=.8, white=.2)]
    cases = [("blend:" + mode, mode) for mode, *_ in BLEND_ITEMS]
    cases += [("effect:" + prop.kind, prop) for prop in props]
    size = 16
    image = bpy.data.images.new("Shader arithmetic readback", width=len(cases)*size, height=size, float_buffer=True)
    image.colorspace_settings.name = "Non-Color"
    mesh = bpy.data.meshes.new("Shader swatches")
    vertices, faces = [], []
    for index in range(len(cases)):
        vertices += [(index, 0, 0), (index+1, 0, 0), (index+1, 1, 0), (index, 1, 0)]
        faces.append(tuple(range(index*4, index*4+4)))
    mesh.from_pydata(vertices, [], faces)
    mesh.uv_layers.new()
    builders = []
    for index, (label, case) in enumerate(cases):
        material = bpy.data.materials.new(label)
        material.use_nodes = True
        material.node_tree.nodes.clear()
        builder = BoundMath(material.node_tree)
        builders.append(builder)
        if isinstance(case, str):
            signal = blend(builder, Signal(tuple(base), 1.), Signal(tuple(source), .79), .61, case)
        else:
            signal = apply_effect(builder, Signal(tuple(source), .39), case)
        emission = builder.node("ShaderNodeEmission")
        builder.link(builder.clamp(signal.color), emission.inputs["Color"])
        output = builder.node("ShaderNodeOutputMaterial")
        builder.link(emission.outputs[0], output.inputs["Surface"])
        target = builder.node("ShaderNodeTexImage")
        target.image = image
        material.node_tree.nodes.active = target
        mesh.materials.append(material)
        mesh.polygons[index].material_index = index
        u0, u1 = index / len(cases), (index+1) / len(cases)
        for loop, uv in zip(mesh.polygons[index].loop_indices, ((u0, 0), (u1, 0), (u1, 1), (u0, 1))):
            mesh.uv_layers.active.data[loop].uv = uv
    obj = bpy.data.objects.new("Shader swatches", mesh)
    scene.collection.objects.link(obj)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    node_counts = [len(builder.tree.nodes) for builder in builders]

    for iteration in range(2):
        if iteration:
            for prop in props:
                prop.amount = .71
                prop.contrast = .87
                prop.strength = .42
                prop.hue = .13
                prop.gamma = .76
                prop.black = .07
                prop.white = .93
            for builder in builders:
                builder.sync()
            assert [len(builder.tree.nodes) for builder in builders] == node_counts
        bpy.ops.object.bake(type="EMIT")
        data = np.empty(len(image.pixels), np.float32)
        image.pixels.foreach_get(data)
        data = data.reshape(size, len(cases)*size, 4)
        maximum = 0.
        for index, (label, case) in enumerate(cases):
            expected = (blend_modes.blend(base, source, .79*.61, case) if isinstance(case, str)
                        else source if not case.enabled or case.strength <= 0
                        else expected_effect(source, case))
            expected = np.clip(expected, 0, 1)
            actual = data[size//2, index*size+size//2, :3]
            maximum = max(maximum, float(np.max(np.abs(actual-expected))))
            np.testing.assert_allclose(actual, expected, atol=3e-5, rtol=2e-5,
                                       err_msg=f"{label}, parameter revision {iteration}")
        print(f"SHADER_NODES_PASS revision={iteration} swatches={len(cases)} max_error={maximum:.8g} nodes={sum(node_counts)}", flush=True)


run()
