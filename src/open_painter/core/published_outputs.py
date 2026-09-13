"""Publish derived mask textures while preserving the editable source stack."""


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
