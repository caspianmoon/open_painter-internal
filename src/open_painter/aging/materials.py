"""Substrate materials and their aging responses.

A material is described by how it *responds*, not by what wear it should show:
how fast ultraviolet light, moisture, oxygen, salts, heat and handling change it,
what its oxide looks like, how hard, porous and brittle it is. Every process in
``model`` reads these numbers, so the same environment ages copper, oak and ABS
differently without a preset per outcome. Values are 0–1 sensitivities unless
noted; colours are linear RGB.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Material:
    id: str
    name: str
    family: str                     # METAL | WOOD | POLYMER | MINERAL
    color: tuple
    roughness: float
    metallic: float
    relief: str = ""                # generated detail source id for the substrate surface, or ''
    procedural: str = ""            # procedural grain when no generated source fits
    procedural_scale: float = 24.0
    uv_sensitivity: float = 0.0     # fading, chalking, embrittlement under light
    moisture_sensitivity: float = 0.0   # swelling, staining, biological growth
    oxidation_rate: float = 0.0     # surface oxide growth on the bare material
    corrosion_rate: float = 0.0     # localized attack where moisture and salt reach damage
    salt_sensitivity: float = 0.0   # how much chlorides accelerate corrosion
    patina: str = "NONE"            # RUST | VERDIGRIS | TARNISH | WHITE_OXIDE | GREY | YELLOW | NONE
    patina_color: tuple = (0.5, 0.5, 0.5)
    hardness: float = 0.5           # resistance to scratching and abrasion
    porosity: float = 0.0           # holds moisture and dirt; feeds biological growth
    thermal_sensitivity: float = 0.0    # checking, cracking under temperature cycles
    bio_susceptibility: float = 0.0     # mould, algae, moss
    processes: tuple = field(default=())


def _metal(id, name, color, roughness, *, relief="brushed_relief_v2", **kw):
    return Material(id, name, "METAL", color, roughness, 1.0, relief=relief, **kw)


def _wood(id, name, color, roughness, **kw):
    return Material(id, name, "WOOD", color, roughness, 0.0, relief="timber_relief_v2", procedural="WOOD_GRAIN",
                    procedural_scale=6.0, **kw)


def _polymer(id, name, color, roughness, **kw):
    return Material(id, name, "POLYMER", color, roughness, 0.0, relief="polymer_grain", **kw)


def _mineral(id, name, color, roughness, **kw):
    return Material(id, name, "MINERAL", color, roughness, 0.0, **kw)


def _textile(id, name, color, roughness, *, relief="fabric_weave", **kw):
    return Material(id, name, "TEXTILE", color, roughness, 0.0, relief=relief, **kw)


def _leather(id, name, color, roughness, **kw):
    return Material(id, name, "LEATHER", color, roughness, 0.0, relief="leather_relief_v2", **kw)


STEEL = (0.68, 0.69, 0.68)
RUST = (0.230, 0.059, 0.018)
VERDIGRIS = (0.055, 0.260, 0.175)
TARNISH = (0.105, 0.056, 0.018)
WHITE_OXIDE = (0.80, 0.80, 0.78)
WOOD_GREY = (0.42, 0.41, 0.38)
YELLOWED = (0.80, 0.68, 0.36)

MATERIALS = {m.id: m for m in (
    # Metals -------------------------------------------------------------------
    _metal("CARBON_STEEL", "Carbon Steel", STEEL, .45, oxidation_rate=.9, corrosion_rate=1.0, salt_sensitivity=1.0,
           patina="RUST", patina_color=RUST, hardness=.75,
           processes=("OXIDATION", "ATMOSPHERIC_CORROSION", "SALT_DEPOSITION", "WATER_STAINING", "ABRASION", "HANDLING", "IMPACT")),
    _metal("STAINLESS_STEEL", "Stainless Steel", (0.72, 0.73, 0.72), .30, oxidation_rate=.05, corrosion_rate=.05, salt_sensitivity=.6,
           patina="RUST", patina_color=(0.30, 0.16, 0.08), hardness=.85,
           processes=("ATMOSPHERIC_CORROSION", "SALT_DEPOSITION", "ABRASION", "HANDLING")),
    _metal("CAST_IRON", "Cast Iron", (0.66, 0.64, 0.60), .64, relief="cast_iron_grain", oxidation_rate=1.0, corrosion_rate=.9,
           salt_sensitivity=.9, patina="RUST", patina_color=(0.074, 0.022, 0.012), hardness=.6, porosity=.2,
           processes=("OXIDATION", "ATMOSPHERIC_CORROSION", "WATER_STAINING", "SOIL_DEPOSITION", "IMPACT")),
    _metal("ALUMINUM", "Aluminum", (0.916, 0.923, 0.924), .29, oxidation_rate=.35, corrosion_rate=.2, salt_sensitivity=.7,
           patina="WHITE_OXIDE", patina_color=WHITE_OXIDE, hardness=.4,
           processes=("OXIDATION", "SALT_DEPOSITION", "ABRASION", "HANDLING", "IMPACT")),
    _metal("COPPER", "Copper", (1.000, 0.650, 0.527), .34, oxidation_rate=.8, corrosion_rate=.3, salt_sensitivity=.8,
           patina="VERDIGRIS", patina_color=VERDIGRIS, hardness=.35,
           processes=("OXIDATION", "PATINATION", "SALT_DEPOSITION", "WATER_STAINING", "HANDLING")),
    _metal("BRASS", "Brass", (0.780, 0.570, 0.260), .34, relief="brass_tarnish", oxidation_rate=.6, corrosion_rate=.2, salt_sensitivity=.6,
           patina="TARNISH", patina_color=TARNISH, hardness=.45,
           processes=("OXIDATION", "PATINATION", "HANDLING", "ABRASION")),
    _metal("BRONZE", "Bronze", (0.610, 0.375, 0.160), .44, relief="cast_iron_grain", oxidation_rate=.6, corrosion_rate=.25, salt_sensitivity=.7,
           patina="VERDIGRIS", patina_color=(0.12, 0.30, 0.22), hardness=.5,
           processes=("OXIDATION", "PATINATION", "SALT_DEPOSITION", "WATER_STAINING", "HANDLING")),
    _metal("ZINC", "Zinc", (0.876, 0.869, 0.854), .40, relief="zinc_spangle", oxidation_rate=.5, corrosion_rate=.3, salt_sensitivity=.8,
           patina="WHITE_OXIDE", patina_color=(0.75, 0.76, 0.74), hardness=.3,
           processes=("OXIDATION", "SALT_DEPOSITION", "WATER_STAINING")),
    _metal("GALVANIZED_STEEL", "Galvanized Steel", (0.80, 0.80, 0.78), .42, relief="zinc_spangle", oxidation_rate=.35, corrosion_rate=.12,
           salt_sensitivity=.9, patina="WHITE_OXIDE", patina_color=(0.75, 0.76, 0.74), hardness=.6,
           processes=("OXIDATION", "ATMOSPHERIC_CORROSION", "SALT_DEPOSITION", "WATER_STAINING", "ABRASION")),
    _metal("TITANIUM", "Titanium", (0.62, 0.60, 0.58), .40, oxidation_rate=.05, corrosion_rate=.02, salt_sensitivity=.1,
           patina="TARNISH", patina_color=(0.35, 0.30, 0.25), hardness=.9,
           processes=("ABRASION", "HANDLING")),
    # Wood ---------------------------------------------------------------------
    _wood("OAK", "Oak", (0.52, 0.36, 0.20), .62, uv_sensitivity=.7, moisture_sensitivity=.6, patina="GREY", patina_color=WOOD_GREY,
          hardness=.5, porosity=.5, thermal_sensitivity=.5, bio_susceptibility=.5,
          processes=("BLEACHING", "WOOD_GREYING", "WOOD_CHECKING", "WATER_ABSORPTION", "SOIL_DEPOSITION", "BIOLOGICAL_GROWTH", "ABRASION", "HANDLING")),
    _wood("PINE", "Pine", (0.74, 0.56, 0.32), .66, uv_sensitivity=.85, moisture_sensitivity=.8, patina="GREY", patina_color=WOOD_GREY,
          hardness=.3, porosity=.7, thermal_sensitivity=.7, bio_susceptibility=.7,
          processes=("BLEACHING", "WOOD_GREYING", "WOOD_CHECKING", "WATER_ABSORPTION", "SOIL_DEPOSITION", "BIOLOGICAL_GROWTH", "ABRASION", "HANDLING", "IMPACT")),
    _wood("WALNUT", "Walnut", (0.30, 0.19, 0.12), .58, uv_sensitivity=.6, moisture_sensitivity=.5, patina="GREY", patina_color=(0.36, 0.33, 0.30),
          hardness=.55, porosity=.45, thermal_sensitivity=.45, bio_susceptibility=.4,
          processes=("BLEACHING", "WOOD_GREYING", "WOOD_CHECKING", "WATER_ABSORPTION", "SOIL_DEPOSITION", "BIOLOGICAL_GROWTH", "HANDLING")),
    _wood("MAHOGANY", "Mahogany", (0.40, 0.18, 0.10), .55, uv_sensitivity=.55, moisture_sensitivity=.4, patina="GREY", patina_color=(0.40, 0.34, 0.30),
          hardness=.55, porosity=.4, thermal_sensitivity=.4, bio_susceptibility=.35,
          processes=("BLEACHING", "WOOD_GREYING", "WOOD_CHECKING", "WATER_ABSORPTION", "HANDLING")),
    _wood("PLYWOOD", "Plywood", (0.72, 0.56, 0.36), .70, uv_sensitivity=.8, moisture_sensitivity=.95, patina="GREY", patina_color=WOOD_GREY,
          hardness=.3, porosity=.75, thermal_sensitivity=.8, bio_susceptibility=.8,
          processes=("BLEACHING", "WOOD_GREYING", "WOOD_CHECKING", "WATER_ABSORPTION", "SOIL_DEPOSITION", "BIOLOGICAL_GROWTH", "IMPACT")),
    _wood("MDF", "MDF", (0.62, 0.50, 0.36), .78, uv_sensitivity=.5, moisture_sensitivity=1.0, patina="GREY", patina_color=(0.45, 0.40, 0.34),
          hardness=.25, porosity=.9, thermal_sensitivity=.5, bio_susceptibility=.8,
          processes=("WATER_ABSORPTION", "SOIL_DEPOSITION", "BIOLOGICAL_GROWTH", "IMPACT")),
    _wood("TREATED_WOOD", "Pressure-Treated Wood", (0.52, 0.48, 0.30), .68, uv_sensitivity=.6, moisture_sensitivity=.35, patina="GREY",
          patina_color=WOOD_GREY, hardness=.35, porosity=.5, thermal_sensitivity=.6, bio_susceptibility=.15,
          processes=("BLEACHING", "WOOD_GREYING", "WOOD_CHECKING", "SOIL_DEPOSITION", "ABRASION")),
    # Polymers -----------------------------------------------------------------
    _polymer("ABS", "ABS", (0.85, 0.85, 0.83), .45, uv_sensitivity=.9, patina="YELLOW", patina_color=YELLOWED, hardness=.4, thermal_sensitivity=.5,
             processes=("UV_PHOTO_DEGRADATION", "YELLOWING", "POLYMER_EMBRITTLEMENT", "ABRASION", "HANDLING", "SOIL_DEPOSITION")),
    _polymer("PVC", "PVC", (0.88, 0.88, 0.86), .42, uv_sensitivity=.75, patina="YELLOW", patina_color=(0.78, 0.70, 0.45), hardness=.45, thermal_sensitivity=.4,
             processes=("UV_PHOTO_DEGRADATION", "YELLOWING", "CHALKING", "POLYMER_EMBRITTLEMENT", "SOIL_DEPOSITION")),
    _polymer("POLYCARBONATE", "Polycarbonate", (0.90, 0.90, 0.90), .20, uv_sensitivity=.7, patina="YELLOW", patina_color=YELLOWED, hardness=.35,
             processes=("UV_PHOTO_DEGRADATION", "YELLOWING", "POLYMER_EMBRITTLEMENT", "ABRASION", "HANDLING")),
    _polymer("POLYPROPYLENE", "Polypropylene", (0.80, 0.80, 0.78), .50, uv_sensitivity=1.0, patina="YELLOW", patina_color=(0.75, 0.72, 0.55), hardness=.3,
             processes=("UV_PHOTO_DEGRADATION", "CHALKING", "POLYMER_EMBRITTLEMENT", "ABRASION", "SOIL_DEPOSITION")),
    _polymer("POLYETHYLENE", "Polyethylene", (0.82, 0.82, 0.80), .55, uv_sensitivity=.85, patina="YELLOW", patina_color=(0.75, 0.72, 0.55), hardness=.25,
             processes=("UV_PHOTO_DEGRADATION", "CHALKING", "POLYMER_EMBRITTLEMENT", "ABRASION", "SOIL_DEPOSITION")),
    _polymer("NYLON", "Nylon", (0.78, 0.78, 0.76), .48, uv_sensitivity=.6, moisture_sensitivity=.4, patina="YELLOW", patina_color=YELLOWED, hardness=.5,
             processes=("UV_PHOTO_DEGRADATION", "YELLOWING", "ABRASION", "HANDLING")),
    _polymer("RUBBER", "Rubber", (0.06, 0.06, 0.06), .85, uv_sensitivity=.8, patina="GREY", patina_color=(0.22, 0.22, 0.21), hardness=.2, thermal_sensitivity=.6,
             processes=("UV_PHOTO_DEGRADATION", "CHALKING", "POLYMER_EMBRITTLEMENT", "COATING_CRACKING", "ABRASION", "SOIL_DEPOSITION")),
    _polymer("SILICONE", "Silicone", (0.85, 0.83, 0.80), .70, uv_sensitivity=.2, patina="YELLOW", patina_color=(0.80, 0.74, 0.55), hardness=.15,
             processes=("YELLOWING", "SOIL_DEPOSITION", "HANDLING")),
    # Mineral ------------------------------------------------------------------
    _mineral("CONCRETE", "Concrete", (0.58, 0.57, 0.55), .90, relief="cement_relief_v2", uv_sensitivity=.1, moisture_sensitivity=.6,
             patina="GREY", patina_color=(0.32, 0.32, 0.31), hardness=.6, porosity=.7, thermal_sensitivity=.5, bio_susceptibility=.7,
             processes=("WATER_STAINING", "SOIL_DEPOSITION", "SALT_DEPOSITION", "BIOLOGICAL_GROWTH", "IMPACT", "THERMAL_CYCLING")),
    _mineral("BRICK", "Brick", (0.55, 0.26, 0.18), .88, uv_sensitivity=.15, moisture_sensitivity=.6, patina="WHITE_OXIDE",
             patina_color=(0.85, 0.84, 0.80), hardness=.5, porosity=.8, thermal_sensitivity=.5, bio_susceptibility=.7,
             processes=("WATER_STAINING", "SOIL_DEPOSITION", "SALT_DEPOSITION", "BIOLOGICAL_GROWTH", "THERMAL_CYCLING")),
    _mineral("PLASTER", "Plaster", (0.86, 0.84, 0.80), .85, uv_sensitivity=.2, moisture_sensitivity=.9, patina="GREY",
             patina_color=(0.55, 0.53, 0.48), hardness=.2, porosity=.9, thermal_sensitivity=.6, bio_susceptibility=.8,
             processes=("WATER_STAINING", "SOIL_DEPOSITION", "BIOLOGICAL_GROWTH", "COATING_CRACKING", "IMPACT")),
    _mineral("MARBLE", "Marble", (0.85, 0.83, 0.80), .30, relief="marble_veins", uv_sensitivity=.05, moisture_sensitivity=.3,
             patina="GREY", patina_color=(0.55, 0.53, 0.50), hardness=.55, porosity=.25, thermal_sensitivity=.3, bio_susceptibility=.35,
             processes=("WATER_STAINING", "SOIL_DEPOSITION", "BIOLOGICAL_GROWTH", "ABRASION", "HANDLING")),
    _mineral("GRANITE", "Granite", (0.42, 0.40, 0.38), .45, relief="granite_minerals", uv_sensitivity=.02, moisture_sensitivity=.15,
             patina="GREY", patina_color=(0.30, 0.30, 0.29), hardness=.9, porosity=.1, thermal_sensitivity=.2, bio_susceptibility=.25,
             processes=("SOIL_DEPOSITION", "BIOLOGICAL_GROWTH", "HANDLING")),
    _mineral("CERAMIC", "Ceramic", (0.90, 0.88, 0.84), .25, uv_sensitivity=.05, moisture_sensitivity=.1, patina="GREY",
             patina_color=(0.55, 0.53, 0.50), hardness=.8, porosity=.05, thermal_sensitivity=.4, bio_susceptibility=.1,
             processes=("SOIL_DEPOSITION", "ABRASION", "IMPACT", "HANDLING")),
    # Textiles and leather: dyes fade, fibres fray and pill where rubbed, hides burnish where
    # handled and crack when they dry out; all of them soak, stain and mildew.
    _textile("CANVAS", "Canvas", (0.55, 0.47, 0.34), .92, uv_sensitivity=.65, moisture_sensitivity=.8, patina="GREY",
             patina_color=(0.70, 0.66, 0.58), hardness=.15, porosity=.8, thermal_sensitivity=.1, bio_susceptibility=.7,
             processes=("BLEACHING", "ABRASION", "SOIL_DEPOSITION", "WATER_ABSORPTION", "BIOLOGICAL_GROWTH")),
    _textile("DENIM", "Denim", (0.06, 0.10, 0.20), .88, relief="denim_twill_v2", uv_sensitivity=.8, moisture_sensitivity=.75, patina="GREY",
             patina_color=(0.42, 0.50, 0.60), hardness=.2, porosity=.7, thermal_sensitivity=.1, bio_susceptibility=.6,
             processes=("BLEACHING", "ABRASION", "SOIL_DEPOSITION", "WATER_ABSORPTION", "BIOLOGICAL_GROWTH")),
    _textile("WOOL", "Wool", (0.50, 0.45, 0.38), .95, uv_sensitivity=.55, moisture_sensitivity=.7, patina="GREY",
             patina_color=(0.62, 0.58, 0.52), hardness=.1, porosity=.85, thermal_sensitivity=.1, bio_susceptibility=.8,
             processes=("BLEACHING", "ABRASION", "SOIL_DEPOSITION", "WATER_ABSORPTION", "BIOLOGICAL_GROWTH")),
    _leather("LEATHER", "Leather", (0.26, 0.12, 0.05), .55, uv_sensitivity=.6, moisture_sensitivity=.6, patina="TARNISH",
             patina_color=(0.14, 0.07, 0.03), hardness=.3, porosity=.5, thermal_sensitivity=.7, bio_susceptibility=.5,
             processes=("BLEACHING", "ABRASION", "HANDLING", "COATING_CRACKING", "SOIL_DEPOSITION", "WATER_ABSORPTION", "BIOLOGICAL_GROWTH")),
)}

FAMILIES = ("METAL", "WOOD", "POLYMER", "MINERAL", "TEXTILE", "LEATHER")


def items():
    """(id, name, description) per material, grouped by family, for enum properties."""
    result = []
    for family in FAMILIES:
        for material in MATERIALS.values():
            if material.family == family:
                result.append((material.id, material.name, f"{family.title()} · {', '.join(p.replace('_', ' ').lower() for p in material.processes)}"))
    return result
