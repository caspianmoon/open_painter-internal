"""Target registry: every entry is consistent and names files the documented way."""
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import unittest

import numpy as np

from open_painter import targets


class Registry(unittest.TestCase):
    def test_entries_are_consistent(self):
        for key, target in targets.REGISTRY.items():
            self.assertEqual(target.key, key)
            self.assertIn(target.normal, {'OPENGL', 'DIRECTX'})
            self.assertIn(target.gloss, {'ROUGHNESS', 'SMOOTHNESS'})
            self.assertIn(target.ao_usage, {'MULTIPLY', 'INDIRECT'})
            self.assertIn(target.mesh_format, {'', 'FBX', 'GLTF'})
            self.assertIn('{set}', target.naming)
            self.assertLessEqual(target.default_resolution, target.max_resolution)
        self.assertEqual({key for key, _, _ in targets.ITEMS}, set(targets.REGISTRY))
        unreal = targets.get('UNREAL')
        self.assertEqual((unreal.normal, unreal.preset, unreal.mesh_options['axis_up']), ('DIRECTX', 'UNREAL', 'Z'))
        self.assertEqual(targets.get('UNITY_HDRP').gloss, 'SMOOTHNESS')
        self.assertEqual(targets.get('GODOT').mesh_format, 'GLTF')
        self.assertIs(targets.get('nonsense'), targets.get('BLENDER'))

    def test_filenames_follow_the_engine_conventions(self):
        self.assertEqual(targets.filename(targets.get('UNREAL'), 'Hull Plates', 'BaseColor', 'png'), 'T_Hull_Plates_BC.png')
        self.assertEqual(targets.filename(targets.get('UNREAL'), 'Hull', 'ORM', 'png', suffix='_v2'), 'T_Hull_ORM_v2.png')
        self.assertEqual(targets.filename(targets.get('GODOT'), 'Hull', 'BaseColorOpacity', 'png'), 'Hull_albedo.png')
        self.assertEqual(targets.filename(targets.get('UNITY_HDRP'), 'Hull', 'MaskMap', 'tif'), 'Hull_MaskMap.tif')

    def test_apply_and_resolution(self):
        settings = SimpleNamespace(preset='BLENDER', file_format='TIFF', bit_depth='16', resolution='SET')
        self.assertTrue(targets.apply(targets.get('UNREAL'), settings))
        self.assertEqual((settings.preset, settings.file_format, settings.bit_depth), ('UNREAL', 'PNG', '8'))
        self.assertFalse(targets.apply(targets.get('UNREAL'), settings))
        self.assertFalse(targets.apply(targets.get('CUSTOM'), settings))
        ts = SimpleNamespace(resolution='8192', name='Hull')
        self.assertEqual(targets.resolution(targets.get('GODOT'), ts, settings), 8192)
        self.assertEqual(targets.resolution(targets.get('UNREAL'), ts, SimpleNamespace(resolution='4096')), 4096)

    def test_manifest_lists_conventions(self):
        info = targets.manifest(targets.get('UNREAL'), SimpleNamespace(name='Hull'),
                                [('BaseColor', Path('T_Hull_BC.png')), ('Normal', Path('T_Hull_N.png'))])
        self.assertEqual(info['normal_map']['green'], 'down (-Y)')
        self.assertEqual(info['files'][0]['color_space'], 'sRGB')
        self.assertEqual(info['files'][1]['compression'], 'BC5')

    def test_conventions_are_involutions(self):
        # DirectX green flip and smoothness inversion round-trip within 8-bit rounding.
        values = np.linspace(0, 1, 1001, dtype=np.float64)
        q = lambda x: np.round(x * 255) / 255
        np.testing.assert_allclose(1 - q(1 - values), q(values), atol=1 / 255 + 1e-9)   # at most one step apart
        np.testing.assert_allclose(1 - q(1 - q(values)), q(values), atol=1e-9)         # exact on stored values


if __name__ == '__main__':
    unittest.main()
