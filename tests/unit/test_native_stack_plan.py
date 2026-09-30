"""Native layer/folder plan (``native_bridge.stack.positions``) against the host walk the
engine uses without the library (``runtime._positions``)."""
import os
from pathlib import Path
import random
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from open_painter.constants import CHANNELS, DEFAULTS
from open_painter.native_bridge import load_library
from open_painter.native_bridge.stack import positions


def reference(chains):
    found, finals, orders = {}, {}, {}

    def walk(steps, backdrop, order):
        for step in steps:
            grouped = None
            if 'children' in step:
                start = backdrop if step['passthrough'] else ('const', (0.0, 0.0, 0.0, 0.0))
                grouped = walk(step['children'], start, order)
            found[step['key']] = (backdrop, grouped)
            order.append(step['key'])
            backdrop = ('unit', step['key'])
        return backdrop

    for channel, steps in chains.items():
        order = []
        finals[channel] = walk(steps, ('const', (*DEFAULTS[channel][:3], 1.0)), order)
        orders[channel] = order
    return found, finals, orders


def chain(rng, prefix, channel, depth=0):
    steps = []
    for index in range(rng.randint(0, 6)):
        key = ('CHANNEL', f'{prefix}{index}', channel)
        if depth < 3 and rng.random() < .3:
            steps.append({'key': key, 'children': chain(rng, f'{prefix}{index}.', channel, depth + 1),
                          'passthrough': rng.random() < .5})
        else:
            steps.append({'key': key})
    return steps


@unittest.skipUnless(os.environ.get('OP_TEST_NATIVE_LIBRARY'), 'native build not supplied')
class NativeStackPlanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib, _ = load_library(os.environ['OP_TEST_NATIVE_LIBRARY'])

    def check(self, chains):
        found, finals, orders = positions(self.lib, SimpleNamespace(chains=chains))
        expected = reference(chains)
        self.assertEqual(found, expected[0])
        # A channel without a chain composes nothing: its result is its default.
        for channel in CHANNELS:
            want = expected[1].get(channel, ('const', (*DEFAULTS[channel][:3], 1.0)))
            self.assertEqual(finals[channel], want)
            self.assertEqual(orders[channel], expected[2].get(channel, []))

    def test_random_hierarchies(self):
        rng = random.Random(3)
        for _ in range(200):
            self.check({channel: chain(rng, 'L', channel) for channel in CHANNELS if rng.random() < .8})

    def test_empty(self):
        self.check({})
        self.check({channel: [] for channel in CHANNELS})


if __name__ == '__main__':
    unittest.main()
