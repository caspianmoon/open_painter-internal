# Master controls of the built-in smart materials

Every one of the fifty catalog recipes has authored, artist-facing master controls
(`src/open_painter/smart_materials/controls.py`). A master control is a convenience binding
to the material's existing layers, masks and channels: it never replaces, flattens, hides or
duplicates them. The inspector shows the controls first when the material folder is
selected; the component layers follow.

## How a control binds

Every control names recipe layers by the label the stamp used for the instance's
deterministic ids (`composer._stamp_module`), so renaming or reordering a layer, saving and
reopening the file and duplicating the material keep the binding (a copy gets its own
instance token and binds to its own layers). A deleted target is reported as unavailable
with the reason; nothing rebinds to a similar name. Instances applied before 0.12.1 bind
the same way — the ids were always deterministic — and modules without authored controls
keep the component-level fallback.

| Kind | Drawn as | Range |
|---|---|---|
| direct: colour, roughness, opacity, generator mask amount, surface field | the real RNA property (`layout.prop`) | the property's own range; amounts and opacity 0–1 |
| inverted (`1 − amount`) | a component of the folder's `master_factor` vector | 0–1 |
| coverage-gated amount (cavity dirt, rust, drips) | `master_factor` | the generator's amount; the layer's opacity fades to none below the authored amount, so 0 = none |
| depth of a groove layer | `master_factor` | 0 = flush, 1 = 0.1 height units below the surface |
| coverage of several layers | `master_factor` | 0 = none, 1 = as authored |
| Levels position / softness of an image mask | `master_factor` | position: 1 = the ramp at the bottom of the image range; softness: 1 = a hard edge |
| multiplier: relief amplitude, detail amount, pattern scale, elevated-film span | a component of the folder's `master_scale` vector | × the authored value; 1 = as authored (soft 0–4, hard 0–32) |

Computed controls store nothing: their getter reads the members and their setter writes
them, so one drag is one undo step, undo restores the members, and snapshots, documents,
module diffs and evaluation signatures never carry the vectors (`layer_stack.DERIVED`).
The authored values a multiplier is relative to come from the module base the stamp
recorded (`modules.read_base`).

Two rules recur below. A layer masked by an image or a procedural (most scratches, pores,
tarnish, smudges, cracks) takes its amount from its **opacity**: a mask's `amount` only
drives generators. A layer masked by a **cavity-dirt, rust or drips generator** takes a
**coverage-gated amount** (0.12.2): those generators keep coverage at amount 0 — CAVITY_DIRT
fills deep cavities (`clip(cavity · (1 + 8·amount) · contrast)`), RUST grows from them, DRIPS
keeps its cavity streaks — so the control is the generator's amount (the spread the recipe
was authored with) and the layer's opacity fades from its authored value to nothing as the
amount falls below the authored amount: 0 is none, the authored value is the material as
authored, 1 spreads the effect as far as the generator goes. Edge wear, chips, dust and
world-normal amounts are none at 0 by themselves and stay direct.

Pattern-scale controls follow Blender's texture convention: a larger scale repeats the
pattern more often, so its features get smaller.

## Controls

### Clean Brushed Steel

| Control | Binds | Kind |
|---|---|---|
| Metal Roughness | Brushed steel · Roughness value | slider |
| Brush Grain Scale | × Brushed steel · UV scale (Color, Roughness, Height) | × authored |
| Brush Relief | × Brushed steel · height amplitude (Height) | × authored |
| Scratch Amount | Micro scratches · layer opacity | slider |
| Scratch Density | × Micro scratches · mask 0 UV scale | × authored |
| Scratch Depth | depth of Micro scratches · Height level (0 flush … 1 = 0.1 below) | 0–1 |
| Anisotropy | texture set · anisotropy | slider |

### Worn Brushed Steel

| Control | Binds | Kind |
|---|---|---|
| Metal Roughness | Brushed substrate · Roughness value | slider |
| Scratch Amount | Cross scratches · layer opacity | slider |
| Edge Polish | Polished edges · mask 0 (EDGE_WEAR generator) amount | slider |
| Handling Marks | Handling oils · layer opacity | slider |
| Grime Amount | Recess grime · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |
| Grime Color | Recess grime · Color colour | colour |
| Anisotropy | texture set · anisotropy | slider |

### Machined Aluminum

| Control | Binds | Kind |
|---|---|---|
| Metal Roughness | Aluminum stock · Roughness value | slider |
| Machining Scale | × Aluminum stock · UV scale (Color, Roughness, Height) | × authored |
| Machining Relief | × Aluminum stock · height amplitude (Height) | × authored |
| Pass Variation | Machining pass variation · layer opacity | slider |
| Pass Scale | × Machining pass variation · UV scale (Roughness) | × authored |
| Anisotropy | texture set · anisotropy | slider |

### Scratched Aluminum

| Control | Binds | Kind |
|---|---|---|
| Metal Roughness | Aluminum substrate · Roughness value | slider |
| Scratch Amount | Deep scratches · layer opacity | slider |
| Scratch Depth | depth of Deep scratches · Height level (0 flush … 1 = 0.1 below) | 0–1 |
| Dent Amount | Impact dents · layer opacity | slider |
| Dent Depth | depth of Impact dents · Height level (0 flush … 1 = 0.1 below) | 0–1 |
| Edge Wear | Worn edges · mask 0 (EDGE_WEAR generator) amount | slider |

### Cast Iron

| Control | Binds | Kind |
|---|---|---|
| Casting Roughness | Casting oxide skin · Roughness value | slider |
| Casting Grain | × Casting oxide skin · detail amount (Color, Roughness) | × authored |
| Surface Relief | × Casting oxide skin · height amplitude (Height) | × authored |
| Oxide Coverage | 1 − Casting oxide skin · mask 0 (EDGE_WEAR generator, inverted) amount | 0–1 |
| Rust Speckling | Pinpoint oxidation · mask 0 (RUST generator) amount, opacity gated to none at 0 | 0–1 |
| Cavity Deposits | Cavity deposits · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |

### Raw Industrial Steel

| Control | Binds | Kind |
|---|---|---|
| Steel Roughness | Blue black mill scale · Roughness value | slider |
| Rolling Grain | × Blue black mill scale · detail amount (Color, Roughness) | × authored |
| Mill Scale Amount | Blue black mill scale · layer opacity | slider |
| Scratch Amount | Rolling scratches · layer opacity | slider |
| Scratch Depth | depth of Rolling scratches · Height level (0 flush … 1 = 0.1 below) | 0–1 |
| Flash Rust | Light flash rust · mask 0 (RUST generator) amount, opacity gated to none at 0 | 0–1 |

### Polished Chrome

| Control | Binds | Kind |
|---|---|---|
| Surface Roughness | Polished chromium · Roughness value | slider |
| Hairline Scratches | Hairline scratches · layer opacity | slider |
| Scratch Depth | depth of Hairline scratches · Height level (0 flush … 1 = 0.1 below) | 0–1 |
| Wipe Marks | Wipe smudges · layer opacity | slider |
| Fingerprints | Fingerprints · layer opacity | slider |
| Fingerprint Scale | × Fingerprints · mask 0 noise scale | × authored |

### Old Brass

| Control | Binds | Kind |
|---|---|---|
| Brass Roughness | Brass alloy · Roughness value | slider |
| Tarnish Amount | Brown tarnish · layer opacity | slider |
| Scratch Amount | Handling scratches · layer opacity | slider |
| Dark Oxidation | Dark recess oxidation · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |
| Edge Polish | Touched edges · mask 0 (EDGE_WEAR generator) amount | slider |
| Tarnish Color | Brown tarnish · Color colour | colour |

### Antique Bronze

| Control | Binds | Kind |
|---|---|---|
| Bronze Roughness | Aged brown surface · Roughness value | slider |
| Brown Aging | 1 − Aged brown surface · mask 0 (EDGE_WEAR generator, inverted) amount | 0–1 |
| Green Patina | Green cavity patina · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |
| Patina Color | Green cavity patina · Color colour | colour |
| Fine Scratches | Fine handling scratches · layer opacity | slider |
| Polished High Points | Polished high points · mask 0 (EDGE_WEAR generator) amount | slider |

### Oxidized Copper

| Control | Binds | Kind |
|---|---|---|
| Oxide Roughness | Brown copper oxide · Roughness value | slider |
| Brown Oxidation | Brown copper oxide · layer opacity | slider |
| Turquoise Patina | Turquoise patina · layer opacity | slider |
| Patina Color | Turquoise patina · Color colour | colour |
| Cavity Deposits | Sheltered green deposits · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |
| Rain Streaking | Rain washed copper · mask 0 (DRIPS generator) amount, opacity gated to none at 0 | 0–1 |

### Worn Painted Metal

| Control | Binds | Kind |
|---|---|---|
| Paint Color | Blue paint coat · Color colour | colour |
| Paint Roughness | Blue paint coat · Roughness value | slider |
| Chip Amount | Blue paint coat · mask 0 (CHIPS generator, inverted) amount | slider |
| Scratch Amount | Scratched exposed steel · layer opacity | slider |
| Dirt Amount | Recess dirt · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |
| Rust Amount | Oxide beneath paint · mask 0 (RUST generator) amount, opacity gated to none at 0 | 0–1 |

### Heavy Chipped Paint

| Control | Binds | Kind |
|---|---|---|
| Paint Color | Flaking yellow paint · Color colour | colour |
| Paint Roughness | Flaking yellow paint · Roughness value | slider |
| Paint Loss | position of the thresholds of Flaking yellow paint · mask 0, Old primer · mask 0 (1 = all lost) | 0–1 |
| Paint Loss Contrast | softness of the thresholds of Flaking yellow paint · mask 0 (1 = hard edge) | 0–1 |
| Scoring Amount | Deep scoring · layer opacity | slider |
| Dust Amount | Trapped dust · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |

### Factory Powder-Coated Metal

| Control | Binds | Kind |
|---|---|---|
| Coat Color | Powder coat · Color colour | colour |
| Coat Roughness | Powder coat · Roughness value | slider |
| Edge Wear Amount | Powder coat · mask 0 (EDGE_WEAR generator, inverted) amount | slider |
| Scratch Amount | Light coating scratches · layer opacity | slider |

### Military Painted Steel

| Control | Binds | Kind |
|---|---|---|
| Paint Color | Olive drab coating · Color colour | colour |
| Paint Roughness | Olive drab coating · Roughness value | slider |
| Paint Wear | Olive drab coating · mask 0 (CHIPS generator, inverted) amount | slider |
| Rust Amount | Corrosion undercoat · mask 0 (RUST generator) amount, opacity gated to none at 0 | 0–1 |
| Scratches | Abrasion scoring · layer opacity | slider |
| Mud | Cavity earth · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |
| Dust | Settled dust · mask 0 (DUST generator) amount | slider |

### Old Enamel Metal

| Control | Binds | Kind |
|---|---|---|
| Enamel Color | Crazed cream enamel · Color colour | colour |
| Enamel Roughness | Crazed cream enamel · Roughness value | slider |
| Paint Loss | Crazed cream enamel · mask 0 (CHIPS generator, inverted) amount | slider |
| Enamel Grain | × Crazed cream enamel · detail amount (Color, Roughness) | × authored |
| Rust Amount | Rust at coating failures · mask 0 (RUST generator) amount, opacity gated to none at 0 | 0–1 |
| Fading | Faded enamel islands · layer opacity | slider |
| Surface Scratches | Hairline surface marks · layer opacity | slider |

### Rusting Painted Steel

| Control | Binds | Kind |
|---|---|---|
| Paint Color | Red paint coat · Color colour | colour |
| Paint Wear | Red paint coat · mask 1 (CHIPS generator, inverted) amount | slider |
| Deep Rust | Deep oxide · layer opacity | slider |
| Fresh Rust | Active orange corrosion · mask 0 (RUST generator) amount, opacity gated to none at 0 | 0–1 |
| Rust Runoff | Rust runoff · mask 0 (DRIPS generator) amount, opacity gated to none at 0 | 0–1 |
| Recess Grime | Recess grime · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |

### Automotive Painted Metal

| Control | Binds | Kind |
|---|---|---|
| Paint Color | Deep red base coat · Color colour | colour |
| Paint Roughness | Deep red base coat · Roughness value | slider |
| Orange Peel | × Deep red base coat · detail amount (Color, Roughness) | × authored |
| Clearcoat Swirls | Clearcoat wash swirls · layer opacity | slider |
| Fine Scratches | Fine clearcoat scratches · layer opacity | slider |
| Scratch Depth | depth of Fine clearcoat scratches · Height level (0 flush … 1 = 0.1 below) | 0–1 |

### Burned Painted Metal

| Control | Binds | Kind |
|---|---|---|
| Paint Color | Blistered green paint · Color colour | colour |
| Crater Amount | coverage: Charred crater interiors opacity, Exposed crater floors opacity (1 = authored) | 0–1 |
| Blister Relief | × Blistered green paint · span (Height) | × authored |
| Scorching | Scorched blister rims · layer opacity | slider |
| Soot | Protected soot · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |
| Ash | Fine ash · mask 0 (DUST generator) amount | slider |

### Heavy Rust

| Control | Binds | Kind |
|---|---|---|
| Rust Coverage | Rust flakes · layer opacity | slider |
| Flake Scale | × Rust flakes · UV scale (Color, Roughness, Height); Rust flakes · mask 0 UV scale | × authored |
| Flake Relief | × Rust flakes · height amplitude (Height) | × authored |
| Fresh Orange Rust | Bright new rust · layer opacity | slider |
| Deep Cavity Rust | Dark rust cavities · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |
| Base Roughness | Dark oxide bed · Roughness value | slider |

### Surface Rust

| Control | Binds | Kind |
|---|---|---|
| Steel Roughness | Intact steel · Roughness value | slider |
| Brown Rust | Thin brown oxide · layer opacity | slider |
| Orange Flash Rust | Orange flash rust · layer opacity | slider |
| Rust Scale | × Thin brown oxide · mask 0 UV scale; Orange flash rust · mask 0 UV scale | × authored |
| Surface Relief | × Intact steel · height amplitude (Height) | × authored |

### Wet Rust

| Control | Binds | Kind |
|---|---|---|
| Wetness | Surface moisture · layer opacity | slider |
| Wet Roughness | Surface moisture · Roughness value | slider |
| Dry Scale Amount | Dry oxide scales · layer opacity | slider |
| Scale Relief | × Dry oxide scales · height amplitude (Height) | × authored |
| Wet Cavities | Wet recesses · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |
| Corrosion Relief | × Dark wet corrosion · height amplitude (Height) | × authored |

### Galvanized Steel

| Control | Binds | Kind |
|---|---|---|
| Metal Roughness | Zinc spangle · Roughness value | slider |
| Spangle Scale | × Zinc spangle · UV scale (Color, Roughness, Height); Sparse zinc oxidation · mask 0 UV scale | × authored |
| Spangle Relief | × Zinc spangle · height amplitude (Height) | × authored |
| White Oxidation | Sparse zinc oxidation · layer opacity | slider |
| Scratches | Manufacturing scratches · layer opacity | slider |

### Welded Steel

| Control | Binds | Kind |
|---|---|---|
| Steel Roughness | Steel sheet · Roughness value | slider |
| Weld Relief | × Solidified weld pools · span (Height) | × authored |
| Weld Pattern Scale | × Solidified weld pools · UV scale (Color, Roughness, Height); Solidified weld pools · mask 0 UV scale; Tempered weld shoulders · mask 0 UV scale | × authored |
| Heat Tint | Tempered weld shoulders · layer opacity | slider |
| Weld Soot | Weld recess soot · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |

### Heat-Treated Metal

| Control | Binds | Kind |
|---|---|---|
| Metal Roughness | Steel substrate · Roughness value | slider |
| Gold Temper | Straw gold temper · layer opacity | slider |
| Brown Temper | Brown temper transition · layer opacity | slider |
| Violet Temper | Violet temper · layer opacity | slider |
| Blue Temper | Blue hot zones · layer opacity | slider |
| Heat Pattern Scale | × Straw gold temper · mask 0 noise scale; Straw gold temper · mask 1 noise scale; Brown temper transition · mask 0 noise scale; Brown temper transition · mask 1 noise scale; Violet temper · mask 0 noise scale; Violet temper · mask 1 noise scale; Blue hot zones · mask 0 noise scale; Blue hot zones · mask 1 noise scale | × authored |

### Greasy Machinery

| Control | Binds | Kind |
|---|---|---|
| Metal Roughness | Machined steel · Roughness value | slider |
| Scratches | Maintenance scratches · layer opacity | slider |
| Grease Buildup | Protected cavity grease · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |
| Oil Film | Dragged oil film · layer opacity | slider |
| Grease Deposits | Thick grease deposits · layer opacity | slider |
| Dust in Grease | Dust stuck in grease · layer opacity | slider |

### Abandoned Machinery

| Control | Binds | Kind |
|---|---|---|
| Paint Color | Peeling teal paint · Color colour | colour |
| Paint Remaining | 1 − Peeling teal paint · mask 1 (CHIPS generator, inverted) amount | 0–1 |
| Deep Corrosion | Deep corrosion · layer opacity | slider |
| Fresh Rust | Orange corrosion blooms · mask 0 (RUST generator) amount, opacity gated to none at 0 | 0–1 |
| Oil Leakage | Stale oil leakage · mask 0 (DRIPS generator) amount, opacity gated to none at 0 | 0–1 |
| Grime | Dense cavity grime · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |
| Dust | Settled dust · mask 0 (DUST generator) amount | slider |

### Injection-Molded Plastic

| Control | Binds | Kind |
|---|---|---|
| Plastic Color | Molded plastic · Color colour | colour |
| Plastic Roughness | Molded plastic · Roughness value | slider |
| Molding Grain | × Molded plastic · detail amount (Color, Roughness) | × authored |
| Grain Scale | × Molded plastic · UV scale (Color, Roughness, Height); Fine molding grain · mask 0 UV scale | × authored |
| Flow Variation | Mold flow variation · layer opacity | slider |
| Micro Texture | Fine molding grain · layer opacity | slider |

### Worn Hard Plastic

| Control | Binds | Kind |
|---|---|---|
| Plastic Color | Plastic body · Color colour | colour |
| Plastic Roughness | Plastic body · Roughness value | slider |
| Scratches | Stress-whitened abrasion · layer opacity | slider |
| Edge Polish | Polished plastic edges · mask 0 (EDGE_WEAR generator) amount | slider |
| Fingerprints | Oily handling smears · layer opacity | slider |
| Dirt | Accumulated dirt · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |

### Soft-Touch Plastic

| Control | Binds | Kind |
|---|---|---|
| Plastic Color | Rubberized coating · Color colour | colour |
| Surface Roughness | Rubberized coating · Roughness value | slider |
| Surface Grain | × Rubberized coating · detail amount (Color, Roughness) | × authored |
| Abrasion | Soft abrasion · layer opacity | slider |
| Handling Oils | Handling oils · layer opacity | slider |

### Sun-Damaged Plastic

| Control | Binds | Kind |
|---|---|---|
| Original Color | Original blue plastic · Color colour | colour |
| Sun Bleaching | Sun bleached surface · mask 0 (WORLD_NORMAL generator) amount | slider |
| Cracking | Open weathering fissures · layer opacity | slider |
| Crack Depth | depth of Open weathering fissures · Height level (0 flush … 1 = 0.1 below) | 0–1 |
| Chalkiness | Chalky grain · layer opacity | slider |
| Surface Dryness | Chalky grain · Roughness value | slider |

### Dirty Rubber

| Control | Binds | Kind |
|---|---|---|
| Rubber Color | Rubber compound · Color colour | colour |
| Rubber Roughness | Rubber compound · Roughness value | slider |
| Grain Amount | × Rubber compound · detail amount (Color, Roughness) | × authored |
| Scoring | Rubber scoring · layer opacity | slider |
| Edge Wear | Rubbed edges · mask 0 (EDGE_WEAR generator) amount | slider |
| Embedded Dirt | Embedded cavity earth · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |

### Cracked Old Rubber

| Control | Binds | Kind |
|---|---|---|
| Rubber Color | Aged rubber · Color colour | colour |
| Crack Scale | × Weathered fissure shoulders · mask 0 UV scale; Open rubber cracks · mask 0 UV scale | × authored |
| Crack Depth | depth of Open rubber cracks · Height level (0 flush … 1 = 0.1 below) | 0–1 |
| Dry Grain | × Aged rubber · detail amount (Color, Roughness) | × authored |
| Bleaching | Bleached patches · layer opacity | slider |
| Dust in Cracks | Dust in cracks · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |

### Fresh Oak

| Control | Binds | Kind |
|---|---|---|
| Wood Color | Oak growth grain · Color colour | colour |
| Grain Scale | × Oak growth grain · UV scale (Color, Roughness, Height); Open vessel beds · mask 0 UV scale; Fine wood fibers · mask 0 UV scale | × authored |
| Grain Relief | × Oak growth grain · height amplitude (Height) | × authored |
| Pore Amount | Open vessel beds · layer opacity | slider |
| Pore Depth | depth of Open vessel beds · Height level (0 flush … 1 = 0.1 below) | 0–1 |
| Fine Fibers | Fine wood fibers · layer opacity | slider |

### Old Worn Wood

| Control | Binds | Kind |
|---|---|---|
| Wood Color | Aged wood grain · Color colour | colour |
| Grain Relief | × Aged wood grain · height amplitude (Height) | × authored |
| Dark Pores | Dark open pores · layer opacity | slider |
| Scratches | Scuffed fiber bundles · layer opacity | slider |
| Edge Polish | Polished edges · mask 0 (EDGE_WEAR generator) amount | slider |
| Crevice Dirt | Crevice dirt · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |

### Painted Wood

| Control | Binds | Kind |
|---|---|---|
| Paint Color | Raised blue paint film · Color colour | colour |
| Paint Roughness | Raised blue paint film · Roughness value | slider |
| Paint Wear | Raised blue paint film · mask 0 (EDGE_WEAR generator, inverted) amount | slider |
| Wood Grain | × Timber substrate · detail amount (Color, Roughness) | × authored |
| Paint Scratches | Scored paint film · layer opacity | slider |
| Crevice Dirt | Dark crevices · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |

### Weathered Outdoor Wood

| Control | Binds | Kind |
|---|---|---|
| Wood Color | Weathered growth grain · Color colour | colour |
| Grain Relief | × Weathered growth grain · height amplitude (Height) | × authored |
| Raised Fibers | Raised gray fibers · layer opacity | slider |
| Splitting | Split timber beds · layer opacity | slider |
| Split Depth | depth of Split timber beds · Height level (0 flush … 1 = 0.1 below) | 0–1 |
| Moisture Streaks | Moisture tracks · mask 0 (DRIPS generator) amount, opacity gated to none at 0 | 0–1 |
| Crevice Dirt | Crevice dirt · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |

### Burnt Wood

| Control | Binds | Kind |
|---|---|---|
| Char Color | Carbonized timber · Color colour | colour |
| Charring | Recessed charcoal fissures · layer opacity | slider |
| Char Relief | × Carbonized timber · height amplitude (Height) | × authored |
| Heat Browning | Heat browned grain · layer opacity | slider |
| Ash Amount | Ash coated char · mask 0 (DUST generator) amount | slider |
| Ash Color | Ash coated char · Color colour | colour |

### Oiled Dark Wood

| Control | Binds | Kind |
|---|---|---|
| Wood Color | Oiled timber grain · Color colour | colour |
| Finish Roughness | Oiled timber grain · Roughness value | slider |
| Grain Relief | × Oiled timber grain · height amplitude (Height) | × authored |
| Pore Amount | Deep vessel pores · layer opacity | slider |
| Fingerprints | Handling smudges · layer opacity | slider |
| Finish Scratches | Fine finish abrasion · layer opacity | slider |

### Rough Concrete

| Control | Binds | Kind |
|---|---|---|
| Concrete Color | Cement binder and grit · Color colour | colour |
| Surface Roughness | Cement binder and grit · Roughness value | slider |
| Surface Relief | × Cement binder and grit · height amplitude (Height) | × authored |
| Aggregate Amount | Exposed aggregate crowns · layer opacity | slider |
| Pore Amount | Open air pockets · layer opacity | slider |
| Pore Depth | depth of Open air pockets · Height level (0 flush … 1 = 0.1 below) | 0–1 |

### Old Dirty Concrete

| Control | Binds | Kind |
|---|---|---|
| Concrete Color | Weathered cement relief · Color colour | colour |
| Surface Relief | × Weathered cement relief · height amplitude (Height) | × authored |
| Pores | Deep concrete pores · layer opacity | slider |
| Mineral Grime | Embedded mineral grime · layer opacity | slider |
| Water Stains | Water stains · mask 0 (DRIPS generator) amount, opacity gated to none at 0 | 0–1 |
| Dirt | Cavity dirt · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |

### Painted Concrete

| Control | Binds | Kind |
|---|---|---|
| Paint Color | Raised wall paint · Color colour | colour |
| Paint Roughness | Raised wall paint · Roughness value | slider |
| Paint Wear | Raised wall paint · mask 0 (EDGE_WEAR generator, inverted) amount | slider |
| Concrete Pitting | Pitted cement · layer opacity | slider |
| Recess Dirt | Dirt along recesses · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |

### Polished Marble

| Control | Binds | Kind |
|---|---|---|
| Marble Color | Marble body · Color colour | colour |
| Surface Roughness | Marble body · Roughness value | slider |
| Vein Scale | × Marble body · UV scale (Color, Roughness, Height); Warm mineral wisps · mask 0 UV scale | × authored |
| Vein Detail | × Marble body · detail amount (Color, Roughness) | × authored |
| Warm Mineral Variation | Warm mineral wisps · layer opacity | slider |
| Micro Scratches | Polish micro abrasion · layer opacity | slider |

### Granite

| Control | Binds | Kind |
|---|---|---|
| Stone Color | Granite mineral field · Color colour | colour |
| Stone Roughness | Granite mineral field · Roughness value | slider |
| Grain Scale | × Granite mineral field · UV scale (Color, Roughness, Height); Pale feldspar · mask 0 UV scale; Dark mica · mask 0 UV scale; Quartz grains · mask 0 UV scale | × authored |
| Feldspar Amount | Pale feldspar · layer opacity | slider |
| Mica Amount | Dark mica · layer opacity | slider |
| Quartz Amount | Quartz grains · layer opacity | slider |

### Weathered Stone

| Control | Binds | Kind |
|---|---|---|
| Stone Color | Stone mineral substrate · Color colour | colour |
| Erosion | Eroded grain beds · layer opacity | slider |
| Erosion Depth | depth of Eroded grain beds · Height level (0 flush … 1 = 0.1 below) | 0–1 |
| Cavity Darkening | Dark stone cavities · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |
| Moisture Stains | Damp mineral staining · layer opacity | slider |
| Moss Amount | Optional moss buildup · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |
| Moss Color | Optional moss buildup · Color colour | colour |

### Worn Leather

| Control | Binds | Kind |
|---|---|---|
| Leather Color | Leather grain relief · Color colour | colour |
| Leather Roughness | Leather grain relief · Roughness value | slider |
| Grain Relief | × Leather grain relief · height amplitude (Height) | × authored |
| Creases | Leather crease beds · layer opacity | slider |
| Scratches | Soft surface scuffing · layer opacity | slider |
| Dark Recesses | Dark recesses · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |
| Burnished Edges | Burnished edges · mask 0 (EDGE_WEAR generator) amount | slider |

### Cracked Old Leather

| Control | Binds | Kind |
|---|---|---|
| Leather Color | Dry leather grain · Color colour | colour |
| Leather Roughness | Dry leather grain · Roughness value | slider |
| Cracking | Recessed dry fissures · layer opacity | slider |
| Crack Depth | depth of Recessed dry fissures · Height level (0 flush … 1 = 0.1 below) | 0–1 |
| Fading | Faded grain crowns · layer opacity | slider |
| Deep Stains | Deep stains · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |
| Edge Abrasion | Abraded hide edges · mask 0 (EDGE_WEAR generator) amount | slider |

### Denim Fabric

| Control | Binds | Kind |
|---|---|---|
| Denim Color | Indigo twill relief · Color colour | colour |
| Weave Scale | × Indigo twill relief · UV scale (Color, Roughness, Height); Pale yarn crowns · mask 0 UV scale; Faded weave ridges · mask 1 UV scale | × authored |
| Weave Relief | × Indigo twill relief · height amplitude (Height) | × authored |
| Visible Weft | Pale yarn crowns · layer opacity | slider |
| Fading | Faded weave ridges · mask 0 (EDGE_WEAR generator) amount | slider |
| Abrasion | Cotton abrasion · layer opacity | slider |
| Dirt | Embedded dirt · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |

### Canvas Fabric

| Control | Binds | Kind |
|---|---|---|
| Canvas Color | Canvas woven relief · Color colour | colour |
| Weave Scale | × Canvas woven relief · UV scale (Color, Roughness, Height); Rough fiber crowns · mask 0 UV scale; Frayed edge fibers · mask 1 UV scale | × authored |
| Weave Relief | × Canvas woven relief · height amplitude (Height) | × authored |
| Loose Fibers | Rough fiber crowns · layer opacity | slider |
| Staining | Irregular stains · layer opacity | slider |
| Fold Dirt | Dirt in folds · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |
| Fraying | Frayed edge fibers · mask 0 (EDGE_WEAR generator) amount | slider |

### Sci-Fi Panel Surface

| Control | Binds | Kind |
|---|---|---|
| Panel Color | Raised panel coating · Color colour | colour |
| Coating Roughness | Raised panel coating · Roughness value | slider |
| Coating Wear | Raised panel coating · mask 0 (EDGE_WEAR generator, inverted) amount | slider |
| Repair Patch Color | Contrasting repair coating · Color colour | colour |
| Repair Patch Amount | Contrasting repair coating · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |
| Service Scratches | Service abrasion · layer opacity | slider |
| Exposed Edges | Exposed panel edges · mask 0 (EDGE_WEAR generator) amount | slider |
| Recess Grime | Panel recess grime · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |

### Post-Apocalyptic Scrap Metal

| Control | Binds | Kind |
|---|---|---|
| Old Paint Color | Old yellow paint film · Color colour | colour |
| Old Paint Remaining | 1 − Old yellow paint film · mask 0 (EDGE_WEAR generator, inverted) amount | 0–1 |
| Repair Paint Color | Later blue repair film · Color colour | colour |
| Deep Rust | Granular rust scales · layer opacity | slider |
| Gouges | Abraded coating edges · layer opacity | slider |
| Oil Leakage | Oil leakage · mask 0 (DRIPS generator) amount, opacity gated to none at 0 | 0–1 |
| Cavity Grime | Deep cavity grime · mask 0 (CAVITY_DIRT generator) amount, opacity gated to none at 0 | 0–1 |
| Dust | Windblown dust · mask 0 (DUST generator) amount | slider |

## Departures from the product specification

The specification named layers and properties the recipes do not always have. Every
departure is listed here; a control's name follows the specification unless the recipe
gave no honest way to keep it.

**Rules applied across materials**

- R1 — "*X* / NOISE amount", "/ RUST amount", "/ SCRATCHES amount" on a layer whose mask is
  an image or a procedural, or that has no mask at all: bound to the layer's opacity
  (0 none → 1 full). The catalog's scratches are `abrasion_mask_v2` image masks, not the
  SCRATCHES generator; its rust films, tarnish, smudges, pores and cracks are image masks.
- R2 — "/ CAVITY_DIRT amount", "/ RUST amount", "/ DRIPS amount": a coverage-gated amount
  (see *How a control binds*), since 0.12.2 including Worn Painted Metal's *Dirt Amount* and
  *Rust Amount* and Heavy Chipped Paint's *Dust Amount*.
- R3 — "procedural scale" of a layer built from a generated detail image: a pattern-scale
  multiplier over every channel and mask that samples the image (its UV scale), so grain,
  pores and fibres stay aligned; a procedural mask scales its noise scale.
- R4 — "height": a multiplier of the height amplitude for generated relief; a 0–1 depth for
  a groove layer (a flat height level below the surface); a multiplier of the Levels span
  for an elevated film (`height_range`).
- R5 — "detail": a multiplier of the colour and roughness amplitudes of the layer's
  generated detail.
- R6 — a surface control named on a layer that another layer covers completely binds the
  visible layer instead: Raw Industrial Steel (mill scale over rolled steel), Oxidized
  Copper (brown oxide over copper, renamed *Oxide Roughness*), Cast Iron (oxide skin over
  the casting: roughness, grain, relief), Antique Bronze (aged brown surface: roughness).

**Per material**

| Material | Departure |
|---|---|
| Clean Brushed Steel | Scratch Amount R1; Scratch Density R3 (the abrasion image's scale); Scratch Depth R4 |
| Worn Brushed Steel | Scratch Amount, Handling Marks R1; Grime Amount R2 |
| Machined Aluminum | Pass Variation R1 (the layer has no mask); Machining Scale, Pass Scale R3 |
| Scratched Aluminum | Scratch Amount, Dent Amount R1; Scratch Depth, Dent Depth R4 |
| Cast Iron | Casting Roughness, Casting Grain, Surface Relief R6; Rust Speckling, Cavity Deposits R2 |
| Raw Industrial Steel | Steel Roughness, Rolling Grain R6; Mill Scale Amount R1 (no mask); Scratch Amount R1; Scratch Depth R4; Flash Rust R2 |
| Polished Chrome | Polish / Roughness → *Surface Roughness*; Hairline Scratches, Wipe Marks, Fingerprints R1; Scratch Depth R4; Fingerprint Scale R3 (procedural noise scale) |
| Old Brass | Tarnish Amount, Scratch Amount R1; Dark Oxidation R2 |
| Antique Bronze | Bronze Roughness R6; Brown Aging = 1 − the inverted edge-wear amount so more is browner; Green Patina R2; Fine Scratches R1 |
| Oxidized Copper | Copper Roughness → *Oxide Roughness* R6; Brown Oxidation (no mask), Turquoise Patina (image mask) R1; Cavity Deposits, Rain Streaking R2 |
| Military Painted Steel | Scratches R1; Rust Amount R2; Earth / Mud → *Mud* R2 (no slash labels) |
| Old Enamel Metal | Crazing → *Enamel Grain* R5 (the enamel's own grain; there is no crazing layer, so the label says what it moves); Rust Amount R2; Fading, Surface Scratches R1 |
| Rusting Painted Steel | Paint Wear binds the CHIPS mask (index 1, under the paint-loss image mask); Deep Rust R1 (no mask); Fresh Rust, Rust Runoff, Recess Grime R2 |
| Automotive Painted Metal | Orange Peel R5; Clearcoat Swirls, Fine Scratches R1; Scratch Depth R4 (the scratches cut into the clearcoat since 0.12.2) |
| Burned Painted Metal | *Paint Remaining* not implementable: the paint has no chips mask; replaced by *Crater Amount* (coverage of the crater interiors and floors, 1 = authored). Blister Relief R4 (Levels span). Scorching → the scorched blister rims' opacity (no "Scorched paint" layer). Soot R2. |
| Heavy Rust | Rust Coverage, Fresh Orange Rust R1; Flake Size → *Flake Scale* R3 (the flakes' image mask and detail; a larger scale means smaller flakes, so the label says scale); Deep Cavity Rust R2 |
| Surface Rust | Brown Rust, Orange Flash Rust R1; Rust Scale scales both oxide masks (they nest the same image); Surface Relief binds the intact steel (the oxide has no height) |
| Wet Rust | Wetness, Dry Scale Amount R1; Wet Cavities R2 |
| Galvanized Steel | Spangle Size → *Spangle Scale* R3; White Oxidation, Scratches R1 |
| Welded Steel | Weld Relief R4 (the weld pools' Levels span); Weld Pattern Scale R3 (pools and shoulders); Gold Heat Tint → *Heat Tint* (the tempered shoulders' opacity, the only tint layer); *Blue Heat Tint* omitted (no blue temper layer); Weld Soot R2 |
| Heat-Treated Metal | the four temper amounts R1 (procedural gradient masks); Heat Pattern Scale is the compound × over the eight gradient masks' noise scale — the RNA minimum of 1 means it cannot go below the authored ×1 |
| Greasy Machinery | Scratches, Oil Film, Dust in Grease R1; Grease Buildup R2; *Oil Drips* omitted (no drips layer) and *Grease Deposits* (the thick grease layer's opacity) added |
| Abandoned Machinery | Deep Corrosion R1 (no mask); Fresh Rust, Oil Leakage, Grime R2 |
| Injection-Molded Plastic | Molding Grain R5; Grain Scale R3; Flow Variation, Micro Texture R1 |
| Worn Hard Plastic | Scratches (stress-whitened abrasion), Fingerprints (oily handling smears) R1; Dirt R2 |
| Soft-Touch Plastic | Surface Grain R5; Abrasion, Handling Oils R1 |
| Sun-Damaged Plastic | Cracking (open weathering fissures), Chalkiness R1; Crack Depth R4 |
| Dirty Rubber | Grain Amount R5; Scoring R1; Embedded Dirt R2 |
| Cracked Old Rubber | Crack Scale scales the two `rubber_cracks_v2` masks (the base layer is polymer grain, not cracks); Crack Depth is the open cracks' depth; Dry Grain R5 (the aged rubber's grain; no "Fine dry grain" layer); Bleaching R1; Dust in Cracks R2 |
| Fresh Oak | Grain Scale R3 (grain, vessel and fibre masks together); Pore Amount, Fine Fibers R1; Pore Depth R4 |
| Old Worn Wood | Dark Pores, Scratches R1; *Dents* omitted (no dents layer); Crevice Dirt R2 |
| Painted Wood | Paint Wear binds the paint film's inverted EDGE_WEAR mask (its chips); Wood Grain R5; Paint Scratches R1; Crevice Dirt R2 |
| Weathered Outdoor Wood | Raised Fibers R1 (an image mask, not WORLD_NORMAL); Splitting R1; Split Depth R4; Moisture Streaks, Crevice Dirt R2 |
| Burnt Wood | Wood Color → *Char Color* (the carbonized timber; no underlying timber layer); Charring → the charcoal fissures' opacity; Char Relief → the carbonized timber's relief; Heat Browning R1 |
| Oiled Dark Wood | Pore Amount, Fingerprints, Finish Scratches R1 |
| Rough Concrete | Aggregate Amount, Pore Amount (the air pockets) R1; Pore Depth R4; *Air Pockets* merged into Pore Amount (one layer) |
| Old Dirty Concrete | Pores R1; *Cracks* and *Crack Depth* omitted (no cracks layer); *Mineral Grime* (embedded mineral grime opacity) added; Water Stains, Dirt R2 |
| Painted Concrete | Paint Wear binds the paint's inverted EDGE_WEAR mask; Concrete Pitting R1; *Paint Cracks* omitted (no such layer); Recess Dirt R2 |
| Polished Marble | Polish / Roughness → *Surface Roughness* (one direction, like every other roughness control); Vein Scale R3; Vein Detail R5; Warm Mineral Variation, Micro Scratches R1 |
| Granite | Grain Scale R3; Feldspar, Mica, Quartz amounts R1 |
| Weathered Stone | Erosion R1; Erosion Depth R4; Cavity Darkening, Moss Amount R2; Moisture Stains R1 (an image mask, not DRIPS) |
| Worn Leather | Creases, Scratches R1; Dark Recesses R2 |
| Cracked Old Leather | Cracking, Fading R1; Crack Depth R4; Deep Stains R2 |
| Denim Fabric | Weave Scale R3; Visible Weft, Abrasion R1; Dirt R2 |
| Canvas Fabric | Weave Scale R3; Fiber Roughness → *Loose Fibers* (it moves the fibre layer's opacity, not a roughness) and Staining R1; Fold Dirt R2 |
| Sci-Fi Panel Surface | Coating Wear binds the coating's inverted EDGE_WEAR mask; Repair Patch Amount R2 (its mask is CAVITY_DIRT); Service Scratches R1; Recess Grime R2 |
| Post-Apocalyptic Scrap Metal | Old Paint Remaining = 1 − the inverted EDGE_WEAR amount; Deep Rust R1 (image mask); Gouges R1; Oil Leakage, Cavity Grime R2 |

Since 0.12.2 the three materials authored before the others changed too: Worn Painted Metal's
*Dirt Amount* and *Rust Amount* and Heavy Chipped Paint's *Dust Amount* are coverage-gated
amounts (R2), and Heavy Chipped Paint's *Paint Loss Black* / *Paint Loss White* are
*Paint Loss* (the position of the paint-loss thresholds, the primer ring following) and
*Paint Loss Contrast* (their softness). Factory Powder-Coated Metal is unchanged.

## Visual problems found in the recipes

Found while validating the controls (`scripts/render_master_controls.py`). 0.12.2 corrected
the ones a top-level control depended on (recipe versions bumped to 2, so existing instances
keep their authored bases and can take the update): Galvanized Steel's white oxidation now
dusts the brighter half of the spangles at a restrained opacity; Burnt Wood's and Burned
Painted Metal's ash lost their dust-speckle multiply for a soft noise breakup and show on
upward surfaces; Scratched Aluminum's dents are sparse real recesses (scale 18, height 0.47,
opacity 0.6) that Dent Amount and Dent Depth move on their own; Automotive Painted Metal's
fine clearcoat scratches cut into the coat (0.494) instead of standing proud; Polished
Chrome's wipe haze (roughness 0.30 at 35 %) and Greasy Machinery's dust in the grease (wider
mask, 70 %) read at their defaults and move with Wipe Marks and Dust in Grease. The remaining
findings are recorded as they stand.

- Raw Industrial Steel: the mill-scale layer (no mask, full opacity) covers the rolled
  steel completely; its grain and roughness never show, only its height.
- Oxidized Copper: the brown oxide covers the copper sheet completely; bare copper shows
  only when the oxide's opacity drops, and renders pale under AgX.
- Cast Iron: the oxide skin's inverted edge mask (amount 0.10) leaves the casting visible
  on the bevels only; the pinpoint oxidation (dark rust) and cavity deposits are nearly
  invisible on the near-black skin.
- Antique Bronze: the aged brown surface covers the casting except at edges.
- CAVITY_DIRT generator: amount 0 keeps full dirt wherever AO < 1/contrast; amount only
  widens the transition band. RUST keeps rust in deep cavities at amount 0 and DRIPS keeps
  its cavity streaks (the reason for rule R2). The generators were not changed: the gate
  lives in the control.
- Sci-Fi *Service abrasion* (0.518) and Post-Apocalyptic *Abraded coating edges* (0.51) sit
  below the coatings they abrade (0.535–0.575 and 0.625–0.715) — cuts relative to the
  coating, raised only where the coating is already worn through — and were left as authored.
- Fixed in 0.12.2 (above): Galvanized white oxidation, the two ash layers, the aluminum
  dents, the automotive clearcoat scratches.

## Validation

- `tests/integration/master_controls.py` (background): every recipe's controls resolve;
  labels are artist-facing; moving each control between its extremes changes the composited
  outputs (a depth lowers height, an inverted control lowers its mask amount, a roughness
  control raises roughness); rename, reorder, duplicate, delete, save and reopen.
- `tests/integration/master_controls_ui.py` (real window): the inspector draws direct and
  computed controls as their properties; undo and redo of direct, inverted and multiplier
  edits one step each; a duplicate's controls stay apart; a deleted target shows as
  unavailable and undo brings it back.
- `scripts/render_master_controls.py` renders every control at its extremes on the beveled
  cavity cube with real mesh maps (Cycles), one contact sheet per material, and records the
  per-channel change in `report.json`. All fifty sheets were inspected by eye for 0.12.1 and
  the changed materials again for 0.12.2; the automated tests do not claim visual quality.
  Fine features (micro scratches, pores, fingerprints) read faintly at 224 px;
  `--size 400 --control "Label"` renders one control larger.
- Coverage-gated amounts (0.12.2): `tests/integration/master_controls.py` sets each one to 0
  and asserts the composite equals the layer hidden, restores the authored value and asserts
  the authored composite returns, and checks that every remaining direct amount belongs to a
  generator that is none at 0.
