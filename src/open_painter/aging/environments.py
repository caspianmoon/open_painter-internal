"""Where a thing lived and how it was treated, as physical exposure parameters.

An environment preset is a set of exposure values (0–1), not an aging
algorithm: the processes in ``model`` read them and interact, so coastal light,
humidity, temperature swings and salt reinforce one another instead of scaling
a "rust" slider. Usage and maintenance presets are the same idea for people.
"""
from dataclasses import dataclass, asdict, replace


@dataclass(frozen=True)
class Exposure:
    uv: float = .5
    humidity: float = .5
    rain: float = .3
    temperature: float = .5
    temperature_cycles: float = .4
    salt: float = .05
    pollution: float = .2
    soil: float = .3
    wind: float = .3

    def as_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Usage:
    handling: float = .4
    abrasion: float = .3
    impact: float = .1
    cleaning: float = .2

    def as_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Maintenance:
    cleaning: float = .2            # regular dust and soil removal
    protective_treatment: float = .0    # wax, oil, sealant: slows light and moisture attack
    repainting: float = .0          # inclination to repaint once the coating fails (continuous form)
    polishing: float = .0
    rust_removal: float = .0
    wood_treatment: float = .0

    def as_dict(self):
        return asdict(self)


ENVIRONMENTS = {
    "INDOOR": Exposure(uv=.10, humidity=.35, rain=.0, temperature=.45, temperature_cycles=.10, salt=.0, pollution=.10, soil=.25, wind=.0),
    "STORAGE": Exposure(uv=.02, humidity=.55, rain=.0, temperature=.40, temperature_cycles=.20, salt=.0, pollution=.05, soil=.45, wind=.0),
    "HUMID_INTERIOR": Exposure(uv=.05, humidity=.85, rain=.0, temperature=.55, temperature_cycles=.15, salt=.05, pollution=.10, soil=.35, wind=.0),
    "OUTDOOR_URBAN": Exposure(uv=.60, humidity=.55, rain=.50, temperature=.50, temperature_cycles=.55, salt=.10, pollution=.60, soil=.50, wind=.40),
    "COASTAL": Exposure(uv=.70, humidity=.80, rain=.60, temperature=.55, temperature_cycles=.40, salt=.95, pollution=.25, soil=.35, wind=.70),
    "ARID": Exposure(uv=.95, humidity=.15, rain=.05, temperature=.85, temperature_cycles=.85, salt=.05, pollution=.10, soil=.85, wind=.60),
    "INDUSTRIAL": Exposure(uv=.45, humidity=.55, rain=.45, temperature=.55, temperature_cycles=.45, salt=.15, pollution=.90, soil=.75, wind=.35),
    "TROPICAL": Exposure(uv=.85, humidity=.95, rain=.85, temperature=.80, temperature_cycles=.25, salt=.30, pollution=.15, soil=.55, wind=.45),
    "ALPINE": Exposure(uv=.90, humidity=.45, rain=.40, temperature=.25, temperature_cycles=.90, salt=.20, pollution=.05, soil=.30, wind=.80),
    "UNDERGROUND": Exposure(uv=.0, humidity=.90, rain=.10, temperature=.35, temperature_cycles=.05, salt=.10, pollution=.20, soil=.90, wind=.0),
}
ENVIRONMENT_LABELS = {
    "INDOOR": "Indoor", "STORAGE": "Storage", "HUMID_INTERIOR": "Humid Interior", "OUTDOOR_URBAN": "Outdoor Urban",
    "COASTAL": "Coastal", "ARID": "Arid", "INDUSTRIAL": "Industrial", "TROPICAL": "Tropical", "ALPINE": "Alpine",
    "UNDERGROUND": "Underground",
}

USAGE = {
    "NONE": Usage(handling=.0, abrasion=.0, impact=.0, cleaning=.0),
    "LIGHT": Usage(handling=.25, abrasion=.15, impact=.05, cleaning=.15),
    "MODERATE": Usage(handling=.50, abrasion=.40, impact=.15, cleaning=.25),
    "HEAVY": Usage(handling=.82, abrasion=.73, impact=.41, cleaning=.30),
    "ABUSED": Usage(handling=.95, abrasion=.95, impact=.85, cleaning=.05),
}
USAGE_LABELS = {"NONE": "None", "LIGHT": "Light", "MODERATE": "Moderate", "HEAVY": "Heavy", "ABUSED": "Abused"}

MAINTENANCE = {
    "NONE": Maintenance(cleaning=.0),
    "OCCASIONAL": Maintenance(cleaning=.25, protective_treatment=.10, polishing=.05),
    "REGULAR": Maintenance(cleaning=.60, protective_treatment=.40, polishing=.25, rust_removal=.20, wood_treatment=.30),
    "METICULOUS": Maintenance(cleaning=.95, protective_treatment=.85, polishing=.70, rust_removal=.70, wood_treatment=.80),
}
MAINTENANCE_LABELS = {"NONE": "None", "OCCASIONAL": "Occasional", "REGULAR": "Regular", "METICULOUS": "Meticulous"}

EVENT_KINDS = ("REPAINT", "CLEAN", "RUST_REMOVAL", "POLISH", "PROTECTIVE_TREATMENT", "WOOD_TREATMENT")
EVENT_LABELS = {"REPAINT": "Repaint", "CLEAN": "Clean", "RUST_REMOVAL": "Rust Removal", "POLISH": "Polish",
                "PROTECTIVE_TREATMENT": "Protective Treatment", "WOOD_TREATMENT": "Wood Treatment"}


def exposure(preset, **overrides):
    """A preset's exposure with fields replaced, for custom environments."""
    base = ENVIRONMENTS.get(preset, ENVIRONMENTS["OUTDOOR_URBAN"])
    return replace(base, **overrides) if overrides else base
