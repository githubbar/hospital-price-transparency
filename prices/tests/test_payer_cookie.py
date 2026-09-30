from urllib.parse import quote

from django.test import TestCase

from prices.payer_normalization import legacy_combined_raw
from prices.views import _decode_payer_cookie, _encode_payer_cookie, _load_payers_index


class PayerCookieTests(TestCase):
    """Selected insurers must fit in a cookie (<4 KB) or browsers silently drop it."""

    def setUp(self):
        self.payers_list, self.group_map, self.key_map = _load_payers_index()
        self.big_groups = sorted(self.group_map, key=lambda g: len(self.group_map[g]))[-3:]

    def test_multiple_full_groups_round_trip_under_cookie_limit(self):
        keys = [k for g in self.big_groups for k in self.group_map[g]]
        cookie = _encode_payer_cookie(keys)
        self.assertLess(len(cookie), 4000)
        self.assertEqual(set(_decode_payer_cookie(cookie)), set(keys))

    def test_partial_group_round_trip(self):
        keys = self.group_map[self.big_groups[0]][:3]
        self.assertEqual(set(_decode_payer_cookie(_encode_payer_cookie(keys))), set(keys))

    def test_legacy_raw_cookie_still_decodes(self):
        keys = self.group_map[self.big_groups[0]][:2]
        legacy = [legacy_combined_raw(self.key_map[k]) for k in keys]
        self.assertEqual(_decode_payer_cookie(quote(';'.join(legacy))), keys)

    def test_search_page_restores_selection_from_cookie(self):
        keys = [k for g in self.big_groups[:2] for k in self.group_map[g]]
        self.client.cookies['selected_payers'] = _encode_payer_cookie(keys)
        response = self.client.get('/')
        self.assertEqual(response.context['selected_payers'], set(keys))
