# Smart material format, version 2

A `.openpaintmaterial` file is either UTF-8 JSON (for constant/procedural-only materials) or a ZIP:

```
material.json
assets/0000.exr
assets/0001.exr
```

`material.json` has `format_version: 2`, `name`, an ordered `layers` array, and optional
`surface` scalar parameters (`anisotropy`, `coat_weight`, `coat_roughness`). Version 1 files remain readable. Each layer has
`id`, `name`, `type` (`FILL`, `PAINT`, `FOLDER`), optional `parent_id`, `visible`, `opacity`,
`blend_mode`, `channels`, and `masks`. Layers run bottom to top. Defaults are Blender property defaults;
missing channels on imported non-folder layers are created disabled. IDs must be unique and folder
references acyclic. Import assigns fresh IDs to all layers, channels' layer references, and masks.

Channels use stable IDs: BASE_COLOR, ROUGHNESS, METALLIC, NORMAL, HEIGHT, AO, EMISSION, OPACITY. Each channel supports
`enabled`, `color` (linear RGBA), `value`, `image`, `projection` (UV/TRIPLANAR), `scale`, `rotation`
(radians) and `offset`. An image reference is `{"asset":"assets/0000.exr","colorspace":"Non-Color"}`.
Export uses lossless 32-bit float EXR. Assets are bundled separately, never base64 JSON. Imported
assets become independently owned, packed Blender images.

Channels may additionally contain `detail_image`, using the same asset-reference
shape. This scalar source modulates editable color/value with `procedural_detail`
and height with `procedural_height`; it replaces procedural detail while present.
Shared image bindings export one EXR per source and import as one shared packed
image. Missing optional fields retain the previous behavior in versions 1 and 2.
Missing referenced images fail before import mutates layers or export writes a file.

Masks support `source` (PAINT/BAKED/GENERATOR/NOISE/FILL/PROCEDURAL/FILTER), `image`, `map_type`, `generator`,
`enabled`, `operation` (MULTIPLY/ADD/NORMAL), `invert`, `black`, `white`, `strength`, `amount`, `contrast`,
`noise_scale`, `noise_strength` and `seed`. Baked maps are resolved in the target texture set; they are
not exported as project map records. A baked map explicitly assigned as a layer texture is bundled.

The importer accepts versions 1 and 2, at most 256 layers and 512 MiB uncompressed ZIP data.
Only directly contained `assets/*.exr` entries can satisfy asset references. Files are read by name
and written to generated temporary paths; archive paths are never extracted directly. Invalid input
raises an actionable error and rolls back newly created Blender data. Imported metadata is not executable.

See `src/open_painter/assets/smart_materials/` for complete human-readable examples.

Version 2 channels also store `opacity`, `blend_mode`, a procedural identifier and its scale, seed,
detail and signed height amplitude, plus an ordered `effects` collection and `active_effect` index.
Mask records with source FILTER store one nested `effect` parameter record; PROCEDURAL masks store
their source identifier. These effects operate on texture data, without shader effect graphs.
Old files without these fields use empty effect stacks and property defaults.

The authored built-in recipe schema in `smart_materials/catalog.py` is separate from this portable
format: the composer expands recipes into the persistent layer/channel/mask records before export.
