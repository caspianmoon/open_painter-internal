The big one: make Open Painter a truly live Blender-native texturing system

The most compelling differentiator would be:

A texture is not a stack of pixels and effects. It is a live Blender graph attached to the actual asset.

Think Geometry Nodes / Shader Editor, but specifically designed for PBR texturing, while keeping the familiar Painter-style layer UI.

This is almost exactly the gap people keep asking Adobe to solve.

Adobe users are explicitly requesting a node-based Painter because complex linear layer/effect stacks become difficult to understand and maintain. One current request asks for hierarchical/nested effects for exactly this reason. Another asks outright for a node texturing window alongside the layer stack.

And Adobe's position historically has been that Painter and Designer intentionally separate painting/layers from full procedural node authoring.

Open Painter doesn't have to respect that separation.

What I would build

You still have:

Layers

Fill Layer
Paint Layer
Material
Folder
Mask

But there is a button:

Open Graph

And suddenly:

                 ┌─ Curvature ─ Levels ─ Edge Wear ─┐
Steel Material ──┤                                   ├─ Mask ─ Layer
                 └─ AO ─ Noise ─ Dirt ──────────────┘

Except this is not merely a visual representation of the layer stack.

You can actually branch things.

                       ┌→ Roughness processing
Material → Grunge ─────┼→ Height processing
                       ├→ Color variation
                       └→ Mask → Edge Damage

Outputs can feed other layers, masks and materials.

You could expose a graph as a reusable Smart Material:

              SMART MATERIAL
                    │
       ┌────────────┼────────────┐
       ↓            ↓            ↓
     Paint      Procedural     Geometry
       │            │            │
       └────────────┼────────────┘
                    ↓
               PBR OUTPUT

That effectively combines the parts people want from Painter and Designer without forcing them into separate applications.

But Blender gives us an even bigger advantage

This is where I think the actual WOW capability is.

Geometry-Aware Materials

Substance Painter mostly understands the asset through baked maps such as curvature, AO, position, normals, thickness, etc.

Open Painter lives inside Blender.

So don't restrict the material system to texture information.

Let the graph query the actual scene and geometry.

For example:

Geometry
   ↓
Faces pointing upward
   ↓
Within 3cm of another object
   ↓
Concave region
   ↓
Add dirt

Or:

Distance to ground
      +
Ambient occlusion
      +
Object orientation
      ↓
Mud accumulation

Or:

Sharp edges
    ↓
Edge exposure
    ↓
Procedural scratches

But also:

Distance to Bolt objects
        ↓
Radial rust

Now imagine a game artist textures a tank.

They add a bolt.

The rust updates.

They move a panel.

The dirt updates.

They bevel an edge.

The edge wear updates.

They change the mesh.

The material updates.

No export, rebake, reimport loop.

That is a real advantage from being inside Blender.

There is clearly pain around this workflow. A recent tool posted by a user exists specifically to automate Blender → Painter → bake → export → Blender because repeating the roundtrip after geometry changes is tedious.


Another huge one people actually complain about
Paint across EVERYTHING

This one is surprisingly important.

Painter can paint across UDIMs, but cannot paint across Texture Sets. Adobe's product manager explicitly confirms that limitation.

People run into exactly the obvious problem:

"I want to draw a tattoo across the hip where two texture sets meet."

And Painter makes the workflow awkward.

There are also long-running requests around merging texture sets at export. Adobe currently has a feature request specifically for this.

Open Painter should simply not care.

Universal Canvas

You paint on the object, not "Texture Set 03."

A brush stroke can cross:

Material A
     ↓
──────────── seam
     ↓
Material B
     ↓
──────────── UDIM
     ↓
Material C

and remain one editable stroke.

Open Painter figures out where the pixels need to go.

That sounds small until someone actually uses it. Then going back to Painter feels primitive.

Another thing users are asking for: actual hierarchy

Painter's effect system is fundamentally linear enough that users are now requesting nested effects.

Don't just implement nested effects.

Implement material modules.

For example:

CAR PAINT
│
├── Base Coat
│
├── Flakes
│
├── Clear Coat
│
└── Damage
    │
    ├── Scratches
    ├── Edge Wear
    ├── Dirt
    └── Rust

Double-click Damage:

Curvature ───────┐
                 ↓
Noise → Levels → Edge Mask → Scratch Scatter
                 ↑
AO ──────────────┘

And "Damage" can be saved as its own reusable module.

You could drag Damage onto 50 different smart materials.

Change Damage v2.

All materials using it can update.

That's closer to procedural software development than Photoshop layers.


Another Blender-only opportunity: actual geometry generation from textures

Why should height always be fake?

Add:

Material → Geometry

Suppose someone creates:

welds
bolts
stitching
scales
rocks
bark
damage
panel seams

Open Painter could offer:

Convert Detail → Geometry

For example:

Painted weld mask
       ↓
Geometry Nodes
       ↓
Actual weld geometry

Or:

Stitch mask
    ↓
Curve extraction
    ↓
Geometry Nodes
    ↓
Actual stitches

Painter cannot compete with Blender's geometry system here.

And importantly, this is useful for game artists because you could then decide:

Keep as geometry

or

Bake back to normal/height

So Open Painter becomes part of the high-poly creation workflow too.

Another one: automatic game-engine truth preview

There's still friction between what an artist sees while authoring and what they eventually see in Blender or their engine. Users ask about Painter → Unreal live linking, and recent Blender/Substance users still report color-management mismatches.

Because Open Painter is already in Blender, we can do something cleaner.

Target Renderer

At the top:

Target:

Blender | Unreal 5 | Unity HDRP | Unity URP | Godot | Custom

Selecting Unreal could automatically configure:

DirectX normals
channel packing
roughness convention
metallic convention
color spaces
texture compression preview
OpenPBR/Principled mapping
export naming
texture resolution
LOD texture behavior

Then:

Engine Preview

Show approximately how the asset will behave under the target engine's expected material configuration before export.

That turns export from a bunch of settings into:

Export → Unreal 5

One more potentially killer feature
Resolution-independent procedural painting

Treat strokes as data rather than immediately destroying them into pixels whenever possible.

Instead of:

brush → pixels

store:

Stroke
├─ spline
├─ pressure
├─ brush
├─ material
├─ seed
├─ projection
└─ parameters

Then rasterize at whatever resolution is required.

So an artist textures at 1K for speed.

Later:

Output Resolution → 8K

Procedural layers, paths, decals, masks and compatible brush strokes rerender at 8K.

Painter has become much better at paths, and users specifically cite its newer path tools as one of the most valuable upgrades.

We should go beyond paths and make most authored content resolution-independent whenever technically possible.