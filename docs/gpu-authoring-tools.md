# Painting and authoring tools

Select a paint layer and open **Paint Material** in its inspector. All tools retain
editable stroke records and support ordinary Blender undo. Export evaluates those
records on the CPU without depending on GPU caches.

## Symmetry and smudging

Enable any of **Symmetry X/Y/Z** before starting a gesture. The axes use the active
object's local frame captured at pointer-down, including mirrored transforms.
Samples on a symmetry plane are deduplicated. Reflections respect eligible surfaces
and texture-set permissions; the entire gesture remains one undo operation.

Choose **Smudge** to transport the accumulated paint layer's enabled channels
together. Smudging preserves coverage and normalizes transported normal values.
It samples that paint layer, rather than the composited material below it.

## Editable text

Choose **Add Layer → Add Text** for a fill layer with a text mask, or select **Text**
as an existing mask's source. The mask inspector exposes content, font, size,
alignment, character spacing, line spacing and position. Enter `\n` for a line break
in Blender's single-line string field. Mask Graphs also provide a **Text** node.

Text settings and the generated mask are saved together. If a custom font is
unavailable, the saved mask preserves the appearance. Restore or replace the font
to regenerate it. Smart materials carry the generated mask; font files are not bundled.

## Brush Graphs

Use the **Brush Graph** action in Paint Material to open a separate graph in the
Open Painter graph editor. Its default Pressure → Flow connection is editable.
Unlinked material outputs use the brush settings. Radius, Flow, Opacity and Alpha
outputs multiply the corresponding brush values or coverage; Angle rotates the
alpha, and Offset moves the dab before resolving a surface hit.

Inputs include pressure, tilt, position, normal, distance, time and sample index.
Math, vector operations and seeded random nodes can drive brush or material outputs.
Invalid graphs are rejected before painting starts. A gesture freezes the graph
and saves its resolved values, so later graph edits affect future gestures.

## Particle painting

Enable **Particle Painting** and adjust particles per dab, emission rate, speed,
spread, gravity, drag, lifetime and bounce. Each resampled dab emits a seeded burst;
collisions use the evaluated mesh and fixed 120 Hz steps. Contact hits go through
the same brush, graph, symmetry and permission checks. Saved contacts replay as
ordinary samples; reopening and exporting never rerun physics.

## Evaluation and UDIMs

New UDIM groups share their recipe; pixels, maps and resolution remain tile-local.
See [UDIM texture sets](udim-workflow.md) for linking and making a group independent.

GPU painting uses Blender's GPU abstraction. Overlapping UV arrangements and
resolved graph or reflected planar dabs use the deterministic CPU source path.
Interactive previews remain bounded at large resolutions; eligible full-resolution
previews are adopted directly, and other commits append to the GPU source cache.
Cache eviction, geometry changes and reload rebuild from saved records.

Actual driver validation for this upgrade used Metal. Vulkan/OpenGL API simulations
and forced pixel publication exercise contracts but do not certify those drivers.
