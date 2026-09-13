"""Native entry points for evaluated bundles and non-destructive atlas staging."""
from pathlib import Path
import bpy
from .operators import SafeOperator
from ..constants import RESOLUTIONS


class OP_OT_production_export(SafeOperator,bpy.types.Operator):
    bl_idname='open_painter.production_export'
    bl_label='Production Export'
    bl_options={'REGISTER','UNDO'}
    action: bpy.props.EnumProperty(items=[('BUNDLE','Evaluated Bundle',''),('ATLAS','Stage Atlas',''),('MEASURE','Measure Density','')])
    geometry: bpy.props.EnumProperty(name='Generated Geometry',items=[('ACTUAL','Actual Geometry','Realized in exported staging scene only'),('OMIT','Omit','Textures cannot preserve generated silhouettes')])
    resolution: bpy.props.EnumProperty(name='New Atlas Resolution',items=RESOLUTIONS,default='2048')
    gutter: bpy.props.IntProperty(name='Atlas Gutter',default=8,min=1,max=64)

    def invoke(self,context,event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self,context):
        if self.action=='BUNDLE': self.layout.prop(self,'geometry')
        elif self.action=='ATLAS':
            self.layout.prop(self,'resolution'); self.layout.prop(self,'gutter')
            self.layout.label(text='New UVs and evaluated copies; final textures frozen')
        else: self.layout.label(text='Measure active texture set; no UV changes')

    def run(self,context):
        from ..core.project_manager import require_set
        ts=require_set(context)
        if self.action=='MEASURE':
            from ..geometry.texel_density import measure
            result=measure(ts)
            self.report({'INFO'},f"{result['texels_per_world_unit']:.1f} px/unit · {result['sampled_occupancy']:.1%} sampled UV occupancy")
        elif self.action=='ATLAS':
            from ..export.atlas import build
            selected=set(context.selected_objects)
            sources=[item for item in context.scene.open_painter.texture_sets if any(ref.object in selected for ref in item.assignments)]
            result,objects=build(context,sources,int(self.resolution),self.gutter)
            self.report({'INFO'},f'Created {result.name}: {len(objects)} staged copies, originals retained')
        else:
            if not ts.export.directory: raise ValueError('Choose an output directory')
            from ..export.mesh_bundle import export_bundle
            from ..export.exporter import safe_name
            path=Path(bpy.path.abspath(ts.export.directory))/(safe_name(ts.name)+'_bundle')
            export_bundle(context,[ts],path,self.geometry)
            self.report({'INFO'},f'Exported evaluated scene, textures and manifest to {path}')
