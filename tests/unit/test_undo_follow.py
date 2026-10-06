"""Which undos restored only numeric values of a set (``evaluation.undo_follow``).

A difference counts as numeric by the field's declaration (a ``numeric`` update callback), in
nested groups and collections too; bookkeeping may differ; anything else, an unknown field, a
collection of another length or a datablock reference that went with the undo does not. Runs in
Blender (``run_tests.py`` discovers it there); skipped without bpy.
"""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

try:
    import bpy
except ImportError:
    bpy = None


class Removed:
    """A datablock reference an undo invalidated: comparing it raises, as bpy's does."""

    def __eq__(self, other):
        raise ReferenceError('StructRNA of type Image has been removed')

    __hash__ = object.__hash__


@unittest.skipIf(bpy is None, 'needs Blender')
class NumericOnlyTests(unittest.TestCase):
    def snapshot(self):
        layer = {'id': 'a', 'opacity': .5, 'blend_mode': 2, 'visible': 1,
                 'channels': [{'channel': 0, 'color': [.1, .2, .3, 1.0], 'enabled': 1,
                               'effects': [{'kind': 'LEVELS', 'strength': 1.0}]}],
                 'masks': [{'id': 'm', 'rotation': 0.0, 'effect': {'kind': 'BLUR', 'radius': 2.0}}]}
        return ((layer, copy.deepcopy(layer) | {'id': 'b'}), {'set_id': 's', 'home': 1},
                {'id': 's', 'resolution': 1, 'edit_serial': 4, 'outputs': [{'channel': 0, 'dirty': 0}]})

    def edited(self, edit):
        before = self.snapshot()
        after = copy.deepcopy(before)
        edit(after)
        from open_painter.evaluation.undo_follow import numeric_only
        return numeric_only(before, after)

    def test_numeric_fields_in_every_group(self):
        cases = {
            'layer opacity': lambda s: s[0][0].update(opacity=.25),
            'layer visibility': lambda s: s[0][1].update(visible=0),
            'channel colour': lambda s: s[0][0]['channels'][0]['color'].__setitem__(0, .9),
            'channel effect': lambda s: s[0][0]['channels'][0]['effects'][0].update(strength=.3),
            'mask rotation': lambda s: s[0][0]['masks'][0].update(rotation=.4),
            'mask effect': lambda s: s[0][0]['masks'][0]['effect'].update(radius=5.0),
            'a numeric value set back to its default (not stored)': lambda s: s[0][0].pop('opacity'),
        }
        for name, edit in cases.items():
            self.assertTrue(self.edited(edit), name)

    def test_bookkeeping_may_differ(self):
        self.assertTrue(self.edited(lambda s: s[2].update(edit_serial=9, dirty=1, active_layer=3)))
        self.assertTrue(self.edited(lambda s: s[2]['outputs'][0].update(dirty=1, revision=2)))
        self.assertTrue(self.edited(lambda s: s[0][0].update(dirty=0)))

    def test_anything_else_rebuilds(self):
        cases = {
            'blend mode': lambda s: s[0][0].update(blend_mode=3),
            'effect kind': lambda s: s[0][0]['channels'][0]['effects'][0].update(kind='BLUR'),
            'mask effect kind': lambda s: s[0][0]['masks'][0]['effect'].update(kind='LEVELS'),
            'channel enabled': lambda s: s[0][0]['channels'][0].update(enabled=0),
            'a mask added': lambda s: s[0][0]['masks'].append({'id': 'n'}),
            'an unknown field': lambda s: s[0][0].update(unknown=1),
            'the set resolution': lambda s: s[2].update(resolution=2),
            'the store': lambda s: s[1].update(set_id='t'),
            'a datablock': lambda s: s[0][0]['channels'][0].update(image=('ID', 'Image', 7)),
        }
        for name, edit in cases.items():
            self.assertFalse(self.edited(edit), name)
        from open_painter.evaluation.undo_follow import numeric_only
        before = self.snapshot()
        self.assertFalse(numeric_only(before, (before[0][:1],) + before[1:]), 'a layer removed')
        self.assertFalse(numeric_only(before, None))

    def changed(self, edit):
        before = self.snapshot()
        after = copy.deepcopy(before)
        edit(after)
        from open_painter.evaluation.undo_follow import changed_layers
        return changed_layers(before, after)

    def test_structural_values_with_collections_in_place_name_their_layers(self):
        cases = {
            'blend mode': lambda s: s[0][0].update(blend_mode=3),
            'a channel switched off': lambda s: s[0][1]['channels'][0].update(enabled=0),
            # A channel's effects are compiled by position: a kind changed in place is patched.
            'an effect kind': lambda s: s[0][0]['channels'][0]['effects'][0].update(kind='BLUR'),
            'a mask effect kind': lambda s: s[0][0]['masks'][0]['effect'].update(kind='LEVELS'),
            'blend mode and opacity': lambda s: s[0][0].update(blend_mode=3, opacity=.1),
        }
        for name, edit in cases.items():
            expected = frozenset({'b'}) if name == 'a channel switched off' else frozenset({'a'})
            self.assertEqual(self.changed(edit), expected, name)
        self.assertEqual(self.changed(lambda s: s[0][0].update(opacity=.1)), frozenset(), 'numeric only')
        both = self.changed(lambda s: (s[0][0].update(blend_mode=3), s[0][1].update(blend_mode=4)))
        self.assertEqual(both, frozenset({'a', 'b'}))

    def test_collections_out_of_place_rebuild(self):
        def swap_masks(s):
            masks = s[0][0]['masks']
            masks.append(dict(masks[0], id='n'))
            masks[0], masks[1] = masks[1], masks[0]
        cases = {
            'a mask added': lambda s: s[0][0]['masks'].append({'id': 'n'}),
            'masks moved': swap_masks,
            'an effect added': lambda s: s[0][0]['channels'][0]['effects'].append({'kind': 'BLUR'}),
            # Effects with ids (anchors) are named by them: another one in place is not the same.
            'a named effect replaced': lambda s: (s[0][0]['channels'][0]['effects'][0].update(id='x'),),
            'a layer replaced': lambda s: s[0][0].update(id='z'),
            'the set': lambda s: s[2].update(resolution=2),
        }
        for name, edit in cases.items():
            self.assertIsNone(self.changed(edit), name)

    def test_a_reference_the_undo_invalidated_rebuilds(self):
        before = self.snapshot()
        before[0][0]['channels'][0]['image'] = Removed()
        after = self.snapshot()
        after[0][0]['channels'][0]['image'] = ('ID', 'Image', 7)
        from open_painter.evaluation.undo_follow import numeric_only
        self.assertFalse(numeric_only(before, after))

    def test_freeze_names_datablocks_by_session(self):
        from open_painter.evaluation.undo_follow import _freeze
        from open_painter.properties.layer import OP_Layer
        image = bpy.data.images.new('Undo follow probe', 4, 4)
        try:
            data = {'channels': [{'image': image, 'effects': [{'image': image}]}],
                    'masks': [{'effect': {'image': image}, 'influence': {'image': image}}]}
            _freeze(OP_Layer, data)
            reference = ('ID', 'Image', image.session_uid)
            self.assertEqual(data['channels'][0]['image'], reference)
            self.assertEqual(data['channels'][0]['effects'][0]['image'], reference)
            self.assertEqual(data['masks'][0]['effect']['image'], reference)
            self.assertEqual(data['masks'][0]['influence']['image'], reference)
        finally:
            bpy.data.images.remove(image)


if __name__ == '__main__':
    unittest.main()
