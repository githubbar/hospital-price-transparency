from urllib.parse import quote

from django.test import TestCase

from prices import views
from prices.payer_normalization import legacy_combined_raw, plan_key

# Real raw names from in_full.sqlite3 that contain commas; the old
# 'payer|plan,payer|plan' encoding split these into bogus pairs such as ('PPO', None).
COMMA_PAIRS = [
    ('Aetna', 'Commercial HMO, PPO, QPOS, Elect Choice, Managed Choice POS'),
    ('AETNA MCR CHOICE', '8955 AETNA CHOICE MEDICARE REPLACEMENT ASC OUTPATIENT ASIN, ECIN, NRIN 20241001'),
]
PLAIN_PAIRS = [('Aetna', 'Aetna Medicare Advantage')]


class CommaInPlanNameTests(TestCase):
    """Sidebar plans whose raw payer/plan names contain ',' must resolve to exactly those pairs."""

    def setUp(self):
        self.comma_key = plan_key(COMMA_PAIRS)
        self.plain_key = plan_key(PLAIN_PAIRS)
        payers_list = [{
            'parent_name': 'Aetna',
            'category': 'commercial',
            'category_label': 'Commercial',
            'plans': [
                {'key': self.comma_key, 'raw_pairs': [list(p) for p in COMMA_PAIRS],
                 'display': 'Commercial', 'product_line': 'Commercial'},
                {'key': self.plain_key, 'raw_pairs': [list(p) for p in PLAIN_PAIRS],
                 'display': 'Medicare Advantage', 'product_line': 'Medicare Advantage'},
            ],
        }]
        self._saved = (views._payers_index_cache, views._payer_plan_mapping_cache)
        views._payer_plan_mapping_cache = None
        # Serve the index from the synthetic list instead of the JSON file
        group_map = {'Aetna': [self.comma_key, self.plain_key]}
        key_map = {self.comma_key: list(COMMA_PAIRS), self.plain_key: list(PLAIN_PAIRS)}
        views._payers_index_cache = (payers_list, group_map, key_map)

    def tearDown(self):
        views._payers_index_cache, views._payer_plan_mapping_cache = self._saved

    def test_selected_pairs_keep_commas(self):
        self.assertEqual(views._selected_payer_pairs([self.comma_key]), COMMA_PAIRS)

    def test_legacy_combined_value_resolves_by_hash_not_split(self):
        legacy = legacy_combined_raw(COMMA_PAIRS)
        self.assertEqual(views._normalize_payer_selection([legacy]), [self.comma_key])
        self.assertEqual(views._decode_payer_cookie(quote(legacy)), [self.comma_key])

    def test_plan_cookie_round_trip(self):
        cookie = views._encode_payer_cookie([self.comma_key])
        self.assertEqual(cookie, quote(f'p:{self.comma_key}', safe=''))
        self.assertEqual(views._decode_payer_cookie(cookie), [self.comma_key])

    def test_group_cookie_expands_to_plan_keys(self):
        cookie = views._encode_payer_cookie([self.comma_key, self.plain_key])
        self.assertEqual(views._decode_payer_cookie(cookie), [self.comma_key, self.plain_key])

    def test_reverse_mapping_uses_full_plan_name(self):
        mapping = views._get_payer_plan_mapping()
        self.assertEqual(mapping[('aetna', COMMA_PAIRS[0][1].lower())], ('Aetna', 'Commercial'))
        self.assertNotIn(('ppo', ''), mapping)

    def test_search_form_submit_restores_selection(self):
        response = self.client.get('/', {'payer': [self.comma_key]}, follow=True)
        self.assertEqual(response.context['selected_payers'], {self.comma_key})
        self.assertContains(response, f'value="{self.comma_key}"')
