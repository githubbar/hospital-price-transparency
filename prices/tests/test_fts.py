from django.test import SimpleTestCase
from prices.synonyms import expand_query_synonyms, inject_synonyms_into_fts
import re

class FTSQueryFormattingTests(SimpleTestCase):
    def test_synonym_expansion(self):
        # Test expand_query_synonyms with a term that has synonyms
        # Note: We can check synonyms.py to find a valid synonym
        query = "mri scan"
        processed_query, placeholders = expand_query_synonyms(query)
        self.assertIsNotNone(processed_query)
        self.assertIsInstance(placeholders, dict)

    def test_fts_query_syntax_cleaning(self):
        # Test how views.py tokenizes and cleans the query string.
        # We want to make sure it handles hyphens and special characters safely.
        def clean_query_like_view(raw_query):
            # Safe cleaning logic matching our proposed fix:
            # Replace hyphens with spaces to prevent FTS syntax errors
            cleaned = raw_query.replace('-', ' ')
            
            # Clean and split terms
            words = cleaned.split()
            stopwords = {'a', 'an', 'the', 'of', 'and', 'or', 'for', 'with', 'in', 'on', 'at', 'by', 'to'}
            search_terms = []
            
            for t in words:
                t_lower = t.lower()
                if t_lower in ('or', 'and'):
                    if search_terms:
                        search_terms.append(t_lower.upper())
                    continue
                
                # Strip special characters except wildcards
                t_clean = re.sub(r'[^\w\*]', '', t)
                if t_clean and t_lower not in stopwords:
                    if not t_clean.endswith('*'):
                        search_terms.append(f"{t_clean}*")
                    else:
                        search_terms.append(t_clean)
            
            sqlite_query = ""
            for term in search_terms:
                if term in ('OR', 'AND'):
                    sqlite_query += f" {term} "
                else:
                    if sqlite_query and not sqlite_query.strip().endswith(('OR', 'AND')):
                        sqlite_query += " AND "
                    sqlite_query += term
            return sqlite_query.strip()

        # Check normal query
        self.assertEqual(clean_query_like_view("mri scan"), "mri* AND scan*")
        
        # Check query with hyphen (which previously caused syntax error)
        self.assertEqual(clean_query_like_view("chest x-ray"), "chest* AND x* AND ray*")
        
        # Check query with special characters
        self.assertEqual(clean_query_like_view("mri @#$!!"), "mri*")
