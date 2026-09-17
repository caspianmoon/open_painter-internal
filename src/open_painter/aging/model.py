"""The aging simulation: accumulated exposure to interacting stresses over a history.

``simulate`` integrates monthly over the asset's life. Each month every process
the construction subscribes to advances its state variables with rates that are
products of exposure, material response and the current state: corrosion needs
exposed substrate *and* moisture *and* salts; cracking needs a film made brittle
by light and heat *and* thermal cycling; delamination needs cracks *and*
moisture *and* poor adhesion. "10 years" alone therefore means nothing — the
history does. Maintenance events (repaint, clean, rust removal, polish,
treatments) rewind the variables they act on at the moment they happen.

Everything here is plain Python so it runs in the pure test runner and in the
Open Graph without Blender.
"""
from dataclasses import dataclass, field, fields, replace
import math

from .coatings import COATINGS
from .environments import Exposure, Maintenance, Usage
from .materials import MATERIALS

STEP = 1.0 / 12.0   # years per integration step
DEFAULT_MAX_AGE = 50.0

# The aging process library: what each process needs, what it changes, which materials
# subscribe (``Material.processes``). ``step`` implements them; the panel and docs read this.
PROCESSES = {
    "OXIDATION": ("Oxidation", "Uniform surface oxide where weather reaches the bare metal", ("moisture", "salt", "pollution"), ("oxidation",)),
    "ATMOSPHERIC_CORROSION": ("Atmospheric corrosion", "Localised attack at damage and under a lifting film", ("moisture", "salt", "pollution", "damage"), ("corrosion",)),
    "PATINATION": ("Patination", "A stable oxide (verdigris, tarnish) replacing raw oxide", ("oxidation", "moisture"), ("patina",)),
    "BLEACHING": ("Bleaching", "Light lifts the colour of bare wood", ("uv",), ("bleaching",)),
    "WOOD_GREYING": ("Greying", "Light and rain turn exposed wood silver-grey", ("uv", "moisture"), ("wood_greying",)),
    "WOOD_CHECKING": ("Checking", "Moisture and temperature swings open cracks along the grain", ("cycles", "humidity"), ("wood_checking",)),
    "WATER_ABSORPTION": ("Water absorption", "Porous surfaces soak up water and stain", ("rain", "humidity"), ("water_staining",)),
    "THERMAL_CYCLING": ("Thermal cycling", "Temperature swings crack brittle surfaces", ("cycles",), ("coating_cracking",)),
    "UV_PHOTO_DEGRADATION": ("UV degradation", "Light fades and dulls polymers and films", ("uv", "temperature"), ("fade", "gloss_loss")),
    "YELLOWING": ("Yellowing", "Light and heat shift clear and pale polymers warm", ("uv", "temperature"), ("yellowing",)),
    "CHALKING": ("Chalking", "A powdery surface after light attacks the binder", ("uv", "temperature"), ("chalking",)),
    "POLYMER_EMBRITTLEMENT": ("Embrittlement", "Light and heat make polymers brittle, then cracked", ("uv", "temperature", "cycles"), ("embrittlement", "coating_cracking")),
    "COATING_CRACKING": ("Surface cracking", "A brittle surface cracks under swings of temperature and moisture", ("cycles", "humidity"), ("coating_cracking",)),
    "SOIL_DEPOSITION": ("Soil deposition", "Particulates settle and stain where nothing washes them off", ("soil", "pollution", "rain", "cleaning"), ("soil",)),
    "SALT_DEPOSITION": ("Salt deposition", "Chlorides crystallise where rain does not rinse", ("salt", "rain"), ("salt",)),
    "BIOLOGICAL_GROWTH": ("Biological growth", "Mould, algae and moss on damp, shaded, porous surfaces", ("moisture", "shade", "porosity"), ("biological",)),
    "WATER_STAINING": ("Water staining", "Runs of rust, soil and minerals carried by rain", ("rain", "corrosion", "soil"), ("water_staining",)),
    "ABRASION": ("Abrasion", "Rubbing and scuffing wear the surface", ("abrasion", "hardness"), ("micro_scratching", "erosion")),
    "HANDLING": ("Handling", "Touch polishes edges and leaves fine scratches", ("handling",), ("handling_polish", "micro_scratching")),
    "IMPACT": ("Impact", "Knocks and drops dent and chip", ("impact", "hardness"), ("impact", "coating_loss")),
}

# The outcomes the engine turns into layers, in causal order.
OUTCOMES = (
    "fade", "gloss_loss", "chalking", "yellowing", "micro_scratching", "coating_cracking", "coating_loss",
    "primer_loss", "substrate_exposure", "oxidation", "corrosion", "patina", "bleaching", "wood_greying",
    "wood_checking", "embrittlement", "dust", "soil", "salt", "biological", "water_staining",
    "handling_polish", "impact", "erosion",
)


@dataclass
class State:
    fade: float = 0.0
    gloss_loss: float = 0.0
    chalking: float = 0.0
    yellowing: float = 0.0
    micro_scratching: float = 0.0
    coating_cracking: float = 0.0
    coating_loss: float = 0.0        # of the paint / top film
    primer_loss: float = 0.0         # of the primer, within the paint loss
    substrate_exposure: float = 0.0  # fraction of the surface where the bare substrate shows
    oxidation: float = 0.0           # uniform surface oxide on the exposed substrate
    corrosion: float = 0.0           # localized attack at damage, spreading under the film
    patina: float = 0.0              # stable oxide (verdigris, tarnish, grey wood) replacing raw oxide
    bleaching: float = 0.0
    wood_greying: float = 0.0
    wood_checking: float = 0.0
    embrittlement: float = 0.0
    dust: float = 0.0
    soil: float = 0.0
    salt: float = 0.0
    biological: float = 0.0
    water_staining: float = 0.0
    handling_polish: float = 0.0
    impact: float = 0.0
    erosion: float = 0.0
    years: float = 0.0
    uv_dose: float = 0.0
    wet_years: float = 0.0

    def as_dict(self):
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def outcomes(self):
        return {name: getattr(self, name) for name in OUTCOMES}


@dataclass(frozen=True)
class Construction:
    material: str
    coats: tuple = ()                   # coating ids, bottom (on the substrate) to top
    paint_color: tuple = (0.18, 0.32, 0.58)
    coating_condition: float = 1.0      # 1 new, 0.5 already half worn when the history starts

    @property
    def substrate(self):
        return MATERIALS[self.material]

    @property
    def coatings(self):
        return [COATINGS[c] for c in self.coats]

    @property
    def outer(self):
        return self.coatings[-1] if self.coats else None

    @property
    def primer(self):
        return next((c for c in self.coatings if c.role == "PRIMER"), None)

    @property
    def film(self):
        """The coats that fail as one film: everything above the primer (or all, without one)."""
        return [c for c in self.coatings if c.role != "PRIMER"] or self.coatings


@dataclass(frozen=True)
class Segment:
    years: float
    exposure: Exposure = field(default_factory=Exposure)
    usage: Usage = field(default_factory=Usage)
    maintenance: Maintenance = field(default_factory=Maintenance)
    label: str = ""


@dataclass(frozen=True)
class Event:
    year: float
    kind: str       # REPAINT | CLEAN | RUST_REMOVAL | POLISH | PROTECTIVE_TREATMENT | WOOD_TREATMENT


def _clamp(value):
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


def _grow(value, rate, dt, ceiling=1.0):
    """Saturating growth: the closer to the ceiling, the slower."""
    return _clamp(value + dt * rate * max(0.0, ceiling - value))


def _decay(value, rate, dt):
    return _clamp(value - dt * rate * value)


def _noise(seed, index):
    """Deterministic per-variable variation in [-1, 1] so seeds tell histories apart."""
    x = math.sin(seed * 12.9898 + index * 78.233) * 43758.5453
    return 2.0 * (x - math.floor(x)) - 1.0


def step(state, construction, exposure, usage, maintenance, dt, seed=0):
    """Advance the state by ``dt`` years under constant conditions."""
    m = construction.substrate
    film = construction.film
    outer = construction.outer
    primer = construction.primer
    processes = set(m.processes)
    wet = exposure.humidity * 0.55 + exposure.rain * 0.45
    protect = 1.0 - 0.6 * maintenance.protective_treatment      # wax, oil, sealant slow light and water
    vary = lambda index, spread=0.25: 1.0 + spread * _noise(seed, index)

    state.years += dt
    state.uv_dose += dt * exposure.uv
    state.wet_years += dt * wet

    if film:
        # --- the outer film: light, heat, moisture and people work on it first -------
        f = film[-1]
        intact = 1.0 - state.coating_loss
        uv = exposure.uv * f.uv_sensitivity * protect
        state.fade = _grow(state.fade, (uv * 0.09 + exposure.temperature * uv * 0.04) * vary(1), dt)
        state.gloss_loss = _grow(state.gloss_loss, (uv * 0.12 + wet * 0.03 + exposure.pollution * 0.04 + usage.abrasion * 0.05) * vary(2), dt)
        state.chalking = _grow(state.chalking, uv * exposure.temperature * f.chalking * 0.14 * vary(3), dt)
        state.yellowing = _grow(state.yellowing, (uv + exposure.temperature * 0.5) * f.yellowing * 0.10 * vary(4), dt)
        state.embrittlement = _grow(state.embrittlement, (uv * 0.07 + exposure.temperature * 0.03 + exposure.temperature_cycles * 0.02) * vary(5), dt)
        state.micro_scratching = _grow(state.micro_scratching,
                                       (usage.abrasion * 0.09 + usage.handling * 0.03 + exposure.wind * exposure.soil * 0.03) / (0.4 + f.hardness) * vary(6), dt)
        # Cracking: a brittle film under thermal cycling and moisture swings.
        cracking = (state.embrittlement ** 1.5 * (0.3 + exposure.temperature_cycles) * 0.22
                    + wet * state.embrittlement * 0.06) / (0.4 + f.flexibility)
        state.coating_cracking = _grow(state.coating_cracking, cracking * vary(7), dt)
        # Loss: chips from impacts and abrasion at edges, delamination where cracks let
        # moisture under a film that no longer holds, lifting over corrosion below.
        loss = ((usage.impact * 0.08 + usage.abrasion * 0.02) / (0.3 + f.adhesion)
                + state.coating_cracking * wet * 0.16 / (0.3 + f.adhesion)
                + state.corrosion * 0.20 + state.coating_loss * wet * 0.03)
        state.coating_loss = _grow(state.coating_loss, loss * vary(8), dt)
        if primer is not None and primer is not f:
            # The primer holds on longer than the paint it carries.
            primer_loss = (usage.impact * 0.04 / (0.3 + primer.adhesion) + state.corrosion * 0.25
                           + state.coating_loss * wet * 0.05) * state.coating_loss
            state.primer_loss = _grow(state.primer_loss, primer_loss * vary(9), dt, ceiling=state.coating_loss)
            exposed = state.primer_loss
            barrier = 1.0 - primer.barrier
        else:
            state.primer_loss = state.coating_loss
            exposed = state.coating_loss
            barrier = 1.0 - f.barrier
        state.substrate_exposure = _clamp(exposed)
        # Moisture that reaches the substrate through the film: cracks, thin spots, edges.
        through = barrier * (0.05 + state.coating_cracking * 0.6) * (1.0 - state.coating_loss)
    else:
        state.substrate_exposure = 1.0
        through = 0.0
        # Bare polymers and wood take the light directly.
        uv = exposure.uv * m.uv_sensitivity * protect
        if m.family == "POLYMER":
            state.fade = _grow(state.fade, uv * 0.08 * vary(1), dt)
            state.gloss_loss = _grow(state.gloss_loss, (uv * 0.10 + usage.abrasion * 0.06) * vary(2), dt)
            state.chalking = _grow(state.chalking, uv * exposure.temperature * 0.10 * vary(3), dt)
            state.yellowing = _grow(state.yellowing, (uv * 0.9 + exposure.temperature * 0.4) * 0.09 * vary(4), dt)
            state.embrittlement = _grow(state.embrittlement, (uv * 0.08 + exposure.temperature * 0.03) * vary(5), dt)
            state.coating_cracking = _grow(state.coating_cracking,
                                           state.embrittlement ** 2 * (0.3 + exposure.temperature_cycles) * m.thermal_sensitivity * 0.18 * vary(7), dt)
        state.micro_scratching = _grow(state.micro_scratching,
                                       (usage.abrasion * 0.09 + usage.handling * 0.03 + exposure.wind * exposure.soil * 0.04) / (0.4 + m.hardness) * vary(6), dt)

    exposed = state.substrate_exposure
    reach = _clamp(exposed + through)          # how much of the substrate the weather reaches

    # --- the substrate --------------------------------------------------------------
    if "OXIDATION" in processes:
        # Salts and acid deposition attack through moisture: a dry desert keeps even salty steel.
        corrosive = 0.04 + wet ** 1.5 * 0.7 + (exposure.salt * m.salt_sensitivity * 1.1 + exposure.pollution * 0.35) * (0.2 + wet)
        state.oxidation = _grow(state.oxidation, m.oxidation_rate * corrosive * reach * 0.30 * vary(10), dt)
        state.oxidation = _decay(state.oxidation, maintenance.rust_removal * 0.5 + maintenance.polishing * 0.15, dt)
    if "ATMOSPHERIC_CORROSION" in processes:
        damage = _clamp(exposed + state.coating_cracking * 0.4 + state.impact * 0.3 + (0.0 if film else 0.6))
        local = wet ** 1.5 * 0.6 + (exposure.salt * m.salt_sensitivity * 1.4 + exposure.pollution * 0.3) * (0.15 + wet)
        state.corrosion = _grow(state.corrosion, m.corrosion_rate * local * damage * 0.35 * vary(11), dt)
        state.corrosion = _decay(state.corrosion, maintenance.rust_removal * 0.6, dt)
    if "PATINATION" in processes:
        state.patina = _grow(state.patina, state.oxidation * (0.3 + wet * 0.7) * 0.25 * vary(12), dt)
        state.patina = _decay(state.patina, maintenance.polishing * 0.4, dt)
    if "BLEACHING" in processes:
        state.bleaching = _grow(state.bleaching, exposure.uv * m.uv_sensitivity * protect * 0.12 * (1.0 - state.coating_loss * 0.0) * reach * vary(13), dt)
    if "WOOD_GREYING" in processes:
        state.wood_greying = _grow(state.wood_greying, exposure.uv * wet * m.uv_sensitivity * 0.75 * reach * vary(14), dt)
        state.wood_greying = _decay(state.wood_greying, maintenance.wood_treatment * 0.3, dt)
    if "WOOD_CHECKING" in processes:
        swing = exposure.temperature_cycles * 0.6 + abs(exposure.humidity - 0.5) * 0.8
        state.wood_checking = _grow(state.wood_checking, swing * m.thermal_sensitivity * m.moisture_sensitivity * 0.10 * reach * vary(15), dt)
    if "POLYMER_EMBRITTLEMENT" in processes and not film:
        pass   # accumulated above through embrittlement / cracking
    if "COATING_CRACKING" in processes and not film and m.family != "POLYMER":
        # A bare hide or plaster cracks as it dries and as the temperature swings.
        dryness = max(0.0, 0.45 - exposure.humidity) * 1.2 + exposure.uv * 0.3
        swing = exposure.temperature_cycles * 0.5
        state.coating_cracking = _grow(state.coating_cracking, (dryness + swing) * m.thermal_sensitivity * 0.09 * protect * vary(16), dt)
    if "THERMAL_CYCLING" in processes:
        state.coating_cracking = _grow(state.coating_cracking, exposure.temperature_cycles * m.thermal_sensitivity * 0.05 * vary(16), dt)
    if "WATER_ABSORPTION" in processes:
        damp = max(0.0, wet - 0.3) + exposure.rain * 0.5      # dry rooms do not stain porous surfaces
        state.water_staining = _grow(state.water_staining, damp * m.moisture_sensitivity * m.porosity * 0.15 * reach * vary(17), dt)

    # --- deposits: what settles on it, and what the weather and people take off ------
    settle = 1.0 - maintenance.cleaning * 0.9 - usage.cleaning * 0.5
    state.dust = _grow(state.dust, (0.08 + exposure.soil * 0.45 + exposure.pollution * 0.25) * max(0.0, settle) * 0.45 * vary(18), dt)
    state.dust = _decay(state.dust, exposure.rain * 0.9 + exposure.wind * 0.3 + usage.handling * 0.4 + maintenance.cleaning * 2.0, dt)
    if "SOIL_DEPOSITION" in processes or film:
        state.soil = _grow(state.soil, (exposure.soil * 0.5 + exposure.pollution * 0.35 + usage.handling * 0.10) * (0.4 + m.porosity) * max(0.0, settle) * 0.22 * vary(19), dt)
        state.soil = _decay(state.soil, exposure.rain * 0.15 + maintenance.cleaning * 0.2, dt)
    if "SALT_DEPOSITION" in processes or exposure.salt > 0.3:
        state.salt = _grow(state.salt, exposure.salt * (1.0 - exposure.rain * 0.5) * 0.35 * vary(20), dt)
        state.salt = _decay(state.salt, exposure.rain * 0.5 + maintenance.cleaning * 0.4, dt)
    if "BIOLOGICAL_GROWTH" in processes or m.bio_susceptibility > 0.3:
        shade = 1.0 - exposure.uv * 0.7
        state.biological = _grow(state.biological, wet ** 2 * shade * (0.2 + m.porosity + m.bio_susceptibility) * 0.12 * vary(21), dt)
        state.biological = _decay(state.biological, exposure.uv * 0.05 + maintenance.cleaning * 0.5 + maintenance.wood_treatment * 0.3, dt)
    if "WATER_STAINING" in processes or state.corrosion > 0.0:
        state.water_staining = _grow(state.water_staining,
                                     exposure.rain * (state.corrosion * 0.8 + state.soil * 0.35 + m.porosity * 0.2) * 0.25 * vary(22), dt)
        state.water_staining = _decay(state.water_staining, maintenance.cleaning * 0.3, dt)

    # --- people ------------------------------------------------------------------
    state.handling_polish = _grow(state.handling_polish, usage.handling * 0.10 * vary(23), dt)
    state.impact = _grow(state.impact, usage.impact * 0.09 / (0.3 + m.hardness) * vary(24), dt)
    state.erosion = _grow(state.erosion, (usage.abrasion * 0.03 + exposure.wind * exposure.soil * 0.05) * (1.0 - m.hardness * 0.7) * 0.5 * vary(25), dt)
    state.gloss_loss = _decay(state.gloss_loss, maintenance.polishing * 0.35, dt)
    state.micro_scratching = _decay(state.micro_scratching, maintenance.polishing * 0.25, dt)
    return state


def apply_event(state, construction, kind):
    """A maintenance event rewinds the variables it acts on."""
    if kind == "REPAINT":
        for name in ("fade", "gloss_loss", "chalking", "yellowing", "embrittlement", "micro_scratching",
                     "coating_cracking", "coating_loss", "primer_loss", "substrate_exposure", "salt", "soil", "dust"):
            setattr(state, name, 0.0)
        # Paint over corrosion hides it; it keeps working under the new film.
        state.corrosion *= 0.7
        state.oxidation *= 0.2
        state.water_staining = 0.0
        state.biological = 0.0
    elif kind == "CLEAN":
        state.dust, state.soil, state.salt = 0.0, state.soil * 0.15, state.salt * 0.1
        state.biological *= 0.3
        state.water_staining *= 0.4
    elif kind == "RUST_REMOVAL":
        state.oxidation *= 0.15
        state.corrosion *= 0.3
        state.water_staining *= 0.5
    elif kind == "POLISH":
        state.gloss_loss *= 0.3
        state.micro_scratching *= 0.4
        state.oxidation *= 0.5
        state.patina *= 0.6
        state.dust = 0.0
    elif kind == "PROTECTIVE_TREATMENT":
        state.embrittlement *= 0.8
        state.salt *= 0.5
    elif kind == "WOOD_TREATMENT":
        state.wood_greying *= 0.3
        state.bleaching *= 0.4
        state.biological *= 0.2
        state.water_staining *= 0.5
    return state


def simulate(construction, segments, events=(), seed=0, until=None, samples=None):
    """Integrate the history; returns the state at ``until`` (default: the end).

    ``samples``, a list, receives (year, outcomes dict) once a year for readouts.
    """
    state = State()
    if construction.coating_condition < 1.0 and construction.coats:
        worn = 1.0 - construction.coating_condition
        state.fade, state.gloss_loss, state.micro_scratching = worn * 0.5, worn * 0.6, worn * 0.4
        state.coating_cracking, state.coating_loss = worn * 0.3, worn * 0.25
        state.primer_loss = worn * 0.1 if construction.primer else state.coating_loss
        state.substrate_exposure = state.primer_loss
    pending = sorted((e for e in events), key=lambda e: e.year)
    horizon = sum(s.years for s in segments)
    if until is None:
        until = horizon
    elapsed = 0.0
    next_sample = 1.0
    for segment in segments:
        end = min(elapsed + segment.years, until)
        while elapsed < end - 1e-9:
            dt = min(STEP, end - elapsed)
            while pending and pending[0].year <= elapsed + 1e-9:
                apply_event(state, construction, pending.pop(0).kind)
            step(state, construction, segment.exposure, segment.usage, segment.maintenance, dt, seed)
            elapsed += dt
            if samples is not None and elapsed + 1e-9 >= next_sample:
                samples.append((round(elapsed, 3), state.outcomes()))
                next_sample += 1.0
        if elapsed >= until - 1e-9:
            break
    while pending and pending[0].year <= elapsed + 1e-9:
        apply_event(state, construction, pending.pop(0).kind)
    state.years = elapsed
    return state


LABELS = (
    (0.04, "PRISTINE"), (0.12, "LIGHTLY USED"), (0.28, "WEATHERED"), (0.50, "WORN"), (0.75, "DAMAGED"), (1.01, "DERELICT"),
)


def severity(state, construction):
    """One number summarising how far the material has gone, for the state label."""
    o = state.outcomes()
    coated = bool(construction.coats)
    parts = [o["fade"] * 0.5, o["gloss_loss"] * 0.4, o["micro_scratching"] * 0.5, o["coating_cracking"] * 0.8 if coated else 0,
             o["coating_loss"] * 1.0, o["substrate_exposure"] * 0.8 if coated else 0, o["oxidation"] * 0.8, o["corrosion"] * 1.0, o["patina"] * 0.5,
             o["wood_greying"] * 0.7, o["wood_checking"] * 0.8, o["bleaching"] * 0.4, o["chalking"] * 0.5, o["yellowing"] * 0.5,
             o["dust"] * 0.2, o["soil"] * 0.4, o["salt"] * 0.3, o["biological"] * 0.6, o["water_staining"] * 0.4, o["impact"] * 0.6,
             o["erosion"] * 0.6]
    return _clamp(sum(parts) / 4.0)


def label(state, construction):
    value = severity(state, construction)
    return next(name for threshold, name in LABELS if value < threshold)


def readout(state, construction):
    """The predicted-state lines the panel shows, as (label, value) pairs that apply."""
    o = state.outcomes()
    m = construction.substrate
    lines = []
    if construction.coats:
        lines += [("Paint fade", o["fade"]), ("Gloss loss", o["gloss_loss"]), ("Chalking", o["chalking"]),
                  ("Micro scratching", o["micro_scratching"]), ("Coating cracking", o["coating_cracking"]),
                  ("Coating loss", o["coating_loss"]), ("Exposed substrate", o["substrate_exposure"])]
        if o["yellowing"] > 0.01:
            lines.append(("Yellowing", o["yellowing"]))
    else:
        lines += [("Micro scratching", o["micro_scratching"])]
        if m.family == "POLYMER":
            lines += [("Fade", o["fade"]), ("Gloss loss", o["gloss_loss"]), ("Yellowing", o["yellowing"]),
                      ("Chalking", o["chalking"]), ("Embrittlement", o["embrittlement"]), ("Surface cracking", o["coating_cracking"])]
    if m.family == "METAL":
        oxide = {"RUST": "Surface rust", "VERDIGRIS": "Oxidation", "TARNISH": "Tarnish", "WHITE_OXIDE": "White oxide"}.get(m.patina, "Oxidation")
        lines += [(oxide, o["oxidation"]), ("Corrosion at damage", o["corrosion"])]
        if "PATINATION" in m.processes:
            lines.append(("Patina", o["patina"]))
    if m.family == "WOOD":
        lines += [("Bleaching", o["bleaching"]), ("Greying", o["wood_greying"]), ("Checking", o["wood_checking"]),
                  ("Water staining", o["water_staining"])]
    if m.family == "MINERAL":
        lines += [("Water staining", o["water_staining"]), ("Erosion", o["erosion"])]
    if m.family == "TEXTILE":
        lines = [line for line in lines if line[0] != "Micro scratching"]
        lines += [("Fading", o["bleaching"]), ("Fraying", o["micro_scratching"]), ("Water staining", o["water_staining"])]
    if m.family == "LEATHER":
        lines = [line for line in lines if line[0] != "Micro scratching"]
        lines += [("Fading", o["bleaching"]), ("Scuffing", o["micro_scratching"]), ("Cracking", o["coating_cracking"]),
                  ("Burnish", o["handling_polish"]), ("Water staining", o["water_staining"])]
    lines += [("Dust", o["dust"]), ("Soil", o["soil"])]
    if o["salt"] > 0.01:
        lines.append(("Salt deposits", o["salt"]))
    if o["biological"] > 0.01:
        lines.append(("Biological growth", o["biological"]))
    soft = m.family in {"TEXTILE", "LEATHER"}        # cloth and hide do not dent; a hide's polish is its burnish
    if o["impact"] > 0.01 and not soft:
        lines.append(("Impact damage", o["impact"]))
    if o["handling_polish"] > 0.02 and not soft:
        lines.append(("Handling polish", o["handling_polish"]))
    return lines


ART_DIRECTION = {
    # multiplier name -> the outcomes it scales
    "corrosion": ("oxidation", "corrosion", "patina", "water_staining"),
    "fading": ("fade", "gloss_loss", "chalking", "yellowing", "bleaching", "wood_greying"),
    "damage": ("micro_scratching", "coating_cracking", "coating_loss", "primer_loss", "substrate_exposure", "wood_checking", "impact", "erosion"),
    "dirt": ("dust", "soil", "salt", "biological"),
}


def directed(state, multipliers):
    """A copy of the state with the art-direction multipliers applied (clamped)."""
    result = replace(state)
    for name, keys in ART_DIRECTION.items():
        factor = float(multipliers.get(name, 1.0))
        if factor == 1.0:
            continue
        for key in keys:
            setattr(result, key, _clamp(getattr(result, key) * factor))
    return result
