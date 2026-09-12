# Projection correction — 0.1.11

[Adobe UV projection](https://experienceleague.adobe.com/en/docs/substance-3d-painter/using/painting/fill-projections/uv-projection) operates in texture space; an image extending outside islands does not itself mean UVs are ignored. [Tri-planar projection](https://experienceleague.adobe.com/en/docs/substance-3d-painter/using/painting/fill-projections/tri-planar-projection) blends three spatial projections before writing the texture atlas.

The previous Painted Steel and Dirty Plastic coating masks were ordinary painted UV images with a fixed noise pattern. Their coverage now uses a distinct Fill mask with tri-planar projection. Geometry is rasterized from assigned material faces through the render UV layer, matching Blender’s unconnected image texture inputs. The editing UV map is left unchanged.

Existing projects are not silently reinterpreted: select the coating mask, open Settings, change Source to Fill and Projection to Triplanar to convert existing coverage while keeping its image. New applications of these presets use projected coverage immediately. This provides spatial breakup, not curvature-aware edge wear; geometry-driven wear still requires mesh maps and an appropriate generator. Other library UV fills remain UV fills.

`tests/integration/projected_masks.py` compares the same surface in separate UV islands, deliberately collapses a different editing map, and checks UV versus projected output and preservation of painted-mask semantics. Geometry remains based on assigned mesh data; evaluated modifier geometry and UDIM projection are not covered by this change.
