"""Anchor registry and item-granular reference graph on plain attribute fixtures."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import unittest
from types import SimpleNamespace

from open_painter.evaluation import anchors
from open_painter.evaluation.references import dependent_channels, issues, warnings


def channel(name, effects=(), anchor='', enabled=True):
    return SimpleNamespace(channel=name, enabled=enabled, anchor=anchor,
                           effects=[SimpleNamespace(kind=kind, id=identifier, name=identifier, enabled=True) for kind, identifier in effects])


def mask(source, identifier, **fields):
    values = dict(id=identifier, source=source, enabled=True, name=identifier, generator='EDGE_WEAR',
                  reference_layer='', reference_mask='', reference_channel='MASK', reference_stage='POST_FILTER',
                  reference_anchor='', detail_height_anchor='', detail_normal_anchor='')
    values.update(fields)
    return SimpleNamespace(**values)


def layer(identifier, masks=(), channels=(), kind='FILL'):
    return SimpleNamespace(id=identifier, name=identifier, type=kind, masks=list(masks), channels=list(channels))


def texture_set(*layers):
    return SimpleNamespace(id='set', edit_serial=1, layers=list(layers))


class AnchorGraph(unittest.TestCase):
    def test_registry_positions_and_consumers(self):
        source = layer('source', [mask('PAINT', 'p'), mask('ANCHOR', 'a'), mask('PAINT', 'q')],
                       [channel('ROUGHNESS', [('LEVELS', ''), ('ANCHOR', 'c')])])
        consumer = layer('consumer', [mask('REFERENCE', 'r', reference_anchor='a'),
                                      mask('GENERATOR', 'g', detail_height_anchor='c')],
                         [channel('BASE_COLOR', anchor='c')])
        ts = texture_set(source, consumer)
        registry = anchors.index(ts)
        self.assertEqual((registry['a'].kind, registry['a'].position), ('MASK', 1))
        self.assertEqual((registry['c'].kind, registry['c'].channel, registry['c'].position), ('CHANNEL', 'ROUGHNESS', 1))
        self.assertEqual([kind for _, kind, _ in anchors.consumers(ts, 'c')], ['CHANNEL', 'DETAIL_HEIGHT'])
        self.assertEqual(anchors.owned(source), ['a', 'c'])
        self.assertEqual(anchors.sort_key(ts, registry['c']), (0, 1, 1))
        self.assertEqual(anchors.label(ts, registry['c']), 'c · source · Roughness filters')
        self.assertEqual(issues(ts), [])
        self.assertEqual(warnings(ts), [])
        self.assertEqual(dependent_channels(ts, 'source', 'MASK'), {'BASE_COLOR'})
        self.assertEqual(dependent_channels(ts, 'source', 'ROUGHNESS'), {'BASE_COLOR'})
        self.assertEqual(dependent_channels(ts, 'consumer'), set())

    def test_same_stack_order_decides_cycles(self):
        below = layer('below', [mask('ANCHOR', 'a'), mask('REFERENCE', 'r', reference_anchor='a')])
        self.assertEqual(issues(texture_set(below)), [])
        above = layer('above', [mask('REFERENCE', 'r', reference_anchor='a'), mask('ANCHOR', 'a')])
        self.assertTrue(any('cycle' in text for text in issues(texture_set(above))))
        own = layer('own', [], [channel('ROUGHNESS', [('ANCHOR', 'c')], anchor='c')])
        self.assertTrue(any('cycle' in text for text in issues(texture_set(own))))
        cross = layer('cross', [], [channel('HEIGHT', [('ANCHOR', 'h')]), channel('BASE_COLOR', anchor='h')])
        self.assertEqual(issues(texture_set(cross)), [])

    def test_missing_anchor_is_a_warning_not_an_error(self):
        consumer = layer('consumer', [mask('REFERENCE', 'r', reference_anchor='gone')], [channel('BASE_COLOR', anchor='gone')])
        ts = texture_set(consumer)
        self.assertEqual(issues(ts), [])
        self.assertEqual(warnings(ts), ['consumer: anchor point is missing'] * 2)
        legacy = layer('legacy', [mask('REFERENCE', 'r', reference_layer='nowhere')])
        self.assertEqual(issues(texture_set(legacy)), [])
        self.assertEqual(warnings(texture_set(legacy)), ['legacy: published source layer is missing'])

    def test_legacy_references_stay_item_granular(self):
        source = layer('source', [mask('PAINT', 'p'), mask('REFERENCE', 'back', reference_layer='consumer')],
                       [channel('HEIGHT')])
        consumer = layer('consumer', [mask('REFERENCE', 'r', reference_layer='source', reference_channel='HEIGHT')],
                         [channel('ROUGHNESS')])
        self.assertEqual(issues(texture_set(source, consumer)), [])
        consumer.masks[0].reference_channel = 'MASK'
        self.assertTrue(any('cycle' in text for text in issues(texture_set(source, consumer))))
        consumer.masks[0].reference_mask = 'p'
        self.assertEqual(issues(texture_set(source, consumer)), [])
        folder = layer('folder', [], [channel('HEIGHT')], kind='FOLDER')
        composite = layer('composite', [mask('REFERENCE', 'r', reference_layer='folder', reference_channel='HEIGHT')])
        self.assertTrue(any('folder' in text for text in issues(texture_set(folder, composite))))


if __name__ == '__main__':
    unittest.main()
