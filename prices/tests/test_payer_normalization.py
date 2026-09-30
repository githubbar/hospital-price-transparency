from django.test import SimpleTestCase

from prices.payer_normalization import UNMATCHED_PAYER, PayerNormalizer, normalize_text


class PayerNormalizationTests(SimpleTestCase):
    """Golden examples for the alias rules in reference/payers/*.csv."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.normalizer = PayerNormalizer()

    def assertMaps(self, payer, plan, canonical, product_line=None):
        match = self.normalizer.match(payer, plan)
        self.assertEqual(match.canonical.name, canonical, f'{payer!r} | {plan!r}')
        if product_line:
            self.assertEqual(match.product_line, product_line, f'{payer!r} | {plan!r}')

    def test_normalize_text_drops_ids_and_dates(self):
        self.assertEqual(normalize_text('MEDICARE REPLACEMENT [2003]'), 'medicare replacement')
        self.assertEqual(normalize_text('HIP - MDWISE 20120101 (ST. MARY)'), 'hip mdwise st mary')

    def test_blue_cross_variants_collapse_to_anthem(self):
        for payer in ('BCBS', 'Blue Cross Blue Shield', 'Anthem BCBS', 'Elevance Health',
                      'ANTHEM BLUE CROSS AND BLUE SHIELD', 'BLUE CROSS - VA (ANTHEM)', 'BC MCR Advantage PPO'):
            self.assertMaps(payer, '', 'Anthem Blue Cross Blue Shield')

    def test_named_out_of_state_blues_stay_separate(self):
        self.assertMaps('BCBSIL', 'Medicare Managed Care Plan PPO', 'Blue Cross Blue Shield of Illinois', 'Medicare Advantage')
        self.assertMaps('BLUE CROSS - NJ (HORIZON)', 'ANTHEM BCBS', 'Blue Cross Blue Shield (Other States)')

    def test_united_prefix_is_not_enough_for_uhc(self):
        self.assertMaps('UNITED AMERICAN [230]', 'UNITED AMERICAN INSURANCE [23001]', UNMATCHED_PAYER)
        self.assertMaps('UNITED MEDICAL RESOURCES [1301]', '', 'UnitedHealthcare')  # UMR
        self.assertMaps('UNITED CHOICE PLUS CHOICE PLUS', '', 'UnitedHealthcare')

    def test_generic_payer_uses_plan_column(self):
        self.assertMaps('MEDICAID [1092]', 'MDWISE EXCEL HIP [318028]', 'MDwise', 'Medicaid')
        self.assertMaps('MANAGED CARE [2000]', 'UNITED HEALTHCARE-CID', 'UnitedHealthcare')

    def test_medicare_traditional_vs_advantage(self):
        self.assertMaps('MEDICARE [1099]', 'MEDICARE PART A and B [109993]', 'Medicare (Traditional)', 'Medicare (Traditional)')
        self.assertMaps('MEDICARE', '100% MANAGED MEDICARE', 'Medicare Advantage (Unspecified)', 'Medicare Advantage')
        self.assertMaps('HARVARD PILGRIM MEDICARE', 'MEDICARE', 'Medicare Advantage (Unspecified)')
        self.assertMaps('BCBS', 'BCBS Medicare', 'Anthem Blue Cross Blue Shield', 'Medicare Advantage')

    def test_named_payer_with_generic_plan_is_not_unspecified(self):
        self.assertMaps('ClaimDoc', 'Commercial', UNMATCHED_PAYER)

    def test_government_programs_win_over_administrators(self):
        self.assertMaps('United Healthcare VACCN', '', 'Veterans Affairs (VA)', 'VA')
        self.assertMaps('Humana Military', '', 'TRICARE', 'TRICARE')

    def test_product_lines(self):
        self.assertMaps('Anthem IN Pathway', '', 'Anthem Blue Cross Blue Shield', 'Marketplace (ACA)')
        self.assertMaps('Anthem IN Pathways for Aging', '', 'Anthem Blue Cross Blue Shield', 'Medicaid')
        self.assertMaps('MHS Ambetter', '', 'Centene (MHS / Ambetter / Wellcare)', 'Marketplace (ACA)')
        self.assertMaps('Cash', 'Discounted Cash', 'Cash / Self-Pay', 'Self-Pay')

    def test_networks_take_priority_over_carrier_in_name(self):
        self.assertMaps('Sagamore Health Network/Cigna', '', 'Sagamore Health Network')
