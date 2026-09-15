"""Composite anchors (layer output, folder composite) in the reference graph."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import unittest
from types import SimpleNamespace

from open_painter.evaluation import anchors
from open_painter.evaluation.references import dependent_channels, graph, issues, warnings


def channel(name, effects=(), anchor='', anchor_channel='', enabled=True):
    return SimpleNamespace(channel=name, enabled=enabled, anchor=anchor, anchor_channel=anchor_channel,
                           effects=[SimpleNamespace(kind=kind, id=identifier, name=identifier, enabled=True)
                                    for kind, identifier in effects])


def mask(source, identifier, **fields):
    values = dict(id=identifier, source=source, enabled=True, name=identifier, generator='EDGE_WEAR',
                  reference_layer='', reference_mask='', reference_channel='MASK', reference_stage='POST_FILTER',
                  reference_anchor='', detail_height_anchor='', detail_normal_anchor='')
    values.update(fields)
    return SimpleNamespace(**values)


def layer(identifier, masks=(), channels=(), kind='FILL', parent='', group='ISOLATED', output=False, group_out=False):
    return SimpleNamespace(id=identifier, name=identifier, type=kind, parent_id=parent, group_mode=group,
                           publish_output=output, publish_group=group_out,
                           masks=list(masks), channels=list(channels))


def texture_set(*layers):
    return SimpleNamespace(id='set', edit_serial=1, layers=list(layers))


def cyclic(ts):
    return any('cycle' in text for text in issues(ts))


class OutputGraph(unittest.TestCase):
    def test_registry_and_labels(self):
        folder = layer('f', kind='FOLDER', group_out=True, output=True)
        ts = texture_set(folder)
        registry = anchors.index(ts)
        self.assertEqual(set(registry), {'f:out', 'f:group'})
        self.assertEqual((registry['f:out'].kind, registry['f:out'].channel), ('OUTPUT', '*'))
        self.assertEqual(anchors.owned(folder), ['f:out', 'f:group'])
        self.assertEqual(anchors.label(ts, registry['f:group']), 'f composite · f · Folder composite')
        self.assertEqual(anchors.channel_of(registry['f:out'], 'HEIGHT'), 'HEIGHT')
        self.assertEqual(anchors.channel_of(registry['f:out'], 'MASK'), anchors.DEFAULT_CHANNEL)
        self.assertEqual(anchors.derived_mapping({'a': 'b'}), {'a': 'b', 'a:out': 'b:out', 'a:group': 'b:group'})
        # A plain layer never owns a group anchor.
        self.assertNotIn('p:group', anchors.index(texture_set(layer('p', group_out=True))))

    def test_composition_edges(self):
        base = layer('base', [], [channel('ROUGHNESS', [('LEVELS', '')])])
        worn = layer('worn', [mask('PAINT', 'm')], [channel('ROUGHNESS')], output=True)
        reader = layer('reader', [], [channel('ROUGHNESS', anchor='worn:out')])
        edges, errors, notes = graph(texture_set(base, worn, reader))
        self.assertEqual(errors, [])
        self.assertEqual(notes, [])
        self.assertIn(('OUT', 'worn', 'ROUGHNESS'), edges[('reader', 'ROUGHNESS', 0)])
        self.assertEqual(edges[('OUT', 'worn', 'ROUGHNESS')],
                         {('worn', 'ROUGHNESS', 0), ('worn', 'MASK', 1), ('BELOW', 'worn', 'ROUGHNESS')})
        self.assertEqual(edges[('BELOW', 'worn', 'ROUGHNESS')], {('OUT', 'base', 'ROUGHNESS')})
        self.assertEqual(edges[('OUT', 'base', 'ROUGHNESS')],
                         {('base', 'ROUGHNESS', 1), ('base', 'MASK', 0), ('BELOW', 'base', 'ROUGHNESS')})
        self.assertNotIn(('BELOW', 'base', 'ROUGHNESS'), edges)   # nothing below the first root layer

    def test_group_edges_and_channel_choice(self):
        folder = layer('f', kind='FOLDER', group_out=True)
        a = layer('a', parent='f', channels=[channel('HEIGHT')])
        b = layer('b', parent='f', channels=[channel('HEIGHT')])
        reader = layer('r', [mask('REFERENCE', 'm', reference_anchor='f:group', reference_channel='HEIGHT')])
        edges, errors, _ = graph(texture_set(folder, a, b, reader))
        self.assertEqual(errors, [])
        self.assertIn(('GROUP', 'f', 'HEIGHT'), edges[('r', 'MASK', 1)])
        self.assertEqual(edges[('GROUP', 'f', 'HEIGHT')], {('OUT', 'b', 'HEIGHT')})
        self.assertEqual(edges[('BELOW', 'b', 'HEIGHT')], {('OUT', 'a', 'HEIGHT')})
        self.assertNotIn(('BELOW', 'a', 'HEIGHT'), edges)   # isolated: no backdrop chain
        passthrough = layer('f', kind='FOLDER', group='PASSTHROUGH', group_out=True)
        base = layer('base', channels=[channel('HEIGHT')])
        edges, errors, _ = graph(texture_set(base, passthrough, a, b, reader))
        self.assertEqual(errors, [])
        self.assertEqual(edges[('GROUP', 'f', 'HEIGHT')], {('OUT', 'b', 'HEIGHT'), ('BELOW', 'f', 'HEIGHT')})
        self.assertEqual(edges[('BELOW', 'a', 'HEIGHT')], {('BELOW', 'f', 'HEIGHT')})
        self.assertEqual(edges[('BELOW', 'f', 'HEIGHT')], {('OUT', 'base', 'HEIGHT')})

    def test_cycle_rules(self):
        producer = layer('p', channels=[channel('ROUGHNESS')], output=True)
        # Consumer above: fine. Consumer below, or the producer itself: cycles.
        self.assertFalse(cyclic(texture_set(producer, layer('c', channels=[channel('ROUGHNESS', anchor='p:out')]))))
        self.assertTrue(cyclic(texture_set(layer('c', channels=[channel('ROUGHNESS', anchor='p:out')]), producer)))
        self.assertTrue(cyclic(texture_set(layer('p', channels=[channel('ROUGHNESS', anchor='p:out')], output=True))))
        # A folder masked by its own child's output is legal; a child reading the folder composite is not.
        folder = layer('f', [mask('REFERENCE', 'm', reference_anchor='k:out')], kind='FOLDER', group_out=True)
        kid = layer('k', parent='f', channels=[channel('ROUGHNESS')], output=True)
        self.assertFalse(cyclic(texture_set(folder, kid)))
        inside = layer('k', parent='f', channels=[channel('ROUGHNESS', anchor='f:group')])
        self.assertTrue(cyclic(texture_set(layer('f', kind='FOLDER', group_out=True), inside)))
        # Cross folder: an earlier isolated folder's composite feeding a later one is fine.
        first = layer('f1', kind='FOLDER', group_out=True)
        second = layer('f2', kind='FOLDER')
        self.assertFalse(cyclic(texture_set(first, layer('x', parent='f1', channels=[channel('ROUGHNESS')]),
                                            second, layer('y', parent='f2', channels=[channel('ROUGHNESS', anchor='f1:group')]))))
        error = issues(texture_set(layer('c', channels=[channel('ROUGHNESS', anchor='p:out')]), producer))[0]
        self.assertIn('c', error)
        self.assertIn('p', error)

    def test_dependent_channels_follow_contributors(self):
        base = layer('base', channels=[channel('ROUGHNESS'), channel('BASE_COLOR')])
        producer = layer('p', channels=[channel('ROUGHNESS'), channel('BASE_COLOR')], output=True)
        above = layer('above', channels=[channel('ROUGHNESS')])
        reader = layer('r', channels=[channel('HEIGHT', anchor='p:out', anchor_channel='ROUGHNESS')])
        ts = texture_set(base, producer, above, reader)
        self.assertEqual(dependent_channels(ts, 'base'), {'HEIGHT'})
        self.assertEqual(dependent_channels(ts, 'base', 'ROUGHNESS'), {'HEIGHT'})
        self.assertEqual(dependent_channels(ts, 'base', 'BASE_COLOR'), set())
        self.assertEqual(dependent_channels(ts, 'above'), set())
        registry = anchors.index(ts)
        self.assertEqual(anchors.contributors(ts, registry['p:out']), {'base', 'p'})
        self.assertEqual(anchors.consumed_channels(reader.channels[0]), (('p:out', 'ROUGHNESS'),))

    def test_missing_composite_anchor_warns(self):
        reader = layer('r', channels=[channel('ROUGHNESS', anchor='gone:out')])
        self.assertEqual(issues(texture_set(reader)), [])
        self.assertTrue(any('missing' in text for text in warnings(texture_set(reader))))


if __name__ == '__main__':
    unittest.main()
