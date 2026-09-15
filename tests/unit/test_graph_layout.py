"""Deterministic Open Graph placement."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import unittest

from open_painter.graph.layout import COLUMN_WIDTH, INDENT, ROW_GAP, height, positions, ranks


class GraphLayout(unittest.TestCase):
    def test_ranks_follow_dependencies(self):
        self.assertEqual(ranks({'a', 'b', 'c'}, [('a', 'b'), ('b', 'c')]), {'a': 0, 'b': 1, 'c': 2})
        self.assertIn(ranks({'a', 'b'}, [('a', 'b'), ('b', 'a')])['a'], {1, 2})   # cycle cut, no recursion error
        self.assertEqual(ranks({'a'}, [('ghost', 'a')]), {'a': 0})          # unknown producers ignored

    def test_positions_are_rows_and_columns(self):
        rows = [('top', 0), ('folder', 0), ('child', 1), ('base', 0)]
        sockets = {'top': 4, 'folder': 1, 'child': 2, 'base': 8}
        result = positions(rows, [('base', 'child'), ('child', 'top')], output='out', sockets=sockets)
        step = ROW_GAP
        self.assertEqual(result['top'], (2 * COLUMN_WIDTH, 0))
        self.assertEqual(result['folder'], (0, -(height(4) + step)))
        self.assertEqual(result['child'], (COLUMN_WIDTH + INDENT, -(height(4) + height(1) + 2 * step)))
        self.assertEqual(result['base'], (0, -(height(4) + height(1) + height(2) + 3 * step)))
        self.assertEqual(result['out'], (3 * COLUMN_WIDTH + INDENT, 0))
        self.assertGreater(height(8), height(0))
        self.assertEqual(positions(rows, [('base', 'child'), ('child', 'top')], output='out', sockets=sockets), result)


if __name__ == '__main__':
    unittest.main()
