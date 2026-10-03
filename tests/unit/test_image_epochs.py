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


@unittest.skipIf(bpy is None, 'needs Blender')
class ReportUpdatesTests(unittest.TestCase):
    """The scheduler tick evaluates pending depsgraph updates first (``report_updates``), so an
    image an operator just referenced is reported before the tick samples it."""

    def fake(self, running=()):
        from unittest.mock import Mock
        evaluate = Mock()
        fake = SimpleNamespace(app=SimpleNamespace(is_job_running=lambda kind: kind in running),
                               context=SimpleNamespace(evaluated_depsgraph_get=evaluate))
        return fake, evaluate

    def test_it_evaluates_the_depsgraph(self):
        from unittest.mock import patch
        from open_painter.evaluation import dependencies
        fake, evaluate = self.fake()
        with patch.object(dependencies, 'bpy', fake):
            dependencies.report_updates()
        evaluate.assert_called_once_with()

    def test_a_render_or_bake_job_owns_the_scene(self):
        from unittest.mock import patch
        from open_painter.evaluation import dependencies
        for job in ('RENDER', 'OBJECT_BAKE'):
            fake, evaluate = self.fake(running={job})
            with patch.object(dependencies, 'bpy', fake):
                dependencies.report_updates()
            evaluate.assert_not_called()

    def test_the_jobs_it_checks_exist(self):
        for job in ('RENDER', 'OBJECT_BAKE'):
            self.assertFalse(bpy.app.is_job_running(job))   # a TypeError would disable the flush


@unittest.skipIf(bpy is None, 'needs Blender')
class OperatorStepTests(unittest.TestCase):
    """An UNDO operator reports pending depsgraph updates before Blender pushes its step: tags
    still pending then would be stored in the step and set again by an undo to it."""

    def execute(self, options, outcome):
        from unittest.mock import patch
        from open_painter.evaluation import dependencies
        from open_painter.ui.operators import SafeOperator

        class Probe(SafeOperator):
            bl_options = options

            def run(self, context):
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

            def report(self, *args):
                pass
        with patch.object(dependencies, 'report_updates') as reported:
            result = Probe().execute(SimpleNamespace(scene=SimpleNamespace()))
        return result, reported.call_count

    def test_a_finished_undo_operator_reports_its_updates(self):
        self.assertEqual(self.execute({'REGISTER', 'UNDO'}, None), ({'FINISHED'}, 1))
        self.assertEqual(self.execute({'REGISTER', 'UNDO'}, {'FINISHED'}), ({'FINISHED'}, 1))

    def test_no_step_reports_nothing(self):
        self.assertEqual(self.execute({'REGISTER'}, None), ({'FINISHED'}, 0))
        self.assertEqual(self.execute({'REGISTER', 'UNDO'}, {'CANCELLED'}), ({'CANCELLED'}, 0))
        self.assertEqual(self.execute({'REGISTER', 'UNDO'}, ValueError('failed')), ({'CANCELLED'}, 0))


if __name__ == '__main__':
    unittest.main()
