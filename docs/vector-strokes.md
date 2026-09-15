# Vector strokes and the universal canvas — 0.6.0 / 0.6.1

The **Vector Brush** keeps a brush gesture as data — a spline of samples with
pressure, radius, tilt and direction, the brush settings and the material values —
and rasterises it on demand. Because a stroke lives in world space on the object,
not in one texture, the same stroke renders on every texture set it touches and at
whatever Output Resolution each set has. Paint at 1K for speed; switch to 8K later
and the strokes re-render crisp.

![A vector stroke on the shader-native preview](vector_strokes.png)

## Painting

- Select a paint layer, open *Vector Brush* in its properties and press **Paint
  Vector Strokes**. Drag on any assigned surface; Esc or right-click finishes.
  Radius is in world units or screen pixels (resolved at the hit); hardness,
  opacity, flow, spacing (fraction of the radius), jitter, an alpha image that
  rakes along the stroke, surface or view-planar projection, Paint or Erase, and
  the channel values (Base Color, Roughness, Metallic, Height, …) are recorded per
  stroke. Tablet pressure scales radius and coverage; tilt is stored.
- **Paint Across Sets**: when a stroke reaches objects of another texture set, a
  paint layer with the same name is created there and *linked* (same stroke key),
  so the stroke is one record rendered by several sets. The *Vector Strokes* box
  lists "Also on: …".
- Every stroke stays editable: the list under the layer selects one; change its
  radius, hardness, flow, projection, mode, alpha or material values, reorder,
  duplicate, delete, or **Rasterize** it into the layer's paint images (image-swap
  undo) when you want pixels. *Purge Orphans* drops strokes no layer renders.
- A mask can render strokes too: mask source **Vector Strokes** with *This Layer's
  Strokes* or a *New Key* painted into with the brush.
- Native pixel painting keeps working on the same layer: strokes composite over the
  painted image.

## How it renders

`painting/stroke_raster.kernel` scores every texel of a set against the samples:
each sample is a disc of its radius in the plane of its normal (or of the view for
planar strokes), with a depth band of one radius, front-facing only, falling off
from hardness outwards, scaled by pressure and flow; samples union with Max or
Flow. The texels come from the set's own world-position and normal rasters
(`geometry/uv_rasterizer.rasterize` over the triangles inside the stroke's
bounds), so seams and sets are invisible to the stroke; a one-texel dilation into
uncovered texels hides seam cracks. Occlusion is the depth band (a stroke on a
thin plate also marks the other side within one radius; the plan's precise
per-sample ray test is a later refinement).

Per-stroke coverage is cached in the set's raster cache by (stroke revision,
resolution, geometry stamp). On the CPU reference the strokes composite under a
paint layer's channel content (`channel_eval._base_source`); in the shader-native
preview they are a raster boundary per channel (`shader_graph._strokes_boundary`)
whose identity is the stroke tuple plus the geometry stamp, so editing a stroke
re-identifies the boundary without rebuilding, resolution changes rebuild the
graph at the new size, undo rehydrates, and a moved or edited mesh re-projects.
Strokes are excluded from smart-material documents (world-space asset data).

Tests: `tests/unit/test_stroke_raster.py`, `tests/integration/vector_strokes.py`
(universal canvas, seams, edits, resolution, geometry, undo, reload),
`stroke_parity.py` (CPU vs shader, re-identification, rehydration),
`vector_brush_ui.py` (real window).

## Native painting across sets (0.6.1)

Blender's own brush can cross sets too: turn on **Paint Across Sets** in the paint
layer's properties before *Paint*. The session then runs in Blender's material
paint mode: every texture set assigned to the object gets a paint layer linked to
the active layer's key (created where missing), each set's display material gets
an owned *Paint Canvas* image node pointing at that set's layer image, and the
face restriction covers the union of the sets' material slots. One stroke down a
seam writes both canvases; change detection, the shader-native fast path and
Finish evaluate every participating set; Finish removes the canvas nodes and
restores image paint mode. Limits: each set paints the same channel; brush undo
is per image (Blender restores both tiles of one stroke together); seam hairlines
between islands of different sets are per image — the Vector Brush has no such
seam. Tests: `tests/integration/multi_set_paint.py`, `multi_set_paint_ui.py`.
