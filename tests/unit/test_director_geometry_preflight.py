import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.ai.visual_context import MAPS, require_geometry_maps
from open_painter.authoring.schema import AuthoringError


class GeometryPreflightTests(unittest.TestCase):
    def setUp(self):
        self.provenance = patch.dict(sys.modules, {'open_painter.geometry.provenance':
            SimpleNamespace(status=lambda ts, entry: entry.state)})
        self.provenance.start()
        self.addCleanup(self.provenance.stop)
        self.ts = SimpleNamespace(name='Weapon', baked_maps=[SimpleNamespace(kind=kind, state='Ready',
            image=SimpleNamespace(has_data=True, size=(512, 512))) for kind, _ in MAPS])

    def check(self, targets=None, **settings):
        require_geometry_maps(targets or [self.ts], **dict(dict(send_maps=True, send_view=True), **settings))

    def test_missing_map_names_bake_action(self):
        self.ts.baked_maps.pop(0)
        with self.assertRaisesRegex(AuthoringError, 'Bake Mesh Maps.*AO.*No instruction was sent'):
            self.check()

    def test_stale_unverified_and_unloaded_are_blocked(self):
        for state in ('Stale · update bake', 'Unverified inputs'):
            self.ts.baked_maps[0].state = state
            with self.assertRaises(AuthoringError): self.check()
        self.ts.baked_maps[0].state = 'Ready'
        self.ts.baked_maps[0].image.has_data = False
        with self.assertRaises(AuthoringError): self.check()

    def test_ready_and_disabled_sharing(self):
        self.check()
        for flag in ('send_maps', 'send_view'):
            with self.assertRaisesRegex(AuthoringError, 'Enable Send'):
                self.check(**{flag: False})

    def test_every_scene_target_checked(self):
        other = SimpleNamespace(name='Other material', baked_maps=[])
        with self.assertRaisesRegex(AuthoringError, 'Other material'):
            self.check([self.ts, other])


if __name__ == '__main__':
    unittest.main()
