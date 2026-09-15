# Open Graph — 0.4.0

The Layers panel's node icon (*Open Graph*) splits the Painter area and shows the
active texture set as a node graph. The graph is a **projection** of the layer
stack, not a second document: every node is a layer, every socket is an anchor
producer or consumer, and the only thing you edit in it is a link, which becomes
an anchor reference on the stack (the same fields the pickers set). The stack
stays the source of truth for painting, evaluation, smart materials and undo.

![Open Graph next to the Painter view](open_graph.png)

## What you see

- **Layer nodes** (paint/fill) and **folder nodes**, one per layer, labelled with
  the layer name. Folders are drawn as frames containing the folder node and its
  children; frames nest.
- **Outputs** on the right of a node: *Output* (the layer blended over its
  backdrop; "(unpublished)" until it is published), *Composite* on folders (the
  child composite before the folder blends), one socket per anchor point the
  layer owns (mask anchors as coverage, channel anchors as content), and — only
  while a legacy whole-layer reference uses it — a read-only *Whole …* socket.
- **Inputs** on the left: one per enabled fill channel (*Content*), one per
  Published Input mask (*Mask: name*) and *Micro Height* / *Micro Normal* for
  generator masks that take micro details. Each input takes one link.
- The **Texture Set Output** node shows the final channels, fed by the topmost
  root layer's output (informational: links into it are refused).
- Node bodies expose visibility, opacity, blend/folder mode, the *Publish* and
  *Composite* toggles and *Edit in Panel*; everything else is edited in the
  Painter panels. Clicking a node selects the layer in the Layers list and the
  list's selection selects the node.

## Editing links

- Drag from any output into an input: the consumer's anchor field is set. Dragging
  from an unpublished *Output* or *Composite* publishes the layer first.
- A link that would create a cycle (an output feeding a layer below it, a folder's
  composite read from inside the folder, …) or an ineligible pairing (a colour
  anchor into *Micro Normal*) is refused: the stack is unchanged, the status line
  explains why, and the link disappears on the next sync. Dropping a new link onto
  an occupied input replaces the reference; if the replacement is refused the
  previous link comes back.
- Deleting a link clears the field (a legacy whole-layer reference is cleared too).
  Deleting a node does not delete the layer: the mirror recreates it.
- *Auto Layout* (header) re-places every node: rows follow the stack order, columns
  follow dependency rank, folder contents are indented. Nodes you moved keep their
  positions across syncs; new layers are placed automatically.

## How it stays in sync

`graph/mirror.py` computes the expected projection (`project`) and a structural
signature (`signature`: ids, hierarchy, names, visibility, publish flags, anchors,
consumer fields — not slider values). The UI lifecycle tick (0.15 s) reconciles
every open graph whose signature changed, editing nodes/sockets/links in place and
keeping `op_id` as the only identity (no RNA reference survives a tick). The tree
is a `bpy.data.node_groups` datablock of type `OpenPainterGraph` with a fake user,
owned by the set id, so undo/redo restores the stack and its mirror together.
`NodeTree.update()` diffs the tree's links against the projection and applies the
user's edit through `graph/edits.py` (`connect` / `disconnect`), validated with
`references.issues`.

Tests: `tests/integration/graph_mirror.py`, `graph_link_edits.py`,
`graph_view_ui.py` (real window), `tests/unit/test_graph_layout.py`.
