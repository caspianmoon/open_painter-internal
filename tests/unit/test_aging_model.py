"""The aging simulation is deterministic, material-specific and answers to history, not to a preset."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from open_painter.aging import coatings, document, environments, materials, model, recipes
from open_painter.aging.environments import ENVIRONMENTS, MAINTENANCE, USAGE
from open_painter.aging.model import Construction, Event, Segment, State


def aged(construction, years, environment="OUTDOOR_URBAN", usage="MODERATE", maintenance="OCCASIONAL", events=(), seed=4182):
    segment = Segment(years, ENVIRONMENTS[environment], USAGE[usage], MAINTENANCE[maintenance], environment)
    return model.simulate(construction, [segment], events, seed=seed)


PAINTED_STEEL = Construction("CARBON_STEEL", ("EPOXY_PRIMER", "POLYURETHANE_PAINT"))


class LibraryTests(unittest.TestCase):
    def test_every_material_names_processes_the_model_knows(self):
        known = set(model.PROCESSES)
        for label, description, inputs, outcomes in model.PROCESSES.values():
            self.assertTrue(label and description and inputs)
            self.assertFalse(set(outcomes) - set(model.OUTCOMES), label)
        subscribed = {p for m in materials.MATERIALS.values() for p in m.processes}
        self.assertEqual(subscribed, known, "every library process has a subscriber and every subscription is in the library")
        for material in materials.MATERIALS.values():
            self.assertIn(material.family, materials.FAMILIES)
            self.assertTrue(material.processes, material.id)
            self.assertFalse(set(material.processes) - known, material.id)
            for value in (material.uv_sensitivity, material.moisture_sensitivity, material.oxidation_rate, material.corrosion_rate,
                          material.salt_sensitivity, material.hardness, material.porosity, material.thermal_sensitivity,
                          material.bio_susceptibility):
                self.assertTrue(0 <= value <= 1, material.id)
        self.assertEqual([item[0] for item in materials.items()][:2], ["CARBON_STEEL", "STAINLESS_STEEL"])
        self.assertEqual(len(materials.items()), len(materials.MATERIALS))

    def test_coatings_and_presets(self):
        for coating in coatings.COATINGS.values():
            self.assertIn(coating.role, coatings.ROLES)
            for value in (coating.uv_sensitivity, coating.adhesion, coating.flexibility, coating.barrier, coating.hardness,
                          coating.chalking, coating.yellowing, coating.thickness, coating.roughness):
                self.assertTrue(0 <= value <= 1, coating.id)
        self.assertTrue(all(c.role == "PRIMER" for c in coatings.COATINGS.values() if c.id in {i[0] for i in coatings.items("PRIMER")}))
        for name, preset in ENVIRONMENTS.items():
            self.assertIn(name, environments.ENVIRONMENT_LABELS)
            self.assertTrue(all(0 <= v <= 1 for v in preset.as_dict().values()), name)
        self.assertEqual(environments.exposure("COASTAL", salt=0.2).salt, 0.2)
        self.assertEqual(environments.exposure("COASTAL").uv, ENVIRONMENTS["COASTAL"].uv)
        self.assertEqual(set(USAGE), set(environments.USAGE_LABELS))
        self.assertEqual(set(MAINTENANCE), set(environments.MAINTENANCE_LABELS))
        self.assertEqual(set(environments.EVENT_KINDS), set(environments.EVENT_LABELS))


class SimulationTests(unittest.TestCase):
    def test_nothing_happens_to_a_new_thing(self):
        state = aged(PAINTED_STEEL, 0)
        self.assertEqual(state.years, 0)
        self.assertTrue(all(value == 0 for key, value in state.outcomes().items()), state.outcomes())
        self.assertEqual(model.label(state, PAINTED_STEEL), "PRISTINE")

    def test_deterministic_and_seed_sensitive(self):
        a, b = aged(PAINTED_STEEL, 10), aged(PAINTED_STEEL, 10)
        self.assertEqual(a.as_dict(), b.as_dict())
        c = aged(PAINTED_STEEL, 10, seed=7)
        self.assertNotEqual(a.as_dict(), c.as_dict())
        # Seeds vary the history, not its kind.
        self.assertEqual(model.label(a, PAINTED_STEEL), model.label(c, PAINTED_STEEL))

    def test_bounded_and_monotone_in_time(self):
        previous = None
        for years in (1, 5, 10, 25, 50, 120):
            state = aged(PAINTED_STEEL, years, "COASTAL", "NONE", "NONE")
            outcomes = state.outcomes()
            self.assertTrue(all(0 <= v <= 1 for v in outcomes.values()), outcomes)
            if previous is not None:
                for key in ("coating_loss", "corrosion", "fade", "coating_cracking"):
                    self.assertGreaterEqual(outcomes[key] + 1e-9, previous[key], key)
            previous = outcomes
        self.assertEqual(model.label(state, PAINTED_STEEL), "DERELICT")

    def test_the_environment_decides_not_the_years(self):
        indoor = aged(PAINTED_STEEL, 10, "INDOOR", "LIGHT", "REGULAR")
        coastal = aged(PAINTED_STEEL, 10, "COASTAL", "MODERATE", "NONE")
        self.assertEqual(model.label(indoor, PAINTED_STEEL), "LIGHTLY USED")
        self.assertIn(model.label(coastal, PAINTED_STEEL), {"WORN", "DAMAGED"})
        self.assertLess(indoor.corrosion * 5, coastal.corrosion)
        self.assertLess(indoor.fade * 3, coastal.fade)
        self.assertGreater(coastal.salt, 0.2)
        self.assertLess(indoor.salt, 0.01)

    def test_materials_age_differently_in_the_same_place(self):
        steel = aged(Construction("CARBON_STEEL"), 20, "COASTAL", "NONE", "NONE")
        stainless = aged(Construction("STAINLESS_STEEL"), 20, "COASTAL", "NONE", "NONE")
        copper = aged(Construction("COPPER"), 20, "COASTAL", "NONE", "NONE")
        oak = aged(Construction("OAK"), 20, "COASTAL", "NONE", "NONE")
        abs_ = aged(Construction("ABS"), 20, "COASTAL", "NONE", "NONE")
        self.assertGreater(steel.corrosion, 0.9)
        self.assertLess(stainless.corrosion, 0.5)
        self.assertGreater(copper.patina, 0.8)
        self.assertEqual(steel.patina, 0)          # steel does not patinate; it rusts
        self.assertGreater(oak.wood_greying, 0.8)
        self.assertEqual(oak.oxidation, 0)
        self.assertGreater(abs_.yellowing, 0.5)
        self.assertGreater(abs_.chalking, 0.3)
        self.assertEqual(abs_.corrosion, 0)

    def test_coatings_protect_and_then_fail(self):
        bare = aged(Construction("CARBON_STEEL"), 10, "OUTDOOR_URBAN", "NONE", "NONE")
        primed = aged(PAINTED_STEEL, 10, "OUTDOOR_URBAN", "NONE", "NONE")
        unprimed = aged(Construction("CARBON_STEEL", ("POLYURETHANE_PAINT",)), 10, "OUTDOOR_URBAN", "NONE", "NONE")
        self.assertGreater(bare.oxidation, primed.oxidation * 4)
        self.assertLess(primed.substrate_exposure, unprimed.substrate_exposure)   # the primer holds on under the paint
        self.assertLessEqual(primed.primer_loss, primed.coating_loss + 1e-9)
        self.assertGreater(unprimed.substrate_exposure, 0.03)   # untouched: the film cracks before it lifts
        worn = Construction("CARBON_STEEL", ("EPOXY_PRIMER", "POLYURETHANE_PAINT"), coating_condition=0.5)
        self.assertGreater(aged(worn, 0).coating_loss, 0.1)
        self.assertGreater(aged(worn, 5).corrosion, aged(PAINTED_STEEL, 5).corrosion)

    def test_usage_is_not_age(self):
        untouched = aged(Construction("BRASS"), 5, "INDOOR", "NONE", "NONE")
        abused = aged(Construction("BRASS"), 5, "INDOOR", "ABUSED", "NONE")
        self.assertEqual(untouched.handling_polish, 0)
        self.assertEqual(untouched.impact, 0)
        self.assertGreater(abused.handling_polish, 0.3)
        self.assertGreater(abused.micro_scratching, 0.3)
        self.assertGreater(abused.impact, 0.15)

    def test_maintenance_slows_and_events_rewind(self):
        neglected = aged(Construction("OAK"), 30, "INDOOR", "NONE", "NONE")
        museum = aged(Construction("OAK"), 30, "INDOOR", "NONE", "METICULOUS")
        self.assertLess(museum.dust, neglected.dust * 0.2)
        self.assertEqual(model.label(museum, Construction("OAK")), "LIGHTLY USED")
        twenty = aged(PAINTED_STEEL, 20, "OUTDOOR_URBAN", "MODERATE", "OCCASIONAL")
        repainted = aged(PAINTED_STEEL, 20, "OUTDOOR_URBAN", "MODERATE", "OCCASIONAL", events=(Event(15, "REPAINT"),))
        self.assertLess(repainted.coating_loss, twenty.coating_loss * 0.6)
        self.assertLess(repainted.fade, twenty.fade * 0.6)
        self.assertGreaterEqual(repainted.impact, twenty.impact * 0.99)   # dents stay under new paint
        self.assertGreater(repainted.corrosion, 0)                         # and rust keeps working under it
        cleaned = aged(Construction("CONCRETE"), 10, "OUTDOOR_URBAN", "NONE", "NONE", events=(Event(10, "CLEAN"),))
        dirty = aged(Construction("CONCRETE"), 10, "OUTDOOR_URBAN", "NONE", "NONE")
        self.assertEqual(cleaned.dust, 0)
        self.assertLess(cleaned.soil, dirty.soil * 0.2)

    def test_history_segments_compose_in_order(self):
        construction = PAINTED_STEEL
        history = [Segment(10, ENVIRONMENTS["INDOOR"], USAGE["LIGHT"], MAINTENANCE["REGULAR"]),
                   Segment(5, ENVIRONMENTS["COASTAL"], USAGE["NONE"], MAINTENANCE["NONE"])]
        reversed_history = list(reversed(history))
        forward, backward = model.simulate(construction, history), model.simulate(construction, reversed_history)
        self.assertAlmostEqual(forward.years, 15)
        self.assertNotAlmostEqual(forward.dust, backward.dust)
        # Five coastal years at the end leave salt on it; ten indoor years afterwards do not.
        self.assertGreater(forward.salt, backward.salt * 2)
        samples = []
        model.simulate(construction, history, samples=samples)
        self.assertEqual([year for year, _ in samples], list(range(1, 16)))
        partial = model.simulate(construction, history, until=10)
        self.assertAlmostEqual(partial.years, 10)
        self.assertLess(partial.corrosion, forward.corrosion)

    def test_labels_readout_and_art_direction(self):
        state = aged(PAINTED_STEEL, 10, "COASTAL")
        lines = dict(model.readout(state, PAINTED_STEEL))
        for name in ("Paint fade", "Gloss loss", "Coating loss", "Exposed substrate", "Surface rust", "Corrosion at damage", "Salt deposits"):
            self.assertIn(name, lines)
        self.assertNotIn("Greying", lines)
        oak = dict(model.readout(aged(Construction("OAK"), 5), Construction("OAK")))
        self.assertIn("Greying", oak)
        self.assertNotIn("Coating loss", oak)
        louder = model.directed(state, {"corrosion": 2.0, "dirt": 0.0})
        self.assertAlmostEqual(louder.corrosion, min(1.0, state.corrosion * 2))
        self.assertEqual(louder.salt, 0)
        self.assertEqual(louder.fade, state.fade)          # untouched groups keep the physics
        self.assertEqual(state.salt, aged(PAINTED_STEEL, 10, "COASTAL").salt)   # the physical state is not mutated
        self.assertEqual([name for _, name in model.LABELS], ["PRISTINE", "LIGHTLY USED", "WEATHERED", "WORN", "DAMAGED", "DERELICT"])


class RecipeTests(unittest.TestCase):
    def test_every_catalog_material_says_what_it_is(self):
        from open_painter.smart_materials.catalog import CATALOG
        from open_painter.ui.dock.assets import MATERIALS as PRESETS
        for name in list(CATALOG) + [entry[0] for entry in PRESETS]:
            data = recipes.construction_for(name, CATALOG.get(name))
            self.assertIsNotNone(data, name)
            self.assertIn(data["material"], materials.MATERIALS, name)
            for coat in data["coats"]:
                self.assertTrue(coat is None or coat in coatings.COATINGS, (name, coat))
            self.assertIn(data["environment"], ENVIRONMENTS, name)
            self.assertIn(data["usage"], USAGE, name)
            self.assertTrue(0 <= data["condition"] <= 1)
        canvas = recipes.construction_for("Canvas Fabric")
        self.assertEqual((canvas["material"], canvas["coats"]), ("CANVAS", (None, None, None, None)))
        car = recipes.construction_for("Automotive Painted Metal")
        self.assertEqual(car["coats"], (None, "EPOXY_PRIMER", "POLYURETHANE_PAINT", "CLEAR_COAT"))
        self.assertEqual(recipes.construction_for("Polished Chrome")["coats"][0], "CHROME_PLATING")
        self.assertLess(recipes.construction_for("Heavy Chipped Paint")["condition"], .5)

    def test_unknown_recipes_are_read_from_their_names(self):
        painted = recipes.construction_for("My Green Aluminium Panel",
                                           {"category": "Metals", "layers": [{"name": "Stock", "color": (.9, .9, .9)}, {"name": "Green paint", "color": (.1, .4, .1)}]})
        self.assertEqual(painted["material"], "ALUMINUM")
        self.assertEqual(painted["coats"][1:3], ("EPOXY_PRIMER", "ACRYLIC_PAINT"))
        self.assertEqual(painted["paint_color"], (.1, .4, .1))
        self.assertIsNone(painted["environment"])
        self.assertEqual(recipes.construction_for("Tweed", {"category": "Fabric and Leather", "layers": []})["material"], "CANVAS")
        self.assertEqual(recipes.construction_for("Old Saddle", {"category": "Fabric and Leather"})["material"], "CANVAS")
        self.assertEqual(recipes.construction_for("Saddle leather")["material"], "LEATHER")
        self.assertIsNone(recipes.construction_for("Something", {"category": "Unknown"}))

    def test_textiles_and_leather_age_their_own_way(self):
        canvas = aged(Construction("CANVAS"), 5, "OUTDOOR_URBAN", "HEAVY", "NONE")
        lines = dict(model.readout(canvas, Construction("CANVAS")))
        self.assertIn("Fraying", lines)
        self.assertIn("Fading", lines)
        self.assertNotIn("Impact damage", lines)
        self.assertNotIn("Micro scratching", lines)
        self.assertEqual(canvas.oxidation, 0)
        self.assertGreater(canvas.micro_scratching, .3)
        dry = aged(Construction("LEATHER"), 15, "ARID", "MODERATE", "NONE")
        damp = aged(Construction("LEATHER"), 15, "HUMID_INTERIOR", "MODERATE", "NONE")
        self.assertGreater(dry.coating_cracking, damp.coating_cracking * 2, "leather cracks as it dries")
        self.assertGreater(damp.biological, dry.biological)
        leather = dict(model.readout(dry, Construction("LEATHER")))
        for name in ("Cracking", "Burnish", "Scuffing", "Fading"):
            self.assertIn(name, leather)
        roles = document.build(dry, Construction("LEATHER"))["roles"]
        self.assertIn("handling_polish", roles)
        self.assertIn("cracking", roles)
        self.assertNotIn("impact", roles)
        self.assertNotIn("oxidation", roles)
        self.assertEqual(document.build(canvas, Construction("CANVAS"))["roles"][:2], ["substrate", "micro_scratching"])


class DocumentTests(unittest.TestCase):
    def test_roles_follow_the_construction(self):
        state = aged(PAINTED_STEEL, 10)
        doc = document.build(state, PAINTED_STEEL, seed=3)
        self.assertEqual(len(doc["layers"]), len(doc["roles"]))
        self.assertEqual(doc["roles"][:4], ["substrate", "oxidation", "primer", "paint"])
        self.assertIn("corrosion", doc["roles"])
        self.assertEqual(doc["roles"][-1], "dust")
        bare = document.build(aged(Construction("OAK"), 10), Construction("OAK"))
        self.assertNotIn("paint", bare["roles"])
        self.assertNotIn("oxidation", bare["roles"])
        self.assertIn("wood_greying", bare["roles"])
        self.assertIn("wood_checking", bare["roles"])
        varnished = Construction("OAK", ("VARNISH",))
        self.assertIn("topcoat", document.build(aged(varnished, 10), varnished)["roles"])
        galvanized = Construction("CARBON_STEEL", ("GALVANIZATION", "EPOXY_PRIMER", "POLYURETHANE_PAINT", "CLEAR_COAT"))
        roles = document.build(aged(galvanized, 10), galvanized)["roles"]
        self.assertEqual([r for r in roles if r in {"conversion", "primer", "paint", "topcoat"}], ["conversion", "primer", "paint", "topcoat"])

    def test_values_are_driven_by_the_state(self):
        new = document.build(aged(PAINTED_STEEL, 0), PAINTED_STEEL)
        old = document.build(aged(PAINTED_STEEL, 25, "COASTAL", "NONE", "NONE"), PAINTED_STEEL)
        by_role_new = dict(zip(new["roles"], new["layers"]))
        by_role_old = dict(zip(old["roles"], old["layers"]))
        # A new paint film covers everything: its chips mask is closed and its colour is the artist's.
        self.assertEqual(by_role_new["paint"]["mask"]["amount"], 0.0)
        self.assertEqual(tuple(by_role_new["paint"]["color"]), PAINTED_STEEL.paint_color)
        self.assertGreater(by_role_old["paint"]["mask"]["amount"], 0.5)
        self.assertGreater(by_role_old["paint"]["roughness"], by_role_new["paint"]["roughness"])
        self.assertGreater(by_role_old["paint"]["color"][0], by_role_new["paint"]["color"][0])   # faded toward light
        self.assertEqual(by_role_new["corrosion"]["opacity"], 0.0)
        # Optional geometry masks sit behind a procedural base so a set without Position or World
        # Normal still gets dust and streaks, and their base opens with the state.
        for role in ("dust", "water_staining", "biological", "soil"):
            self.assertFalse(by_role_new[role]["mask"].get("optional"), role)
            self.assertTrue(any(m.get("optional") for m in by_role_new[role].get("mask_effects", ())), role)
        self.assertGreater(by_role_new["dust"]["mask"]["black"], by_role_old["dust"]["mask"]["black"])
        self.assertGreater(by_role_old["corrosion"]["opacity"], 0.8)
        self.assertEqual(by_role_new["dust"]["opacity"], 0.0)
        for spec in new["layers"] + old["layers"]:
            self.assertTrue(0 <= spec.get("opacity", 1) <= 1)
            self.assertTrue(all(0 <= v <= 1 for v in spec["color"]))


if __name__ == "__main__":
    unittest.main()
