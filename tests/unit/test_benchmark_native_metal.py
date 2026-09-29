"""Paired comparisons keep the pair as the unit, and preserve metric direction."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from benchmark_native_metal import summarize


def sample(rate, latency):
    return {'summary': dict(feedback_hz=rate, commands=100, superseded=40,
        callback_ms={'p95': latency, 'p99': latency * 2},
        evaluation_ms={'p95': latency}, receipt_to_draw_ms={'p95': latency, 'p99': latency * 2},
        displayed_state_age_ms={'p95': latency}, ideal_tracking_error={'p95': latency / 100})}


class PairedMetalSummaryTests(unittest.TestCase):
    def test_pair_percentages_are_not_ratios_of_aggregate_medians(self):
        rows = [{'legacy': sample(10, 4), 'native': sample(20, 8)},
                {'legacy': sample(20, 8), 'native': sample(20, 8)}]
        result = summarize(rows)
        rate = result['feedback_hz']
        self.assertEqual(rate['legacy_median'], 15)
        self.assertEqual(rate['native_median'], 20)
        self.assertEqual(rate['paired_percent_changes'], [100, 0])
        self.assertEqual(rate['median_paired_percent_change'], 50)
        self.assertEqual(rate['native_better_pairs'], 1)
        self.assertEqual(result['callback_p95_ms']['native_better_pairs'], 0)
        self.assertIsNone(result['received_commands']['native_better_pairs'])

    def test_regression_is_retained_with_correct_direction(self):
        result = summarize([{'legacy': sample(20, 10), 'native': sample(10, 30)}])
        self.assertEqual(result['feedback_hz']['median_paired_percent_change'], -50)
        self.assertEqual(result['receipt_to_draw_p95_ms']['median_paired_percent_change'], 200)
        self.assertEqual(result['receipt_to_draw_p95_ms']['native_better_pairs'], 0)


if __name__ == '__main__':
    unittest.main()
