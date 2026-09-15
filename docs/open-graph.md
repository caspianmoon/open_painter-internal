# Open Graph — 0.4.0, node-based editing since 0.8.0

The Layers panel's node icon (*Open Graph*) splits the Painter area and shows the
active texture set as a node graph. The graph is a **projection** of the layer
stack, not a second document: every node is a layer, every socket is an anchor
producer or consumer, and every layer, mask and channel filter is a node, links show the
actual data flow, and edits in it (links, new nodes, deleted nodes) become the
same stack edits the Painter panels make. The stack stays the source of truth
for painting, evaluation, smart materials and undo.

![Open Graph next to the Painter view](open_graph.png)

## What you see

- **Layer nodes** (paint/fill) and **folder nodes**, one per layer, labelled with
  the layer name. Folders are drawn as frames containing the folder node and its
  children; a layer that owns masks or filters gets its own frame around them.
- **Mask nodes**, one per mask, chained left to right in stack order: *Previous*
  in, *Coverage* out, the last one into the layer's *Mask* input. The node body
  shows the mask's enable/name, combine operation, invert, strength, levels and
  its main source controls (generator kind and amount, noise scale, scene field
  kind, filter kind, image). Published Input masks take their anchor on *Published
  Input*; generator masks with micro details take *Micro Height* / *Micro Normal*.
  An Anchor Point mask's *Coverage* output is the anchor other layers read.
- **Filter nodes**, one per channel filter, chained into the layer's channel
  input (*BaseColor*, *Roughness*, …) with the filter's controls on the body. An
  Anchor Point filter's output is a channel anchor.
- **Composition links**: every layer's *Output* feeds the *Backdrop* of the layer
  above it, the top child feeds its folder's *Children* input, and the top root
  layer feeds the **Texture Set Output** node — the stack's actual data flow.
- Layer node bodies expose visibility, opacity, blend/folder mode, the *Publish*
  and *Composite* toggles and *Edit in Panel*. Clicking any node selects its layer
  (and mask or filter) in the Painter panels; the list's selection selects the node.

## Editing

- **Add** (Shift+A) adds to the active layer: *Mask ▸* painted, fill image, baked
  map, noise, generators, procedurals, scene fields, filter masks, Published Input
  and Anchor Point; *Channel Filter ▸* the filter catalog and an Anchor Point for
  the channel selected in the Painter panel; plus Fill/Paint/Folder layers. New
  nodes are appended to their chain.
- **Anchor links**: drag an anchor output (a layer's *Output* / *Composite*, an
  Anchor Point mask or filter) into a consumer socket — a Published Input mask,
  a micro-detail slot, or a channel's content (its first filter when the channel
  is filtered). Dragging from an unpublished *Output* or *Composite* publishes
  the layer first. Deleting such a link clears the reference.
- **Order links**: drag a mask's *Coverage* into another mask's *Previous* (or the
  layer's *Mask*) to move it there in the chain; the same for filters within one
  channel. Drag a layer's *Output* into another layer's *Backdrop* to place it
  directly under that layer (into its folder if needed), or into a folder's
  *Children* to make it the folder's top child. Composition links cannot be
  deleted: chains have no gaps, and the sync redraws them.
- **Deleting a node** deletes that mask or filter from the stack; painted masks
  and layers are deleted in the Painter panel, and the mirror recreates their node.
- A link that would create a cycle, chain masks across layers, or pair
  incompatible sockets is refused: the stack is unchanged, the status line
  explains why, and the link disappears on the next sync.
- *Auto Layout* (header) re-places every node: rows follow the stack order, a
  layer's mask chain shares its row, filter chains sit under it, columns follow
  dependency rank. Nodes you moved keep their positions across syncs.

## How it stays in sync

`graph/mirror.py` computes the expected projection (`project`) and a structural
signature (`signature`: ids, hierarchy, names, visibility, publish flags, anchors,
consumer fields, mask and filter chains — not slider values). The UI lifecycle tick (0.15 s) reconciles
every open graph whose signature changed, editing nodes/sockets/links in place and
keeping `op_id` as the only identity (no RNA reference survives a tick). The tree
is a `bpy.data.node_groups` datablock of type `OpenPainterGraph` with a fake user,
owned by the set id, so undo/redo restores the stack and its mirror together.
`NodeTree.update()` diffs the tree's nodes and links against the projection and
applies the user's edit through `graph/edits.py` (`connect` / `disconnect` for
anchors, `reorder` for chain and backdrop links, `delete` for removed nodes),
validated with `references.issues`.

Tests: `tests/integration/graph_mirror.py`, `graph_link_edits.py`,
`graph_view_ui.py` (real window), `tests/unit/test_graph_layout.py`.
