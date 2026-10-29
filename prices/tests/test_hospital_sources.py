import io
from datetime import date
from unittest.mock import patch

from django.test import SimpleTestCase

from extract_hospital_sources import parse_date, read_meta
from prices import views


class ParseDateTests(SimpleTestCase):
    def test_formats_hospitals_use(self):
        self.assertEqual(parse_date('2026-04-01'), '2026-04-01')
        self.assertEqual(parse_date('4/1/2026'), '2026-04-01')
        self.assertEqual(parse_date('2026-04-01T00:00:00Z'), '2026-04-01')

    def test_unreadable_date(self):
        self.assertIsNone(parse_date(''))
        self.assertIsNone(parse_date('Q2 2026'))


class ReadMetaTests(SimpleTestCase):
    def test_reads_cms_metadata_rows(self):
        f = io.StringIO('﻿hospital_name,last_updated_on,version\n"Adams Memorial",2026-08-14,2.0.0\ndescription,code|1\n')
        self.assertEqual(read_meta(f), ('Adams Memorial', '2026-08-14'))

    def test_file_without_metadata_rows(self):
        f = io.StringIO('description,code|1,standard_charge|gross\nMRI,73721,100\n')
        self.assertEqual(read_meta(f), (None, None))


class PriceSourcesTests(SimpleTestCase):
    SOURCES = {
        'a': {'name': 'Zeta Hospital', 'last_updated_on': '2020-01-01', 'downloaded': '2026-10-04',
              'file_url': 'https://z.example/file.csv', 'file_bytes': 52_000_000, 'page_url': None},
        'b': {'name': 'Alpha Hospital', 'last_updated_on': date.today().isoformat(), 'downloaded': '2026-10-04',
              'file_url': 'https://a.example/file.csv', 'file_bytes': None, 'page_url': 'https://a.example/prices'},
        'c': {'name': 'Undated Hospital', 'last_updated_on': None, 'downloaded': '2026-10-04',
              'file_url': 'https://u.example/file.csv', 'file_bytes': 1_000_000, 'page_url': None},
    }

    def test_rows_sorted_with_date_range_and_stale_count(self):
        with patch.object(views, '_get_hospital_sources', return_value=self.SOURCES):
            result = views._price_sources({'a', 'b', 'c', 'missing'})
        self.assertEqual([r['name'] for r in result['rows']], ['Alpha Hospital', 'Undated Hospital', 'Zeta Hospital'])
        self.assertEqual(result['oldest'], date(2020, 1, 1))
        self.assertEqual(result['newest'], date.today())
        self.assertEqual(result['stale_count'], 1)
        self.assertEqual(result['rows'][2]['file_mb'], 52)
        self.assertFalse(result['rows'][1]['stale'])
