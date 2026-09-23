import importlib
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))


class AttributeScopeTests(unittest.TestCase):
    def test_selected_object_does_not_require_group_on_other_meshes(self):
        selected = NS(name='Body', vertex_groups={'horn': NS(index=0)})
        other = NS(name='Eyes', vertex_groups={})
        uv = np.array([[0., 0.], [1., 0.], [0., 1.]], np.float32)
        mesh = NS(vertices=[NS(index=i, groups=[NS(group=0, weight=1.)]) for i in range(3)])
        triangle = np.array([[0, 1, 2]])
        entries = [dict(object=obj, mesh=mesh, uv=uv, tri_loops=triangle, tri_vertices=triangle)
                   for obj in (selected, other)]

        @contextmanager
        def targets(ts):
            yield entries

        # Geometry acquisition is a Blender boundary; the field calculation is
        # real NumPy code, including interpolation and UV overlap semantics.
        modules = {'open_painter.geometry.queries': NS(),
                   'open_painter.geometry.snapshots': NS(targets=targets)}
        with patch.dict(sys.modules, modules):
            fields = importlib.import_module('open_painter.generators.scene_fields')
            field = NS(object=selected, kind='VERTEX_GROUP', attribute='horn')
            values = fields._attribute(None, field, 16)
            self.assertGreater(values.sum(), 0)
            self.assertEqual(float(values.max()), 1.)
            field.object = None
            with self.assertRaisesRegex(ValueError, 'Eyes: missing vertex group'):
                fields._attribute(None, field, 16)


if __name__ == '__main__':
    unittest.main()
