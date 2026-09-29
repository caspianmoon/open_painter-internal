"""Versioned Gate 0 fixture additions; cold source construction only."""
FIXTURES = {
    'plain': 'two_fills_plain_v1',
    'masked': 'two_fills_cellular_mask_v1',
    'image': 'nonuniform_image_fill_v1',
    'paint': 'paint_layer_v1',
}


def add_source(kind, scene, ts, coat, size):
    import bpy
    import numpy as np
    from open_painter.core import layer_stack as stack
    if kind == 'image':
        im = bpy.data.images.new('Gate 0 UV gradient v1', size, size, alpha=True, float_buffer=True)
        im.colorspace_settings.name = 'Non-Color'
        values = np.ones((size, size, 4), np.float32)
        values[..., 0] = (np.arange(size, dtype=np.float32)[None, :] + .5) / size
        values[..., 1] = (np.arange(size, dtype=np.float32)[:, None] + .5) / size
        values[..., 2] = .2
        im.pixels.foreach_set(values.ravel())
        stack.channel_for(coat, 'BASE_COLOR').image = im
    elif kind == 'paint':
        from open_painter.painting import strokes
        from types import SimpleNamespace
        layer = stack.add(ts, 'PAINT', 'Gate 0 localized stroke v1')
        settings = SimpleNamespace(radius=.2, hardness=.6, opacity=.9, flow=1.,
                                   accumulate='MAX', alpha_image=None, projection='SURFACE',
                                   blend='PAINT', base_color=(1., .1, .05, 1.), roughness=.1,
                                   metallic=0., height=.5, ao=1., emission=(0., 0., 0., 1.),
                                   opacity_value=1., normal=(.5, .5, 1., 1.),
                                   channels={'BASE_COLOR', 'ROUGHNESS'})
        # World positions, normal, pressure, radius, rake and tangent. Samples
        # lie on the cube's top face and pass through the existing brush raster.
        samples = np.array([[-.2, .1, 1., 0., 0., 1., .4, .2, 0., 0., 1., 0., 0.],
                            [0., .1, 1., 0., 0., 1., .8, .2, 0., 0., 1., 0., 0.],
                            [.2, .1, 1., 0., 0., 1., 1., .2, 0., 0., 1., 0., 0.]], np.float32)
        strokes.record(scene.open_painter, ts, layer, samples, settings, universal=False)
