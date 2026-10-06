"""``resource_keys.colorspace_name`` reads an image's colour space from its struct's bytes.

The RNA getter lists every colour space of the configuration on each read; every evaluation
reads each sampled image's. The bytes must read as the getter does, after a change too, and an
offset no embedded struct could have keeps every read on RNA. Runs in Blender (``run_tests.py``
discovers it there); skipped without bpy.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

try:
    import bpy
except ImportError:
    bpy = None


@unittest.skipIf(bpy is None, 'needs Blender')
class ColorspaceNameTests(unittest.TestCase):
    def setUp(self):
        self.images = [bpy.data.images.new('Colour space probe', 4, 4),
                       bpy.data.images.new('Colour space probe float', 4, 4, float_buffer=True)]
        self.spaces = []
        for space in ('Non-Color', 'sRGB', 'Linear Rec.709', 'ACEScg', 'AgX Log', 'Linear CIE-XYZ E'):
            try:
                self.images[0].colorspace_settings.name = space   # this configuration has it
            except TypeError:
                continue
            self.spaces.append(space)
        self.assertGreaterEqual(len(self.spaces), 3, self.spaces)

    def tearDown(self):
        for image in self.images:
            bpy.data.images.remove(image)

    def test_the_bytes_read_as_the_getter_after_every_change(self):
        from open_painter.evaluation.resource_keys import colorspace_name, _colorspace_checked
        for image in self.images:
            self.assertEqual(colorspace_name(image), image.colorspace_settings.name)
            self.assertIn(image.as_pointer(), _colorspace_checked)   # the bytes serve it from now on
            for space in self.spaces:
                image.colorspace_settings.name = space
                self.assertEqual(colorspace_name(image), image.colorspace_settings.name, space)

    def test_an_implausible_offset_keeps_rna(self):
        from open_painter.evaluation import resource_keys
        image = self.images[0]
        with patch.object(resource_keys, '_colorspace_offset', False), \
                patch.object(resource_keys, '_colorspace_checked', set()) as checked:
            for space in self.spaces[:3]:
                image.colorspace_settings.name = space
                self.assertEqual(resource_keys.colorspace_name(image), space)
            self.assertFalse(checked)

    def test_bytes_that_disagree_are_not_trusted(self):
        from open_painter.evaluation import resource_keys
        image = self.images[1]
        with patch.object(resource_keys, '_colorspace_checked', set()) as checked, \
                patch.object(resource_keys, '_stored_colorspace', return_value='Something else'):
            self.assertEqual(resource_keys.colorspace_name(image), image.colorspace_settings.name)
            self.assertFalse(checked)


if __name__ == '__main__':
    unittest.main()
