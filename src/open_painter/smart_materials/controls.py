"""Master controls of smart materials (UI plan phase 2; all fifty recipes authored in 0.12.1).

A catalog recipe authors a handful of artist-facing controls that reach into its own
layers: *Paint Color* is the paint coat's base colour, *Chip Amount* the amount of its
chips mask, *Grain Scale* the size of the timber pattern every layer of the material
shares. Each control names recipe layers by the label ``composer._stamp_module`` used
when it minted the instance's deterministic ids, so a binding survives renaming,
reordering, saving and duplication (a copy gets its own instance token and the same
plain ids). Nothing is manufactured: a control that is not authored here does not
exist, and a control whose layer was deleted reports a broken binding rather than
editing something with a similar name.

Two kinds of binding:

* A **direct** control draws the actual RNA property (``layout.prop(owner, field)``):
  colours, roughness values, layer opacity, generator mask amounts, the texture set's
  surface fields. Editing the layer moves the control and one edit is one undo step.
* A **computed** control is one component of the folder's ``master_factor`` (0–1) or
  ``master_scale`` (× the authored value) vector property, whose getter and setter run
  through :func:`master_values` and :func:`set_master_values`:

  - ``invert``: 1 − a mask amount, for a label that reads the other way (*Paint Remaining*
    over an inverted chips mask);
  - ``coverage``: the amount of a generator mask whose generator keeps coverage at amount 0
    (cavity dirt, rust, drips), gated so that 0 means none: the layer's opacity fades from
    its authored value to nothing as the amount falls below the authored amount;
  - ``depth``: a groove layer's height level below the surface (0 flush, 1 =
    ``DEPTH_SPAN`` height units deep);
  - ``levels``: the position (``levels_center``) or the softness (``levels_width``) of an
    image mask's black/white thresholds, moved together for masks that share one image;
  - ``scale``: a multiplier of the authored values of one or more members — a relief
    amplitude, a detail amount, the shared UV scale of a generated pattern across every
    channel and mask that samples it, an elevated film's Levels span. ``× 1`` is the
    material as authored; the bases come from the module base the stamp recorded.

  An artist-facing amount reaches visually none at 0: a generator amount that cannot is
  wrapped in ``coverage``, and an image-masked layer takes its amount from its opacity.

  Nothing is stored for a computed control: it reads and writes its members, so undo,
  save and duplication work on the members alone.
"""
import re
from dataclasses import dataclass, field as dataclass_field
from uuid import UUID, uuid5
from ..core.layer_store import project_of as _project_of

DEPTH_SPAN = 0.1
LEVELS_SPAN = 0.4          # a Levels width control at 0 is this wide (fully soft), at 1 a hard edge
FAMILIES = {"factor": "master_factor", "multiplier": "master_scale"}
SLOTS = 8


@dataclass(frozen=True)
class Member:
    """One property a computed control reads or writes."""
    layer: str
    field: str
    channel: str = ""
    mask: int = -1
    effect: int = -1           # index of a channel effect (the Levels effect of an elevated film)
    part: str = ""             # "range": the effect's output span, white above black


@dataclass(frozen=True)
class Control:
    label: str
    layer: str = ""            # recipe layer name (the stamp label) of a direct control
    field: str = ""            # RNA property on the owner
    channel: str = ""          # owner is this channel of the layer
    mask: int = -1             # owner is this mask of the layer (index in the recipe)
    effect: int = -1           # owner is this channel filter, when authored
    kind: str = "float"        # float, color, bool (direct) or factor, multiplier (computed)
    scope: str = "layer"       # "set": a surface field of the texture set (anisotropy)
    mode: str = ""             # computed: invert, depth or scale
    members: tuple = ()
    description: str = ""

    @property
    def computed(self):
        return self.kind in FAMILIES


@dataclass
class Binding:
    control: Control
    owner: object = None       # the RNA struct carrying ``field``
    layer: object = None
    field: str = ""
    index: int = -1            # component of the family vector of a computed control
    members: list = dataclass_field(default_factory=list)   # (owner, field, base) of a computed control
    reason: str = ""           # why the binding is broken (owner is None)

    @property
    def ok(self):
        return self.owner is not None

    @property
    def value(self):
        if not self.ok:
            return None
        if self.control.computed:
            return read(self)
        return getattr(self.owner, self.field)


# ---- authoring vocabulary ------------------------------------------------------------------

def color(label, layer, description=""):
    return Control(label, layer, "color", channel="BASE_COLOR", kind="color", description=description)


def roughness(label, layer, description=""):
    return Control(label, layer, "value", channel="ROUGHNESS", description=description)


def opacity(label, layer, description=""):
    """The visible amount of an image-masked or unmasked layer: its opacity."""
    return Control(label, layer, "opacity", description=description)


def amount(label, layer, mask=0, description=""):
    """The amount of a generator mask that is none at 0 (edge wear, chips, dust, world normal)."""
    return Control(label, layer, "amount", mask=mask, description=description)


def coverage_amount(label, layer, mask=0, description=""):
    """The amount of a cavity-dirt, rust or drips mask, gated so that 0 shows none of the layer."""
    return Control(label, kind="factor", mode="coverage",
                   members=(Member(layer, "opacity"), Member(layer, "amount", mask=mask)), description=description)


def levels_center(label, *targets, description=""):
    """Where an image mask's thresholds sit: 1 − the centre of the first mask; the other masks
    (sharing the image) move by the same amount, so a primer ring keeps following its chips."""
    return Control(label, kind="factor", mode="levels_center",
                   members=tuple(Member(layer, "black", mask=mask, part="levels") for layer, mask in targets),
                   description=description)


def levels_width(label, layer, mask=0, description=""):
    """How hard an image mask's edge is: 1 − its black-to-white width over LEVELS_SPAN."""
    return Control(label, kind="factor", mode="levels_width",
                   members=(Member(layer, "black", mask=mask, part="levels"),), description=description)


def level(label, layer, mask, field, description=""):
    return Control(label, layer, field, mask=mask, description=description)


def surface(label, field, description=""):
    return Control(label, field=field, scope="set", description=description)


def inverted(label, layer, mask=0, description=""):
    """1 − a generator mask amount, when the label reads against the mask (an inverted chips mask)."""
    return Control(label, kind="factor", mode="invert", members=(Member(layer, "amount", mask=mask),),
                   description=description)


def depth(label, layer, description=""):
    """How far a groove layer's height level sits below the surface."""
    return Control(label, kind="factor", mode="depth", members=(Member(layer, "value", channel="HEIGHT"),),
                   description=description)


def relief(label, layer, description=""):
    """× the authored amplitude of a layer's generated height relief."""
    return Control(label, kind="multiplier", mode="scale",
                   members=(Member(layer, "procedural_height", channel="HEIGHT"),), description=description)


def film_relief(label, layer, description=""):
    """× the authored elevation span of a raised film (its Levels effect on Height)."""
    return Control(label, kind="multiplier", mode="scale",
                   members=(Member(layer, "output_white", channel="HEIGHT", effect=0, part="range"),),
                   description=description)


def coverage(label, *layers, description=""):
    """Opacity of several layers that form one feature, as a fraction of their authored opacities."""
    return Control(label, kind="factor", mode="scale", members=tuple(Member(layer, "opacity") for layer in layers),
                   description=description)


def _spec(recipe, layer):
    from .catalog import CATALOG
    return next(spec for spec in CATALOG[recipe]["layers"] if spec["name"] == layer)


def _detail_channels(recipe, layer):
    """The channels ``composer._fill`` gives the layer's generated detail image (its amplitudes)."""
    spec = _spec(recipe, layer)
    source = spec.get("generated_source") or {}
    selected = set(spec.get("channels", ("BASE_COLOR", "ROUGHNESS", "METALLIC", "HEIGHT")))
    result = []
    if source.get("color") and "BASE_COLOR" in selected:
        result.append(("BASE_COLOR", "procedural_detail"))
    if source.get("roughness") and "ROUGHNESS" in selected:
        result.append(("ROUGHNESS", "procedural_detail"))
    # Height carries the image when the layer has relief (an amplitude or an elevated film's
    # range); a groove layer (height_value) keeps its flat level and samples nothing.
    if ("HEIGHT" in selected and "height_value" not in spec
            and any(key in spec for key in ("height", "height_range"))
            and (source.get("height") or "height_range" in spec)):
        result.append(("HEIGHT", "procedural_height"))
    return result


def detail(label, recipe, layer, description=""):
    """× the authored colour and roughness amounts of a layer's generated detail."""
    members = tuple(Member(layer, field, channel=channel) for channel, field in _detail_channels(recipe, layer)
                    if channel != "HEIGHT")
    return Control(label, kind="multiplier", mode="scale", members=members, description=description)


def pattern_scale(label, recipe, layers=(), masks=(), description=""):
    """× the authored UV scale of a pattern: every channel sampling the layer's detail image and every
    mask named as (layer, index) — an image mask scales its image, a procedural mask its noise scale."""
    from .composer import masks_for
    members = []
    for layer in layers:
        members.extend(Member(layer, "scale", channel=channel) for channel, _ in _detail_channels(recipe, layer))
    for layer, index in masks:
        settings = masks_for(_spec(recipe, layer))[index]
        members.append(Member(layer, "scale" if settings.get("generated_source") else "noise_scale", mask=index))
    return Control(label, kind="multiplier", mode="scale", members=tuple(members), description=description)


def _paint_controls(coat, chips_mask_layer=None, scratches=None, dirt=None, rust=None, coat_mask=0):
    """The common painted-metal vocabulary: colour and roughness of the coat, then the wear amounts."""
    result = [
        color("Paint Color", coat, "Base colour of the paint coat"),
        roughness("Paint Roughness", coat, "Roughness of the paint coat"),
    ]
    if chips_mask_layer:
        result.append(amount("Chip Amount", chips_mask_layer, mask=coat_mask,
                             description="Amount of the chips mask that exposes what lies under the paint"))
    if scratches:
        result.append(opacity("Scratch Amount", scratches, "Opacity of the scratched-steel layer"))
    if dirt:
        result.append(coverage_amount("Dirt Amount", dirt, description="Amount of the cavity dirt; none at 0"))
    if rust:
        result.append(coverage_amount("Rust Amount", rust, description="Amount of the rust under the paint; none at 0"))
    return result


# Authored per catalog recipe. Layer names are the recipe's own (catalog.py); mask indices
# count the recipe's masks_for(spec) order. docs/master-controls.md lists every control,
# what it binds and where it departs from the product specification. Two rules recur:
# a layer masked by an image or a procedural takes its amount from ``opacity`` (the mask's
# ``amount`` only drives generators); a cavity-dirt, rust or drips layer takes a
# ``coverage_amount`` (its generator keeps coverage at amount 0, so the gate makes 0 none).
# Pattern-scale controls follow Blender's texture convention: a larger scale repeats the
# pattern more often, so its features get smaller.
CONTROLS = {
    # ---- Metals ----
    "Clean Brushed Steel": [
        roughness("Metal Roughness", "Brushed steel"),
        pattern_scale("Brush Grain Scale", "Clean Brushed Steel", layers=("Brushed steel",)),
        relief("Brush Relief", "Brushed steel"),
        opacity("Scratch Amount", "Micro scratches"),
        pattern_scale("Scratch Density", "Clean Brushed Steel", masks=(("Micro scratches", 0),)),
        depth("Scratch Depth", "Micro scratches"),
        surface("Anisotropy", "anisotropy"),
    ],
    "Worn Brushed Steel": [
        roughness("Metal Roughness", "Brushed substrate"),
        opacity("Scratch Amount", "Cross scratches"),
        amount("Edge Polish", "Polished edges"),
        opacity("Handling Marks", "Handling oils"),
        coverage_amount("Grime Amount", "Recess grime"),
        color("Grime Color", "Recess grime"),
        surface("Anisotropy", "anisotropy"),
    ],
    "Machined Aluminum": [
        roughness("Metal Roughness", "Aluminum stock"),
        pattern_scale("Machining Scale", "Machined Aluminum", layers=("Aluminum stock",)),
        relief("Machining Relief", "Aluminum stock"),
        opacity("Pass Variation", "Machining pass variation"),
        pattern_scale("Pass Scale", "Machined Aluminum", layers=("Machining pass variation",)),
        surface("Anisotropy", "anisotropy"),
    ],
    "Scratched Aluminum": [
        roughness("Metal Roughness", "Aluminum substrate"),
        opacity("Scratch Amount", "Deep scratches"),
        depth("Scratch Depth", "Deep scratches"),
        opacity("Dent Amount", "Impact dents"),
        depth("Dent Depth", "Impact dents"),
        amount("Edge Wear", "Worn edges"),
    ],
    "Cast Iron": [
        # The oxide skin covers the casting except at edges (an inverted edge mask at 0.10):
        # the visible roughness, grain and relief are the skin's.
        roughness("Casting Roughness", "Casting oxide skin"),
        detail("Casting Grain", "Cast Iron", "Casting oxide skin"),
        relief("Surface Relief", "Casting oxide skin"),
        inverted("Oxide Coverage", "Casting oxide skin",
                 description="How much of the casting keeps its oxide skin (the inverted edge mask)"),
        coverage_amount("Rust Speckling", "Pinpoint oxidation"),
        coverage_amount("Cavity Deposits", "Cavity deposits"),
    ],
    "Raw Industrial Steel": [
        # The mill scale covers the rolled steel completely (no mask, full opacity): the
        # visible surface is the scale, so its roughness and grain carry the controls.
        roughness("Steel Roughness", "Blue black mill scale"),
        detail("Rolling Grain", "Raw Industrial Steel", "Blue black mill scale"),
        opacity("Mill Scale Amount", "Blue black mill scale"),
        opacity("Scratch Amount", "Rolling scratches"),
        depth("Scratch Depth", "Rolling scratches"),
        coverage_amount("Flash Rust", "Light flash rust"),
    ],
    "Polished Chrome": [
        roughness("Surface Roughness", "Polished chromium"),
        opacity("Hairline Scratches", "Hairline scratches"),
        depth("Scratch Depth", "Hairline scratches"),
        opacity("Wipe Marks", "Wipe smudges"),
        opacity("Fingerprints", "Fingerprints"),
        pattern_scale("Fingerprint Scale", "Polished Chrome", masks=(("Fingerprints", 0),)),
    ],
    "Old Brass": [
        roughness("Brass Roughness", "Brass alloy"),
        opacity("Tarnish Amount", "Brown tarnish"),
        opacity("Scratch Amount", "Handling scratches"),
        coverage_amount("Dark Oxidation", "Dark recess oxidation"),
        amount("Edge Polish", "Touched edges"),
        color("Tarnish Color", "Brown tarnish"),
    ],
    "Antique Bronze": [
        # The aged brown surface covers the casting except at edges: its roughness is the visible one.
        roughness("Bronze Roughness", "Aged brown surface"),
        inverted("Brown Aging", "Aged brown surface",
                 description="How much of the bronze keeps its brown aged surface (the inverted edge mask)"),
        coverage_amount("Green Patina", "Green cavity patina"),
        color("Patina Color", "Green cavity patina"),
        opacity("Fine Scratches", "Fine handling scratches"),
        amount("Polished High Points", "Polished high points"),
    ],
    "Oxidized Copper": [
        # The brown oxide covers the copper sheet completely: the visible roughness is its own.
        roughness("Oxide Roughness", "Brown copper oxide"),
        opacity("Brown Oxidation", "Brown copper oxide"),
        opacity("Turquoise Patina", "Turquoise patina"),
        color("Patina Color", "Turquoise patina"),
        coverage_amount("Cavity Deposits", "Sheltered green deposits"),
        coverage_amount("Rain Streaking", "Rain washed copper"),
    ],
    # ---- Painted and coated metals ----
    "Worn Painted Metal": _paint_controls("Blue paint coat", chips_mask_layer="Blue paint coat",
                                          scratches="Scratched exposed steel", dirt="Recess dirt",
                                          rust="Oxide beneath paint"),
    "Heavy Chipped Paint": [
        color("Paint Color", "Flaking yellow paint"),
        roughness("Paint Roughness", "Flaking yellow paint"),
        levels_center("Paint Loss", ("Flaking yellow paint", 0), ("Old primer", 0),
                      description="How much of the paint has flaked away; the primer ring follows the chips"),
        levels_width("Paint Loss Contrast", "Flaking yellow paint", 0,
                     description="How hard the chip edges are: soft flaking at 0, a crisp break at 1"),
        opacity("Scoring Amount", "Deep scoring"),
        coverage_amount("Dust Amount", "Trapped dust"),
    ],
    "Factory Powder-Coated Metal": [
        color("Coat Color", "Powder coat"),
        roughness("Coat Roughness", "Powder coat"),
        amount("Edge Wear Amount", "Powder coat"),
        opacity("Scratch Amount", "Light coating scratches"),
    ],
    "Military Painted Steel": [
        color("Paint Color", "Olive drab coating"),
        roughness("Paint Roughness", "Olive drab coating"),
        amount("Paint Wear", "Olive drab coating", description="Amount of the chips that expose the steel"),
        coverage_amount("Rust Amount", "Corrosion undercoat"),
        opacity("Scratches", "Abrasion scoring"),
        coverage_amount("Mud", "Cavity earth"),
        amount("Dust", "Settled dust"),
    ],
    "Old Enamel Metal": [
        color("Enamel Color", "Crazed cream enamel"),
        roughness("Enamel Roughness", "Crazed cream enamel"),
        amount("Paint Loss", "Crazed cream enamel", description="Amount of the chips that break the enamel"),
        detail("Enamel Grain", "Old Enamel Metal", "Crazed cream enamel"),
        coverage_amount("Rust Amount", "Rust at coating failures"),
        opacity("Fading", "Faded enamel islands"),
        opacity("Surface Scratches", "Hairline surface marks"),
    ],
    "Rusting Painted Steel": [
        color("Paint Color", "Red paint coat"),
        amount("Paint Wear", "Red paint coat", mask=1, description="Amount of the chips that break the paint"),
        opacity("Deep Rust", "Deep oxide"),
        coverage_amount("Fresh Rust", "Active orange corrosion"),
        coverage_amount("Rust Runoff", "Rust runoff"),
        coverage_amount("Recess Grime", "Recess grime"),
    ],
    "Automotive Painted Metal": [
        color("Paint Color", "Deep red base coat"),
        roughness("Paint Roughness", "Deep red base coat"),
        detail("Orange Peel", "Automotive Painted Metal", "Deep red base coat"),
        opacity("Clearcoat Swirls", "Clearcoat wash swirls"),
        opacity("Fine Scratches", "Fine clearcoat scratches"),
        depth("Scratch Depth", "Fine clearcoat scratches"),
    ],
    "Burned Painted Metal": [
        color("Paint Color", "Blistered green paint"),
        coverage("Crater Amount", "Charred crater interiors", "Exposed crater floors",
                 description="How much of the paint the burst blisters have taken"),
        film_relief("Blister Relief", "Blistered green paint"),
        opacity("Scorching", "Scorched blister rims"),
        coverage_amount("Soot", "Protected soot"),
        amount("Ash", "Fine ash"),
    ],
    # ---- Rust, damage and industrial ----
    "Heavy Rust": [
        opacity("Rust Coverage", "Rust flakes"),
        pattern_scale("Flake Scale", "Heavy Rust", layers=("Rust flakes",), masks=(("Rust flakes", 0),)),
        relief("Flake Relief", "Rust flakes"),
        opacity("Fresh Orange Rust", "Bright new rust"),
        coverage_amount("Deep Cavity Rust", "Dark rust cavities"),
        roughness("Base Roughness", "Dark oxide bed"),
    ],
    "Surface Rust": [
        roughness("Steel Roughness", "Intact steel"),
        opacity("Brown Rust", "Thin brown oxide"),
        opacity("Orange Flash Rust", "Orange flash rust"),
        pattern_scale("Rust Scale", "Surface Rust", masks=(("Thin brown oxide", 0), ("Orange flash rust", 0))),
        relief("Surface Relief", "Intact steel"),
    ],
    "Wet Rust": [
        opacity("Wetness", "Surface moisture"),
        roughness("Wet Roughness", "Surface moisture"),
        opacity("Dry Scale Amount", "Dry oxide scales"),
        relief("Scale Relief", "Dry oxide scales"),
        coverage_amount("Wet Cavities", "Wet recesses"),
        relief("Corrosion Relief", "Dark wet corrosion"),
    ],
    "Galvanized Steel": [
        roughness("Metal Roughness", "Zinc spangle"),
        pattern_scale("Spangle Scale", "Galvanized Steel", layers=("Zinc spangle",),
                      masks=(("Sparse zinc oxidation", 0),)),
        relief("Spangle Relief", "Zinc spangle"),
        opacity("White Oxidation", "Sparse zinc oxidation"),
        opacity("Scratches", "Manufacturing scratches"),
    ],
    "Welded Steel": [
        roughness("Steel Roughness", "Steel sheet"),
        film_relief("Weld Relief", "Solidified weld pools"),
        pattern_scale("Weld Pattern Scale", "Welded Steel", layers=("Solidified weld pools",),
                      masks=(("Solidified weld pools", 0), ("Tempered weld shoulders", 0))),
        opacity("Heat Tint", "Tempered weld shoulders"),
        coverage_amount("Weld Soot", "Weld recess soot"),
    ],
    "Heat-Treated Metal": [
        roughness("Metal Roughness", "Steel substrate"),
        opacity("Gold Temper", "Straw gold temper"),
        opacity("Brown Temper", "Brown temper transition"),
        opacity("Violet Temper", "Violet temper"),
        opacity("Blue Temper", "Blue hot zones"),
        pattern_scale("Heat Pattern Scale", "Heat-Treated Metal",
                      masks=tuple((layer, index) for layer in ("Straw gold temper", "Brown temper transition",
                                                              "Violet temper", "Blue hot zones")
                                  for index in (0, 1)),
                      description="Repeats of the temper bands across the surface, all four zones together"),
    ],
    "Greasy Machinery": [
        roughness("Metal Roughness", "Machined steel"),
        opacity("Scratches", "Maintenance scratches"),
        coverage_amount("Grease Buildup", "Protected cavity grease"),
        opacity("Oil Film", "Dragged oil film"),
        opacity("Grease Deposits", "Thick grease deposits"),
        opacity("Dust in Grease", "Dust stuck in grease"),
    ],
    "Abandoned Machinery": [
        color("Paint Color", "Peeling teal paint"),
        inverted("Paint Remaining", "Peeling teal paint", mask=1,
                 description="How much of the teal paint the chips have left"),
        opacity("Deep Corrosion", "Deep corrosion"),
        coverage_amount("Fresh Rust", "Orange corrosion blooms"),
        coverage_amount("Oil Leakage", "Stale oil leakage"),
        coverage_amount("Grime", "Dense cavity grime"),
        amount("Dust", "Settled dust"),
    ],
    # ---- Plastic and rubber ----
    "Injection-Molded Plastic": [
        color("Plastic Color", "Molded plastic"),
        roughness("Plastic Roughness", "Molded plastic"),
        detail("Molding Grain", "Injection-Molded Plastic", "Molded plastic"),
        pattern_scale("Grain Scale", "Injection-Molded Plastic", layers=("Molded plastic",),
                      masks=(("Fine molding grain", 0),)),
        opacity("Flow Variation", "Mold flow variation"),
        opacity("Micro Texture", "Fine molding grain"),
    ],
    "Worn Hard Plastic": [
        color("Plastic Color", "Plastic body"),
        roughness("Plastic Roughness", "Plastic body"),
        opacity("Scratches", "Stress-whitened abrasion"),
        amount("Edge Polish", "Polished plastic edges"),
        opacity("Fingerprints", "Oily handling smears"),
        coverage_amount("Dirt", "Accumulated dirt"),
    ],
    "Soft-Touch Plastic": [
        color("Plastic Color", "Rubberized coating"),
        roughness("Surface Roughness", "Rubberized coating"),
        detail("Surface Grain", "Soft-Touch Plastic", "Rubberized coating"),
        opacity("Abrasion", "Soft abrasion"),
        opacity("Handling Oils", "Handling oils"),
    ],
    "Sun-Damaged Plastic": [
        color("Original Color", "Original blue plastic"),
        amount("Sun Bleaching", "Sun bleached surface"),
        opacity("Cracking", "Open weathering fissures"),
        depth("Crack Depth", "Open weathering fissures"),
        opacity("Chalkiness", "Chalky grain"),
        roughness("Surface Dryness", "Chalky grain"),
    ],
    "Dirty Rubber": [
        color("Rubber Color", "Rubber compound"),
        roughness("Rubber Roughness", "Rubber compound"),
        detail("Grain Amount", "Dirty Rubber", "Rubber compound"),
        opacity("Scoring", "Rubber scoring"),
        amount("Edge Wear", "Rubbed edges"),
        coverage_amount("Embedded Dirt", "Embedded cavity earth"),
    ],
    "Cracked Old Rubber": [
        color("Rubber Color", "Aged rubber"),
        pattern_scale("Crack Scale", "Cracked Old Rubber",
                      masks=(("Weathered fissure shoulders", 0), ("Open rubber cracks", 0))),
        depth("Crack Depth", "Open rubber cracks"),
        detail("Dry Grain", "Cracked Old Rubber", "Aged rubber"),
        opacity("Bleaching", "Bleached patches"),
        coverage_amount("Dust in Cracks", "Dust in cracks"),
    ],
    # ---- Wood ----
    "Fresh Oak": [
        color("Wood Color", "Oak growth grain"),
        pattern_scale("Grain Scale", "Fresh Oak", layers=("Oak growth grain",),
                      masks=(("Open vessel beds", 0), ("Fine wood fibers", 0))),
        relief("Grain Relief", "Oak growth grain"),
        opacity("Pore Amount", "Open vessel beds"),
        depth("Pore Depth", "Open vessel beds"),
        opacity("Fine Fibers", "Fine wood fibers"),
    ],
    "Old Worn Wood": [
        color("Wood Color", "Aged wood grain"),
        relief("Grain Relief", "Aged wood grain"),
        opacity("Dark Pores", "Dark open pores"),
        opacity("Scratches", "Scuffed fiber bundles"),
        amount("Edge Polish", "Polished edges"),
        coverage_amount("Crevice Dirt", "Crevice dirt"),
    ],
    "Painted Wood": [
        color("Paint Color", "Raised blue paint film"),
        roughness("Paint Roughness", "Raised blue paint film"),
        amount("Paint Wear", "Raised blue paint film", description="Amount of the edge wear that breaks the paint film"),
        detail("Wood Grain", "Painted Wood", "Timber substrate"),
        opacity("Paint Scratches", "Scored paint film"),
        coverage_amount("Crevice Dirt", "Dark crevices"),
    ],
    "Weathered Outdoor Wood": [
        color("Wood Color", "Weathered growth grain"),
        relief("Grain Relief", "Weathered growth grain"),
        opacity("Raised Fibers", "Raised gray fibers"),
        opacity("Splitting", "Split timber beds"),
        depth("Split Depth", "Split timber beds"),
        coverage_amount("Moisture Streaks", "Moisture tracks"),
        coverage_amount("Crevice Dirt", "Crevice dirt"),
    ],
    "Burnt Wood": [
        color("Char Color", "Carbonized timber"),
        opacity("Charring", "Recessed charcoal fissures"),
        relief("Char Relief", "Carbonized timber"),
        opacity("Heat Browning", "Heat browned grain"),
        amount("Ash Amount", "Ash coated char"),
        color("Ash Color", "Ash coated char"),
    ],
    "Oiled Dark Wood": [
        color("Wood Color", "Oiled timber grain"),
        roughness("Finish Roughness", "Oiled timber grain"),
        relief("Grain Relief", "Oiled timber grain"),
        opacity("Pore Amount", "Deep vessel pores"),
        opacity("Fingerprints", "Handling smudges"),
        opacity("Finish Scratches", "Fine finish abrasion"),
    ],
    # ---- Stone, concrete and construction ----
    "Rough Concrete": [
        color("Concrete Color", "Cement binder and grit"),
        roughness("Surface Roughness", "Cement binder and grit"),
        relief("Surface Relief", "Cement binder and grit"),
        opacity("Aggregate Amount", "Exposed aggregate crowns"),
        opacity("Pore Amount", "Open air pockets"),
        depth("Pore Depth", "Open air pockets"),
    ],
    "Old Dirty Concrete": [
        color("Concrete Color", "Weathered cement relief"),
        relief("Surface Relief", "Weathered cement relief"),
        opacity("Pores", "Deep concrete pores"),
        opacity("Mineral Grime", "Embedded mineral grime"),
        coverage_amount("Water Stains", "Water stains"),
        coverage_amount("Dirt", "Cavity dirt"),
    ],
    "Painted Concrete": [
        color("Paint Color", "Raised wall paint"),
        roughness("Paint Roughness", "Raised wall paint"),
        amount("Paint Wear", "Raised wall paint", description="Amount of the edge wear that breaks the paint"),
        opacity("Concrete Pitting", "Pitted cement"),
        coverage_amount("Recess Dirt", "Dirt along recesses"),
    ],
    "Polished Marble": [
        color("Marble Color", "Marble body"),
        roughness("Surface Roughness", "Marble body"),
        pattern_scale("Vein Scale", "Polished Marble", layers=("Marble body",), masks=(("Warm mineral wisps", 0),)),
        detail("Vein Detail", "Polished Marble", "Marble body"),
        opacity("Warm Mineral Variation", "Warm mineral wisps"),
        opacity("Micro Scratches", "Polish micro abrasion"),
    ],
    "Granite": [
        color("Stone Color", "Granite mineral field"),
        roughness("Stone Roughness", "Granite mineral field"),
        pattern_scale("Grain Scale", "Granite", layers=("Granite mineral field",),
                      masks=(("Pale feldspar", 0), ("Dark mica", 0), ("Quartz grains", 0))),
        opacity("Feldspar Amount", "Pale feldspar"),
        opacity("Mica Amount", "Dark mica"),
        opacity("Quartz Amount", "Quartz grains"),
    ],
    "Weathered Stone": [
        color("Stone Color", "Stone mineral substrate"),
        opacity("Erosion", "Eroded grain beds"),
        depth("Erosion Depth", "Eroded grain beds"),
        coverage_amount("Cavity Darkening", "Dark stone cavities"),
        opacity("Moisture Stains", "Damp mineral staining"),
        coverage_amount("Moss Amount", "Optional moss buildup"),
        color("Moss Color", "Optional moss buildup"),
    ],
    # ---- Fabric and leather ----
    "Worn Leather": [
        color("Leather Color", "Leather grain relief"),
        roughness("Leather Roughness", "Leather grain relief"),
        relief("Grain Relief", "Leather grain relief"),
        opacity("Creases", "Leather crease beds"),
        opacity("Scratches", "Soft surface scuffing"),
        coverage_amount("Dark Recesses", "Dark recesses"),
        amount("Burnished Edges", "Burnished edges"),
    ],
    "Cracked Old Leather": [
        color("Leather Color", "Dry leather grain"),
        roughness("Leather Roughness", "Dry leather grain"),
        opacity("Cracking", "Recessed dry fissures"),
        depth("Crack Depth", "Recessed dry fissures"),
        opacity("Fading", "Faded grain crowns"),
        coverage_amount("Deep Stains", "Deep stains"),
        amount("Edge Abrasion", "Abraded hide edges"),
    ],
    "Denim Fabric": [
        color("Denim Color", "Indigo twill relief"),
        pattern_scale("Weave Scale", "Denim Fabric", layers=("Indigo twill relief",),
                      masks=(("Pale yarn crowns", 0), ("Faded weave ridges", 1))),
        relief("Weave Relief", "Indigo twill relief"),
        opacity("Visible Weft", "Pale yarn crowns"),
        amount("Fading", "Faded weave ridges"),
        opacity("Abrasion", "Cotton abrasion"),
        coverage_amount("Dirt", "Embedded dirt"),
    ],
    "Canvas Fabric": [
        color("Canvas Color", "Canvas woven relief"),
        pattern_scale("Weave Scale", "Canvas Fabric", layers=("Canvas woven relief",),
                      masks=(("Rough fiber crowns", 0), ("Frayed edge fibers", 1))),
        relief("Weave Relief", "Canvas woven relief"),
        opacity("Loose Fibers", "Rough fiber crowns"),
        opacity("Staining", "Irregular stains"),
        coverage_amount("Fold Dirt", "Dirt in folds"),
        amount("Fraying", "Frayed edge fibers"),
    ],
    # ---- Specialized hero materials ----
    "Sci-Fi Panel Surface": [
        color("Panel Color", "Raised panel coating"),
        roughness("Coating Roughness", "Raised panel coating"),
        amount("Coating Wear", "Raised panel coating", description="Amount of the edge wear that breaks the coating"),
        color("Repair Patch Color", "Contrasting repair coating"),
        coverage_amount("Repair Patch Amount", "Contrasting repair coating"),
        opacity("Service Scratches", "Service abrasion"),
        amount("Exposed Edges", "Exposed panel edges"),
        coverage_amount("Recess Grime", "Panel recess grime"),
    ],
    "Post-Apocalyptic Scrap Metal": [
        color("Old Paint Color", "Old yellow paint film"),
        inverted("Old Paint Remaining", "Old yellow paint film",
                 description="How much of the old yellow paint the edge wear has left"),
        color("Repair Paint Color", "Later blue repair film"),
        opacity("Deep Rust", "Granular rust scales"),
        opacity("Gouges", "Abraded coating edges"),
        coverage_amount("Oil Leakage", "Oil leakage"),
        coverage_amount("Cavity Grime", "Deep cavity grime"),
        amount("Dust", "Windblown dust"),
    ],
}


def slots(controls):
    """Family vector component of every computed control, in authoring order."""
    result, counters = {}, {family: 0 for family in FAMILIES}
    for control in controls:
        if control.computed:
            result[control.label] = counters[control.kind]
            counters[control.kind] += 1
    if any(count > SLOTS for count in counters.values()):
        raise ValueError("A recipe authors more computed controls than the family vectors hold")
    return result


_SLOTS = {name: slots(controls) for name, controls in CONTROLS.items()}


def catalog_name(folder):
    from .modules import catalog_name as name_of
    return name_of(folder) if folder is not None and folder.type == "FOLDER" else ""


def available(folder):
    """The authored controls of this folder's recipe, or [] (an ordinary folder has none)."""
    if getattr(folder, 'master_controls', ''):
        from .portable import profile
        return profile(folder)[1]
    return list(CONTROLS.get(catalog_name(folder), ()))


def has_controls(folder):
    return bool(available(folder))


def _plain_id(folder, label):
    from .composer import catalog_module_id
    module_id = folder.module_id if getattr(folder, 'master_controls', '') else catalog_module_id(catalog_name(folder))
    return uuid5(UUID(module_id), "layer:" + label).hex


def _instance_id(folder, label):
    return (folder.module_instance + '.' if folder.module_instance else '') + _plain_id(folder, label)


def _instance_mask_id(folder, label, index):
    from .composer import catalog_module_id
    module_id = folder.module_id if getattr(folder, 'master_controls', '') else catalog_module_id(catalog_name(folder))
    return (folder.module_instance + '.' if folder.module_instance else '') + uuid5(UUID(module_id), f'mask:{label}:{index}').hex


def _instance_effect_id(folder, label, channel, index):
    from .composer import catalog_module_id
    module_id = folder.module_id if getattr(folder, 'master_controls', '') else catalog_module_id(catalog_name(folder))
    return (folder.module_instance + '.' if folder.module_instance else '') + uuid5(UUID(module_id), f'effect:{label}:{channel}:{index}').hex


def _owner(ts, folder, layer_label, channel="", mask=-1, effect=-1):
    """The RNA struct a control or member names, or (None, reason)."""
    layer_id = _instance_id(folder, layer_label)
    layer = next((item for item in ts.layers if item.id == layer_id), None)
    if layer is None:
        return None, layer, f"layer '{layer_label}' was deleted"
    owner = layer
    if channel:
        owner = next((prop for prop in layer.channels if prop.channel == channel), None)
        if owner is None:
            return None, layer, f"{layer_label} has no {channel.replace('_', ' ').title()} channel"
        if effect >= 0:
            effect_id = _instance_effect_id(folder, layer_label, channel, effect)
            owner = next((item for item in owner.effects if item.id == effect_id), None)
            if owner is None:
                return None, layer, f"the height levels of '{layer_label}' were removed"
    elif mask >= 0:
        mask_id = _instance_mask_id(folder, layer_label, mask)
        owner = next((item for item in layer.masks if item.id == mask_id), None)
        if owner is None:
            return None, layer, f"the mask of '{layer_label}' was deleted"
    return owner, layer, ""


# ---- authored bases (the module base the stamp recorded) ---------------------------------------

_bases = {}


def clear_cache():
    _bases.clear()


def _base_layers(folder):
    key = (folder.module_id, folder.module_version)
    if key not in _bases:
        from .modules import read_base
        layers = read_base(*key) or []
        _bases[key] = {layer.get("id"): layer for layer in layers}
    return _bases[key]


def _base_value(folder, member):
    """The authored value of a member's field, from the module base; None when unknown."""
    layer = _base_layers(folder).get(_plain_id(folder, member.layer))
    if layer is None:
        return None
    try:
        if member.channel:
            channel = next(item for item in layer["channels"] if item["channel"] == member.channel)
            if member.effect >= 0:
                effect = channel["effects"][member.effect]
                if member.part == "range":
                    return effect["output_white"] - effect["output_black"]
                return effect[member.field]
            return channel[member.field]
        if member.mask >= 0:
            mask = layer["masks"][member.mask]
            if member.part == "levels":
                return (mask["black"] + mask["white"]) / 2, mask["white"] - mask["black"]
            return mask[member.field]
        return layer[member.field]
    except (KeyError, IndexError, StopIteration):
        return None


# ---- resolution ------------------------------------------------------------------------------

def resolve(ts, folder, control):
    """Bind a control to the actual RNA owner and field of this instance, or explain why not."""
    binding = Binding(control)
    if control.scope == "set":
        if not hasattr(ts, control.field):
            binding.reason = f"{control.field} is not a texture set property"
            return binding
        binding.owner, binding.field = ts, control.field
        return binding
    if control.computed:
        for member in control.members:
            owner, layer, reason = _owner(ts, folder, member.layer, member.channel, member.mask, member.effect)
            if owner is None:
                binding.reason = reason
                return binding
            if not hasattr(owner, member.field):
                binding.reason = f"{member.field} is not a property of {member.layer}"
                return binding
            base = _base_value(folder, member) if control.mode in {"scale", "coverage", "levels_center"} else None
            if control.mode in {"scale", "coverage", "levels_center"} and base is None:
                binding.reason = f"the authored value of '{member.layer}' is not recorded"
                return binding
            binding.members.append((owner, member, base))
            if binding.layer is None:
                binding.layer = layer
        if control.mode == "scale" and not any(_magnitude(base) for _, _, base in binding.members):
            binding.reason = f"'{control.label}' has nothing to scale: its authored values are zero"
            return binding
        binding.owner, binding.field = folder, FAMILIES[control.kind]
        binding.index = slots(available(folder))[control.label]
        return binding
    owner, layer, reason = _owner(ts, folder, control.layer, control.channel, control.mask, control.effect)
    if owner is None:
        binding.reason = reason
        return binding
    binding.layer = layer
    if not hasattr(owner, control.field):
        binding.reason = f"{control.field} is not a property of {control.layer}"
        return binding
    binding.owner, binding.field = owner, control.field
    return binding


def bindings(ts, folder):
    return [resolve(ts, folder, control) for control in available(folder)]


def components(ts, folder):
    """The material's direct child layers, top of the stack first."""
    return [layer for layer in reversed(list(ts.layers)) if layer.parent_id == folder.id]


# ---- computed values -----------------------------------------------------------------------------

def _magnitude(base):
    return abs(base[0]) if isinstance(base, (list, tuple)) else abs(base or 0)


def _current(owner, member):
    if member.part == "range":
        return owner.output_white - owner.output_black
    value = getattr(owner, member.field)
    return value[0] if member.field == "scale" else value


def read(binding):
    """The value a computed control shows: derived from its members, never stored."""
    control = binding.control
    owner, member, base = binding.members[0]
    if control.mode == "invert":
        return 1 - getattr(owner, member.field)
    if control.mode == "depth":
        return min(max((0.5 - getattr(owner, member.field)) / DEPTH_SPAN, 0.0), 1.0)
    if control.mode == "coverage":
        layer_owner, mask_owner = binding.members[0][0], binding.members[1][0]
        return 0.0 if layer_owner.opacity <= 0 else mask_owner.amount
    if control.mode == "levels_center":
        return min(max(1 - (owner.black + owner.white) / 2, 0.0), 1.0)
    if control.mode == "levels_width":
        return min(max(1 - (owner.white - owner.black) / LEVELS_SPAN, 0.0), 1.0)
    for owner, member, base in binding.members:
        if _magnitude(base):
            value = _current(owner, member) / (base[0] if isinstance(base, (list, tuple)) else base)
            return min(max(value, 0.0), 1.0) if control.kind == "factor" else max(value, 0.0)
    return 1.0


def write(binding, value):
    """Apply a computed control's value to every member (each write is an ordinary property edit)."""
    control = binding.control
    if control.mode == "invert":
        owner, member, _ = binding.members[0]
        setattr(owner, member.field, min(max(1 - value, 0.0), 1.0))
        return
    if control.mode == "depth":
        owner, member, _ = binding.members[0]
        setattr(owner, member.field, 0.5 - min(max(value, 0.0), 1.0) * DEPTH_SPAN)
        return
    if control.mode == "coverage":
        # The amount is the generator's; the opacity fades out below the authored amount.
        (layer_owner, _, base_opacity), (mask_owner, _, base_amount) = binding.members
        value = min(max(value, 0.0), 1.0)
        mask_owner.amount = value
        layer_owner.opacity = base_opacity * min(1.0, value / base_amount if base_amount > 0 else 1.0)
        return
    if control.mode == "levels_center":
        base_center = binding.members[0][2][0]
        shift = (1 - min(max(value, 0.0), 1.0)) - base_center
        for owner, _, (center, _) in binding.members:
            _set_levels(owner, center + shift, owner.white - owner.black)
        return
    if control.mode == "levels_width":
        owner, _, _ = binding.members[0]
        _set_levels(owner, (owner.black + owner.white) / 2, (1 - min(max(value, 0.0), 1.0)) * LEVELS_SPAN)
        return
    for owner, member, base in binding.members:
        if member.part == "range":
            owner.output_white = min(owner.output_black + base * value, 1.0)
        elif member.field == "scale":
            setattr(owner, "scale", (max(base[0] * value, 0.001), max(base[1] * value, 0.001)))
        else:
            setattr(owner, member.field, base * value)


def _set_levels(mask, middle, width):
    """Black and white of a mask around ``middle``, the ramp kept whole inside 0–1."""
    width = min(max(width, 0.005), 1.0)
    middle = min(max(middle, width / 2), 1 - width / 2)
    mask.black, mask.white = middle - width / 2, middle + width / 2


def _owning_set(folder):
    """The texture set a folder belongs to (the RNA path names it; a scan confirms it)."""
    project = _project_of(folder)
    if project is None:
        return None
    try:
        from ..core.layer_store import scene_path
        match = re.search(r"texture_sets\[(\d+)\]\.layers\[(\d+)\]", scene_path(folder))
    except ValueError:
        match = None
    if match:
        ts = project.texture_sets[int(match.group(1))]
        if ts.layers[int(match.group(2))].as_pointer() == folder.as_pointer():
            return ts
    pointer = folder.as_pointer()
    return next((ts for ts in project.texture_sets if any(item.as_pointer() == pointer for item in ts.layers)), None)


def master_values(folder, family):
    """Getter of the folder's family vector: every computed control of that family, others neutral."""
    values = [1.0 if family == "multiplier" else 0.0] * SLOTS
    if not folder.module_id or not available(folder):
        return values
    ts = _owning_set(folder)
    if ts is None:
        return values
    for binding in bindings(ts, folder):
        if binding.control.kind == family and binding.ok:
            values[binding.index] = read(binding)
    return values


def set_master_values(folder, family, values):
    """Setter of the folder's family vector: the components that changed reach their members."""
    if not folder.module_id or not available(folder):
        return
    ts = _owning_set(folder)
    if ts is None:
        return
    current = master_values(folder, family)
    for binding in bindings(ts, folder):
        if binding.control.kind == family and binding.ok and abs(values[binding.index] - current[binding.index]) > 1e-9:
            write(binding, float(values[binding.index]))
