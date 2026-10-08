from django.test import SimpleTestCase

from extract_shoppable import cash_only_prices


class CashOnlyPricesTests(SimpleTestCase):
    # IU Health lists its $49 heart scan as cash-only but runs it through the self-pay discount
    IU_ROW = [
        (12.06, 'Self-Pay', 'Other - Self-Pay', 'outpatient'),
        (12.06, 'Cash', 'Discounted Cash', 'outpatient'),
        (49.0, 'Gross', 'Gross Charge', 'outpatient'),
    ]

    def test_cash_only_line_uses_gross_charge(self):
        prices = cash_only_prices('SCREEN HEART CALCIUM CASH ONLY', self.IU_ROW)
        self.assertEqual(prices, [
            (49.0, 'Cash', 'Discounted Cash', 'outpatient'),
            (49.0, 'Gross', 'Gross Charge', 'outpatient'),
        ])

    def test_self_pay_only_wording(self):
        prices = cash_only_prices('Heart scan - self-pay only', self.IU_ROW)
        self.assertEqual([p[0] for p in prices], [49.0, 49.0])

    def test_other_lines_unchanged(self):
        self.assertEqual(cash_only_prices('CT HEART WO CONTRAST', self.IU_ROW), self.IU_ROW)
        self.assertEqual(cash_only_prices('Cash payment discount', self.IU_ROW), self.IU_ROW)

    def test_cash_only_without_gross_unchanged(self):
        row = [(12.06, 'Cash', 'Discounted Cash', 'outpatient')]
        self.assertEqual(cash_only_prices('CASH ONLY SCAN', row), row)
