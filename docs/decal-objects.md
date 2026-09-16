# Decal objects (0.9.6)

Painter 12 added *Warp to Geometry* because projecting decals onto curved
surfaces is a real problem. Open Painter lives inside Blender, so a decal can simply
be a Blender object: an image Empty whose transform *is* the projection.

## The decal is an Empty

*Layers* tool strip → **Add Decal** (`open_painter.decal`, action ADD) asks for a
name, a base-colour image and a size, then creates:

- `OP_Decal_<name>`: an image Empty at the 3D cursor, facing the viewport, that
  draws the base-colour image in the viewport. Its local XY plane (−0.5…0.5, times
  `empty_display_size`) carries the images; its −Z is the projection direction.
- A fill layer whose channels use the **Decal** projection and point at the Empty
  (`OP_Layer.decal_object`). Assign any images to its channels — Base Color,
  Roughness, Metallic, Normal, Height, Emission — with the usual channel pickers.

Move the Empty and the texture moves; rotate it and the texture rotates; scale it
and the texture scales. Shift+D the Empty (or use *Duplicate Decal*) and the copy
becomes its own layer, sharing the images and following its own Empty. Everything
stays non-destructive: the layer keeps its blend mode, opacity and masks, and the
projection is re-evaluated from the object.

Per layer: **Projection Depth** (how far above and below the decal plane surfaces
still receive it), **Edge Softness** (a fade of the border as a fraction of the
decal size) and **Front Faces Only**. *Select Decal Object* jumps to the Empty.

## Channels

Colour-like channels sample the image through the decal frame with alpha =
coverage × image alpha (clipped outside the image). **Normal** images are
authored in the decal's frame (X right, Y up, Z toward the viewer): the layer
rotates them into world space with the Empty's axes and then into the target's
tangent frame (tangent from the render UVs, bitangent = normal × tangent), so a
decal normal combines exactly like any other tangent normal and turns with the
decal.

## How it evaluates

The shader compiles the decal natively: a *Texture Coordinate* node bound to the
Empty gives decal-local positions, the image samples with clipping, and depth,
facing, softness and the frame axes are uniforms — so moving the Empty updates the
viewport with no Python work at all. The scheduler mirrors the Empty's world axes
and scale into `decal_axes` / `decal_scale` uniforms and invalidates the set when
the object moves (`dependencies.geometry_key` carries every decal frame), which is
what keeps export, 2D snapshots and the CPU reference in step
(`painting/decals.sample`, using the world position/normal rasters and the
per-texel tangent frame from `painting/projection.tangents`). Shader and CPU agree
to float precision.

The Open Graph layer node shows the Empty's location, rotation and scale and the
projection depth, so decal transforms can be edited from the node graph.

Portable materials keep the Empty's name and re-link on import.

## Limits

One Empty per layer (duplicate for more instances). Mirrored UV islands flip the
bitangent sign of decal normals (the same convention limit as authored normal
maps). The projection is planar along the decal's −Z; there is no wrapping around
strongly curved geometry beyond what the projection depth admits.

## Tests

`tests/integration/decal_objects.py`: a 0.5 m decal on a 2 m plane covers the
middle quarter with red left / blue right; moving, rotating and scaling the Empty
move, rotate and scale the footprint; depth, facing and softness gate coverage; a
tilted decal normal lands in the tangent frame and turns with the decal; shader
parity is exact (Base Color 0.0, Normal 6e-8); a live session re-syncs the axes on
a move; Shift+D and *Duplicate Decal* create independent layers; a saved material
re-links its Empty on import.
