"""From a simulated state to a layer recipe.

Every outcome of the simulation becomes one fill layer whose masks are the
geometry-aware generators (edge wear and chips read curvature, cavity dirt and
rust read occlusion, dust reads the upward-facing normal, drips read position and
occlusion) or procedural fields, so where the aging lands is decided by the mesh
and how much by the history. Layers are listed bottom to top in causal order:
substrate, its oxide, primer, paint, top coat, then what happens to the film and
what settles on it. Each carries a ``role`` the engine uses to re-drive its
parameters when the settings move, without rebuilding.
"""
from ..smart_materials.catalog import layer, mask, surface_detail
from .materials import MATERIALS

DIRT = (0.049, 0.032, 0.020)
DUST = (0.230, 0.184, 0.133)
SALT = (0.82, 0.83, 0.80)
MOSS = (0.06, 0.12, 0.03)
STREAK = (0.16, 0.10, 0.05)
LIGHT_GREY = (0.72, 0.72, 0.70)


def _mix(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def _lighten(color, amount):
    return _mix(color, (0.86, 0.86, 0.84), amount)


def _rough(base, loss):
    """Gloss loss moves a coat's roughness from its intact value toward matte."""
    return max(0.0, min(1.0, base + (0.9 - base) * loss))


def _amount(value, low=0.02, high=0.85):
    """A generator's coverage amount for a 0–1 state value (never fully closed or open)."""
    return low + (high - low) * max(0.0, min(1.0, value))


def _level(closed, open_, value):
    """A mask's black point for a 0-1 state value: high (little coverage) when new, ``open_`` when fully aged."""
    return closed + (open_ - closed) * max(0.0, min(1.0, value))


def build(state, construction, seed=0):
    """The recipe document: {'layers': [...], 'roles': [...]} for the state.

    ``roles`` runs parallel to ``layers`` and names the outcome each layer
    materialises; the engine keeps that mapping in the folder's custom data.
    """
    o = state.outcomes()
    m = construction.substrate
    coats = construction.coatings
    paint = next((c for c in coats if c.role == "PAINT"), None)
    primer = construction.primer
    topcoat = next((c for c in coats if c.role == "TOPCOAT"), None)
    conversion = next((c for c in coats if c.role == "CONVERSION"), None)
    layers, roles = [], []

    def add(role, spec):
        layers.append(spec)
        roles.append(role)

    # --- substrate --------------------------------------------------------------------
    substrate = dict(color=m.color, roughness=m.roughness, metallic=m.metallic)
    if m.family == "WOOD":
        substrate["color"] = _lighten(m.color, o["bleaching"] * 0.7)
        substrate["roughness"] = min(1.0, m.roughness + o["wood_greying"] * 0.2)
    if m.family == "POLYMER" and not coats:
        substrate["color"] = _mix(_lighten(m.color, o["fade"] * 0.5), m.patina_color, o["yellowing"] * 0.8)
        substrate["roughness"] = _rough(m.roughness, o["gloss_loss"] * 0.6 + o["chalking"] * 0.3)
    detail = surface_detail(m.relief, scale=(1.5, 1.5), color=.05, roughness=.08, height=0.025) if m.relief else None
    add("substrate", layer(f"{m.name} substrate", substrate["color"], substrate["roughness"], substrate["metallic"],
                           procedural=m.procedural or None, scale=m.procedural_scale, height=0.025 if detail else None,
                           generated_source=detail, projection="TRIPLANAR" if m.family in {"WOOD", "MINERAL"} else None))

    # --- what the bare substrate does ---------------------------------------------------
    if m.family == "METAL":
        oxide_name = {"RUST": "Surface rust", "VERDIGRIS": "Oxide bloom", "TARNISH": "Tarnish", "WHITE_OXIDE": "White oxide"}.get(m.patina, "Oxide")
        rough = .92 if m.patina == "RUST" else .75
        add("oxidation", layer(oxide_name, m.patina_color, rough, 0.0 if m.patina in {"RUST", "VERDIGRIS", "WHITE_OXIDE"} else .4,
                               opacity=min(1.0, o["oxidation"] * 1.1), height=0.02 if m.patina == "RUST" else None,
                               generated_source=surface_detail("rust_relief_v2", scale=(1.2, 1.2), color=.25, roughness=.10, height=.04) if m.patina == "RUST" else None,
                               mask=mask("RUST" if m.patina == "RUST" else "CAVITY_DIRT", _amount(o["oxidation"], .05, .95), 40, noise_strength=.6, contrast=2.2),
                               mask_effects=(mask("NOISE", generated_source={"id": "rust_detail", "scale": (2, 2)}, black=.30, white=.75),)))
        if "PATINATION" in m.processes:
            add("patina", layer("Patina", m.patina_color, .70, 0.0, opacity=o["patina"],
                                mask=mask("CAVITY_DIRT", _amount(o["patina"], .15, .95), 30, noise_strength=.55, contrast=1.6),
                                mask_effects=(mask("NOISE", scale=18, procedural="GRUNGE_MEDIUM", black=.25, white=.80),)))
    if m.family == "WOOD":
        # Greying is strongest where sun and rain reach: upward faces when World Normal is baked.
        add("wood_greying", layer("Weathered grey", m.patina_color, .80, 0.0, opacity=o["wood_greying"],
                                  mask=mask("NOISE", scale=9, procedural="WOOD_GRAIN", black=_level(.55, .05, o["wood_greying"]), white=.9),
                                  mask_effects=(mask("DUST", _amount(o["wood_greying"], .2, .95), 30, noise_strength=.45, contrast=1.3,
                                                     optional=True, operation="MULTIPLY", black=.0, white=.8),)))
        add("wood_checking", layer("Checking", _mix(m.color, (0.05, 0.03, 0.02), .7), .85, 0.0, channels=("BASE_COLOR", "HEIGHT"),
                                   opacity=o["wood_checking"], height_value=.42,
                                   mask=mask("NOISE", scale=7, procedural="CRACKS", black=.55, white=.9, contrast=3)))
    if m.family == "MINERAL":
        add("erosion", layer("Weathered surface", _mix(m.color, m.patina_color, .5), min(1.0, m.roughness + .1), 0.0, opacity=o["erosion"],
                             mask=mask("EDGE_WEAR", _amount(o["erosion"], .1, .8), 40, noise_strength=.5, contrast=1.5)))

    # --- the protection stack, bottom to top ---------------------------------------------
    if conversion is not None:
        add("conversion", layer(conversion.name, conversion.color, _rough(conversion.roughness, o["gloss_loss"] * .5), conversion.metallic,
                                opacity=1.0, generated_source=surface_detail("zinc_spangle", color=.06, roughness=.06),
                                mask=mask("CHIPS", _amount(o["coating_loss"], .0, .8), 45, invert=True, noise_strength=.5, contrast=1.4)))
    if primer is not None:
        add("primer", layer(f"{primer.name}", primer.color, _rough(primer.roughness, o["chalking"] * .5), 0.0, opacity=1.0,
                            mask=mask("CHIPS", _amount(o["primer_loss"], .0, .8), 42, invert=True, noise_strength=.45, contrast=1.4)))
    if paint is not None:
        color = construction.paint_color
        color = _lighten(color, o["fade"] * 0.55)
        color = _mix(color, (0.88, 0.86, 0.80), o["chalking"] * 0.5)
        color = _mix(color, (0.80, 0.68, 0.36), o["yellowing"] * 0.4)
        add("paint", layer(f"{paint.name}", color, _rough(paint.roughness, o["gloss_loss"] + o["chalking"] * .5), 0.0, opacity=1.0,
                           generated_source=surface_detail("polymer_grain", color=.02, roughness=.05) if paint.id == "POWDER_COAT" else None,
                           mask=mask("CHIPS", _amount(o["coating_loss"], .0, .8), 42, invert=True, noise_strength=.45, contrast=1.4),
                           mask_effects=(mask("NOISE", scale=5, procedural="GRUNGE_HEAVY", black=.0, white=1.0,
                                              strength=min(1.0, o["corrosion"] * 0.9 + o["coating_loss"] * 0.4), invert=True, operation="MULTIPLY"),)))
    if topcoat is not None:
        tint = _mix((0.5, 0.5, 0.5), (0.62, 0.52, 0.28), o["yellowing"] * 0.6)
        add("topcoat", layer(f"{topcoat.name}", tint, _rough(topcoat.roughness, o["gloss_loss"]), 0.0,
                             channels=("ROUGHNESS",) if o["yellowing"] < 0.02 else ("BASE_COLOR", "ROUGHNESS"), opacity=0.85,
                             blend_mode="OVERLAY" if o["yellowing"] >= 0.02 else None,
                             mask=mask("CHIPS", _amount(o["coating_loss"], .0, .8), 42, invert=True, noise_strength=.45, contrast=1.4)))

    # --- what happens to the film ----------------------------------------------------------
    if coats or m.family == "POLYMER":
        add("cracking", layer("Coating cracks", (0.05, 0.04, 0.03), .9, 0.0, channels=("BASE_COLOR", "ROUGHNESS", "HEIGHT"),
                              opacity=o["coating_cracking"], height_value=.40,
                              mask=mask("NOISE", scale=9, procedural="CRACKS", black=.45, white=.92, contrast=2.5),
                              mask_effects=(mask("DUST", .6, 30, noise_strength=.4, contrast=1.2, optional=True, operation="MULTIPLY", black=.0, white=.6),)))
    add("micro_scratching", layer("Micro scratches", _lighten(construction.paint_color if paint else m.color, .3),
                                  .55 if paint else max(.35, m.roughness - .1), m.metallic if not paint else 0.0,
                                  channels=("ROUGHNESS", "HEIGHT"), opacity=o["micro_scratching"], height_value=.485,
                                  mask=mask("NOISE", generated_source={"id": "abrasion_mask_v2", "scale": (.85, .85)}, black=.035, white=.62,
                                            projection="TRIPLANAR", triplanar_hardness=8)))
    if m.family == "METAL":
        add("handling_polish", layer("Handling polish", m.color, max(0.05, m.roughness - .2), m.metallic, channels=("BASE_COLOR", "ROUGHNESS", "METALLIC"),
                                     opacity=o["handling_polish"], mask=mask("EDGE_WEAR", _amount(o["handling_polish"], .1, .5), 90, noise_strength=.4, contrast=1.3)))
        rust_color = m.patina_color if m.patina == "RUST" else _mix(m.patina_color, (0.02, 0.02, 0.02), .3)
        add("corrosion", layer("Corrosion at damage", rust_color, .95, 0.0, opacity=o["corrosion"], height=0.03,
                               generated_source=surface_detail("rust_relief_v2", scale=(1.6, 1.6), color=.3, roughness=.08, height=.05),
                               mask=mask("CHIPS", _amount(o["coating_loss"] + o["corrosion"] * .5 + (.2 if not coats else 0), .05, .9), 42, noise_strength=.5, contrast=1.3),
                               mask_effects=(mask("NOISE", generated_source={"id": "rust_detail", "scale": (2, 2)}, black=.25, white=.70),)))
    add("impact", layer("Impact dents", _mix(m.color, (0.1, 0.1, 0.1), .2), min(1.0, m.roughness + .2), m.metallic if not coats else 0.0,
                        channels=("ROUGHNESS", "HEIGHT"), opacity=o["impact"], height_value=.44,
                        mask=mask("NOISE", scale=70, procedural="CONCRETE_PORES", black=.72, white=.98, contrast=2)))

    # --- what settles on it ------------------------------------------------------------------
    # Streaks run down from cavities and along the height when Position is baked; procedural streaks otherwise.
    add("water_staining", layer("Water streaks", STREAK if m.family != "METAL" or m.patina != "RUST" else m.patina_color, .85, 0.0,
                                opacity=o["water_staining"], channels=("BASE_COLOR", "ROUGHNESS"),
                                mask=mask("NOISE", scale=6, procedural="GRUNGE_STREAKS", black=_level(.75, .30, o["water_staining"]), white=.95),
                                mask_effects=(mask("DRIPS", _amount(o["water_staining"], .1, .9), 28, noise_strength=.35, contrast=2,
                                                   optional=True, operation="MULTIPLY", black=.0, white=.8),)))
    add("salt", layer("Salt deposits", SALT, .95, 0.0, opacity=o["salt"],
                      mask=mask("CAVITY_DIRT", _amount(o["salt"], .1, .8), 36, noise_strength=.5, contrast=2),
                      mask_effects=(mask("NOISE", scale=24, procedural="GRUNGE_SPOTS", black=.35, white=.85),)))
    # Growth and soil favour shade, the ground and downward faces (Position and World Normal when baked).
    add("biological", layer("Biological growth", MOSS, .9, 0.0, opacity=o["biological"],
                            mask=mask("CAVITY_DIRT", _amount(o["biological"], .2, .9), 22, noise_strength=.6, contrast=1.8),
                            mask_effects=(mask("DUST", .5, 30, noise_strength=.3, contrast=1.2, invert=True, optional=True, operation="MULTIPLY", black=.0, white=.7),
                                          mask("POSITION", .6, 1, contrast=1.5, invert=True, optional=True, operation="MULTIPLY", black=.0, white=.85))))
    add("soil", layer("Soil", DIRT, .92, 0.0, opacity=o["soil"],
                      mask=mask("CAVITY_DIRT", _amount(o["soil"], .15, .9), 32, noise_strength=.55, contrast=2.2),
                      mask_effects=(mask("NOISE", generated_source={"id": "concrete_pores", "scale": (2, 2)}, black=.3, white=.65),
                                    mask("POSITION", .5, 1, contrast=1.2, invert=True, optional=True, operation="MULTIPLY", black=.0, white=.9))))
    # Dust settles on upward faces when World Normal is baked; a dust grunge carries it otherwise.
    add("dust", layer("Dust", DUST, .95, 0.0, opacity=o["dust"],
                      mask=mask("NOISE", scale=40, procedural="GRUNGE_DUST", black=_level(.6, .1, o["dust"]), white=.9),
                      mask_effects=(mask("DUST", _amount(o["dust"], .2, .9), 48, noise_strength=.35, contrast=1.4,
                                         optional=True, operation="MULTIPLY", black=.0, white=.8),)))
    return {"layers": layers, "roles": roles, "version": 1}


# Fields the engine drives on every re-simulation; everything else is the artist's.
DRIVEN = ("opacity", "BASE_COLOR.color", "ROUGHNESS.value", "METALLIC.value", "mask.amount", "mask.strength",
          "mask.black", "mask.white")
