"""Shared GUI probes for image-backed and owned GPU painting inputs."""
import numpy as np
from open_painter.core import image_manager as images
from open_painter.evaluation import shader_preview
from open_painter.painting import gesture
from open_painter.ui.vector_brush import OP_OT_vector_brush

class BoundGPU:
    """Probe owned textures as well as the legacy Blender image inputs."""
    def __init__(self, resource):
        self.resource = resource

    def get(self, key, default=None):
        if key == 'op_gesture_preview':
            brush = OP_OT_vector_brush._active
            preview = brush._gesture.preview if brush is not None and brush._gesture is not None else None
            return bool(preview and preview.gpu and any(r is self.resource for r in preview.gpu.outputs.values()))
        return default


def input_pixels(value):
    if isinstance(value, BoundGPU):
        from open_painter.evaluation.engine import runtime
        resource = value.resource
        n = resource.dimensions[0]
        data = np.asarray(runtime.device().readback(resource.texture, n)).reshape(n, n, 4)
        return data[..., [0, 0, 0, 1]] if resource.mapping == 'rg' else data
    return images.pixels(value)


def bound_inputs(scene, ts, layer):
    """The shader session's current layer inputs, including owned GPU resources."""
    graph = shader_preview.session_graph(scene, ts)
    result = {}
    if graph is None:
        return result
    for node in graph.nodes():
        resource = getattr(node, 'gpu_resource', None)
        if node.bl_idname != "ShaderNodeTexImage" or (node.image is None and resource is None):
            continue
        channel = gesture.Preview._channel_of(node, layer)
        if channel is not None:
            result[channel] = BoundGPU(resource) if resource is not None else node.image
    return result
