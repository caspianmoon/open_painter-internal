"""Deterministic Open Graph placement."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import unittest

from open_painter.graph.layout import COLUMN_WIDTH, INDENT, ROW_GAP, height, positions, ranks


class GraphLayout(unittest.TestCase):
    def test_nested_branches_with_equal_rank_do_not_overlap(self):
        result = positions([('layer', 0)], [('a', 'group'), ('b', 'group'), ('group', 'layer')],
                           inline={'layer': ['group', 'a', 'b']}, sockets={'a': 10, 'b': 5})
        self.assertEqual(result['a'][0], result['b'][0])
        self.assertGreaterEqual(result['a'][1] - result['b'][1], height(10) + ROW_GAP)
        self.assertEqual(len(result), len(set(result.values())))

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

    def test_mask_chain_shares_the_row_and_filters_sit_under_it(self):
        rows = [('layer', 0), ('base', 0)]
        sockets = {'layer': 3, 'base': 2, 'm1': 9, 'm2': 2, 'f1': 2}
        links = [('m1', 'm2'), ('m2', 'layer'), ('f1', 'layer')]
        result = positions(rows, links, output='out', sockets=sockets,
                           inline={'layer': ['m1', 'm2']}, subrows={'layer': [['f1']]})
        step = ROW_GAP
        self.assertEqual(result['layer'], (2 * COLUMN_WIDTH, 0))       # right of its mask chain
        self.assertEqual(result['m1'], (0, 0))                           # same row, left to right
        self.assertEqual(result['m2'], (COLUMN_WIDTH, 0))
        self.assertEqual(result['f1'], (INDENT, -(height(9) + step)))    # the tallest row member sets the step
        self.assertEqual(result['base'], (0, -(height(9) + height(2) + 2 * step)))
        self.assertEqual(result['out'], (3 * COLUMN_WIDTH + INDENT, 0))


if __name__ == '__main__':
    unittest.main()
