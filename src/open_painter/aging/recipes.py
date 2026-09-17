"""What each smart material *is*, for the aging simulation.

Applying a catalog recipe tells the Material Aging panel the construction it paints —
"Canvas Fabric" is canvas, "Automotive Painted Metal" is steel under a grey primer, a red
base coat and a clear coat — and suggests the history the recipe already depicts, so *Age
Material* continues the look instead of starting from painted steel. Recipes the table does
not name are read from their category and their names.
"""
from .materials import MATERIALS

# name -> dict(material, coats=(conversion, primer, paint, topcoat ids or None), paint_color, condition,
#              environment, years, usage): construction always; the history only for a set that is not aged yet.
STEEL = "CARBON_STEEL"
BLUE = (0.04, 0.13, 0.24)
CONSTRUCTIONS = {
    "Clean Brushed Steel": dict(material="STAINLESS_STEEL", environment="INDOOR", years=1, usage="LIGHT"),
    "Worn Brushed Steel": dict(material="STAINLESS_STEEL", environment="INDOOR", years=8, usage="HEAVY"),
    "Machined Aluminum": dict(material="ALUMINUM", environment="INDOOR", years=1, usage="LIGHT"),
    "Scratched Aluminum": dict(material="ALUMINUM", environment="INDOOR", years=6, usage="HEAVY"),
    "Cast Iron": dict(material="CAST_IRON", environment="STORAGE", years=15, usage="LIGHT"),
    "Raw Industrial Steel": dict(material=STEEL, environment="INDUSTRIAL", years=2, usage="MODERATE"),
    "Polished Chrome": dict(material=STEEL, coats=("CHROME_PLATING", None, None, None), environment="INDOOR", years=3, usage="MODERATE"),
    "Old Brass": dict(material="BRASS", environment="INDOOR", years=30, usage="MODERATE"),
    "Antique Bronze": dict(material="BRONZE", environment="OUTDOOR_URBAN", years=60, usage="LIGHT"),
    "Oxidized Copper": dict(material="COPPER", environment="COASTAL", years=25, usage="NONE"),
    "Worn Painted Metal": dict(material=STEEL, coats=(None, "EPOXY_PRIMER", "POLYURETHANE_PAINT", None), paint_color=BLUE,
                               environment="OUTDOOR_URBAN", years=10, usage="MODERATE"),
    "Heavy Chipped Paint": dict(material=STEEL, coats=(None, "ZINC_PRIMER", "OIL_PAINT", None), paint_color=(0.4, 0.17, 0.02), condition=.35,
                                environment="INDUSTRIAL", years=20, usage="HEAVY"),
    "Factory Powder-Coated Metal": dict(material=STEEL, coats=(None, None, "POWDER_COAT", None), paint_color=(0.05, 0.07, 0.07),
                                        environment="INDOOR", years=1, usage="LIGHT"),
    "Military Painted Steel": dict(material=STEEL, coats=(None, "ZINC_PRIMER", "OIL_PAINT", None), paint_color=(0.07, 0.08, 0.03),
                                   environment="OUTDOOR_URBAN", years=12, usage="HEAVY"),
    "Old Enamel Metal": dict(material="CAST_IRON", coats=(None, None, "ENAMEL", None), paint_color=(0.61, 0.54, 0.4),
                             environment="HUMID_INTERIOR", years=40, usage="MODERATE"),
    "Rusting Painted Steel": dict(material=STEEL, coats=(None, None, "OIL_PAINT", None), paint_color=(0.24, 0.04, 0.03),
                                  environment="COASTAL", years=12, usage="LIGHT"),
    "Automotive Painted Metal": dict(material=STEEL, coats=(None, "EPOXY_PRIMER", "POLYURETHANE_PAINT", "CLEAR_COAT"), paint_color=(0.31, 0.01, 0.02),
                                     environment="OUTDOOR_URBAN", years=4, usage="MODERATE"),
    "Burned Painted Metal": dict(material=STEEL, coats=(None, None, "OIL_PAINT", None), paint_color=(0.03, 0.04, 0.02), condition=.2,
                                 environment="INDUSTRIAL", years=5, usage="NONE"),
    "Heavy Rust": dict(material=STEEL, environment="COASTAL", years=25, usage="NONE"),
    "Surface Rust": dict(material=STEEL, environment="HUMID_INTERIOR", years=3, usage="LIGHT"),
    "Wet Rust": dict(material=STEEL, environment="TROPICAL", years=15, usage="NONE"),
    "Galvanized Steel": dict(material="GALVANIZED_STEEL", environment="OUTDOOR_URBAN", years=5, usage="LIGHT"),
    "Welded Steel": dict(material=STEEL, environment="INDUSTRIAL", years=1, usage="MODERATE"),
    "Heat-Treated Metal": dict(material=STEEL, environment="INDOOR", years=1, usage="LIGHT"),
    "Greasy Machinery": dict(material=STEEL, environment="INDUSTRIAL", years=10, usage="HEAVY"),
    "Abandoned Machinery": dict(material=STEEL, coats=(None, None, "OIL_PAINT", None), paint_color=(0.03, 0.1, 0.08), condition=.4,
                                environment="INDUSTRIAL", years=30, usage="NONE"),
    "Injection-Molded Plastic": dict(material="ABS", environment="INDOOR", years=0, usage="NONE"),
    "Worn Hard Plastic": dict(material="ABS", environment="INDOOR", years=8, usage="HEAVY"),
    "Soft-Touch Plastic": dict(material="RUBBER", environment="INDOOR", years=3, usage="MODERATE"),
    "Sun-Damaged Plastic": dict(material="POLYPROPYLENE", environment="ARID", years=8, usage="LIGHT"),
    "Dirty Rubber": dict(material="RUBBER", environment="OUTDOOR_URBAN", years=4, usage="HEAVY"),
    "Cracked Old Rubber": dict(material="RUBBER", environment="ARID", years=20, usage="LIGHT"),
    "Fresh Oak": dict(material="OAK", environment="INDOOR", years=0, usage="NONE"),
    "Old Worn Wood": dict(material="OAK", environment="INDOOR", years=40, usage="HEAVY"),
    "Painted Wood": dict(material="PINE", coats=(None, None, "ACRYLIC_PAINT", None), paint_color=(0.04, 0.16, 0.23),
                         environment="OUTDOOR_URBAN", years=8, usage="LIGHT"),
    "Weathered Outdoor Wood": dict(material="PINE", environment="OUTDOOR_URBAN", years=20, usage="NONE"),
    "Burnt Wood": dict(material="OAK", environment="STORAGE", years=2, usage="NONE"),
    "Oiled Dark Wood": dict(material="WALNUT", coats=(None, None, None, "OIL_FINISH"), environment="INDOOR", years=10, usage="MODERATE"),
    "Rough Concrete": dict(material="CONCRETE", environment="OUTDOOR_URBAN", years=5, usage="NONE"),
    "Old Dirty Concrete": dict(material="CONCRETE", environment="INDUSTRIAL", years=40, usage="NONE"),
    "Painted Concrete": dict(material="CONCRETE", coats=(None, None, "ACRYLIC_PAINT", None), paint_color=(0.62, 0.59, 0.49),
                             environment="OUTDOOR_URBAN", years=10, usage="NONE"),
    "Polished Marble": dict(material="MARBLE", environment="INDOOR", years=20, usage="MODERATE"),
    "Granite": dict(material="GRANITE", environment="OUTDOOR_URBAN", years=30, usage="LIGHT"),
    "Weathered Stone": dict(material="GRANITE", environment="TROPICAL", years=80, usage="NONE"),
    "Worn Leather": dict(material="LEATHER", environment="INDOOR", years=12, usage="HEAVY"),
    "Cracked Old Leather": dict(material="LEATHER", environment="ARID", years=30, usage="MODERATE"),
    "Denim Fabric": dict(material="DENIM", environment="INDOOR", years=3, usage="HEAVY"),
    "Canvas Fabric": dict(material="CANVAS", environment="OUTDOOR_URBAN", years=4, usage="MODERATE"),
    "Sci-Fi Panel Surface": dict(material="ALUMINUM", coats=(None, "EPOXY_PRIMER", "POLYURETHANE_PAINT", None), paint_color=(0.04, 0.05, 0.06),
                                 environment="STORAGE", years=6, usage="HEAVY"),
    "Post-Apocalyptic Scrap Metal": dict(material=STEEL, coats=(None, None, "OIL_PAINT", None), paint_color=(0.3, 0.18, 0.03), condition=.25,
                                         environment="INDUSTRIAL", years=35, usage="HEAVY"),
    # The shelf's surface presets (ui/dock/assets.MATERIALS).
    "Painted Steel": dict(material=STEEL, coats=(None, "EPOXY_PRIMER", "POLYURETHANE_PAINT", None), paint_color=(0.08, 0.35, 0.6),
                          environment="INDOOR", years=2, usage="LIGHT"),
    "Dirty Plastic": dict(material="ABS", environment="INDUSTRIAL", years=6, usage="HEAVY"),
    "Rough Wood": dict(material="PINE", environment="STORAGE", years=10, usage="MODERATE"),
    "Fine Wood": dict(material="OAK", environment="INDOOR", years=1, usage="LIGHT"),
    "Weathered Paint": dict(material=STEEL, coats=(None, None, "OIL_PAINT", None), paint_color=(0.2, 0.25, 0.2), condition=.5,
                            environment="OUTDOOR_URBAN", years=15, usage="LIGHT"),
    "Brushed Steel": dict(material="STAINLESS_STEEL", environment="INDOOR", years=1, usage="LIGHT"),
    "Copper": dict(material="COPPER", environment="INDOOR", years=1, usage="LIGHT"),
    "Matte Rubber": dict(material="RUBBER", environment="INDOOR", years=1, usage="LIGHT"),
    "Porcelain": dict(material="CERAMIC", environment="INDOOR", years=5, usage="MODERATE"),
    "Red Paint": dict(material=STEEL, coats=(None, "EPOXY_PRIMER", "POLYURETHANE_PAINT", None), paint_color=(0.48, 0.025, 0.018),
                      environment="INDOOR", years=1, usage="LIGHT"),
}

# Category and name keywords for recipes the table does not name (user libraries, later catalog entries).
KEYWORDS = (
    ("aluminum", "ALUMINUM"), ("aluminium", "ALUMINUM"), ("brass", "BRASS"), ("bronze", "BRONZE"), ("copper", "COPPER"),
    ("chrome", "CARBON_STEEL"), ("stainless", "STAINLESS_STEEL"), ("iron", "CAST_IRON"), ("galvan", "GALVANIZED_STEEL"),
    ("zinc", "ZINC"), ("titanium", "TITANIUM"), ("steel", "CARBON_STEEL"), ("metal", "CARBON_STEEL"),
    ("oak", "OAK"), ("pine", "PINE"), ("walnut", "WALNUT"), ("mahogany", "MAHOGANY"), ("plywood", "PLYWOOD"), ("mdf", "MDF"),
    ("wood", "OAK"), ("timber", "OAK"),
    ("rubber", "RUBBER"), ("silicone", "SILICONE"), ("nylon", "NYLON"), ("polycarbonate", "POLYCARBONATE"), ("pvc", "PVC"),
    ("plastic", "ABS"), ("polymer", "ABS"),
    ("concrete", "CONCRETE"), ("cement", "CONCRETE"), ("brick", "BRICK"), ("plaster", "PLASTER"), ("marble", "MARBLE"),
    ("granite", "GRANITE"), ("stone", "GRANITE"), ("ceramic", "CERAMIC"), ("tile", "CERAMIC"),
    ("leather", "LEATHER"), ("hide", "LEATHER"), ("denim", "DENIM"), ("canvas", "CANVAS"), ("wool", "WOOL"),
    ("fabric", "CANVAS"), ("cloth", "CANVAS"), ("textile", "CANVAS"),
)
CATEGORY_DEFAULTS = {
    "Metals": "CARBON_STEEL", "Painted and Coated Metals": "CARBON_STEEL", "Rust, Damage and Industrial": "CARBON_STEEL",
    "Plastic and Rubber": "ABS", "Wood": "OAK", "Stone, Concrete and Construction": "CONCRETE", "Fabric and Leather": "CANVAS",
    "Specialized Hero Materials": "CARBON_STEEL",
}
PAINT_WORDS = ("paint", "coat", "enamel", "film", "lacquer")


def infer(name, recipe=None):
    """A construction for a recipe the table does not name: keywords, then the category; a
    layer named like paint makes it painted steel (or painted whatever it is) in that colour."""
    lowered = name.lower()
    material = next((m for word, m in KEYWORDS if word in lowered), None)
    if material is None and recipe is not None:
        material = CATEGORY_DEFAULTS.get(recipe.get("category", ""))
    if material is None or material not in MATERIALS:
        return None
    result = dict(material=material)
    layers = (recipe or {}).get("layers", ())
    painted = next((l for l in layers if any(w in l.get("name", "").lower() for w in PAINT_WORDS)), None)
    if painted is not None and MATERIALS[material].family in {"METAL", "WOOD", "MINERAL"}:
        result.update(coats=(None, "EPOXY_PRIMER" if MATERIALS[material].family == "METAL" else None, "ACRYLIC_PAINT", None),
                      paint_color=tuple(float(c) for c in painted["color"][:3]))
    return result


def construction_for(name, recipe=None):
    """dict(material, coats, paint_color, condition, environment, years, usage) or None."""
    data = CONSTRUCTIONS.get(name) or infer(name, recipe)
    if data is None:
        return None
    coats = tuple(data.get("coats") or (None, None, None, None))
    return dict(material=data["material"], coats=coats, paint_color=data.get("paint_color"), condition=data.get("condition", 1.0),
                environment=data.get("environment"), years=data.get("years"), usage=data.get("usage"))


def adopt(ts, name, recipe=None):
    """Set the set's aging construction from the recipe; a set not aged yet also takes the
    history the recipe depicts. Returns the construction used, or None when unknown."""
    data = construction_for(name, recipe)
    if data is None:
        return None
    from . import engine
    aging = ts.aging
    had_folder = engine.folder_of(ts) is not None
    with engine.batch():
        aging.material = data["material"]
        for field, value in zip(("conversion", "primer", "paint", "topcoat"), data["coats"]):
            setattr(aging, field, value or "NONE")
        if data["paint_color"] is not None:
            aging.paint_color = data["paint_color"]
        aging.coating_condition = float(data["condition"])
        if not had_folder:
            if data["environment"]:
                aging.environment = data["environment"]
            if data["years"] is not None:
                aging.age_years = float(data["years"])
            if data["usage"]:
                aging.usage = data["usage"]
    if had_folder and aging.live:
        engine.apply(ts)       # a new construction can change the roster; the folder rebuilds or re-drives
    return data
