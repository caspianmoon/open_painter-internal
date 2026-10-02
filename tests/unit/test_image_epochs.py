"""Image epochs move for content updates only.

A memfile undo re-syncs the images it restores with no geometry, shading or transform flag
and keeps their pixels; a paint stroke, an image undo and ``update_tag()`` tag the whole
image. Runs in Blender (``run_tests.py`` discovers it there); skipped without bpy.
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

try:
    import bpy
except ImportError:
    bpy = None


@unittest.skipIf(bpy is None, 'needs Blender')
class ImageEpochTests(unittest.TestCase):
    def setUp(self):
        from open_painter.evaluation import dependencies
        self.dependencies = dependencies
        self.image = bpy.data.images.new('Image epoch probe', 4, 4)
        self.output = bpy.data.images.new('Image epoch output', 4, 4)
        self.output['op_engine_output'] = True

    def tearDown(self):
        for image in (self.image, self.output):
            bpy.data.images.remove(image)

    def report(self, image, geometry=False, shading=False, transform=False):
        update = SimpleNamespace(id=SimpleNamespace(original=image), is_updated_geometry=geometry,
                                 is_updated_shading=shading, is_updated_transform=transform)
        self.dependencies.on_depsgraph(bpy.context.scene, SimpleNamespace(updates=[update]))

    def epoch(self, image):
        return self.dependencies.image_epochs().get(image.as_pointer())

    def test_a_content_update_moves_the_epoch(self):
        for flags in (dict(geometry=True, shading=True, transform=True), dict(shading=True), dict(geometry=True)):
            before = self.epoch(self.image)
            self.report(self.image, **flags)
            self.assertIsNotNone(self.epoch(self.image), flags)
            self.assertNotEqual(self.epoch(self.image), before, flags)

    def test_a_memfile_undo_resync_keeps_the_epoch(self):
        self.report(self.image, shading=True)
        before, counter = self.epoch(self.image), self.dependencies.image_epoch()
        self.report(self.image)
        self.assertEqual(self.epoch(self.image), before)
        self.assertEqual(self.dependencies.image_epoch(), counter)

    def test_the_engines_outputs_never_move_it(self):
        # (An earlier test's image may have left an epoch at this address: compare, not None.)
        before, counter = self.epoch(self.output), self.dependencies.image_epoch()
        self.report(self.output, geometry=True, shading=True, transform=True)
        self.assertEqual(self.epoch(self.output), before)
        self.assertEqual(self.dependencies.image_epoch(), counter)


if __name__ == '__main__':
    unittest.main()
