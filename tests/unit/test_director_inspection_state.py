import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.ai.viewport import draw_view, view_identity


class InspectionStateTests(unittest.TestCase):
    def test_offscreen_restores_view_even_on_draw_error(self):
        for fail in (False, True):
            view = SimpleNamespace(view_rotation=[1., 0., 0., 0.], view_location=[1., 2., 3.],
                view_distance=5., view_perspective='ORTHO', view_camera_offset=[0., 0.], view_camera_zoom=0.)
            space = SimpleNamespace(region_3d=view)
            def draw(*args, **kwargs):
                view.view_rotation[0] = .8
                view.view_perspective = 'PERSP'
                view.view_distance = 20.
                if fail:
                    raise RuntimeError('draw failed')
            screen = SimpleNamespace(draw_view3d=draw)
            if fail:
                with self.assertRaises(RuntimeError):
                    draw_view(screen, None, None, space, None, None, None)
            else:
                draw_view(screen, None, None, space, None, None, None)
            self.assertEqual(view.view_rotation, [1., 0., 0., 0.])
            self.assertEqual(view.view_perspective, 'ORTHO')
            self.assertEqual(view.view_distance, 5.)

    def test_evaluation_noise_is_not_a_camera_edit(self):
        def identity(value):
            return view_identity(SimpleNamespace(view_matrix=[[value]], window_matrix=[[1.]]))
        self.assertEqual(identity(.34567890), identity(.34567893))
        self.assertNotEqual(identity(.34567890), identity(.35))


if __name__ == '__main__':
    unittest.main()
