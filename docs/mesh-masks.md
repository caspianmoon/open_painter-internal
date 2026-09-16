# Mesh masks (0.9.2)

Painter users ask for a tree of the FBX meshes with checkboxes and work around its
absence with material IDs, geometry masks and opacity. Open Painter lives inside
Blender and owns the geometry, so a mask can simply *be* a Blender structure.

## Mask source: Mesh Mask

Layer masks gain the source **Mesh Mask** (`OP_Mask.mesh`, an `OP_MeshSelector`).
*Select By* chooses what drives the coverage:

| Kind | Coverage | Inputs |
| --- | --- | --- |
| Object | every assigned face of one object | Object |
| Collection | every assigned object in a collection, child collections included; link another screw to the collection and the mask follows | Collection, Include Child Collections |
| Material | faces whose slot holds a material — now, or before this texture set took the slot over (the imported material ID) | Material |
| Vertex Group | group weights interpolated across faces | Group name |
| Face Set | one sculpt face set | Face set id |
| Face Selection | the current face selection of the assigned meshes | — |
| UV Island | the islands containing the selected faces, or one island by index | Object, Island |
| Normal Direction | faces within an angle of a direction, with a soft falloff past it | Direction, Angle, Softness |
| Geometry Attribute | a named point, face or corner attribute: float, int, bool, or the length of a vector | Attribute name |

Coverage is exact: the assigned faces are rasterized through the render UVs at the
set resolution (`evaluation/mesh_masks.raster`), per face for membership kinds and
interpolated per corner for weights, attributes and normals. The usual mask
controls apply on top — black/white levels, invert, strength, combine mode — so a
collection mask multiplied by a generator restricts wear to the screws:

```
Collection: Screws  →  Mesh Mask  →  Rust layer
```

## Staying live

`mesh_masks.signature` names everything the coverage depends on — the target
list, collection membership, slot materials and original materials, mesh epochs
and vertex-group or attribute presence, selection flags, transforms for normal
kinds. The CPU stack caches the raster under that signature; the shader samples it
as a cached boundary image keyed by the same signature. The depsgraph handler now
treats collection, material and object updates as relevant, and `dependencies.refresh`
invalidates a set whose mesh-mask signatures changed — so linking an object to the
collection, reassigning a slot or editing weights re-evaluates the mask without a
rebake or manual refresh. Face selection in object mode does not raise a depsgraph
update on its own; entering or leaving edit mode does.

The Open Graph mask node shows the selector kind and its main input. Portable
materials store the selector's names, not the datablock pointers; on import the
selector re-links by name (`resolve`).

## Tests

`tests/integration/mesh_masks.py`: a body and two screws share one texture set;
a Collection mask covers only the linked screw and follows a `collection.objects.link`;
each kind is checked on planted data (original slot material, a vertex group, a
face attribute, a face set, selection, islands, ±Z normals); the shader boundary
matches the CPU reference exactly off the island edge band; a simulated depsgraph
update re-evaluates the live session; a saved material keeps the collection name and
re-links it.
