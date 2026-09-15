"""Publish derived mask textures while preserving the editable source stack."""


def publish_channel(ts, channel):
    """A packed snapshot of one evaluated channel output, for geometry consumers."""
    from ..core import image_manager as images
    from ..evaluation.evaluator import LayerEvaluator
    from ..evaluation.references import issues
    errors = issues(ts)
    if errors:
        raise ValueError(' | '.join(errors))
    LayerEvaluator(ts.id_data).evaluate_texture_set(ts.id)
    output = images.output(ts, channel)
    snapshot = images.create(f'{ts.name}_{channel.title()} Published', int(ts.resolution),
                             color_data=channel in {'BASE_COLOR', 'EMISSION'})
    images.write(snapshot, images.pixels(output.image), pack=True)
    snapshot['open_painter_published_channel'] = channel
    snapshot['open_painter_published_revision'] = ts.edit_serial
    snapshot['open_painter_geometry_texture'] = True
    return snapshot


def publish_mask(ts, layer):
    from ..evaluation.mask_eval import bake_mask_texture
    from ..evaluation.references import issues
    errors = issues(ts)
    if errors:
        raise ValueError(' | '.join(errors))
    image = bake_mask_texture(ts,layer)
    image['open_painter_published_layer'] = layer.id
    image['open_painter_published_revision'] = ts.edit_serial
    image['open_painter_published_stage'] = 'POST_FILTER_MASK'
    return image
