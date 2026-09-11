# Blender Open Painter

Implementation-ready build plan for a Blender extension that provides a Substance Painter-style, non-destructive PBR texturing workflow inside Blender.

## 1. Product Goal

Build an open-source Blender extension that gives artists a fast, coherent texture-painting workflow built around:

- Texture Sets
- Multi-channel PBR layers
- Paint Layers
- Fill Layers
- Folders
- Masks
- Procedural generators
- Mesh-map baking
- Smart Materials
- Export presets

The extension should feel native to Blender while removing the need to manually manage large shader-node graphs for normal texturing work.

The v0.1 target is not full Substance Painter parity. The target is a stable and useful vertical slice that lets an artist:

1. assign a mesh to a texture set,
2. create a layer stack,
3. paint or fill multiple PBR channels,
4. attach masks,
5. bake Normal, AO, and Curvature maps,
6. use those maps in procedural masks,
7. save/reuse smart materials,
8. export game-ready texture sets.

## 2. Non-Goals for v0.1

Do not implement these yet:

- custom renderer
- custom UV editor
- procedural authoring comparable to Substance Designer
- particle brushes
- clone/heal tools
- path brushes
- custom GPU mesh baker from scratch
- distributed baking
- AI texture generation
- photogrammetry
- advanced decal system
- custom viewport engine
- complex UDIM authoring beyond basic support

These are later milestones.

## 3. Technical Constraints

Use Blender's extension/add-on system.

Primary language:

- Python

Use Blender APIs for:

- UI
- scene integration
- texture painting
- image creation
- material binding
- Cycles baking
- asset registration
- custom properties

Use GPU APIs only where useful and safe.

Do not require compiled native dependencies for v0.1.

Do not create a giant shader-node graph that directly mirrors every layer and mask operation.

The plugin must own its own texture-project data model and evaluate that data model into a controlled set of channel textures.

## 4. Repository Layout

Create the repository with this structure:

```text
blender-open-painter/
├── README.md
├── LICENSE
├── pyproject.toml
├── blender_manifest.toml
├── src/
│   └── open_painter/
│       ├── __init__.py
│       ├── registration.py
│       ├── constants.py
│       ├── preferences.py
│       ├── properties/
│       │   ├── __init__.py
│       │   ├── project.py
│       │   ├── texture_set.py
│       │   ├── layer.py
│       │   ├── mask.py
│       │   ├── channel.py
│       │   └── bake.py
│       ├── core/
│       │   ├── project_manager.py
│       │   ├── texture_set_manager.py
│       │   ├── layer_stack.py
│       │   ├── channel_manager.py
│       │   ├── image_manager.py
│       │   ├── material_manager.py
│       │   └── validation.py
│       ├── evaluation/
│       │   ├── evaluator.py
│       │   ├── blend_modes.py
│       │   ├── channel_eval.py
│       │   ├── mask_eval.py
│       │   └── cache.py
│       ├── painting/
│       │   ├── paint_session.py
│       │   ├── brush_bridge.py
│       │   ├── channel_paint.py
│       │   └── projection.py
│       ├── baking/
│       │   ├── bake_manager.py
│       │   ├── cycles_baker.py
│       │   ├── bake_jobs.py
│       │   ├── cage.py
│       │   └── maps/
│       │       ├── normal.py
│       │       ├── ao.py
│       │       └── curvature.py
│       ├── generators/
│       │   ├── base.py
│       │   ├── edge_wear.py
│       │   ├── dirt.py
│       │   ├── dust.py
│       │   ├── position_gradient.py
│       │   └── noise.py
│       ├── smart_materials/
│       │   ├── format.py
│       │   ├── serializer.py
│       │   ├── loader.py
│       │   └── library.py
│       ├── export/
│       │   ├── exporter.py
│       │   ├── channel_pack.py
│       │   └── presets/
│       │       ├── blender.py
│       │       ├── unreal.py
│       │       ├── unity.py
│       │       └── custom.py
│       ├── ui/
│       │   ├── workspace.py
│       │   ├── panels.py
│       │   ├── layer_list.py
│       │   ├── properties_panel.py
│       │   ├── baking_panel.py
│       │   ├── export_panel.py
│       │   └── operators.py
│       ├── assets/
│       │   ├── brushes/
│       │   ├── alphas/
│       │   ├── smart_materials/
│       │   └── generators/
│       └── utils/
│           ├── logging.py
│           ├── paths.py
│           ├── ids.py
│           └── blender_helpers.py
└── tests/
    ├── unit/
    └── integration/
```

## 5. Core Product Model

The plugin must define its own project data structure.

The hierarchy is:

```text
OpenPainterProject
└── TextureSet[]
    ├── resolution
    ├── assigned objects/material slots
    ├── baked maps
    └── LayerStack
        └── Layer[]
            ├── PaintLayer
            ├── FillLayer
            ├── FolderLayer
            └── Mask
```

### 5.1 Required PBR channels

Support these channels in v0.1:

- Base Color
- Roughness
- Metallic
- Normal
- Height
- Ambient Occlusion

Represent channels internally with stable enum IDs:

```python
BASE_COLOR
ROUGHNESS
METALLIC
NORMAL
HEIGHT
AO
```

Every layer can independently enable or disable each channel.

### 5.2 Layer base properties

Every layer must have:

```text
id
name
type
visible
opacity
blend_mode
channels
mask
parent folder
order
```

Supported blend modes for v0.1:

- Normal
- Multiply
- Add
- Subtract
- Overlay

Do not implement dozens of blend modes initially.

### 5.3 Paint Layer

A Paint Layer stores raster content per enabled channel.

For example:

```text
Layer: Scratches
Base Color: disabled
Roughness: enabled
Normal: enabled
Height: enabled
```

The plugin should allocate channel images lazily. Do not create six images for every layer unless required.

### 5.4 Fill Layer

A Fill Layer provides constant values or assigned textures for one or more channels.

For each channel allow:

```text
constant value
texture image
projection mode
scale
rotation
offset
```

Projection support in v0.1:

- UV
- Triplanar

Planar and camera projection are later milestones.

### 5.5 Folder Layer

Folders group child layers and provide:

- visibility
- opacity
- optional mask

Folder masks affect all child layers.

## 6. Data Persistence

All project state must survive saving and reopening the .blend file.

Use Blender PropertyGroups and stable UUID-style IDs.

Do not depend on Python module globals for persistent state.

Store project references in the scene and/or material data using Blender-supported property mechanisms.

Texture images can be:

- packed into the .blend, or
- stored in a managed project folder.

For v0.1, default to packed images where practical.

Add a project validation routine that detects:

- missing images
- missing objects
- invalid texture-set assignments
- missing baked maps
- broken layer references

## 7. Texture Set System

A Texture Set represents one logical set of PBR output maps.

Users must be able to:

- create a texture set
- rename it
- assign one or more mesh objects
- choose resolution
- remove objects
- duplicate texture sets
- delete texture sets

Supported resolutions:

```text
512
1024
2048
4096
8192
```

Default:

```text
2048
```

Texture sets must support meshes with multiple materials, but v0.1 may restrict one texture set to explicitly assigned material slots.

## 8. Material Integration

The extension should generate and manage one Blender material output setup per Texture Set.

The generated material should remain simple and predictable.

Use a controlled Principled BSDF graph:

```text
BaseColor Output -> Principled Base Color
Roughness Output -> Principled Roughness
Metallic Output -> Principled Metallic
Normal Output -> Normal Map -> Principled Normal
Height Output -> Bump -> Principled Normal
AO -> multiplied with BaseColor only in preview if enabled
```

Do not expose internal evaluation complexity through hundreds of nodes.

The plugin owns the generated nodes.

Mark managed nodes with custom properties so they can be safely found and rebuilt.

Provide an operator:

```text
Repair Open Painter Material
```

that recreates missing managed nodes without destroying unrelated user nodes when possible.

## 9. Evaluation Engine

This is the central technical subsystem.

The layer stack must be composited into one output image per channel.

Target outputs:

```text
<TextureSet>_BaseColor
<TextureSet>_Roughness
<TextureSet>_Metallic
<TextureSet>_Normal
<TextureSet>_Height
<TextureSet>_AO
```

The evaluator must:

1. walk layers from bottom to top,
2. skip invisible layers,
3. calculate the effective mask,
4. calculate layer opacity,
5. composite each active channel,
6. update the texture-set output image,
7. refresh the managed material.

### 9.1 Evaluation architecture

Start with correctness rather than ultimate speed.

Use Blender images plus NumPy if available in Blender's Python environment.

Keep evaluation modular so GPU evaluation can replace CPU composition later.

Interface example:

```python
class LayerEvaluator:
    def evaluate_texture_set(self, texture_set_id: str) -> None:
        ...

    def evaluate_channel(self, texture_set_id: str, channel: str) -> Image:
        ...
```

### 9.2 Dirty-state system

Do not recompute the entire project after every property edit.

Track dirty state at minimum by:

```text
texture_set
channel
layer
```

Examples:

Changing a roughness value should not rebuild Base Color.

Changing a mask on a layer should dirty all enabled channels on that layer and every layer above it that depends on the result.

Implement debounced evaluation for slider edits.

## 10. Mask System

Every layer and folder may have one mask stack.

The mask resolves to grayscale 0..1.

Support these mask sources in v0.1:

- painted mask
- baked map
- generator
- procedural noise

Support these mask operations:

- invert
- levels
- multiply
- add

The internal design should allow future node-graph masks, even if v0.1 presents a simplified stack UI.

## 11. Procedural Generators

Implement the generator abstraction first.

Interface:

```python
class Generator:
    id: str
    name: str

    def evaluate(self, texture_set, parameters):
        ...
```

Required built-in generators:

### Edge Wear

Inputs:

- curvature map
- optional AO
- procedural noise

Parameters:

```text
wear amount
contrast
noise scale
noise strength
invert
```

### Dirt

Inputs:

- AO
- curvature
- noise

Parameters:

```text
amount
contrast
noise scale
```

### Dust

Inputs:

- world-space normal or normal approximation
- noise

Parameters:

```text
direction threshold
amount
noise scale
```

### Position Gradient

Inputs:

- baked position data if available

Parameters:

```text
axis
start
end
invert
```

If Position is not baked in v0.1, this generator may be deferred to v0.2.

## 12. Baking System

The user must not have to manually configure Blender's bake settings for normal texture workflows.

Provide a dedicated Bake Mesh Maps panel.

UI:

```text
High Poly Objects
Low Poly / Texture Set
Resolution
Margin
Cage Mode
Ray Distance

[ ] Normal
[ ] AO
[ ] Curvature

[ BAKE SELECTED MAPS ]
```

### 12.1 High/low assignment

Support explicit:

```text
high-poly object list
low-poly texture-set object list
```

Do not rely purely on selection order.

### 12.2 Normal Bake

Wrap Blender/Cycles selected-to-active baking.

Automate:

- active object
- selected objects
- bake image creation
- image-node setup
- selected-to-active toggle
- ray distance
- margin
- tangent normal configuration

Restore relevant user state after the bake.

### 12.3 AO Bake

Bake AO into a managed texture-set map.

### 12.4 Curvature Bake

Blender does not directly provide the exact Substance curvature workflow as a one-click output.

Implement v0.1 curvature by deriving it from one of these sources, in priority order:

1. baked normal map image processing,
2. geometry-based vertex/face curvature approximation projected to UV,
3. another reliable Blender-native method.

Keep curvature generation isolated behind:

```python
bake_curvature(texture_set)
```

so its implementation can be replaced later.

### 12.5 Cage handling

Support:

```text
None
Automatic Distance
Custom Cage Object
```

For automatic distance, provide a simple value first.

Later versions may estimate cage distance from object bounds.

## 13. Painting Integration

Do not build a completely new brush engine in v0.1.

Bridge Blender Texture Paint.

The plugin should manage which underlying channel image receives strokes.

Provide a Paint Layer mode where the user chooses active channels.

For v0.1, support painting one channel at a time if multi-channel simultaneous painting proves too invasive.

However, architecture must support later multi-channel stroke replication.

Required brush controls surfaced in the Open Painter panel:

- size
- strength
- spacing
- alpha texture
- color/value

Do not recreate every Blender brush property.

## 14. Workspace and UI

Create a dedicated workspace preset called:

```text
Open Painter
```

The exact arrangement can evolve, but provide these primary panels:

### Left side

Assets

- Materials
- Smart Materials
- Brushes
- Alphas

### Center

3D Viewport

### Right side

Layer Stack

### Lower/right properties

Contextual properties for:

- selected layer
- selected mask
- generator
- texture set

### Tabs or panels

Provide easy access to:

```text
Layers
Texture Set
Bake
Export
```

Do not attempt to perfectly clone Substance Painter's UI.

The extension should look and behave like Blender.

## 15. Layer UI

Use a Blender UIList for the stack.

Each row must show:

```text
visibility toggle
layer icon
name
mask indicator
```

Operators:

```text
Add Paint Layer
Add Fill Layer
Add Folder
Add Mask
Duplicate
Delete
Move Up
Move Down
Rename
```

Support drag reorder if practical. If drag reorder becomes disproportionately complex, provide move buttons in v0.1.

## 16. Smart Materials

Smart Materials are serialized layer stacks.

Create a versioned file format.

Recommended:

```text
.openpaintmaterial
```

Use JSON for metadata.

Example conceptual structure:

```json
{
  "format_version": 1,
  "name": "Painted Steel",
  "layers": [
    {
      "type": "fill",
      "name": "Steel",
      "channels": {
        "base_color": [0.08, 0.09, 0.1, 1.0],
        "roughness": 0.28,
        "metallic": 1.0
      }
    },
    {
      "type": "fill",
      "name": "Edge Wear",
      "mask": {
        "generator": "edge_wear"
      }
    }
  ]
}
```

If assets such as textures or alphas are required, package the material as a ZIP container with a custom extension.

Do not embed large textures as base64 in JSON.

Required operators:

```text
Save Layer Stack as Smart Material
Import Smart Material
Apply Smart Material
Delete Local Smart Material
```

Include at least three built-in demo materials:

- Painted Steel
- Dirty Plastic
- Rough Wood

These only need enough quality to validate the system.

## 17. Export System

Provide an Export Textures panel.

Inputs:

```text
output directory
resolution override
file format
bit depth where relevant
preset
```

Supported image formats in v0.1:

- PNG
- TIFF if straightforward

Required presets:

### Blender

```text
BaseColor
Roughness
Metallic
Normal
Height
AO
```

### Unreal Engine 5

```text
BaseColor
Normal
ORM
```

ORM packing:

```text
R = AO
G = Roughness
B = Metallic
```

### Unity

Implement one clearly named preset, preferably Metallic workflow.

Document exact packing.

### Custom

Allow channel assignment to:

```text
R
G
B
A
```

## 18. File Naming

Default pattern:

```text
{texture_set}_{channel}.{ext}
```

Example:

```text
Robot_BaseColor.png
Robot_Normal.png
Robot_ORM.png
```

Allow a prefix and suffix.

## 19. Undo and Safety

All operators that change Blender data should support Blender undo where reasonably possible.

Before destructive operations:

- validate references
- do not overwrite user images unexpectedly
- generate unique managed names

Never delete a Blender material, image, object, or node unless it is clearly marked as owned by Open Painter or the user explicitly requested deletion.

Managed data blocks must have a custom property such as:

```python
["open_painter_managed"] = True
```

## 20. Performance Requirements

v0.1 does not need to match Substance Painter performance.

It must, however, remain usable at:

```text
2K texture
20 layers
6 channels
```

Target interactive edits:

- parameter changes should update within roughly one second for ordinary 2K projects where possible
- painting should not trigger full evaluation after every individual dab
- defer expensive recomposition until stroke end or a short debounce interval

Implement caching early.

## 21. Error Handling

Do not fail silently.

Create a central logger.

UI errors should explain actionable problems such as:

```text
No active texture set.
No UV map found on ObjectName.
Curvature generator requires a baked curvature map.
High-poly source list is empty.
Texture image is missing.
```

Do not expose Python tracebacks to ordinary users unless developer mode is enabled.

## 22. Developer Mode

Add an add-on preference:

```text
Developer Mode
```

When enabled:

- verbose logging
- IDs in UI
- evaluation timing
- rebuild buttons
- data validation output

This will make the system much easier to debug while it is young.

## 23. Testing

Create automated tests wherever Blender allows reliable testing.

At minimum test:

### Data model

- create texture set
- create layer
- reorder layer
- delete layer
- parent layer to folder
- save/reload IDs

### Evaluation

- normal blend
- multiply blend
- opacity
- masks
- disabled channels

### Export

- correct file naming
- ORM channel packing
- correct dimensions

### Serialization

- smart material export/import round trip

### Integration tests

Use Blender background mode where practical.

Create a simple test scene containing:

```text
Cube_LP
Cube_HP
```

and test bake setup automatically.

## 24. Implementation Phases

Codex should execute these phases sequentially.

Do not jump ahead until the acceptance criteria for the current phase are satisfied.

# Phase 0: Extension Skeleton

Implement:

- manifest
- registration
- preferences
- package structure
- logging
- developer mode
- empty Open Painter panels

Acceptance criteria:

- extension installs without errors
- extension enables/disables cleanly
- no registration leaks after reloading
- Open Painter panel appears

# Phase 1: Project + Texture Sets

Implement:

- project PropertyGroups
- texture sets
- object/material assignment
- resolution setting
- stable IDs
- persistence

Acceptance criteria:

- create texture set
- assign cube
- save .blend
- reopen .blend
- assignment remains valid

# Phase 2: Layer Stack

Implement:

- Paint Layer metadata
- Fill Layer
- Folder
- visibility
- opacity
- ordering
- blend modes
- per-channel toggles
- UIList

Acceptance criteria:

- user can build and reorder a 10-layer stack
- data survives reload
- folders correctly contain children

# Phase 3: Channel Outputs + Evaluation

Implement:

- managed output images
- evaluator
- Normal/Multiply/Add/Subtract/Overlay
- dirty tracking
- material binding

Acceptance criteria:

A Fill Layer changing Base Color visibly changes the model without manual node editing.

Two layers blend correctly.

Changing Roughness does not recompute unrelated Base Color output.

# Phase 4: Masks

Implement:

- painted masks
- mask stack model
- invert
- levels
- multiply

Acceptance criteria:

- user can mask a Fill Layer
- mask affects all enabled channels on that layer
- mask survives reload

# Phase 5: Mesh Baking

Implement:

- bake panel
- explicit high/low assignment
- Normal
- AO
- Curvature
- automated Cycles setup
- restore Blender state

Acceptance criteria:

With provided HP/LP cube test meshes:

- pressing one button produces Normal and AO images
- images are registered on the texture set
- curvature can be generated
- user does not need to create image nodes manually

# Phase 6: Generators

Implement:

- generator abstraction
- Edge Wear
- Dirt
- procedural Noise

Acceptance criteria:

Applying Edge Wear to a mask visibly selects exposed curved areas using the baked curvature map.

Applying Dirt produces concentration in occluded areas using AO.

# Phase 7: Smart Materials

Implement:

- serializer
- loader
- library UI
- three demo materials

Acceptance criteria:

- user can save a custom layer stack
- restart Blender
- apply material to another texture set
- hierarchy and parameters match

# Phase 8: Painting Workflow

Implement:

- create Paint Layer channel images
- enter Blender Texture Paint automatically
- choose target channel
- expose core brush settings
- refresh composite after stroke

Acceptance criteria:

- user creates Paint Layer
- selects Roughness
- paints in viewport
- final roughness output updates automatically

# Phase 9: Export

Implement:

- Blender preset
- Unreal preset
- Unity preset
- custom channel packing
- naming

Acceptance criteria:

UE5 export generates:

```text
Asset_BaseColor.png
Asset_Normal.png
Asset_ORM.png
```

with correct ORM channel assignment.

# Phase 10: UX Stabilization

Fix:

- panel hierarchy
- context handling
- selection syncing
- invalid states
- undo bugs
- stale image issues
- performance bottlenecks

Add:

- tooltips
- empty states
- confirmations only where destructive
- demo file

## 25. First Release Definition

Tag `v0.1.0` only when a user can complete this workflow from a fresh Blender scene:

```text
Import LP mesh
Import HP mesh
↓
Create Texture Set
↓
Assign LP mesh
↓
Assign HP bake source
↓
Bake Normal + AO + Curvature
↓
Create base Fill Layer
↓
Create second material layer
↓
Add Edge Wear mask
↓
Create Paint Layer
↓
Paint roughness manually
↓
Save Smart Material
↓
Export Unreal textures
```

without manually manipulating Blender shader nodes or manually configuring Cycles bake nodes.

## 26. UX Principles

When deciding between two implementations, use these principles:

1. Artists should manipulate artistic concepts, not Blender internals.
2. Common operations should take one or two clicks.
3. Do not expose unnecessary shader-node complexity.
4. The .blend must remain recoverable even if the add-on is disabled.
5. Do not silently destroy user data.
6. Prefer explicit state over selection-order magic.
7. Prefer simple and stable over clever.
8. Every expensive operation needs caching or dirty-state tracking.
9. The UI should feel native to Blender.
10. Substance Painter is a workflow reference, not a UI to copy pixel-for-pixel.

## 27. Coding Rules for Codex

When implementing this repository:

- Keep modules small.
- Do not put the whole add-on in `__init__.py`.
- Add type hints to non-trivial functions.
- Add docstrings to public classes and core systems.
- Avoid circular imports.
- Centralize registration.
- Do not hardcode scene object names.
- Use stable generated IDs.
- Do not store critical state only in Python memory.
- Add logging around baking and evaluation.
- Do not catch exceptions with empty `except` blocks.
- Do not destroy user data to resolve an error.
- Prefer operators for user-triggered mutations.
- Keep evaluation logic independent from UI code.
- Keep Blender-specific access behind managers/helpers where practical.

## 28. Required Documentation

Before v0.1, write:

```text
README.md
CONTRIBUTING.md
ARCHITECTURE.md
SMART_MATERIAL_FORMAT.md
```

README must include:

- what the project is
- installation
- supported Blender version
- first-use workflow
- limitations
- screenshots placeholder

ARCHITECTURE.md must explain:

```text
Project data model
Layer evaluation
Image ownership
Material ownership
Baking
Caching
Serialization
```

## 29. Future Roadmap After v0.1

Do not implement these until v0.1 is stable.

### v0.2

- better curvature baker
- world normal
- position
- thickness
- Material ID
- Object ID
- more generators
- more filters
- simultaneous multi-channel brush strokes
- improved triplanar

### v0.3

- UDIM workflow improvements
- mask node graph
- filter layers
- blur/sharpen
- histogram scan
- directional warp
- stencil projection
- planar projection

### v0.4

- GPU evaluation
- custom GPU baker experiments
- high-performance live compositing
- better large-texture performance

### v1.0

A stable Blender-native alternative for the most common Substance Painter workflows:

```text
Bake
Layer
Mask
Generate
Paint
Reuse
Export
```

## 30. Codex Starting Instruction

Start with Phase 0 and Phase 1 only.

Do not scaffold fake implementations for later phases unless an interface is required by current code.

After Phase 1:

1. run all tests,
2. verify Blender registration/unregistration,
3. create a minimal sample `.blend` workflow,
4. document architectural decisions,
5. then proceed to Phase 2.

Every phase should leave the repository in a runnable state.

Do not treat placeholders or TODO comments as completion.

The final objective is an artist-usable tool, not merely an add-on whose Python modules import successfully.
