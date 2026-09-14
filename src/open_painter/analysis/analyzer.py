"""Measured inventory and explicit resource estimates; no guessed GPU cost."""
from ..evaluation.memory import shared


def report(ts):
    images={image for layer in ts.layers for image in
            [*(prop.image for prop in layer.channels),*(prop.detail_image for prop in layer.channels),
             *(mask.image for mask in layer.masks),
             *(mask.influence.image for mask in layer.masks)] if image}
    images.update(output.image for output in ts.outputs if output.image)
    persistent=sum(image.size[0]*image.size[1]*(16 if image.is_float else 4) for image in images)
    helpers=[obj for obj in ts.id_data.objects if obj.get('source_texture_set')==ts.id and obj.get('open_painter_geometry_effect')=='INSTANCE_HELPER']
    return dict(authored_resolution=int(ts.resolution),image_count=len(images),
        estimated_image_buffer_bytes=persistent,controlled_cache_bytes=shared.used,
        cache_allowance_bytes=shared.allowance,layer_count=len(ts.layers),
        filter_count=sum(len(prop.effects) for layer in ts.layers for prop in layer.channels)+
                     sum(mask.source=='FILTER' for layer in ts.layers for mask in layer.masks),
        instances=sum(obj.get('instance_count',0) for obj in helpers),
        estimated_geometry_triangles=sum(obj.get('estimated_triangles',0) for obj in helpers),
        gpu_cost='Not measured by inventory',
        exclusions='Image estimate excludes undo, GPU copies, compression and Blender overhead')
