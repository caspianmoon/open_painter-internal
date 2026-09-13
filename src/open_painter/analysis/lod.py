"""Explicit evaluated mesh LOD staging; visual quality remains artist-reviewed."""
import bpy


def create(context,source,ratio=.5):
    if source.type!='MESH' or not 0<ratio<=1:
        raise ValueError('Choose a mesh and a decimation ratio in (0, 1]')
    graph=context.evaluated_depsgraph_get()
    mesh=bpy.data.meshes.new_from_object(source.evaluated_get(graph),preserve_all_data_layers=True,depsgraph=graph)
    result=bpy.data.objects.new(source.name+' LOD Preview',mesh)
    context.scene.collection.objects.link(result)
    result.matrix_world=source.matrix_world
    modifier=result.modifiers.new('Artist-reviewed LOD','DECIMATE'); modifier.ratio=ratio
    result['open_painter_lod_source']=source.name
    result['open_painter_lod_ratio']=ratio
    return result
