import os
import sys
import django
import re
import difflib

# Setup Django environment
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import connection
from prices.synonyms import expand_query_synonyms, inject_synonyms_into_fts

def run_fts_query_with_spellcheck(user_query):
    print(f"\n--- Testing User Query with Spellcheck: '{user_query}' ---")
    
    # 1. Expand synonyms
    processed_query, placeholder_map = expand_query_synonyms(user_query)
    print(f"  After Synonyms Expansion: '{processed_query}'")
    
    # 2. Spellchecking
    corrected_query = processed_query
    words_to_correct = [
        w for w in re.findall(r'\b[a-zA-Z]{3,}\b', processed_query)
        if not (w.startswith('__') and w.endswith('__'))
    ]
    
    EXEMPT_ACRONYMS = {
        'acl', 'mri', 'ct', 'cbc', 'ekg', 'ecg', 'eeg', 'emg', 'iv', 'icu', 
        'er', 'cpt', 'drg', 'apc', 'cdm', 'rc', 'mrc', 'pcp', 'pft', 'std', 
        'uti', 'dna', 'rna', 'papr', 'hmo', 'ppo', 'cns', 'pns', 'egd', 'esd', 
        'gerd', 'ibs', 'ibd', 'copd', 'als', 'ms', 'tb', 'sti', 'hpv', 'hiv', 
        'aids', 'hcpcs', 'aprt', 'drg', 'msdrg', 'apcdrg', 'icd', 'icd9', 'icd10'
    }

    if words_to_correct:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT word FROM unique_words")
                vocab = [row[0] for row in cursor.fetchall()]
                vocab_set = set(vocab)
                
                query_changed = False
                for word in words_to_correct:
                    clean_word = word.lower()
                    if clean_word in EXEMPT_ACRONYMS:
                        continue
                    if clean_word not in vocab_set:
                        # Not found, search closest matches
                        matches = difflib.get_close_matches(clean_word, vocab, n=1, cutoff=0.85)
                        if matches:
                            corrected_word = matches[0]
                            corrected_query = re.sub(r'\b' + re.escape(word) + r'\b', corrected_word, corrected_query, flags=re.IGNORECASE)
                            query_changed = True
                if query_changed:
                    print(f"  [spellcheck] Original: '{processed_query}' -> Corrected: '{corrected_query}'")
        except Exception as e:
            print(f"  Spellcheck error: {e}")
            
    # 3. Build SQLite FTS5 Query
    stopwords = {'a', 'an', 'the', 'of', 'and', 'or', 'for', 'with', 'in', 'on', 'at', 'by', 'to'}
    search_terms = []
    terms_raw = corrected_query.split()
    
    for i, t in enumerate(terms_raw):
        t_lower = t.lower()
        if t_lower in ('or', 'and'):
            if search_terms:
                search_terms.append(t_lower.upper())
            continue
            
        t_clean = re.sub(r'[^\w\*-]', '', t)
        if t_clean and t_lower not in stopwords:
            if '__SYN_' in t_clean:
                search_terms.append(t_clean)
            elif not t_clean.endswith('*'):
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
    sqlite_query = sqlite_query.strip()
    
    # 4. Inject synonyms
    final_query = inject_synonyms_into_fts(sqlite_query, placeholder_map)
    print(f"  Final SQLite FTS5 Query: '{final_query}'")
    
    # 5. Run it
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT procedures.code, procedures.description, count(prices.id) as price_count
                FROM fts_procedures
                JOIN procedures ON procedures.id = fts_procedures.procedure_id
                JOIN prices ON prices.procedure_id = procedures.id
                WHERE fts_procedures MATCH %s
                GROUP BY procedures.id
                ORDER BY price_count DESC
                LIMIT 3
            """, [final_query])
            rows = cursor.fetchall()
            print(f"  Results Count: {len(rows)}")
            for idx, r in enumerate(rows):
                print(f"    {idx+1}. Code: {r[0]} | Desc: {r[1]} | Prices count: {r[2]}")
    except Exception as e:
        print(f"  Error: {e}")

def main():
    # Test standard queries
    run_fts_query_with_spellcheck("allergy shot")
    run_fts_query_with_spellcheck("scratch test")
    
    # Test NEW synonyms from download.csv search logs
    run_fts_query_with_spellcheck("allergy vials shot")
    run_fts_query_with_spellcheck("sleep apnea")
    run_fts_query_with_spellcheck("tubal ligation")
    run_fts_query_with_spellcheck("penile implant")
    run_fts_query_with_spellcheck("ankle tendon repair")
    run_fts_query_with_spellcheck("knee replacement")
    run_fts_query_with_spellcheck("c secion") # with spelling typo
    run_fts_query_with_spellcheck("c section") # new synonym
    run_fts_query_with_spellcheck("cesarean") # new synonym

    # Test clinical exemptions and custom vocabulary additions
    run_fts_query_with_spellcheck("acl") # Should not correct to 'facil'
    run_fts_query_with_spellcheck("chest radiography") # Should not correct to 'chest angiography'
    run_fts_query_with_spellcheck("kne replacement") # Typing typo: should correct to 'knee replacement'

if __name__ == '__main__':
    main()
