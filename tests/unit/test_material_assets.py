"""Portable image references are validated before Blender allocation."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from open_painter.smart_materials.format import read


class MaterialAssetTests(unittest.TestCase):
    def archive(self, directory, reference, assets):
        path = Path(directory) / "detail.openpaintmaterial"
        with ZipFile(path, "w") as archive:
            archive.writestr("material.json", json.dumps({"format_version": 2, "layers": [{
                "id": "fill", "type": "FILL", "channels": [{"detail_image": reference}],
            }]}))
            for name in assets:
                archive.writestr(name, b"validated separately by Blender image loading")
        return path

    def test_shared_optional_detail_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.archive(directory, {"asset": "assets/0000.exr", "colorspace": "Non-Color"}, ["assets/0000.exr"])
            data, assets = read(path)
            self.assertEqual(data["layers"][0]["channels"][0]["detail_image"]["asset"], "assets/0000.exr")
            self.assertEqual(list(assets), ["assets/0000.exr"])

    def test_missing_or_unsafe_detail_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            for key in ("assets/absent.exr", "assets/../escape.exr", ["assets/0000.exr"]):
                path = self.archive(directory, {"asset": key}, ["assets/0000.exr"])
                with self.assertRaisesRegex(ValueError, "Missing smart material texture"):
                    read(path)

    def test_invalid_detail_colorspace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.archive(directory, {"asset": "assets/0000.exr", "colorspace": 42}, ["assets/0000.exr"])
            with self.assertRaisesRegex(ValueError, "colorspace"):
                read(path)
