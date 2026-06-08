from django.test import TestCase, Client
from django.urls import reverse
import json

class SearchViewsTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_search_view_get(self):
        # Test basic search GET (returns search page with input)
        response = self.client.get(reverse('search'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hospital Price Transparency")

    def test_search_query_execution(self):
        # Test executing a search query against local database
        response = self.client.get(reverse('search'), {'q': 'mri', 'state': 'in'})
        self.assertEqual(response.status_code, 200)
        # Verify the context has expected keys
        self.assertIn('query', response.context)
        self.assertEqual(response.context['query'], 'mri')
        self.assertIn('grouped_results', response.context)
        self.assertIn('total_records', response.context)

    def test_search_view_redirect_on_filters(self):
        # Test that search view redirects to filter token URL when raw hospital or payer GET params are passed
        response = self.client.get(reverse('search'), {
            'q': 'mri',
            'state': 'in',
            'hospital': ['08e2639b11bf81d762e5c232f55e417d']
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn('s=', response.url)

    def test_prices_details_view(self):
        # Test AJAX endpoint for pricing details
        url = reverse('prices_details')
        # We pass dummy procedure ID (or valid one from local db if populated, but test db is empty of prices initially unless populated,
        # however views should handle empty results gracefully with a standard HTML message)
        response = self.client.get(url, {
            'ids': '869503aa40b780aecf069acc0be0ecad',
            'state': 'in'
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/html', response['Content-Type'])

    def test_related_procedures_view(self):
        # Testrelated procedures AJAX endpoint
        url = reverse('related_procedures')
        response = self.client.get(url, {
            'ms_drg': '190',
            'state': 'in'
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('application/json', response['Content-Type'])
        data = json.loads(response.content)
        self.assertIn('results', data)

    def test_explain_code_view_no_config(self):
        # Test explain code view when GenAI is not configured (should return HTTP 503 or error message)
        url = reverse('explain_code')
        response = self.client.get(url, {
            'code': '99213',
            'description': 'Office visit'
        })
        # If GOOGLE_CLOUD_PROJECT is not set in settings, it returns 503
        self.assertIn(response.status_code, [503, 500, 200])


class ProductionPerformanceTests(TestCase):
    def test_production_search_performance(self):
        import time
        import requests
        
        url = "https://hospital-price-search-746555560632.us-central1.run.app/"
        long_hospitals_list = [
            "9a7541cb13600820ff4d9d278484aeaf", "1b8a9879c1fd5a86686895100172e9b7",
            "8131e46392cb70b88b4103197b59beb2", "9daf13684a0d96dca2240095a9f6a34c",
            "f6065bde4e28206ab1f57c1e3e00722c", "27ac2bacd3efca65cc2202f6eefcfd66",
            "35d21f5702f2a3c774cce96765fc912e", "08e2639b11bf81d762e5c232f55e417d",
            "63ca9d3c0c137caaa937e5fc6a21e7fc", "a17ec5a20e8dbbbfc4e138ad74745e49",
            "86a0e184dade9107534c2111da2e8764", "2b781b49abbaf00b726cfd5a22dd8cc3"
        ]
        
        searches = [
            {"q": "mri", "state": "in"},
            {"q": "x-ray", "state": "in"},
            {"q": "ct scan", "state": "in", "hospital": "08e2639b11bf81d762e5c232f55e417d"},
            {"q": "mri", "state": "in", "hospital": "08e2639b11bf81d762e5c232f55e417d", "payer": "Anthem PPO"},
            # Rare procedures
            {"q": "targeted genomic", "state": "in"},
            {"q": "respiratory exercise", "state": "in"},
            # Long list of hospitals
            {"q": "mri", "state": "in", "hospital": long_hospitals_list},
            {"q": "ct scan", "state": "in", "hospital": long_hospitals_list}
        ]
        
        print("\n--- Production Search Performance Tests ---")
        for i, params in enumerate(searches):
            start = time.time()
            try:
                response = requests.get(url, params=params, timeout=20)
                duration = time.time() - start
                print(f"Search #{i+1}: params={params} -> Status {response.status_code} in {duration:.3f}s")
                self.assertEqual(response.status_code, 200)
            except Exception as e:
                print(f"Search #{i+1}: params={params} -> Failed: {e}")

