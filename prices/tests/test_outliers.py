from django.test import SimpleTestCase

from prices.outliers import MIN_PRICES, price_fences, robust_stats, unusual


class OutlierTests(SimpleTestCase):
    # Chest X-ray: typical prices $40-$400, plus a $0.01 and a $4,315 data error
    XRAY = [40 + 18 * i for i in range(21)] + [0.01, 4315.0]

    def test_flags_far_low_and_far_high(self):
        fences = price_fences(self.XRAY)
        self.assertEqual(unusual(0.01, fences), 'low')
        self.assertEqual(unusual(4315.0, fences), 'high')
        self.assertIsNone(unusual(40, fences))
        self.assertIsNone(unusual(400, fences))

    def test_bimodal_procedure_keeps_both_modes(self):
        # Surgeon's fee around $600 and facility fee around $12,000 under the same code
        values = [550 + i for i in range(15)] + [11500 + 50 * i for i in range(15)]
        fences = price_fences(values)
        self.assertFalse(any(unusual(v, fences) for v in values))

    def test_too_few_prices_to_judge(self):
        self.assertIsNone(price_fences([100] * (MIN_PRICES - 2) + [0.01]))
        self.assertIsNone(unusual(0.01, None))

    def test_robust_stats_leave_out_unusual(self):
        stats = robust_stats(self.XRAY)
        self.assertEqual(stats['min'], 40)
        self.assertEqual(stats['max'], 400)
        self.assertEqual(stats['count'], len(self.XRAY))
        self.assertEqual(stats['unusual'], 2)

    def test_robust_stats_small_sample_unchanged(self):
        self.assertEqual(robust_stats([5, 10, 15]), {'min': 5, 'max': 15, 'avg': 10.0, 'count': 3, 'unusual': 0})
        self.assertIsNone(robust_stats([]))
