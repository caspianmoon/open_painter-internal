# Physical scale awareness (0.9.5)

A scratch should not have the same apparent size on a pistol, a truck, a building
and a coffee mug. Blender knows the assigned meshes' dimensions, so Open Painter
can author procedural sizes in metres and resolve them per asset.

## Density

For a texture set, **density** is the world length of one UV tile:
√(3D area / UV area) over the assigned faces (world space, so object scale
counts). Texel size = density / resolution. The Texture Set panel shows both
(*Texel 0.24 mm · UV tile 1.20 m*). The value is cached against the evaluated
geometry and assignments.

## Physical units

Channels, masks and effects have a **Units** switch: *Texture* (repeats and
texels, as before) or *Physical* (metres). In Physical units:

| Owner | Metre field | Resolved texture value |
| --- | --- | --- |
| Channel procedural | Feature Size (one noise cell) | `procedural_scale` = density / size (UV) or 1 / size (Triplanar, already world space) |
| Mask procedural / noise / generator | Feature Size | `noise_scale`, same rule |
| Effect (any neighbourhood kind) | Radius | `radius` = radius / texel size |
| Warp effects | Noise Feature Size | driving-noise `scale` = density / size |

The resolved value is shown next to the field (*= 30.72 repeats on this mesh*) and
clamped to the property's range when the physical size is out of reach (2 mm
scratches on a 3 m tile ask for more repeats than the noise supports — the
readout shows the clamp).

`evaluation/physical.sync` rewrites the texture-unit values whenever they would
differ: a metre field or the units switch changes (the field's own callback),
the projection, source or kind changes (structural callbacks), the assigned
geometry changes (`dependencies.refresh` — scale the object and the repeats
follow), the assignments change, the resolution changes (filter radii in
texels), a material is imported, or a file loads. Evaluators and shader bindings
keep reading the texture fields, so nothing else changed.

## Lock and transfer

*Lock Sizes to Metres* (`open_painter.physical_scale`, action LOCK) converts every
scalable owner of the set to Physical units, deriving the metre sizes from the
current values — the look stays exactly the same, but the sizes now travel with
the material. Save it as a smart material and load it on another asset: the
importer resolves the metre sizes for that asset's density, so the same 4 mm rust
spots appear four times as many across a body four times larger. *Back to Texture
Units* keeps the look and returns to repeats and texels; *Resolve Now* re-derives
after an external change.

## Limits

Density is one number per set (the area-weighted average); islands with very
different texel densities within one set get the average. Edge-wear widths from
baked curvature and micro-detail radii stay in texels. Generator noise on large
assets can hit the 512-repeat ceiling of `noise_scale`.

## Tests

`tests/integration/physical_scale.py`: a 1 m and a 4 m cube in two sets — the same
10 cm cells resolve to four times the repeats on the truck and the same 2 cm blur
to a quarter of the texels; Triplanar resolves per world unit; a resolution change
doubles the texel radius; scaling the object re-resolves through the geometry
dependency in a live session; Lock keeps the look and Unlock keeps the values; a
material saved from the mug re-normalizes on the truck.
