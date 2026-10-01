"""Small target-build API probes, not an experimental renderer or UDIM implementation.

Run via Blender --python ... -- --report PATH. GUI mode exits after reporting.
"""
import argparse
import json
from pathlib import Path
import sys
import traceback
import bpy
import numpy as np


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    report=dict(blender=bpy.app.version_string,build_hash=bpy.app.build_hash.decode(),background=bpy.app.background,capabilities={})
    def probe(name, fn):
        try:
            report['capabilities'][name]=dict(status='PASS',details=fn())
        except AssertionError as exc:
            report['capabilities'][name]=dict(status='FAILED',reason=str(exc))
        except Exception as exc:
            report['capabilities'][name]=dict(status='UNAVAILABLE',reason=str(exc))
    def image_transfer():
        image=bpy.data.images.new('Probe float transfer',width=8,height=8,float_buffer=True)
        try:
            values=np.linspace(0,1,8*8*4,dtype=np.float32)
            image.pixels.foreach_set(values)
            image.update()
            result=np.empty_like(values); image.pixels.foreach_get(result)
            np.testing.assert_array_equal(values,result)
            return 'Float RGBA full-buffer foreach_set/get round-trip; no subrectangle upload proven.'
        finally: bpy.data.images.remove(image)
    def evaluated_geometry():
        obj=bpy.context.scene.objects.get('Cube')
        modifier=obj.modifiers.new('Probe bevel','BEVEL'); modifier.width=.1
        evaluated=None
        try:
            deps=bpy.context.evaluated_depsgraph_get()
            evaluated=obj.evaluated_get(deps)
            mesh=evaluated.to_mesh(preserve_all_data_layers=True,depsgraph=deps)
            assert len(mesh.vertices)>len(obj.data.vertices)
            assert len(mesh.corner_normals)==len(mesh.loops)
            return dict(base_vertices=len(obj.data.vertices),evaluated_vertices=len(mesh.vertices),corner_normals=len(mesh.corner_normals))
        finally:
            if evaluated: evaluated.to_mesh_clear()
            obj.modifiers.remove(modifier)
    def attributes():
        obj=bpy.context.scene.objects.get('Cube')
        group=bpy.data.node_groups.new('Probe stored attribute','GeometryNodeTree')
        modifier=obj.modifiers.new('Probe attribute','NODES'); modifier.node_group=group
        evaluated=None
        try:
            group.interface.new_socket(name='Geometry',in_out='INPUT',socket_type='NodeSocketGeometry')
            group.interface.new_socket(name='Geometry',in_out='OUTPUT',socket_type='NodeSocketGeometry')
            inp=group.nodes.new('NodeGroupInput'); out=group.nodes.new('NodeGroupOutput')
            store=group.nodes.new('GeometryNodeStoreNamedAttribute'); store.data_type='FLOAT'; store.domain='POINT'
            store.inputs['Name'].default_value='probe_field'; store.inputs['Value'].default_value=.25
            group.links.new(inp.outputs['Geometry'],store.inputs['Geometry'])
            group.links.new(store.outputs['Geometry'],out.inputs['Geometry'])
            deps=bpy.context.evaluated_depsgraph_get(); evaluated=obj.evaluated_get(deps)
            mesh=evaluated.to_mesh(preserve_all_data_layers=True,depsgraph=deps)
            values=np.empty(len(mesh.vertices),np.float32)
            mesh.attributes['probe_field'].data.foreach_get('value',values)
            np.testing.assert_array_equal(values,.25)
            return 'Named float POINT attribute survives evaluated snapshot.'
        finally:
            if evaluated: evaluated.to_mesh_clear()
            obj.modifiers.remove(modifier); bpy.data.node_groups.remove(group)
    def gpu_probe():
        import gpu
        # Background Blender may abort at low-level GPU calls; do not request a context there.
        if bpy.app.background:
            raise RuntimeError('No window GPU context: GPU calls intentionally skipped in background process.')
        shader=gpu.shader.from_builtin('UNIFORM_COLOR')
        texture=gpu.types.GPUTexture((8,8),format='RGBA32F')
        framebuffer=gpu.types.GPUFrameBuffer(color_slots=texture)
        with framebuffer.bind(): framebuffer.clear(color=(.25,.5,.75,1))
        pixels=np.array(texture.read()).reshape(-1,4)
        np.testing.assert_allclose(pixels,np.tile((.25,.5,.75,1),(64,1)),atol=1e-6)
        offscreen=gpu.types.GPUOffScreen(8,8)
        offscreen.free()
        return dict(backend=gpu.platform.backend_type_get(),renderer=gpu.platform.renderer_get(),shader='UNIFORM_COLOR',texture='RGBA32F',readback=True)
    probe('image_full_transfer',image_transfer)
    probe('evaluated_geometry_and_corner_normals',evaluated_geometry)
    probe('geometry_nodes_attribute',attributes)
    probe('gpu_context_shader_texture_readback',gpu_probe)
    report['capabilities']['native_udim_paint_bake_addressing']=dict(status='NOT_TESTED',reason='Creating a tiled Image would not prove brush/bake tile addressing. No UDIM path is enabled by this increment.')
    report['capabilities']['partial_image_upload']=dict(status='NOT_PROVEN',reason='Only full-buffer Image transfer tested.')
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n', encoding="utf-8")
    print(json.dumps(report,indent=2),flush=True)
    if not bpy.app.background: bpy.ops.wm.quit_blender()

if bpy.app.background: main()
else:
    def deferred():
        try: main()
        except Exception:
            traceback.print_exc(); bpy.ops.wm.quit_blender()
    bpy.app.timers.register(deferred,first_interval=1)
