# Research applied to Open Painter 0.1.9

The user's latest annotated screenshot defines the layout. The implementation remains the Blender add-on restored from 0.1.6, not the separate application experiment.

- [Adobe: Toolbars](https://experienceleague.adobe.com/en/docs/substance-3d-painter/using/interface/toolbars): separate tools and contextual properties. Applied as a narrow tool strip, top brush settings, and bottom library.
- [Adobe: Interface](https://experienceleague.adobe.com/en/docs/substance-3d-painter/using/interface/interface): assets, layer stack, and properties remain distinct sections.
- [Adobe: Layer management performance](https://experienceleague.adobe.com/en/docs/substance-3d-painter/using/technical-support/performance-guidelines/layer-management): stack changes and resolution affect evaluation cost. Preserve dirty-channel and regional composition; measure memory separately from latency. No silent texture downscaling.
- [Blender: Principled BSDF](https://docs.blender.org/manual/en/latest/render/shader_nodes/shader/principled.html): coordinate base color, metalness and roughness. Painted/rusted coatings and wood remain dielectric; conductor presets use metallic=1.
- [Blender: Color management](https://docs.blender.org/manual/en/latest/render/color_management.html): color images use sRGB, data maps use Non-Color. Use OpenGL tangent normals.
- [Poly Haven license](https://polyhaven.com/license), [API documentation](https://github.com/Poly-Haven/Public-API): assets are CC0. Powered by Poly Haven. Downloads happen only through the build-time fetch script; runtime use is offline.

## Material provenance

- [Rough Wood](https://polyhaven.com/a/rough_wood)
- [Fine Grained Wood](https://polyhaven.com/a/fine_grained_wood)
- [Rusty Painted Metal](https://polyhaven.com/a/rusty_painted_metal), named Weathered Paint in the library. The supplied color/roughness/normal set describes the coating; it is not treated as a uniformly metallic surface.

Exact map URLs and SHA-256 hashes are stored in `src/open_painter/assets/surfaces/catalog.json`. Downloads verify provider MD5 checksums before writing files. Thumbnails are Cycles renders of the actual Open Painter material outputs.

## Validation

See `.blender-test/paint-scan-benchmark.json`, `draw-cache-benchmark.json`, and `shelf-result.json` for local measurements. Paint scan figures cover raster copying/comparison, not end-to-end input-to-display latency. GPU rectangle figures measure CPU submission for identical 100-rectangle batches, not full viewport FPS.
