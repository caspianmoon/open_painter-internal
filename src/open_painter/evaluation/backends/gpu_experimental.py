"""Disabled-by-default offscreen fragment experiment; never a viewport renderer.

Uses documented GPUShaderCreateInfo/GPUTexture/GPUFrameBuffer APIs. Resources
belong to this explicit window-context scope and are released on close.
https://docs.blender.org/api/current/gpu.types.html
"""
from time import perf_counter
import numpy as np
import bpy
from .base import validate
from . import cpu
from ...core import image_manager as images

FRAGMENT='''
void main() {
  ivec2 p=ivec2(gl_FragCoord.xy);
  ivec2 size=textureSize(current_image,0);
  vec4 value=texelFetch(current_image,p,0);
  if (opcode==0) value=parameters;
  else if (opcode==1) {
    vec4 src=texelFetch(source_image,p,0);
    float a=clamp(src.a*texelFetch(mask_image,p,0).r*parameters.x,0.0,1.0);
    value=vec4(src.rgb*a+value.rgb*(1.0-a),a+value.a*(1.0-a));
  } else if (opcode==2) {
    value.rgb=pow(clamp((value.rgb-parameters.x)/(parameters.y-parameters.x),0.0,1.0),vec3(1.0/parameters.z));
  } else {
    float radius=parameters.x;
    int half_width=int(ceil(radius*3.0));
    vec3 sum=vec3(0.0); float total=0.0;
    for (int i=-24;i<=24;i++) {
      if (abs(i)>half_width) continue;
      float weight=exp(-float(i*i)/(2.0*radius*radius));
      ivec2 q=clamp(p+(opcode==3?ivec2(i,0):ivec2(0,i)),ivec2(0),size-1);
      sum+=texelFetch(current_image,q,0).rgb*weight; total+=weight;
    }
    value.rgb=sum/total;
  }
  fragColor=value;
}
'''


class Backend:
    def __init__(self,enabled=False):
        self.shader=None; self.batch=None
        self.enabled=enabled and not bpy.app.background
        self.window=bpy.context.window.as_pointer() if bpy.context.window else None
        self.compile_seconds=0
        if self.enabled:
            import gpu
            from gpu_extras.batch import batch_for_shader
            start=perf_counter()
            info=gpu.types.GPUShaderCreateInfo()
            info.vertex_in(0,'VEC2','position')
            info.fragment_out(0,'VEC4','fragColor')
            info.sampler(0,'FLOAT_2D','current_image'); info.sampler(1,'FLOAT_2D','source_image'); info.sampler(2,'FLOAT_2D','mask_image')
            info.push_constant('INT','opcode'); info.push_constant('VEC4','parameters')
            info.vertex_source('void main(){gl_Position=vec4(position,0.0,1.0);}')
            info.fragment_source(FRAGMENT)
            self.shader=gpu.shader.create_from_info(info)
            self.batch=batch_for_shader(self.shader,'TRIS',{'position':((-1,-1),(3,-1),(-1,3))})
            self.compile_seconds=perf_counter()-start

    def close(self):
        self.batch=None; self.shader=None

    def execute(self,source_image,mask_image,operations,target=None):
        validate(operations)
        start=perf_counter()
        source=images.pixels(source_image); mask=images.pixels(mask_image)
        if source.shape!=mask.shape:
            raise ValueError('Experimental source and mask dimensions must match')
        read_seconds=perf_counter()-start
        report=dict(compile_s=self.compile_seconds,image_read_s=read_seconds,backend='CPU',
                    fallback='Disabled or no window GPU context',gpu_resource_bytes=0)
        if not self.enabled:
            result=cpu.execute(source,mask,operations)
        else:
            import gpu
            if not bpy.context.window or bpy.context.window.as_pointer()!=self.window or self.shader is None:
                raise RuntimeError('Experimental GPU window context changed or was closed')
            h,w=source.shape[:2]
            old_viewport=gpu.state.viewport_get(); old_blend=gpu.state.blend_get(); old_depth=gpu.state.depth_test_get()
            old_scissor=gpu.state.scissor_get(); old_depth_mask=gpu.state.depth_mask_get()
            textures=[]; frames=[]
            try:
                upload=perf_counter()
                for array in (source,mask):
                    textures.append(gpu.types.GPUTexture((w,h),format='RGBA32F',data=gpu.types.Buffer('FLOAT',array.size,array.ravel())))
                for _ in range(2): textures.append(gpu.types.GPUTexture((w,h),format='RGBA32F'))
                frames=[gpu.types.GPUFrameBuffer(color_slots=texture) for texture in textures[2:]]
                report.update(upload_create_s=perf_counter()-upload,gpu_resource_bytes=source.nbytes*4,backend=gpu.platform.backend_type_get(),fallback='')
                gpu.state.blend_set('NONE'); gpu.state.depth_test_set('NONE')
                gpu.state.depth_mask_set(False)
                current=textures[0]; target_index=0
                compute=perf_counter()
                for operation in operations:
                    kind=operation['kind']
                    steps=[3,4] if kind=='GAUSSIAN_BLUR' else [{'FILL':0,'OVER':1,'LEVELS':2}[kind]]
                    params=(operation['color'] if kind=='FILL' else
                            (operation.get('opacity',1),0,0,0) if kind=='OVER' else
                            (operation.get('black',0),operation.get('white',1),operation.get('gamma',1),0) if kind=='LEVELS' else
                            (operation.get('radius',2),0,0,0))
                    for opcode in steps:
                        with frames[target_index].bind():
                            gpu.state.viewport_set(0,0,w,h)
                            gpu.state.scissor_set(0,0,w,h)
                            self.shader.bind()
                            self.shader.uniform_sampler('current_image',current)
                            self.shader.uniform_sampler('source_image',textures[0]); self.shader.uniform_sampler('mask_image',textures[1])
                            self.shader.uniform_int('opcode',opcode); self.shader.uniform_float('parameters',params)
                            self.batch.draw(self.shader)
                        current=textures[2+target_index]; target_index=1-target_index
                report['submit_s']=perf_counter()-compute
                readback=perf_counter()
                result=np.array(current.read(),dtype=np.float32).reshape(h,w,4)
                report['readback_s']=perf_counter()-readback
            finally:
                gpu.state.viewport_set(*old_viewport); gpu.state.blend_set(old_blend); gpu.state.depth_test_set(old_depth)
                gpu.state.scissor_set(*old_scissor); gpu.state.depth_mask_set(old_depth_mask)
                frames.clear(); textures.clear()
        publish=perf_counter()
        if target: images.write(target,result,pack=False)
        report['image_write_s']=perf_counter()-publish
        report['end_to_end_s']=perf_counter()-start
        return result,report
