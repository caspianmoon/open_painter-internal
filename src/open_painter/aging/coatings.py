"""Protection layers: primers, paints, top coats and conversion coatings.

Aging attacks the outermost layer first. A coating is described by what it
resists (light, moisture, abrasion), how well it holds on (adhesion), how it
gives (flexibility: a brittle film cracks under thermal cycling) and how it
looks while intact (gloss, colour). Once its integrity drops the layer below is
exposed and the substrate's own processes take over.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Coating:
    id: str
    name: str
    role: str                   # PRIMER | PAINT | TOPCOAT | CONVERSION
    roughness: float            # intact gloss level as roughness
    metallic: float = 0.0
    color: tuple = None         # None: the artist's paint colour
    uv_sensitivity: float = 0.5     # fading, chalking, embrittlement
    adhesion: float = 0.5           # resistance to chipping and delamination
    flexibility: float = 0.5        # resistance to cracking under cycling
    barrier: float = 0.5            # how well it keeps moisture and salts off the layer below
    hardness: float = 0.5           # resistance to scratching
    chalking: float = 0.0           # powdery surface after UV attack
    yellowing: float = 0.0          # warm shift under UV / heat (varnish, clear coats)
    thickness: float = 0.5          # thicker films chip as flakes rather than wear through


COATINGS = {c.id: c for c in (
    Coating("ZINC_PRIMER", "Zinc Primer", "PRIMER", .75, color=(0.55, 0.56, 0.52), uv_sensitivity=.4, adhesion=.8, flexibility=.5,
            barrier=.7, hardness=.5, thickness=.4),
    Coating("EPOXY_PRIMER", "Epoxy Primer", "PRIMER", .70, color=(0.45, 0.40, 0.32), uv_sensitivity=.8, adhesion=.9, flexibility=.4,
            barrier=.85, hardness=.6, chalking=.6, thickness=.4),
    Coating("OIL_PAINT", "Oil Paint", "PAINT", .35, uv_sensitivity=.7, adhesion=.5, flexibility=.3, barrier=.5, hardness=.35,
            chalking=.5, yellowing=.4, thickness=.5),
    Coating("ACRYLIC_PAINT", "Acrylic Paint", "PAINT", .40, uv_sensitivity=.35, adhesion=.6, flexibility=.7, barrier=.5, hardness=.4,
            chalking=.25, thickness=.4),
    Coating("POWDER_COAT", "Powder Coat", "PAINT", .30, uv_sensitivity=.3, adhesion=.85, flexibility=.6, barrier=.85, hardness=.75,
            chalking=.3, thickness=.7),
    Coating("POLYURETHANE_PAINT", "Polyurethane Paint", "PAINT", .25, uv_sensitivity=.2, adhesion=.75, flexibility=.75, barrier=.8,
            hardness=.65, chalking=.15, thickness=.5),
    Coating("EPOXY_PAINT", "Epoxy Paint", "PAINT", .30, uv_sensitivity=.8, adhesion=.9, flexibility=.35, barrier=.9, hardness=.7,
            chalking=.7, yellowing=.3, thickness=.6),
    Coating("CLEAR_COAT", "Clear Coat", "TOPCOAT", .12, color=None, uv_sensitivity=.35, adhesion=.6, flexibility=.6, barrier=.6,
            hardness=.6, yellowing=.2, thickness=.3),
    Coating("VARNISH", "Varnish", "TOPCOAT", .25, color=None, uv_sensitivity=.7, adhesion=.5, flexibility=.4, barrier=.5, hardness=.45,
            chalking=.2, yellowing=.6, thickness=.3),
    Coating("GALVANIZATION", "Galvanization", "CONVERSION", .42, metallic=1.0, color=(0.80, 0.80, 0.78), uv_sensitivity=.0, adhesion=.95,
            flexibility=.8, barrier=.7, hardness=.45, thickness=.3),
    Coating("ANODIZING", "Anodizing", "CONVERSION", .30, metallic=.9, color=(0.62, 0.64, 0.66), uv_sensitivity=.2, adhesion=.95,
            flexibility=.9, barrier=.8, hardness=.85, thickness=.2),
    Coating("CHROME_PLATING", "Chrome Plating", "CONVERSION", .08, metallic=1.0, color=(0.55, 0.56, 0.55), uv_sensitivity=.0, adhesion=.85,
            flexibility=.3, barrier=.75, hardness=.9, thickness=.15),
    Coating("ENAMEL", "Vitreous Enamel", "PAINT", .12, uv_sensitivity=.15, adhesion=.6, flexibility=.05, barrier=.95, hardness=.95,
            chalking=.0, yellowing=.05, thickness=.6),
    Coating("OIL_FINISH", "Oil Finish", "TOPCOAT", .45, color=None, uv_sensitivity=.6, adhesion=.4, flexibility=.9, barrier=.35,
            hardness=.2, chalking=.0, yellowing=.3, thickness=.1),
)}

ROLES = ("PRIMER", "PAINT", "TOPCOAT", "CONVERSION")


def items(role=None):
    """(id, name, description) of coatings, optionally one role, for enum properties."""
    return [(c.id, c.name, f"{c.role.title()}: adhesion {c.adhesion:.0%}, UV resistance {1 - c.uv_sensitivity:.0%}")
            for c in COATINGS.values() if role is None or c.role == role]
