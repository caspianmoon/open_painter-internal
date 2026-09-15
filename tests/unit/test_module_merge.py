"""Module id namespacing and the three-way update merge on plain dicts."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import unittest

from open_painter.core.material_inheritance import preview
from open_painter.smart_materials import modules


def layer(identifier, parent='', masks=(), effects=(), anchor='', **fields):
    values = dict(id=identifier, parent_id=parent, name=identifier, type='FILL', opacity=1.0,
                  masks=[dict(id=m, source='REFERENCE', reference_anchor=ref) for m, ref in masks],
                  channels=[dict(channel='ROUGHNESS', layer_id=identifier, anchor=anchor, value=.5,
                                 effects=[dict(id=e, kind='LEVELS', strength=1.0) for e in effects])])
    values.update(fields)
    return values


class Namespacing(unittest.TestCase):
    def test_round_trip_and_outside_references(self):
        layers = [layer('root', type='FOLDER'), layer('a', 'root', masks=[('m', 'outside')], effects=['e']),
                  layer('b', 'root', masks=[('n', 'm')], anchor='a:out')]
        mapping = modules.namespace_mapping(layers, 'tok')
        self.assertEqual(mapping['a'], 'tok.a')
        self.assertEqual(mapping['a:out'], 'tok.a:out')
        inst = modules.remap(layers, mapping)
        self.assertEqual([l['id'] for l in inst], ['tok.root', 'tok.a', 'tok.b'])
        self.assertEqual(inst[1]['parent_id'], 'tok.root')
        self.assertEqual(inst[1]['masks'][0]['reference_anchor'], 'outside')   # kept verbatim
        self.assertEqual(inst[2]['masks'][0]['reference_anchor'], 'tok.m')
        self.assertEqual(inst[2]['channels'][0]['anchor'], 'tok.a:out')
        self.assertEqual(inst[2]['channels'][0]['layer_id'], 'tok.b')
        self.assertEqual(inst[1]['channels'][0]['effects'][0]['id'], 'tok.e')
        back = modules.remap(inst, modules.denamespace_mapping(inst, 'tok'))
        self.assertEqual(back, layers)
        self.assertEqual(modules.namespace_mapping(layers, ''), {})

    def test_two_instances_do_not_collide(self):
        layers = [layer('root', type='FOLDER'), layer('a', 'root')]
        first = modules.remap(layers, modules.namespace_mapping(layers, 'one'))
        second = modules.remap(layers, modules.namespace_mapping(layers, 'two'))
        self.assertTrue(modules.owned_ids(first).isdisjoint(modules.owned_ids(second)))

    def test_comparable_strips_ui_state(self):
        cleaned = modules.comparable([dict(id='a', dirty=True, expanded=False, opacity=.123456789, color=(1, 2),
                                           masks=[dict(id='m', active_effect=2)], channels=[])])
        self.assertEqual(cleaned, [dict(id='a', opacity=.123456789, color=[1, 2], masks=[dict(id='m')], channels=[])])


class Merge(unittest.TestCase):
    def test_parent_edits_apply_and_local_edits_win(self):
        base = [layer('a', opacity=1.0), layer('b', opacity=1.0)]
        parent = [layer('a', opacity=.5), layer('b', opacity=1.0), layer('c')]      # v2 tunes a, adds c
        local = [layer('a', opacity=1.0), layer('b', opacity=.2)]                  # user tuned b
        merged, conflicts = modules.merge(base, parent, local)
        self.assertEqual(conflicts, [])
        self.assertEqual([(l['id'], l['opacity']) for l in merged], [('a', .5), ('b', .2), ('c', 1.0)])

    def test_conflicts_local_wins_and_reported(self):
        base = [layer('a', opacity=1.0)]
        parent = [layer('a', opacity=.5)]
        local = [layer('a', opacity=.3)]
        merged, conflicts = modules.merge(base, parent, local)
        self.assertEqual(merged[0]['opacity'], .3)
        self.assertEqual(conflicts, ['material.layers.a.opacity'])
        # v2 removes a layer the user edited: conflict, the user's layer stays.
        merged, conflicts = modules.merge([layer('a'), layer('b', opacity=1.0)], [layer('a')],
                                          [layer('a'), layer('b', opacity=.7)])
        self.assertEqual([l['id'] for l in merged], ['a', 'b'])
        self.assertEqual(conflicts, ['material.layers.b'])
        # v2 removes an untouched layer: it goes.
        merged, conflicts = modules.merge([layer('a'), layer('b')], [layer('a')], [layer('a'), layer('b')])
        self.assertEqual([l['id'] for l in merged], ['a'])
        self.assertEqual(conflicts, [])

    def test_local_additions_are_silent(self):
        base = [layer('a')]
        parent = [layer('a', opacity=.5)]
        local = [layer('a'), layer('mine')]
        merged, conflicts = modules.merge(base, parent, local)
        self.assertEqual([l['id'] for l in merged], ['a', 'mine'])
        self.assertEqual(merged[0]['opacity'], .5)
        self.assertEqual(conflicts, [])
        # A key only the local side has (a newer add-on wrote it) is not a conflict either.
        merged, conflicts = preview({'x': 1}, {'x': 2}, {'x': 1, 'extra': True})
        self.assertEqual(merged, {'x': 2, 'extra': True})
        self.assertEqual(conflicts, [])

    def test_lists_without_ids_merge_positionally(self):
        merged, conflicts = preview({'v': [1, 2, 3]}, {'v': [1, 9, 3]}, {'v': [7, 2, 3]})
        self.assertEqual(merged, {'v': [7, 9, 3]})
        self.assertEqual(conflicts, [])
        merged, conflicts = preview({'v': [1, 2]}, {'v': [1, 9, 3]}, {'v': [7, 2]})
        self.assertEqual(merged, {'v': [7, 2]})
        self.assertEqual(conflicts, ['material.v'])

    def test_effects_and_masks_merge_by_id_inside_layers(self):
        base = [layer('a', effects=['e'], masks=[('m', 'x')])]
        parent = [layer('a', effects=['e', 'f'], masks=[('m', 'x')])]
        local = [layer('a', effects=['e'], masks=[('m', 'y')])]
        merged, conflicts = modules.merge(base, parent, local)
        self.assertEqual([e['id'] for e in merged[0]['channels'][0]['effects']], ['e', 'f'])
        self.assertEqual(merged[0]['masks'][0]['reference_anchor'], 'y')
        self.assertEqual(conflicts, [])


if __name__ == '__main__':
    unittest.main()
