# Natural material relief review

The 18 catalog entries from Fresh Oak through Post-Apocalyptic Scrap Metal were revised with materially stronger and material-specific surface relief. Five new grayscale inputs were generated with the built-in `image_gen` tool: oak vessel anatomy, aged hide grain and creases, denim twill, granular cement, and charred timber fissures. Original tool-returned 1254 × 1254 RGB pixels are preserved in `src/open_painter/assets/generated/`; the 2048 × 2048 request is not presented as the actual source resolution. These are artistic scalar inputs, not measured scans or guaranteed seamless maps.

Exact prompts, source paths, dimensions and SHA-256 hashes are recorded in [natural-relief-prompts.json](natural-relief-prompts.json). The matching provenance fragment is [natural-relief-provenance.json](natural-relief-provenance.json). Root library provenance includes these assets alongside the metal, paint-loss and abrasion sources.

## Recipe changes

- Timber relief now follows generated open vessels; aged wood uses deeper pores, oiled wood retains narrower relief, and burnt wood uses its own fractured carbonized source.
- Cement and leather use generated relief with matching masks. Their isotropic sources use triplanar projection to avoid stretched UV pole patterns.
- Denim uses a dedicated diagonal twill source rather than the plain canvas source. Actual render inspection led to finer repeat scales and lower cloth relief, preserving rounded yarn detail without the initial oversized ridges.
- Painted wood, painted concrete, panel coatings and scrap use elevated coating height ranges. Paint-loss and abrasion masks share their color and height boundaries. Isotropic damage uses generated bitmap masks and triplanar projection.
- Granite and polished marble retain their distinct mineral sources; marble relief remains restrained because a polished mineral surface should remain nearly level.

## Render verification

Representative materials were composed and rendered in Blender 5.2.1 LTS with actual 2048px texture outputs, a curved preview mesh, 768px renders and 32 Cycles samples. Full-frame renders were visually inspected; generated height and combined-normal map statistics were checked. All material source hashes and recipes are recorded in each render context.

| Material | Final reviewed directory | Height range | Combined normal XY RMS |
|---|---|---:|---:|
| Fresh Oak | `.blender-test/material-relief/natural` | 0.0642 | 0.1452 |
| Rough Concrete | `.blender-test/material-relief/natural-refined` | 0.1527 | 0.2469 |
| Cracked Old Leather | `.blender-test/material-relief/natural-refined` | 0.1256 | 0.3087 |
| Denim Fabric | `.blender-test/material-relief/natural-refined` | 0.0481 | 0.1507 |
| Canvas Fabric | `.blender-test/material-relief/natural-refined` | 0.0652 | 0.1505 |
| Burnt Wood | `.blender-test/material-relief/natural-refined` | 0.2081 | 0.2650 |
| Post-Apocalyptic Scrap Metal | `.blender-test/material-relief/natural` | 0.2691 | 0.3210 |

The first run rendered six entries successfully, then rejected Burnt Wood because its process loaded the source registry before the new charred source was registered. A new process rendered Burnt Wood successfully with the final registry. An earlier sandboxed invocation exited before script execution during Blender device discovery; successful runs used an isolated workspace profile outside the sandbox.

Directional timber grain and cloth weaving intentionally follow authored UVs; on this latitude/longitude sphere they converge at its poles. Their detail orientation and scale remain editable. The isotropic stone, hide and chipped paint surfaces avoid that pole distortion through triplanar projection. Full-catalog testing and the final comparison gallery are tracked separately by the root task.
