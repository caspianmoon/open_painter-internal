"""An undo that restores no datablock keeps the engine's sessions.

A memfile step reads the file into a new Main; an image paint, sculpt or edit-mode step restores
pixels or a mesh in place. ``cache.on_undo`` starts every session over after the first kind only,
and after any undo while a set is evaluated on the CPU. Runs in Blender (``run_tests.py``
discovers it there); skipped without bpy.
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

try:
    import bpy
except ImportError:
    bpy = None


@unittest.skipIf(bpy is None, 'needs Blender')
class UndoKindTests(unittest.TestCase):
    def undo(self, before, after, backends=('LIVE',), pre=True):
        """Run the undo handlers with bpy.data at ``before`` then ``after``; True if every
        session started over (``_relearn``)."""
        from open_painter.evaluation import cache, shader_resources
        sets = [SimpleNamespace(preview_backend=backend) for backend in backends]
        scene = SimpleNamespace(open_painter=SimpleNamespace(texture_sets=sets))
        main = SimpleNamespace(pointer=before)
        fake = SimpleNamespace(data=SimpleNamespace(as_pointer=lambda: main.pointer, scenes=[scene]),
                               context=SimpleNamespace(mode='OBJECT'))
        with patch.object(cache, 'bpy', fake), patch.object(cache, '_relearn') as relearn, \
                patch.object(shader_resources, 'retire_unloaded_leaves') as retire, \
                patch.object(shader_resources, 'freeze_new_leaves') as freeze:
            if pre:
                cache.on_undo_pre(None)
            main.pointer = after
            cache.on_undo(None)
        retire.assert_called_once_with()
        freeze.assert_called_once_with(True)   # whichever kind: a later memfile undo frees new leaves
        return relearn.called

    def test_a_memfile_step_starts_every_session_over(self):
        self.assertTrue(self.undo(1, 2))

    def test_a_step_restoring_no_datablock_keeps_the_sessions(self):
        self.assertFalse(self.undo(1, 1))

    def test_a_cpu_set_starts_over_after_any_undo(self):
        self.assertTrue(self.undo(1, 1, backends=('LIVE', 'CPU')))

    def test_an_undo_without_its_pre_handler_starts_over(self):
        from open_painter.evaluation import cache
        cache._undo_main = None
        self.assertTrue(self.undo(1, 1, pre=False))

    def test_the_main_recorded_serves_one_undo(self):
        from open_painter.evaluation import cache
        self.assertFalse(self.undo(1, 1))
        self.assertIsNone(cache._undo_main)


if __name__ == '__main__':
    unittest.main()
