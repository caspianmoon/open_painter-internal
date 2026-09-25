# Material aging (0.10.0)

Tell Open Painter what a thing is, where it lived, how it was used and how old it
is. It ages the material accordingly.

The earlier *Age Response* was a set of wear presets with a slider: it could not
know that copper goes green while steel rusts, that a museum chair is thirty
years old and still fine, or that a repaint fifteen years ago left dents under
fresh paint. 0.10.0 replaces it with a **material-specific aging simulation**.
Age is a first-class, nondestructive parameter of the texture set: keyframe it,
scrub it, change the environment, and the layers follow — while everything the
artist changes inside the aging folder stays theirs.

## What it is

*Painter* sidebar → **Material Aging** (`OP_PT_native_aging`, `ts.aging`).

**Material** is the substrate: carbon and stainless steel, cast iron, aluminium,
copper, brass, bronze, zinc, galvanized steel, titanium; oak, pine, walnut,
mahogany, plywood, MDF, treated wood; ABS, PVC, polycarbonate, polypropylene,
polyethylene, nylon, rubber, silicone; concrete, brick, plaster, marble, granite,
ceramic; canvas, denim, wool; leather (`aging/materials.py`). A material is described by how it *responds* —
sensitivity to light, moisture, salts and heat, oxidation and corrosion rates,
its oxide (rust, verdigris, tarnish, white oxide, grey), hardness, porosity,
brittleness, biological susceptibility — and by the processes it subscribes to
from the process library: oxidation, atmospheric corrosion, patination,
bleaching, wood greying, wood checking, water absorption, thermal cycling, salt
and soil deposition, biological growth, water staining, UV degradation,
yellowing, chalking, embrittlement, abrasion, handling, impact (`model.PROCESSES`: label,
description, inputs, outcomes per process). Nothing in a material says which
wear to show; the processes decide from the history.

**Construction** is the protection on top of it (`aging/coatings.py`): an
optional *conversion coating* (galvanization, anodizing, chrome plating), a
*primer* (zinc, epoxy), a *paint* (oil, acrylic, powder coat, polyurethane, epoxy,
vitreous enamel) with the artist's paint colour, and a *top coat* (clear coat,
varnish, oil finish). Each coating has
its own UV sensitivity, adhesion, flexibility, moisture barrier, hardness,
chalking and yellowing. **Coating Condition** starts the history with a coat
that is already worn.

**Smart materials say what they are (0.10.1).** Applying a catalog recipe or a
shelf preset sets the construction: *Canvas Fabric* is canvas, *Automotive
Painted Metal* is steel under an epoxy primer, a red base coat and a clear coat,
*Polished Chrome* is chrome-plated steel, *Heavy Chipped Paint* starts with its
coating half gone. A set that is not aged yet also takes the history the recipe
depicts (*Heavy Rust*: twenty-five coastal years; *Fresh Oak*: new), so *Age
Material* continues the look; a set with an aging folder keeps its history and
rebuilds the folder for the new construction. `aging/recipes.py` names every
recipe and preset; a material it does not know is read from its name and
category ("aluminium", "leather", a layer named like paint).

Textiles fade, fray and pill where they rub, soak, stain and mildew; leather
also burnishes where handled and cracks as it dries (the *Cracking* line reads
the humidity). Neither dents.

## How old, where, how used

**Age** is simulated exposure time in years (`age_years`, 0–200, soft 50).

**Environment** presets — Indoor, Storage, Humid Interior, Outdoor Urban,
Coastal, Arid, Industrial, Tropical, Alpine, Underground — are physical exposure
parameters (`aging/environments.py`): UV, humidity, rain, temperature,
temperature cycles, salt, pollution, soil and wind. Choosing a preset copies its
values into the **Exposure** sliders; move any slider and the preset reads
*Custom*. **Environment History** ages through a sequence of periods instead
(years, environment, usage, maintenance per period; the periods are scaled to the
age): five years in a factory, ten in a shed, three on a beach compose in that
order, and the order matters — salt from the coast is still on a thing that went
back indoors only if the coast came last.

**Use** is separate from age: Handling (touch: polish on edges, fine scratches),
Abrasion, Impact Damage and Cleaning, with None / Light / Moderate / Heavy /
Abused presets. A thirty-year-old brass handle nobody touched has no polish.

**Maintenance** slows processes continuously — Cleaning Frequency, Protective
Treatment, Polishing, Rust Removal, Wood Treatment — and **events** rewind them at
a moment of the history: Repaint (a new film; the dents stay and the rust keeps
working under it), Clean, Rust Removal, Polish, Protective Treatment, Wood
Treatment, each at a year of the history (0 is new, the age is now; the row
shows "n y ago"). A new event has just happened; move it back to let the thing
re-age after it — rust and dirt return, so a rust removal twenty-five years
before the end changes little, and an event after the age has not happened yet
(the row says so).

## The simulation

`aging/model.py` integrates the history monthly (`STEP = 1/12`). Each month every
process the construction subscribes to advances its state variables at rates
that are products of exposure, material response and the current state:
corrosion needs exposed substrate *and* moisture *and* salts; a film cracks once
light and heat have made it brittle *and* thermal cycling works on it;
delamination needs cracks *and* moisture *and* poor adhesion; rust under paint
lifts the paint. Salts and acid deposition attack only through moisture, so a
desert keeps even salty steel. Growth saturates (`_grow`), maintenance decays
(`_decay`), and a per-variable noise keyed by the **Seed** makes two histories of
the same kind differ in their particulars. The state has 24 outcomes — fade,
gloss loss, chalking, yellowing, micro scratching, coating cracking, coating
loss, primer loss, substrate exposure, oxidation, corrosion, patina, bleaching,
wood greying, wood checking, embrittlement, dust, soil, salt, biological growth,
water staining, handling polish, impact, erosion — plus years, UV dose and wet
years. It is plain Python (no `bpy`), tested by `tests/unit/test_aging_model.py`
and usable from the Open Graph.

**Predicted State** in the panel is the readout of the state for this material:
paint fade 34 %, gloss loss 52 %, coating loss 18 %, exposed substrate 18 %,
surface rust 20 %… and a label from the summed severity: Pristine, Lightly Used,
Weathered, Worn, Damaged, Derelict. The prediction is memoised on the settings.

Reference points the calibration holds: primed polyurethane steel after ten
urban years of moderate use reads *Weathered* (coating loss ≈ 20 %, rust at
damage < 10 %); the same thing indoors, lightly used and regularly kept, *Lightly
Used*; twenty-five neglected coastal years, *Derelict* with 90 % coating loss;
copper after thirty coastal years is fully green; stainless steel at the coast
tea-stains but does not rust through; a meticulously kept oak chair is *Lightly
Used* after thirty years; unprotected oak greys within a few outdoor years; ABS
in the desert yellows and chalks.

## From state to layers

`aging/document.py` builds recipe version 2. Layers remain editable and run from
substrate through conversion, primer, paint and topcoat to corrosion and deposits.

- **Coating failure:** a continuous Weathering Front field separates broad damage
  islands from fine broken boundaries. Age moves the threshold over a fixed field,
  so existing damage grows instead of changing position. Paint, primer and
  conversion share the seed and coordinates, with deeper coats failing inside the
  upper coat's damage. Under-film corrosion narrows the surviving primer transition.
  Curvature adds localized edge chips. Paint has separate subtle pigment, roughness
  and orange-peel microtexture; its broad weathering field does not drive height.
- **Corrosion:** coverage is confined to exposed substrate. Pigment variation uses
  a different spatial scale from Corrosion Grain's pits and granules. Roughness and
  shallow height use the grain; broad color patches do not distort the surface.
  Rust, copper oxide and other substrate-specific colors remain distinct.
- **Cracks and scratches:** finite branched fissures and tapered abrasion replace
  complete cellular crack networks and repeated image scratches. Paint scratches
  are restricted to surviving paint. Handling polish changes coated surfaces'
  roughness without exposing metallic color through intact paint.
- **Deposits and runoff:** salt is sparse granular residue. Dirt and biological
  coverage vary spatially with cavity bias. Runoff uses finite downward trails
  with varied lengths and tapered ends, sampled from baked Position and biased
  by AO. It is an art-directed procedural, not a water-flow simulation or a claim
  that each trail starts at a particular geometric cavity.

The folder still requires **AO and Curvature**. **Position and World Normal** are
optional: runoff uses Position for vertical alignment, and dust uses upward-facing
normals. Without these maps, projection-based procedural fallbacks remain editable;
runoff direction then follows the chosen projection axes. Baking the optional maps
after creating a folder enables **Rebuild Folder** to add the geometry masks.
Projection and scale matter: inspect at the asset's intended size and adjust the
individual channel and mask scales. These fields have no repeated image tiles,
but very fine detail still needs adequate texture resolution.

**Existing projects:** saved version-1 folders keep `aging/document_v1.py` for live
updates and retain artist edits. The panel offers **Rebuild with New Procedurals**
to opt in. Like the existing Rebuild command, this replaces the aging folder and
its internal edits. New aging folders use version 2 automatically. This recipe
revision is independent of the add-on's release version.

## Nondestructive

`aging/engine.py` builds the **Material Aging** folder (isolated) once and then
*drives* it. Every layer carries `OP_Layer.aging_role`; on any settings change the
engine re-simulates, rebuilds the recipe and writes only the driven fields —
opacity, base colour, roughness and metallic values, mask amount / strength /
levels and explicit procedural seeds — into the layers by role. A field the artist changed since the engine
last wrote it is an override: it is left alone from then on (the record lives in
`ts.aging.state_data`), until **Reset Art**. Layers the artist adds inside the
folder are theirs and survive re-drives. Changing the construction so the roster
changes (another material family, coats added or removed) rebuilds the folder.
**Live** off keeps the prediction updating and freezes the layers until *Re-age*.
**Randomize History** picks another seed. **Remove** deletes the folder and
forgets the record.

**Art Direction** — Corrosion, Fading, Damage, Dirt multipliers — is applied
after the simulation, on the outcome groups, before the recipe; the readout shows
`68 %  (physics 34 %)` when it differs from the physics. Presets are not
preserved or fought: the physics stays the reference, the multipliers are the
artist's exaggeration.

**Age is a parameter.** `ts.aging.age_years` is an ordinary RNA float:
keyframe it (`scene.open_painter.texture_sets[i].aging.age_years`) and
`dependencies.refresh` re-drives the folder on every frame whose age differs
from the applied one, so a prop can age across a shot. Two texture sets with the
same settings and seed age identically; the set duplicate carries the settings
and its own folder; the material document (`.openpaintmaterial`, `aging` block)
carries the history, the settings and the override record, so an imported
material re-simulates from the same story and keeps what the artist changed.
Undo restores settings and layers together.

## Open Graph

The *Material Aging* folder mirrors into the Open Graph like any folder. Showing
the age on its node is a small change for the graph's owner (`graph/mirror.py`
`draw_node`): when `layer.type == 'FOLDER' and layer.aging_role == 'folder'`, draw
`ts.aging.age_years`, `environment` and the *Predicted State* label from
`aging.engine.prediction(ts)['label']` in a box; the property is the same RNA
value the panel edits, so the node, the panel and the timeline agree.

## Removed

*Age Response* (`effect_semantic` roles on fill layers, the *Age / Environment
State* dialog, `core/material_states.py`, `core/environment_profiles.py`) is
gone. `OP_Layer.effect_semantic` and `OP_TextureSet.material_state_data` remain
as hidden fields so older files and documents still load.

## Tests

`tests/unit/test_aging_model.py` (pure Python): library integrity, determinism
and seed sensitivity, bounds and monotonicity, environment over years, materials
in the same place, coatings protecting and failing, use ≠ age, maintenance and
events, history order, labels, readout, art direction, recipe roles and driven
values. `tests/integration/material_aging.py` (background): missing maps, folder
build, age 0 = the artist's paint and 25 coastal years ≠ (CPU evaluation), indoor
vs coastal, preset → sliders → Custom, overrides and Reset Art, the artist's own
layer inside the folder, art direction readout, usage and care, events, history
periods, randomize, roster rebuild, Live off, a keyframed age through
`dependencies.refresh`, document round trip with overrides, set duplicate, undo,
remove.

## Procedural regression checks

`tests/unit/test_weathering_fields.py` checks deterministic strip evaluation,
nonrepeating coordinates, monotonic damage growth, finite runoff support and
nested coating settings. `tests/integration/material_aging.py` checks actual mask
coverage, live seed updates and opt-in migration in addition to the history and
serialization tests above. `tests/integration/weathering_shader.py` compares
Blender emission-bake readbacks with CPU channel evaluation.

For controlled visual comparisons, run:

```sh
blender -b --factory-startup --python-exit-code 1 \
  --python scripts/render_aging_review.py -- --output /tmp/aging-review --case paint --years 12
blender -b --factory-startup --python-exit-code 1 \
  --python scripts/render_aging_review.py -- --output /tmp/aging-review --case copper --years 12
```

Each pair uses identical geometry, baked mesh maps, history, camera and lighting.
It saves editable Blender scenes and the simulated outcomes beside the renders.
Numerical checks verify consistency; they do not establish photorealism.
