# Mask architecture (0.9.3)

Painter's *main mask* concept mixes painted data, fill values, blending and strokes
into one slab that is hard to take apart later. Open Painter's mask stack never had
that slab, and 0.9.3 removes the last limitation — it was linear.

## Everything is a source

A layer's mask is an ordered stack of entries. Each entry is one **source** with its
own **operation** (Multiply, Add, Normal, Lighten/Max, Darken/Min), **strength**,
**black/white levels** and **invert** — nothing is baked into anything else until you
ask for it:

```
MASK
 ├── Painted        (its own image; paint it again any time)
 ├── Curvature      (Generator or Baked Map)
 ├── Noise          (Procedural)
 ├── Vertex Group   (Mesh Mask)
 ├── AO             (Baked Map)
 ├── Geometry       (Mesh Mask: object, collection, material, selection, island, normal, attribute)
 ├── Scene Field    (live analysis)
 ├── Vector Strokes (editable strokes)
 ├── Filter         (Levels, blur, … applied to the coverage so far)
 ├── Published Input / Anchor (masks feeding other layers)
 ├── Mask Group     (a nested stack)
 └── Custom Graph   (your own Mask Graph: Open Painter nodes)
```

Painted data stays a separate entry with its own image, so it can be re-painted,
re-blended or removed without touching the fills around it. Strokes are entries.
Fill values, blend modes and strengths are edited after the fact. Anchors publish the
coverage at a point in the stack for other layers; Published Inputs read it.

## Masks feed masks: groups

**Mask Group** (`source = GROUP`) holds a nested stack. Its members follow it in the
list, indented; they combine among themselves first, and the result then combines
with the outer stack through the group's own operation, strength, levels and invert —
exactly like any other source:

```
Curvature ──┐
            Multiply → Levels → [Wear group] ──max──▸ MASK
Noise ──────┘                                          │
                                        Painted ──mul──┘
```

Groups nest. Disabling a group removes its whole run. *Add* while a member (or the
group) is active adds into that group; the list buttons move an entry into the group
above it, lift it out (after the group), move a group with its members as a block,
and dissolve a group in place. Anchors inside a group publish the coverage so far,
including the partial group. `core/mask_stack.py` keeps member runs contiguous.

Both evaluators walk the same structure: the CPU reference recursively
(`mask_eval._stack`), the shader compiler into nested node chains
(`shader_graph._mask_chain`), so parity holds for nested stacks.

## Custom Graph

`source = GRAPH` reads a **[Mask Graph](mask-graphs.md)** you author with Open
Painter's own nodes; a starter graph (Coordinates → Noise → Ramp → Mask Output) is
created for you. The engine renders the graph as a GPU pass of its own, and the CPU
reference (export, 2D snapshots, the CPU backend) runs the same steps in NumPy, so
the two agree to the last bits. A mask saved by an earlier version that points at a
Blender **shader node group** keeps its behaviour:

- Inputs named UV / Vector, Position and Normal are wired, and the first output is the
  coverage.
- It is evaluated by the CPU reference, a Cycles bake over the assigned meshes cached by
  a content signature of the tree.

Custom graphs are not portable: saving a smart material with one is refused (bake the
mask stack first).

## Tests

`tests/integration/mask_groups.py`: the exact CPU model of a nested stack against a
NumPy formula, the structure operators (contiguity, moves, nesting, dissolve, delete,
group-aware add), shader parity of the nested stack (1e-4), a custom ramp group on
both paths and its content signature, the live session, the serializer refusal.
