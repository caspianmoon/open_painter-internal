"""Pure composition and serialization tests (run with Blender's Python)."""

import sys, unittest, tempfile, json
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from open_painter.evaluation.blend_modes import blend, sample_uv
from open_painter.export.channel_pack import pack
from open_painter.smart_materials.format import read


class MathTests(unittest.TestCase):
    def test_opacity_extremes(self):
        a = np.array([0.2, 0.5, 0.8])
        b = np.array([0.8, 0.1, 0.2])
        np.testing.assert_allclose(blend(a, b, 0), a)
        np.testing.assert_allclose(blend(a, b, 1), b)
        np.testing.assert_allclose(blend(a, b, 0.25), a * 0.75 + b * 0.25)

    def test_overlay_both_branches(self):
        np.testing.assert_allclose(
            blend(np.array([0.25, 0.75]), np.array([0.4, 0.4]), 1, "OVERLAY"), [0.2, 0.7]
        )

    def test_packing(self):
        channels = {
            k: np.full((2, 2, 4), v, dtype=np.float32)
            for k, v in [("AO", 0.8), ("ROUGHNESS", 0.3), ("METALLIC", 0.7)]
        }
        np.testing.assert_allclose(
            pack(channels, ("AO", "ROUGHNESS", "METALLIC", "ONE"))[0, 0], [0.8, 0.3, 0.7, 1]
        )
        np.testing.assert_allclose(
            pack(channels, ("METALLIC", "ZERO", "ZERO", "ONE_MINUS_ROUGHNESS"))[0, 0],
            [0.7, 0, 0, 0.7],
        )

    def test_packing_only_constants(self):
        # Zero/One slots load no channel, so the exporter sizes the output itself.
        np.testing.assert_allclose(pack({}, ("ONE", "ZERO", "ONE", "ONE_MINUS_ONE"), shape=(2, 2, 4))[1, 1], [1, 0, 1, 0])
        with self.assertRaises(ValueError):
            pack({}, ("ONE", "ONE", "ONE", "ONE"))

    def test_uv_centers(self):
        data = np.arange(16, dtype=np.float32).reshape(2, 2, 4)
        np.testing.assert_allclose(sample_uv(data, np.array(0.25), np.array(0.25)), data[0, 0])

    def test_invalid_hierarchy_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.openpaintmaterial"
            path.write_text(
                json.dumps(
                    {
                        "format_version": 1,
                        "layers": [{"id": "a", "type": "FOLDER", "parent_id": "a"}],
                    }
                ), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "hierarchy"):
                read(path)
