"""Lossless provider context factoring; no Blender or provider required."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from open_painter.authoring import context, schema


def expand_context(value):
    """Independent decoder implementing the provider's documented merge rule."""
    tables = value.get("context_encoding", {}).get("defaults", {})
    def expand(item):
        if isinstance(item, list):
            return [expand(child) for child in item]
        if not isinstance(item, dict):
            return item
        combined = deepcopy(tables[item["$defaults"]]) if "$defaults" in item else {}
        combined.update({key: child for key, child in item.items() if key != "$defaults"})
        return {key: expand(child) for key, child in combined.items()}
    return expand({key: child for key, child in value.items() if key != "context_encoding"})


def large_context():
    values = dict(enabled=True, opacity=1.0, value=.5, color=[.12345678901234567, .2, .3, 1],
        procedural="", procedural_scale=8.0, procedural_seed=1, procedural_detail=.15,
        procedural_height=.02, projection="UV", scale=[1.0, 1.0], offset=[0.0, -0.0],
        rotation=0.0, triplanar_hardness=4.0)
    layers = []
    for index in range(32):
        channels = [dict(channel=channel, values=dict(deepcopy(values), enabled=index % 2 == 0),
            image=False, material_source_id="source:" + str(index), effects=[])
            for channel in schema.CHANNEL["enum"]]
        layers.append(dict(id="layer:" + str(index), in_scope=index > 0, parent_id="",
            values=dict(name="Layer " + str(index), visible=True, opacity=.8, group_mode="ISOLATED"),
            channels=channels, masks=[]))
    regions = [dict(id="region:" + str(index), object_id="object:" + str(index % 4),
        kind="CONNECTED_COMPONENT", face_count=index + 1, triangle_count=index + 2,
        topology_digest="0123456789abcdef" * 4,
        provenance="Geometry-derived region; no semantic label or artist geometry modification. " * 5,
        uncertainty="Connected membership does not establish semantic meaning. " * 4,
        material_space=dict(render_uv="UVMap", validity="FINITE_NONDEGENERATE_IN_0_1", overlap_checked=False),
        center_world=[index * .00012345678901234567, 0.0, -0.0], semantic_label=None)
        for index in range(300)]
    regions[0]["optional"] = None  # Other records lack this field altogether.
    regions[1]["topology_digest"] = "different topology must override inherited value"
    return dict(layers=layers, regions=regions, protected_paths=[["layers", "layer:0"]],
                anchors=[dict(id="anchor:exact", layer_id="layer:0")])


class ContextCompactionTests(unittest.TestCase):
    def test_small_context_keeps_original_representation(self):
        small = dict(layers=[], regions=[], optional=None)
        self.assertIs(context.compact(small), small)
        self.assertNotIn("context_encoding", small)

    def test_large_context_roundtrips_exactly_without_input_mutation(self):
        original = large_context()
        before = json.dumps(original, sort_keys=True)
        self.assertGreater(len(before.encode()), schema.MAX_BYTES)
        compacted = context.compact(original)
        self.assertLess(len(json.dumps(compacted).encode()), schema.MAX_BYTES)
        self.assertEqual(json.dumps(expand_context(compacted), sort_keys=True), before)
        self.assertEqual(json.dumps(original, sort_keys=True), before)
        self.assertEqual(context.compact(original), compacted)
        self.assertEqual(context.compact(compacted), compacted)
        self.assertIn("channel_values", compacted["context_encoding"]["defaults"])
        self.assertTrue(any(key.startswith("regions:") for key in compacted["context_encoding"]["defaults"]))

    def test_identity_controls_and_missing_vs_null_remain_explicit(self):
        compacted = context.compact(large_context())
        for layer in compacted["layers"]:
            self.assertIn("id", layer)
            self.assertIn("in_scope", layer)
            for channel in layer["channels"]:
                self.assertTrue({"enabled", "opacity", "color", "value", "procedural"} <= channel["values"].keys())
        restored = expand_context(compacted)
        self.assertIsNone(restored["regions"][0]["optional"])
        self.assertNotIn("optional", restored["regions"][1])
        self.assertIn("different topology", restored["regions"][1]["topology_digest"])
        self.assertEqual(restored["protected_paths"], [["layers", "layer:0"]])


if __name__ == "__main__":
    unittest.main()
