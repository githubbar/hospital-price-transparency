import os
import sys
import time
import django

# Setup Django environment
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import connection
from prices.views import resolve_active_dbs
import csv

def get_shoppable_codes():
    shoppable_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reference', 'shoppable_codes.csv')
    codes = set()
    if os.path.exists(shoppable_path):
        with open(shoppable_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                codes.add(row['code'].strip().upper())
    return codes

def find_obscure_code(shoppable_codes):
    """Find a CPT/HCPCS code that is in in_full.sqlite3 but not in the shoppable list."""
    print("Finding a valid obscure code in the full database...")
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'in_full.sqlite3')
    if not os.path.exists(db_path):
        # Try db.sqlite3 as fallback
        db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'db.sqlite3')
        
    import sqlite3
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Query codes from procedures table
    cursor.execute("SELECT code, code_type, id FROM procedures WHERE code_type IN ('CPT', 'HCPCS') LIMIT 500;")
    rows = cursor.fetchall()
    conn.close()
    
    for code, code_type, pid in rows:
        code_clean = code.strip().upper()
        if code_clean and code_clean not in shoppable_codes:
            print(f"Selected obscure code: '{code}' ({code_type}) - ID: {pid}")
            return code, pid
            
    # Default fallback
    return "99213", "847291a273291"

def test_search(query):
    print(f"\n==========================================")
    print(f"TESTING SEARCH QUERY: '{query}'")
    print(f"==========================================")
    
    from prices.views import search
    from django.test import RequestFactory
    
    factory = RequestFactory()
    request = factory.get(f'/?q={query}')
    
    # Enable session support for the request mock
    from django.contrib.sessions.middleware import SessionMiddleware
    middleware = SessionMiddleware(lambda r: None)
    middleware.process_request(request)
    request.session['is_human'] = True
    request.session.save()
    
    start_time = time.time()
    response = search(request)
    elapsed = time.time() - start_time
    
    print(f"Response Status: {response.status_code}")
    print(f"Elapsed Time: {elapsed:.4f} seconds")
    
    # Access context variables in rendered response if available
    context = getattr(response, 'context_data', None)
    procedure_ids = []
    if context:
        print(f"Results Count: {context.get('results_count')}")
        grouped_results = context.get('grouped_results')
        print(f"Results list size: {len(grouped_results)}")
        if grouped_results and grouped_results[0].get('variants'):
            variant = grouped_results[0]['variants'][0]
            procedure_ids = variant.get('procedure_ids', [])
    else:
        # Fallback to parsing HTML content
        content = response.content.decode('utf-8')
        import re as _re
        m = _re.search(r'Showing\s+(\d+)\s+total items found', content, _re.IGNORECASE)
        if m:
            print(f"SUCCESS: Found results in rendered HTML! Count: {m.group(1)}")
            
            # Find data-procedure-ids
            ids_matches = _re.findall(r'data-procedure-ids="([^"]+)"', content)
            if ids_matches:
                first_ids = ids_matches[0]
                procedure_ids = [pid.strip() for pid in first_ids.split(',') if pid.strip()]
                print(f"  First result variant parsed from HTML -> IDs: {procedure_ids}")
        elif "no results found" in content.lower():
            print("SUCCESS: Search page rendered with 'No results found'.")
        else:
            print("WARNING: Rendered content could not be parsed for count.")
            
    return procedure_ids if procedure_ids else None

def test_search_with_filter(query, hospital_id):
    print(f"\n==========================================")
    print(f"TESTING SEARCH QUERY WITH HOSPITAL FILTER: '{query}' ({hospital_id})")
    print(f"==========================================")
    
    from prices.views import search
    from django.test import RequestFactory
    
    factory = RequestFactory()
    request = factory.get(f'/?q={query}&hospital={hospital_id}')
    
    # Enable session support
    from django.contrib.sessions.middleware import SessionMiddleware
    middleware = SessionMiddleware(lambda r: None)
    middleware.process_request(request)
    request.session['is_human'] = True
    request.session.save()
    
    start_time = time.time()
    response = search(request)
    elapsed = time.time() - start_time
    
    print(f"Response Status: {response.status_code}")
    print(f"Elapsed Time: {elapsed:.4f} seconds")
    
    # If the response is a redirect (Flow A), follow it
    if response.status_code == 302:
        redirect_url = response['Location']
        print(f"Redirected to: {redirect_url}")
        request = factory.get(redirect_url)
        middleware.process_request(request)
        request.session['is_human'] = True
        request.session.save()
        
        start_time = time.time()
        response = search(request)
        elapsed = time.time() - start_time
        print(f"After redirect response Status: {response.status_code}")
        print(f"After redirect elapsed Time: {elapsed:.4f} seconds")

    content = response.content.decode('utf-8')
    import re as _re
    m = _re.search(r'Showing\s+(\d+)\s+total items found', content, _re.IGNORECASE)
    if m:
        print(f"SUCCESS: Found results with hospital filter! Count: {m.group(1)}")
    elif "no results found" in content.lower():
        print("SUCCESS: Search page rendered with 'No results found'.")
    else:
        print("WARNING: Rendered content could not be parsed.")

def test_prices_details(procedure_ids_list):
    print(f"\n==========================================")
    print(f"TESTING DYNAMIC PRICES AJAX ENDPOINT for IDs: {procedure_ids_list}")
    print(f"==========================================")
    
    from prices.views import prices_details
    from django.test import RequestFactory
    
    ids_str = ",".join(procedure_ids_list)
    factory = RequestFactory()
    request = factory.get(f'/prices/?ids={ids_str}&state=in')
    
    start_time = time.time()
    response = prices_details(request)
    elapsed = time.time() - start_time
    
    print(f"Response Status: {response.status_code}")
    print(f"Elapsed Time: {elapsed:.4f} seconds")
    
    content = response.content.decode('utf-8')
    if "table-responsive" in content:
        print("SUCCESS: Pricing table snippet successfully generated!")
        rows_count = content.count('<tr>') - 1 # exclude header row
        print(f"  Consolidated payer/plan rows rendered: {rows_count}")
    else:
        print("FAIL: Pricing table snippet not generated.")
        print(content[:500])

def main():
    shoppable = get_shoppable_codes()
    print(f"Loaded {len(shoppable)} shoppable codes.")
    
    obscure_code, obscure_pid = find_obscure_code(shoppable)
    
    print("\n--- TEST 1: Common / Shoppable Search ('mri') ---")
    mri_pids = test_search("mri")
    
    print("\n--- TEST 2: Common / Shoppable Code Search ('70551') ---")
    c70551_pids = test_search("70551")
    
    print(f"\n--- TEST 3: Obscure / Fallback Search ('{obscure_code}') ---")
    obscure_pids = test_search(obscure_code)
    
    # Run AJAX detail tests if pids are retrieved
    if mri_pids:
        test_prices_details(mri_pids)
    if obscure_pids:
        test_prices_details(obscure_pids)

    # Run filter search tests
    from prices.views import _load_hospitals
    hospitals = _load_hospitals()
    target_hosp = next((h for h in hospitals if h['id'] == '4a9b1a40735ce620f718e6aea4d8f5c1'), None)
    if target_hosp:
        print(f"\n--- TEST 4: Filter Search ('mri', hospital='{target_hosp['name']}') ---")
        test_search_with_filter("mri", target_hosp['id'])
    elif hospitals:
        first_hosp_id = hospitals[0]['id']
        print(f"\n--- TEST 4: Filter Search ('mri', hospital='{hospitals[0]['name']}') ---")
        test_search_with_filter("mri", first_hosp_id)

if __name__ == "__main__":
    main()
