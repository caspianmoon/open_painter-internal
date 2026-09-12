# Smart material format, version 1

A `.openpaintmaterial` file is either UTF-8 JSON (for constant/procedural-only materials) or a ZIP:

```
material.json
assets/0000.exr
assets/0001.exr
```

`material.json` has `format_version: 1`, `name`, and an ordered `layers` array. Each layer has
`id`, `name`, `type` (`FILL`, `PAINT`, `FOLDER`), optional `parent_id`, `visible`, `opacity`,
`blend_mode`, `channels`, and `masks`. Layers run bottom to top. Defaults are Blender property defaults;
missing channels on imported non-folder layers are created disabled. IDs must be unique and folder
references acyclic. Import assigns fresh IDs to all layers, channels' layer references, and masks.

Channels use stable IDs: BASE_COLOR, ROUGHNESS, METALLIC, NORMAL, HEIGHT, AO. Each channel supports
`enabled`, `color` (linear RGBA), `value`, `image`, `projection` (UV/TRIPLANAR), `scale`, `rotation`
(radians) and `offset`. An image reference is `{"asset":"assets/0000.exr","colorspace":"Non-Color"}`.
Export uses lossless 32-bit float EXR. Assets are bundled separately, never base64 JSON. Imported
assets become independently owned, packed Blender images.

Masks support `source` (PAINT/BAKED/GENERATOR/NOISE), `image`, `map_type`, `generator`,
`enabled`, `operation` (MULTIPLY/ADD), `invert`, `black`, `white`, `strength`, `amount`, `contrast`,
`noise_scale`, `noise_strength` and `seed`. Baked maps are resolved in the target texture set; they are
not exported as project map records. A baked map explicitly assigned as a layer texture is bundled.

The importer only accepts the current version, at most 256 layers and 512 MiB uncompressed ZIP data.
Only directly contained `assets/*.exr` entries can satisfy asset references. Files are read by name
and written to generated temporary paths; archive paths are never extracted directly. Invalid input
raises an actionable error and rolls back newly created Blender data. Imported metadata is not executable.

See `src/open_painter/assets/smart_materials/` for complete human-readable examples.

Layers may also contain `effects` and `mask_effects` arrays of ordered Levels/Filter records. These additive version-1 fields store parameters and selection indices; importing and duplicating regenerate effect IDs. Old materials without these fields retain empty effect stacks.
