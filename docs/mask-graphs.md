# Mask Graphs

A **Custom Graph** mask takes its coverage from a **Mask Graph**, built from Open Painter's
own node types. It's edited in the **Open Painter Graph** editor, the one Open Painter node
editor: a Mask Graph is an Open Painter Graph tree marked as one. The [Open Painter
engine](open-painter-engine.md) evaluates it as one GPU pass. The CPU reference (export,
2D snapshots, the CPU backend) evaluates it with the same formulas. No Blender shader group
is built, and editing a graph never touches a material.

## Authoring

- A new Custom Graph mask starts with Coordinates → Noise → Ramp → Mask Output.
- **Edit Mask Graph** steps into the graph inside the Open Painter Graph editor, the way you
  enter a node group. It's in the mask's panel in the Layers view and on the Custom Graph
  node in the texture set's graph.
  - The breadcrumb shows the texture set's graph above it.
  - **Back to Texture Set Graph** in the header, or Tab, returns to it.
  - With no graph editor open, Edit Mask Graph opens one on half of the current area.
- Shift+A in the editor adds nodes from the Input, Texture, Converter and Output menus.
- The Open Graph templates *Noise → Ramp*, *Cells → Ramp* and *Layered Noise → Ramp*
  create Mask Graphs. Each template owns its graph. Picking the same graph on another
  Custom Graph mask shares it deliberately.
- A graph has exactly one **Mask Output**; its *Coverage* input is the mask's value.

| Node | Settings | Sockets |
| --- | --- | --- |
| Coordinates | — | → UV, Position, Normal (world space, from the assigned meshes) |
| Value | value | → Value |
| Noise | FBM, Perlin, Simplex, Value, Fractal, Cloud, Ridged, Turbulence, Multi Fractal, Directional, Anisotropic, Warped; scale, seed | Vector → Value |
| Cells | Voronoi, Cellular, Worley; scale, seed | Vector → Value |
| Pattern | Checker, Stripes, Dots, Grid, Pattern, Tile, Brick and Hex Pattern, Gradient, Linear and Radial Gradient; scale, seed | Vector → Value |
| Surface | every other catalog source the engine runs on the GPU (grunge, wear, grain, marks); scale, seed | Vector → Value |
| Mesh Map | AO, Curvature, Thickness, Position, UV Border, Mean Curvature, Selection, Bevel Mask (the set's baked maps) | → Value |
| Math | Add, Subtract, Multiply, Divide, Power, Minimum, Maximum, Less Than, Greater Than, Modulo, Snap, One Minus, Absolute, Square Root, Round, Floor, Ceil, Fraction, Sine, Cosine; Clamp | A, B → Value |
| Mix | Mix, Multiply, Screen, Overlay, Add, Subtract, Difference, Lighten, Darken; Clamp | Factor, A, B → Value |
| Ramp | Linear, Ease, Constant; stops (position, value) | Fac → Value |
| Levels | black, white, gamma, output black, output white | Value → Value |
| Mapping | offset, rotation, scale (about the UV centre) | Vector → Vector |
| Mask Output | — | Coverage |

Linking rules:

- An unlinked Vector input reads the UV.
- A reroute passes its input through.
- A muted node passes its first input of the same kind.
- A Math operation with one input hides B.
- Value sockets link to value sockets and Vector to Vector. A mismatched link, a loop, a
  missing or second Mask Output, or a node from another tree type makes the graph
  invalid. The set's engine status then says why and the last result stays on screen.
  Fixing the graph resumes rendering.

## How it is evaluated

`procedural_graph/program.py` is the only code that walks the tree. It reads the tree into
ordered steps, and both evaluators run those same steps:

- `gpu.py` emits the steps as engine compiler nodes.
- `cpu.py` runs them in NumPy float32, operation by operation with the same formulas. That
  includes Blender Math's safe division and its compatible power and modulo.
- Procedural sources use the catalog's integer hash, so the two paths agree to the last bits
  of transcendental functions. `tests/integration/mask_graphs.py` compares every node type,
  operation, blend and ramp mode:
  - sources and converters agree within 2.5·10⁻⁵;
  - baked mesh maps agree within 1.2·10⁻⁴, which is bilinear sampling of the map.

In the engine a Mask Graph is a unit of its own, `('GRAPH', layer, mask)`: one pass whose
result the mask samples. It is never compiled into the mask that reads it. That has two
consequences:

- An edit elsewhere in the stack doesn't render the graph again.
- Profile Engine reports the graph's cost on its own line (see
  [Layer performance](layer-performance.md#engine-report)).

Every number is a uniform. Dragging a node's slider, an unlinked socket's value or a ramp
stop compiles nothing. A structural edit rebuilds and recompiles the graph's unit alone:

- adding, removing or relinking a node;
- changing a kind, an operation, a blend, Clamp or the interpolation;
- adding or removing a ramp stop, or moving a stop past another.

## How edits reach the engine

- **Property edits** notify through their update callbacks, as numeric or structural.
- **Tree edits** (nodes, links, mutes) arrive through `NodeTree.update`.
  - That callback compares the graph's unit signature: its structure plus which nodes carry
    it. Moving a node or adding an unlinked one changes nothing. Replacing a node with an
    identical one rebuilds the unit.
  - Blender calls `update` before it validates a new link, so `program.py` ignores
    `NodeLink.is_valid` and checks socket types and loops itself.
- **Removed nodes.** The engine's uniforms are bound to graph nodes, sockets and ramp stops.
  Before reading any of them, the engine checks that each is still in the graph with the
  same RNA type. When a node is removed, a ramp stop array is reallocated or the whole graph
  is deleted, the engine rebuilds the unit instead of reading freed memory. Deleting a graph
  fires no callback, so this check is what covers that case.

## Masks from earlier versions

A Custom Graph mask saved by an earlier version points at a Blender shader group:

- It keeps working through the CPU reference, a Cycles bake over the assigned meshes cached
  by the tree's content.
- **Edit Shader Group** opens it in the Shader Editor.

New masks and templates always create Mask Graphs. Saving a smart material with a Custom
Graph mask is still refused, because graphs don't travel with materials.

## Tests

- `tests/integration/mask_graphs.py` covers:
  - GPU/CPU parity for every source, operation, blend and ramp mode, levels and mapping;
  - numeric edits compiling nothing, and structural edits rebuilding one unit;
  - an invalid graph and its recovery;
  - save and reload;
  - templates and starter graphs;
  - removed nodes, reallocated stops and a deleted graph never being read.
- `tests/integration/graph_templates_ui.py` (real window): a template's graph is entered inside
  the texture set's Open Painter Graph editor, with no second editor. The older Edit Shader
  Group entry point finds it there, and Back returns to the texture set's graph.
- `tests/integration/mask_graphs.py` also checks that each kind of Open Painter Graph accepts
  only its own nodes. A graph saved as a tree type of its own by an earlier 0.13 build is
  rebuilt node for node when the file opens.
- `tests/integration/engine_stages.py`: a structural graph edit rebuilds only the graph's
  unit, and the profile times the graph's pass.
