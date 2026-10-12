# Smart material format, version 5

A `.openpaintmaterial` file is either UTF-8 JSON (for constant/procedural-only materials) or a ZIP:

```
material.json
assets/0000.exr
assets/0001.exr
```

`material.json` has `format_version: 5`, `name`, an ordered `layers` array, and optional
`surface` scalar parameters (`anisotropy`, `coat_weight`, `coat_roughness`,
`height_distance`, `ior`, `transmission_weight`, `subsurface_weight`,
`subsurface_scale`, `sheen_weight`, `sheen_roughness`). IOR is finite in [1, 4],
subsurface scale in [0, 10] metres, height distance non-negative and every other value in
[0, 1]. The optical settings drive the material's Principled BSDF and stay editable
texture-set properties; opacity is independent of transmission. Applying a material
without them restores IOR 1.5, no transmission, subsurface or sheen, a 0.001 m
subsurface scale and sheen roughness 0.5. Texture exports do not encode them: set them
in the target renderer. Missing `height_distance` preserves the target texture set's
existing setting. Versions 1–4 remain readable. Each layer has
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

Masks support `source` (PAINT/BAKED/GENERATOR/NOISE/FILL/PROCEDURAL/FILTER/SCENE/REFERENCE/ANCHOR), `image`, `map_type`, `generator`,
`enabled`, `operation` (MULTIPLY/ADD/NORMAL), `invert`, `black`, `white`, `strength`, `amount`, `contrast`,
`noise_scale`, `noise_strength`, `seed`, `edge_width`, the REFERENCE fields (`reference_layer`, `reference_mask`,
`reference_channel`, `reference_stage`, `reference_anchor`) and the generator micro-detail fields
(`detail_height_anchor`, `detail_normal_anchor`, `detail_height_strength`, `detail_normal_strength`, `detail_radius`). Baked maps are resolved in the target texture set; they are
not exported as project map records. A baked map explicitly assigned as a layer texture is bundled.

The importer accepts versions 1 to 5, at most 256 layers and 512 MiB uncompressed ZIP data.
Only directly contained `assets/*.exr` entries can satisfy asset references. Files are read by name
and written to generated temporary paths; archive paths are never extracted directly. Invalid input
raises an actionable error and rolls back newly created Blender data. Imported metadata is not executable.

See `src/open_painter/assets/smart_materials/` for complete human-readable examples.

Version 2 channels also store `opacity`, `blend_mode`, a procedural identifier and its scale, seed,
detail and signed height amplitude, plus an ordered `effects` collection and `active_effect` index.
Mask records with source FILTER store one nested `effect` parameter record; PROCEDURAL masks store
their source identifier. These effects operate on texture data, without shader effect graphs.
Old files without these fields use empty effect stacks and property defaults.

Version 3 adds anchor points. A mask with `source: ANCHOR` and a channel effect with `kind: ANCHOR`
and an `id` publish the stack prefix before them; consumers store that id in a mask's `reference_anchor`,
`detail_height_anchor` or `detail_normal_anchor`, or in a channel's `anchor`. Import assigns fresh ids to
anchors and remaps consumers; an id that does not resolve is kept verbatim and reported as a missing anchor
after import, while a reference cycle is refused and rolled back. Add-ons older than 0.2.9 cannot read version 3.

The authored built-in recipe schema in `smart_materials/catalog.py` is separate from this portable
format: the composer expands recipes into the persistent layer/channel/mask records before export.

Open Painter 0.13.1 also supports data-only smart profiles on version 4 modules. A root
folder's `master_controls` JSON string contains `version: 1`, `required_maps` and one
to eight `controls` using the native `Control`/`Member` vocabulary. Targets have stable
UUID5 identities under the module UUID: `layer:<label>`, `mask:<label>:<index>` and
`effect:<label>:<channel>:<index>`. Controls survive namespacing, rename, duplicate,
save/import and module updates. Only whitelisted numeric/color properties are allowed;
profiles cannot execute code. Missing required bakes are reported before insertion.

A channel's optional `mesh_map` names its destination texture-set bake (for example
`NORMAL`). Its image is resolved on insertion and after rebaking, and omitted from saved
archives. This prevents a saved material from carrying the donor object's normal map.
Installed profiled modules appear in the Smart Materials shelf and material picker;
the module data and textures are distributed separately from the core add-on.

## Version 5 authoring data

Stroke records retain the existing packed float32 sample rows and optional surface
anchors. `payload_version: 1` and `dab_payload` add a JSON object with `version: 1`
and exactly one `dabs` record per sample. Records contain resolved material/brush
`values`, an optional world-space smudge `donor`, and an optional reflected
`planar` direction. Import validates dimensions, finite numbers and supported
fields. `brush_graph_data` contains the frozen graph program and stable graph/node
IDs for provenance; evaluation uses the resolved samples. Particle contacts use
ordinary samples and never require simulation during replay or export.

TEXT masks contain a `text` settings record with `schema_version: 1`: content, font path, normalized size,
alignment, character/line spacing, position and the generated image asset. Font
files are not bundled. A missing font retains the saved image. Native Mask Graphs
are portable through `graph_data` version 1: an allowlisted node list, custom
settings, input values and links. Text nodes include their generated image assets.
Legacy Blender shader graphs still require baking before material export.

In blend files, linked UDIM sets carry `shared_stack_version: 1`, `shared_stack_id` and `shared_stack_owner`.
The owner holds the authoritative recipe; tile collections materialize that recipe
using matching layer IDs while retaining their own images, baked maps, resolution
and caches. Material export captures the selected tile's recipe and pixel assets;
import creates an independent material with fresh IDs. Existing blend files with
empty sharing markers retain independent tile stacks.
