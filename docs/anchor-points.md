# Anchor points — 0.2.9

Substance 3D Painter's [Anchor Point](https://experienceleague.adobe.com/en/docs/substance-3d-painter/using/effects/anchor-point)
exposes the result of a stack at a position so other layers can reuse it. Open
Painter 0.2.9 adds the same capability on top of the 0.2.3 published inputs:
named anchors inside mask stacks and channel filter stacks, consumers on
Published Input masks, fill channel content and generator micro details, and
find-references / locate navigation.

## Workflow

- **Mask anchor:** Masks → Add Mask Effect → *Add Anchor Point*. The anchor is a
  row in the mask list; move it like any mask. It publishes the coverage
  accumulated by the masks above it in the list (the items evaluated before it)
  and changes nothing itself.
- **Channel anchor:** Properties → channel → FILTERS → *Anchor*. The anchor is a
  row in that channel's filter list and publishes the channel's straight RGBA
  after the filters above it, before later filters, opacity, blend mode or masks.
  Paint layers can publish their painted channel this way.
- **Published Input mask:** the mask's *Anchor* picker lists every anchor in the
  texture set as *Name · Layer · stack*, sorted by stack position; anchors above
  the consumer are marked "(above)". The legacy layer/channel/stage controls
  remain for masks without an anchor.
- **Fill content:** a fill layer's channel has a *Content Anchor* picker. The
  anchor replaces the colour, value or image; surface detail and the channel's
  own filters still apply on top, so *anchor + Gaussian Blur* is a blurred copy
  of another layer's channel. Paint layers keep their canvas.
- **Micro Details:** Edge Wear, Chips, Dirt, Cavity Dirt, Rust and Drips masks
  offer *Micro Height* and *Micro Normal* anchors with intensity sliders and a
  texel radius. Painted or procedural relief then receives wear on its convex
  rim and dirt in its concave ring, like baked geometry.
- **Find references:** selecting an anchor shows *Used by N* with one row per
  consumer and a select button. **Locate:** every consumer shows a magnifier
  that selects the anchor's layer and stack row.
- **Deleting** an anchor (or its layer, or baking a mask stack) reports
  "Broke N references: …" in the status box; nothing else changes.

## Semantics

| Anchor | Publishes | Consumers | Missing anchor reads as |
|---|---|---|---|
| Mask anchor (`OP_Mask.source == 'ANCHOR'`, identified by the mask id) | coverage of `masks[:position]`; an empty prefix is 1 | Published Input mask (`reference_anchor`), fill content (gray, opaque), Micro Height (gray as height) | mask value 0 before levels; fill content transparent; no micro detail |
| Channel anchor (`OP_TextureEffect.kind == 'ANCHOR'` with an `id`) | straight RGBA of `effects[:position]`, alpha = paint/tile coverage | Published Input mask (red for scalars, Rec.709 luminance for colour channels, times alpha), fill content, Micro Height (HEIGHT), Micro Normal (NORMAL) | as above |
| Layer output (`OP_Layer.publish_output`, id `<layer id>:out`) — 0.4.0 | the layer blended over its backdrop, in its group's context: root layers publish the stack composed up to and including them; inside an isolated folder the straight colour with alpha = coverage; inside a passthrough folder the composite over the folder's backdrop. Every channel is available; the consumer picks one (`anchor_channel` on fill content, `reference_channel` on masks, HEIGHT/NORMAL for micro details; Base Color when unspecified). A hidden or disabled producer publishes its backdrop. | the same consumers | as above |
| Folder composite (`OP_Layer.publish_group` on a folder, id `<folder id>:group`) — 0.4.0 | the folder's child composite before the folder's own opacity, mask and blend: isolated folders publish straight colour with alpha = child coverage; passthrough folders publish the children composed over the folder's backdrop. A folder whose channel is disabled publishes nothing (isolated) or its backdrop (passthrough). | the same consumers | as above |

Ordering rule: any **acyclic** reference is allowed — above, below or inside the
same layer's stack. The dependency graph is item-granular: a mask depends on the
items before it in its stack, a channel filter on the filters before it, a
consumer on the anchor's position. Composite anchors add composition nodes
(`('OUT', layer, channel)` = the layer's content, mask stack and backdrop;
`('BELOW', layer, channel)` = the previous sibling's output or a passthrough
parent's backdrop; `('GROUP', folder, channel)` = the top child's output), so
the same check derives that a layer output may only feed layers *above* it, a
layer cannot read its own output, and a folder's composite cannot be read from
inside that folder — while a folder masked by one of its children's outputs is
fine, since the child never sees the folder's mask. `references.issues()` reports cycles (they
are refused when picked and block evaluation); `references.warnings()` reports
missing anchors and published sources, which evaluate as neutral instead of
aborting. The legacy layer-granular cycle rule is gone with it: a source mask
stack may read a consumer's mask stack while that consumer reads the source's
Height channel.

Micro details: `generators/micro_details.py` measures signed convexity
`clip((h − G_σ(h))·8)` from an anchored height (composited over neutral 0.5 so
unpainted texels are flat) and `clip(G_σ(∂x sx + ∂y sy)·2.5σ)` from an anchored
tangent normal, both encoded 0.5-centred so the intensity sliders stay live
shader uniforms. Edge Wear / Chips take `max(baked band, convex)`; Dirt lowers AO
by the cavity and raises curvature by the convexity; Cavity Dirt, Rust and Drips
take `max(baked cavity, cavity)`. The shader-native path uploads the encoded
raster once as a boundary resource keyed by the anchor's transitive inputs.

## Evaluation

- CPU reference: `evaluation/anchors.py` indexes anchors; `references.evaluate_anchor`
  evaluates the prefix with the existing evaluators (`evaluate_masks` on a
  derived `layer id:anchor:<id>` snapshot, `channel_eval.source` on the effect
  prefix). Composite anchors run the composition loop itself:
  `evaluation/composite.Composer` (the same code the channel outputs use)
  composes the producer's group up to the producer (`output`) or the folder's
  children (`group`) and returns straight RGBA rows. Consumers:
  `references.evaluate` (masks), `channel_eval._base_source` (fill content),
  `generators/micro_details.py` (generators).
- Engine (`evaluation/shader_graph.py`, compiled by `engine/compiler.py`): `source(layer, channel, count)`
  and `mask(layer, through)` memoize every prefix, so a consumer and the owning
  layer share one node chain; anchor items compile to nothing. A layer output is
  the producer's CHANNEL unit's existing `Out`/`Out:Alpha` exports (or, when that
  layer is untouched for the channel, whatever `_wire` would feed it:
  `_composite_backdrop`); a folder composite is a `Group`/`Group:Alpha` export
  added to published folders' channel units. Identity keys of raster boundaries
  include the anchor's transitive inputs (`_anchor_dependencies`; for composite
  anchors every contributing layer's blend state, content and masks), and any
  anchor edit is a structural rebuild scoped to the affected units.
- Legacy GPU live masks decline stacks with micro-detail anchors (CPU fallback);
  anchored fill channels take the CPU source fallback.

## Persistence

`.openpaintmaterial` format version 3 (files 1–3 import). Anchor ids are mask
ids or filter-item ids; import, layer duplication and texture-set duplication
assign fresh ids and remap `reference_anchor`, `anchor`, `detail_height_anchor`
and `detail_normal_anchor`. Anchors outside a duplicated subtree keep pointing
at the originals. Unknown anchor ids import verbatim and report as missing; a
cyclic file is refused and rolled back. Older add-ons cannot read version 3.

## Evidence

- `tests/integration/anchor_points.py`: mid-stack mask capture (.25 through the
  anchor vs .125 for the whole stack), mid-filter channel capture (.7 vs .9),
  blur on anchored content, same-stack order (below allowed, above a cycle, own
  channel a cycle), dirtying through anchors, operators, deletion report,
  group/set duplication, material round trip, dangling and cyclic files, `.blend`
  reopen.
- `tests/integration/micro_detail_anchors.py`: a 64 px rivet gets a worn rim
  (coverage > .5 at r 56–64, < .05 inside, outside and far), the painted variant
  matches, Cavity Dirt rings outside (r 65–72), a normal anchor drives Dirt,
  editing the relief dirties consumers and moves the rings, the shader path
  uploads two `MicroDetails:` rasters with live intensity bindings.
- `tests/integration/shader_sources.py`: ten anchor parity cases (mask prefix,
  channel prefix, anchored content after a raster boundary with one shared
  resource, luminance reference, same-layer cross-channel, modulated consumer,
  missing anchor, Edge Wear / Cavity Dirt / Dirt micro details) at max error
  ≤ 2.1e-6 against the CPU reference.
- `tests/unit/test_anchor_graph.py`, `tests/unit/test_micro_details.py`.

## Limits

- Anchors reference within one texture set; folder channels cannot be anchored
  (folders have no channel content), folder mask stacks can.
- Fill content anchors ignore projection, tiling and UV transforms: the anchor
  is already in texture space.
- Changing an anchor mask's source to something else, or a filter item's kind,
  drops the anchor; consumers report it as missing until re-picked.
- Anchors are evaluation inputs, not exported textures; use Bake Mask Stack or
  export for files.
