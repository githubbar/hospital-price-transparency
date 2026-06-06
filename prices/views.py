from django.shortcuts import render
from django.conf import settings
from django.core.paginator import Paginator
from django.http import JsonResponse, HttpResponseRedirect
from django.views.decorators.http import require_GET
from django.core.cache import cache
from django.db import connection
import difflib
from urllib.parse import urlencode as _urlencode
import csv
import hashlib
import json
import os
import re
import time
import uuid
import requests
from prices.synonyms import expand_query_synonyms, inject_synonyms_into_fts


def resolve_active_dbs(cursor, selected_states, force_full=False):
    """
    Given a list of state codes (e.g. ['in', 'ky']), attaches necessary SQLite files
    and returns a list of database prefixes to use in queries.
    Supports force_full=True to query the full database, otherwise caches and queries
    the fast shoppable database in /tmp.
    """
    import shutil
    db_dir = os.environ.get('SQLITE_DB_DIR', settings.BASE_DIR)
    active_dbs = []
    
    # If no states are selected, default to 'in'
    if not selected_states:
        selected_states = ['in']
        
    for state in selected_states:
        state = state.lower().strip()
        
        # Decide suffix and paths
        suffix = "_full" if force_full else "_aggregate"
        db_filename = f"{state}{suffix}.sqlite3"
        source_path = os.path.join(db_dir, db_filename)
        
        if not force_full:
            # We copy aggregate db to local container ephemeral /tmp RAM disk
            db_path = os.path.join('/tmp', db_filename)
            gz_filename = db_filename + ".gz"
            source_gz_path = os.path.join(db_dir, gz_filename)
            
            # Determine which source file is newer/relevant
            use_gz = False
            if os.path.exists(source_gz_path):
                if os.path.exists(source_path):
                    if os.path.getmtime(source_gz_path) >= os.path.getmtime(source_path):
                        use_gz = True
                        source_file = source_gz_path
                    else:
                        use_gz = False
                        source_file = source_path
                else:
                    use_gz = True
                    source_file = source_gz_path
            else:
                use_gz = False
                source_file = source_path

            need_copy = not os.path.exists(db_path)
            if not need_copy and os.path.exists(source_file):
                if os.path.getmtime(source_file) > os.path.getmtime(db_path):
                    need_copy = True
                    print(f"[RAM Cache] Source file {source_file} is newer than cached version. Updating...")
            
            if need_copy:
                # Try compressed source first for 13x speed & no composite GCS FUSE corruption
                if use_gz and os.path.exists(source_gz_path):
                    try:
                        os.makedirs('/tmp', exist_ok=True)
                        tmp_gz_path = os.path.join('/tmp', gz_filename)
                        print(f"[RAM Cache] Copying compressed {gz_filename} to local RAM disk (/tmp)...")
                        shutil.copy2(source_gz_path, tmp_gz_path)
                        
                        print(f"[RAM Cache] Decompressing {gz_filename} to {db_filename}...")
                        import gzip
                        with gzip.open(tmp_gz_path, 'rb') as f_in:
                            with open(db_path, 'wb') as f_out:
                                shutil.copyfileobj(f_in, f_out)
                                
                        # Delete temp archive to free RAM
                        os.remove(tmp_gz_path)
                        print(f"[RAM Cache] Decompression complete!")
                        need_copy = False
                    except Exception as e:
                        print(f"[RAM Cache] Failed to copy/decompress {gz_filename}: {e}")
                        # Clean up failed decompression attempt if any
                        if os.path.exists(db_path):
                            try: os.remove(db_path)
                            except: pass
                            
                # Fallback to direct copy of uncompressed file if present
                if (not os.path.exists(db_path) or need_copy) and os.path.exists(source_path):
                    try:
                        os.makedirs('/tmp', exist_ok=True)
                        print(f"[RAM Cache] Copying uncompressed {db_filename} to local RAM disk (/tmp)...")
                        shutil.copy2(source_path, db_path)
                    except Exception as e:
                        print(f"[RAM Cache] Failed to copy {db_filename} to /tmp: {e}")
                        if not os.path.exists(db_path):
                            db_path = source_path
            else:
                db_path = db_path
            
            if not os.path.exists(db_path):
                db_path = source_path
        else:
            db_path = source_path
        
        if os.path.exists(db_path):
            try:
                db_alias = f"{state}_db"
                cursor.execute("PRAGMA database_list")
                attached = [row[1] for row in cursor.fetchall()]
                
                # Detach any existing mapping to prevent conflicts when switching aggregate/full
                if db_alias in attached:
                    cursor.execute(f"DETACH DATABASE `{db_alias}`")
                
                attach_sql = f"ATTACH DATABASE 'file:{db_path}?mode=ro' AS `{db_alias}`"
                cursor.execute(attach_sql)
                print(f"[SQLite Attach] Attached {db_filename} as `{db_alias}`")
                
                if not force_full:
                    full_db_filename = f"{state}_full.sqlite3"
                    full_db_path = os.path.join(db_dir, full_db_filename)
                    if os.path.exists(full_db_path):
                        full_db_alias = f"{state}_full_db"
                        if full_db_alias in attached:
                            cursor.execute(f"DETACH DATABASE `{full_db_alias}`")
                        full_attach_sql = f"ATTACH DATABASE 'file:{full_db_path}?mode=ro' AS `{full_db_alias}`"
                        cursor.execute(full_attach_sql)
                        print(f"[SQLite Attach] Attached {full_db_filename} as `{full_db_alias}`")
                
                active_dbs.append(state)
            except Exception as e:
                print(f"[SQLite Attach] Error attaching {db_filename}: {e}")
        else:
            # Fallback for 'in' (Indiana): if in_aggregate.sqlite3/in_full.sqlite3 doesn't exist, map it to 'main'
            if state == 'in':
                active_dbs.append('main')
            else:
                print(f"[SQLite Attach] Database file not found: {db_path}")
            
    if not active_dbs:
        active_dbs.append('main')
        
    return active_dbs


def _load_hospitals():
    """Load Indiana hospital list from reference file with computed ES-compatible IDs."""
    cache_key = 'indiana_hospitals_list_v2'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        ref_path = os.path.join(settings.BASE_DIR, 'reference', 'indiana_hospitals.json')
        with open(ref_path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
            
        # Load mapping to translate reference IDs to database IDs
        mapping = {}
        mapping_path = os.path.join(settings.BASE_DIR, 'prices', 'hospital_mapping.json')
        if os.path.exists(mapping_path):
            try:
                with open(mapping_path, 'r', encoding='utf-8') as mf:
                    mapping = json.load(mf)
            except Exception as e:
                print(f"Error loading hospital mapping: {e}")

        result = []
        for h in raw:
            name = h.get('name', '').strip()
            if not name:
                continue
            # Apply same cleaning as load_to_es.py clean_hospital_name()
            cleaned = re.sub(r'^\d+[\W_]+', '', name)
            cleaned = cleaned.replace('_', ' ').replace('-', ' ').replace('.', ' ')
            cleaned = re.sub(r'\s+', ' ', cleaned).strip().title()
            h_id = hashlib.md5(cleaned.lower().encode('utf-8')).hexdigest()
            
            # Map reference ID to database ID if mapping exists
            if h_id in mapping:
                h_id = mapping[h_id]['db_id']
                
            result.append({
                'id': h_id,
                'name': cleaned,
                'city': h.get('city', '').title(),
                'zip': h.get('zip', ''),
                'lat': float(h.get('lat', 0) or 0),
                'lng': float(h.get('long', 0) or 0),
            })
        result.sort(key=lambda h: (h['city'], h['name']))
        cache.set(cache_key, result, 86400)  # cache 24 h
        return result
    except Exception as e:
        print(f'Error loading hospital registry: {e}')
        return []



def _save_filter_token(hospital_ids):
    """Store selected hospital IDs in cache under a short token. Returns the token."""
    token = uuid.uuid4().hex[:16]
    cache.set(f'filt:{token}', hospital_ids, 30 * 86400)  # 30-day expiry
    return token


def _load_filter_token(token):
    """Retrieve hospital IDs for a token. Returns None if expired or invalid."""
    if not token or len(token) != 16:
        return None
    return cache.get(f'filt:{token}')

FIELD_TOOLTIPS = {
    'description': "Description of each item or service provided by the hospital that corresponds to the standard charge the hospital has established.",
    'code_1': "Any code(s) used by the hospital for purposes of billing or accounting for the item or service.",
    'setting': "Indicates whether the item or service is provided in connection with an inpatient admission or an outpatient department visit.",
    'payer_plan': "The name of the third party payer and specific plan associated with the standard charge.",
    'standard_charge_negotiated_dollar': "Payer-specific negotiated charge (encoded as a dollar amount) that a hospital has negotiated with a third party payer for the corresponding item or service.",
    'standard_charge_gross': "Gross charge is the charge for an individual item or service that is reflected on a hospital's chargemaster, absent any discounts.",
    'standard_charge_discounted_cash': "Discounted cash price is defined as the charge that applies to an individual who pays cash (or cash equivalent) for a hospital item or service."
}

def get_hospital_name():
    # Attempt to read from the specific CSV file in the project root
    # In a real app, this should be in the database or settings
    try:
        csv_path = os.path.join(settings.BASE_DIR, '351720796_indiana-university-health-bloomington-inc_snippet.csv')
        if os.path.exists(csv_path):
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                # Skip header
                next(reader)
                # Read second line which contains the hospital name
                row = next(reader)
                if row:
                    return row[0] # First column is hospital_name
    except Exception:
        pass
    return "Hospital Price Transparency"

def generate_distribution_svg(prices):
    if not prices or len(prices) < 2:
        return None

    try:
        min_p = min(prices)
        max_p = max(prices)
        avg_p = sum(prices) / len(prices)
        
        if min_p == max_p:
            return None

        # Create histogram bins
        num_bins = 15
        val_range = max_p - min_p
        bin_width = val_range / num_bins
        counts = [0] * num_bins
        
        for p in prices:
            bin_idx = int((p - min_p) / bin_width)
            if bin_idx >= num_bins:
                bin_idx = num_bins - 1
            counts[bin_idx] += 1
            
        max_count = max(counts)
        if max_count == 0:
            return None
            
        # Generage SVG points for a smooth-ish curve (polygon)
        # We'll map x from 0 to 100, y from 30 (bottom) to 0 (top)
        points = []
        points.append("0,30") # Start bottom-left
        
        for i, count in enumerate(counts):
            x = (i + 0.5) * (100 / num_bins)
            # Normalize height: max_count corresponds to full height (say 25px out of 30px to leave margin)
            height = (count / max_count) * 25
            y = 30 - height
            points.append(f"{x:.1f},{y:.1f}")
            
        points.append("100,30") # End bottom-right
        
        points_str = " ".join(points)
        
        # Calculate X position for the average line
        avg_x = ((avg_p - min_p) / val_range) * 100
        # Clamp to 0-100 just in case floating point weirdness
        avg_x = max(0, min(100, avg_x))
        
        svg = f'''<svg viewBox="0 0 100 30" width="100" height="30" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
            <polygon points="{points_str}" fill="rgba(13, 110, 253, 0.2)" stroke="#0d6efd" stroke-width="1" style="opacity: 0.6;"/>
            <line x1="{avg_x}" y1="0" x2="{avg_x}" y2="30" stroke="red" stroke-width="1.5" stroke-dasharray="2,1" />
        </svg>'''
        return svg
    except Exception:
        return None

def verify_turnstile(token, ip=None):
    print("verify_turnstile: Test message")

    # Skip verification in debug mode
    if settings.DEBUG:
        print("verify_turnstile: DEBUG mode, skipping verification")
        return True

    secret = getattr(settings, 'TURNSTILE_SECRET_KEY', None)
    if not secret:
        print("verify_turnstile: No secret key configured")
        return True
    
    # Check for placeholder/testing key logic
    if secret.startswith('1x000'):
        # Testing mode always passes
        return True

    try:
        data = {'secret': secret, 'response': token}
        if ip:
            data['remoteip'] = ip

        print(f"verify_turnstile: Verifying token with secret ending in ...{secret[-4:]}")
        r = requests.post('https://challenges.cloudflare.com/turnstile/v0/siteverify', data=data, timeout=5)
        result = r.json()
        print(f"verify_turnstile: Result -> {result}")
        return result.get('success', False)
    except Exception as e:
        print(f"verify_turnstile: Exception -> {e}")
        return True

def faq(request):
    return render(request, 'prices/faq.html')


def search(request):
    query = request.GET.get('q', '')
    try:
        page_number = int(request.GET.get('page', 1))
    except (ValueError, TypeError):
        page_number = 1

    # Turnstile Verification
    error_message = None
    remote_addr = request.META.get('REMOTE_ADDR', '')
    is_local = remote_addr in ('127.0.0.1', '::1', 'localhost')
    if query:
        # Skip verification in debug mode or on localhost
        if settings.DEBUG or is_local:
            pass
        # If already verified in session, skip
        elif request.session.get('is_human', False):
            pass
        else:
            token = request.GET.get('cf-turnstile-response')
            if token:
                # Handle proxy headers for correct IP
                remote_ip = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', '')).split(',')[0]
                
                if verify_turnstile(token, remote_ip):
                    request.session['is_human'] = True
                    request.session.modified = True 
                else:
                    error_message = "Security check failed. Please refresh and try again."
            else:
                # If page 1 (fresh search), require token
                # If page > 1 (pagination), require session
                # In both cases, if we are here (no session, no token), we block.
                error_message = "Security check required."

    items_per_page = 20
    grouped_results = []
    results_count = 0
    elapsed_time = 0
    page_obj = None
    total_records = 0

    selected_states = request.GET.getlist('state')
    # If no state selected, default to 'in' (Indiana)
    if not selected_states:
        selected_states = ['in']
    selected_states_set = set(selected_states)

    # Get total records count across active/selected databases (best effort)
    cache_key = f"total_records:{','.join(sorted(selected_states))}"
    total_records = cache.get(cache_key)
    
    if total_records is None:
        total_records = 0
        db_dir = os.environ.get('SQLITE_DB_DIR', '')
        
        # Statically define counts for production state databases in GCS to avoid 14.4M row scans
        STATIC_STATE_COUNTS = {
            'in': 9502, # Indiana database total unique grouped procedures
        }
        
        if db_dir == "/mnt/gcs" and all(s in STATIC_STATE_COUNTS for s in selected_states):
            total_records = sum(STATIC_STATE_COUNTS[s] for s in selected_states)
            cache.set(cache_key, total_records, 86400 * 30)  # Cache for 30 days
        else:
            try:
                with connection.cursor() as cursor:
                    temp_active_dbs = resolve_active_dbs(cursor, selected_states)
                    for db in temp_active_dbs:
                        db_prefix = f"{db}_db." if db != "main" else ""
                        try:
                            cursor.execute(f"SELECT COUNT(*) FROM {db_prefix}procedures")
                            total_records += cursor.fetchone()[0]
                        except Exception:
                            pass
                cache.set(cache_key, total_records, 86400 * 30)  # Cache for 30 days
            except Exception as e:
                print(f"Error fetching total records: {e}")

    # Fetch unique Payers and Plans for the dropdown (pre-computed statically to avoid 14.4M row scans in serverless)
    try:
        json_path = os.path.join(settings.BASE_DIR, 'prices', 'static_payers_and_plans.json')
        with open(json_path, 'r', encoding='utf-8') as f:
            payers_list = json.load(f)
    except Exception as e:
        print(f"Error loading static payers and plans: {e}")
        payers_list = []

    selected_payers = request.GET.getlist('payer')  # multi-select list; always kept in URL
    selected_payers_set = set(selected_payers)
    
    # Parse selected payer-plans into specific raw tuples (payer_raw, plan_raw)
    selected_payer_plans = []
    for item in selected_payers:
        for sub_item in item.split(','):
            if '|' in sub_item:
                parts = sub_item.split('|', 1)
                selected_payer_plans.append((parts[0], parts[1]))
            else:
                selected_payer_plans.append((sub_item, None))
    hospitals_list = _load_hospitals()
    hospital_cities = sorted(set(h['city'] for h in hospitals_list if h['city']))

    # ── Hospital filter via shareable token / cookies ─────────────────────────
    # Flow A – ?hospital=<md5>&... submitted (fresh form submit, e.g. JS disabled): create token, redirect, set cookie.
    # Flow B – ?s=<token> present: restore hospital IDs from cache, sync cookie.
    # Flow C – fall back to cookie: read from 'selected_hospitals' cookie.
    filter_token = request.GET.get('s', '')
    token_expired = False
    raw_hospital_ids = request.GET.getlist('hospital')

    if raw_hospital_ids:
        # Flow A: pack hospitals into a token and redirect to clean URL, also writing the cookie
        token = _save_filter_token(raw_hospital_ids)
        redirect_params = [('q', query), ('s', token)] + [('payer', p) for p in selected_payers] + [('state', s) for s in selected_states]
        response = HttpResponseRedirect(f"{request.path}?{_urlencode(redirect_params)}")
        response.set_cookie('selected_hospitals', ','.join(raw_hospital_ids), max_age=30*86400, path='/', samesite='Lax')
        return response
    elif filter_token:
        # Flow B: restore from cache
        cached_ids = _load_filter_token(filter_token)
        if cached_ids is not None:
            selected_hospitals = cached_ids
        else:
            token_expired = True
            selected_hospitals = []
    else:
        # Flow C: restore from cookie
        selected_hospitals = []
        cookie_val = request.COOKIES.get('selected_hospitals', '')
        if cookie_val:
            try:
                from urllib.parse import unquote
                decoded = unquote(cookie_val)
                if decoded:
                    selected_hospitals = [h.strip() for h in decoded.split(',') if h.strip()]
            except Exception:
                pass

    selected_hospitals_set = set(selected_hospitals)  # MD5 IDs used for filtering

    # Build base query string for pagination (preserves all filters except page)
    _params = request.GET.copy()
    _params.pop('page', None)
    base_query_string = _params.urlencode()

    if query and not error_message:
        start_time = time.time()
        
        # --- Synonym Query Expansion ---
        processed_query, placeholder_map = expand_query_synonyms(query)
        
        # --- Spelling Auto-Correction (Typo Tolerance) ---
        corrected_query = processed_query
        # Exclude common clinical acronyms and standard medical terms from autocorrect
        EXEMPT_ACRONYMS = {
            'acl', 'mri', 'ct', 'cbc', 'ekg', 'ecg', 'eeg', 'emg', 'iv', 'icu', 
            'er', 'cpt', 'drg', 'apc', 'cdm', 'rc', 'mrc', 'pcp', 'pft', 'std', 
            'uti', 'dna', 'rna', 'papr', 'hmo', 'ppo', 'cns', 'pns', 'egd', 'esd', 
            'gerd', 'ibs', 'ibd', 'copd', 'als', 'ms', 'tb', 'sti', 'hpv', 'hiv', 
            'aids', 'hcpcs', 'aprt', 'drg', 'msdrg', 'apcdrg', 'icd', 'icd9', 'icd10'
        }
        
        # Avoid checking placeholders (they start/end with __) and exempt acronyms
        words_to_correct = [
            w for w in re.findall(r'\b[a-zA-Z]{3,}\b', processed_query)
            if not (w.startswith('__') and w.endswith('__')) and w.lower() not in EXEMPT_ACRONYMS
        ]

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
                        print(f"[spellcheck] Original: '{query}' -> Corrected: '{corrected_query}'")
            except Exception as e:
                print(f"Spellcheck error: {e}")

        # Smarter query formatting for SQLite FTS5:
        # 1. Clean terms, ignore basic common English stopwords to avoid search failures
        stopwords = {'a', 'an', 'the', 'of', 'and', 'or', 'for', 'with', 'in', 'on', 'at', 'by', 'to'}
        
        search_terms = []
        terms_raw = corrected_query.split()
        
        for i, t in enumerate(terms_raw):
            t_lower = t.lower()
            # Natively support 'OR' / 'AND' logical operators if the user typed them
            if t_lower in ('or', 'and'):
                if search_terms: # Only add if there is a preceding term
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
                    
        # Reassemble with spacing, but ensure we don't end up with consecutive operators
        sqlite_query = ""
        for term in search_terms:
            if term in ('OR', 'AND'):
                sqlite_query += f" {term} "
            else:
                if sqlite_query and not sqlite_query.strip().endswith(('OR', 'AND')):
                    sqlite_query += " AND "
                sqlite_query += term
        sqlite_query = sqlite_query.strip()

        # Re-inject synonyms into the SQLite MATCH query
        sqlite_query = inject_synonyms_into_fts(sqlite_query, placeholder_map)

        try:
            with connection.cursor() as cursor:
                # 1. Resolve active databases by attaching them (Try shoppable first, fallback to full if filters are active)
                has_filters = bool(selected_hospitals or selected_payers)
                active_dbs = resolve_active_dbs(cursor, selected_states, force_full=has_filters)
                is_currently_full = has_filters
                
                # 2. Count total matching records across all active databases
                def _build_count_query(dbs, is_full=False):
                    parts = []
                    params = []
                    for db in dbs:
                        db_prefix = f"{db}_db." if db != "main" else ""
                        price_db_prefix = f"{db}_db." if (db == "main" or is_full) else f"{db}_full_db."
                        db_where_conditions = ["fts_procedures MATCH %s"]
                        db_params = [sqlite_query]
                        
                        if selected_hospitals:
                            h_placeholders = ",".join(["%s"] * len(selected_hospitals))
                            db_where_conditions.append(
                                f"EXISTS (SELECT 1 FROM {price_db_prefix}prices p JOIN {price_db_prefix}hospitals h ON p.hospital_id = h.id WHERE p.procedure_id = {db_prefix}fts_procedures.procedure_id AND h.hospital_hash IN ({h_placeholders}))"
                            )
                            db_params.extend(selected_hospitals)
                            
                        if selected_payer_plans:
                            sub_conditions = []
                            for p_raw, pl_raw in selected_payer_plans:
                                if pl_raw is not None:
                                    sub_conditions.append(f"(pay.name = %s AND pl.name = %s)")
                                    db_params.extend([p_raw, pl_raw])
                                else:
                                    sub_conditions.append(f"(pay.name = %s)")
                                    db_params.extend([p_raw])
                            db_where_conditions.append(
                                f"EXISTS (SELECT 1 FROM {price_db_prefix}prices p JOIN {price_db_prefix}plans pl ON p.plan_id = pl.id JOIN {price_db_prefix}payers pay ON pl.payer_id = pay.id WHERE p.procedure_id = {db_prefix}fts_procedures.procedure_id AND ({' OR '.join(sub_conditions)}))"
                            )
                            
                        db_where_clause = " WHERE " + " AND ".join(db_where_conditions)
                        parts.append(f"SELECT DISTINCT procedure_id FROM {db_prefix}fts_procedures {db_where_clause}")
                        params.extend(db_params)
                    
                    sql = f"SELECT COUNT(*) FROM ({' UNION '.join(parts)})"
                    return sql, params

                combined_count_sql, count_params = _build_count_query(active_dbs, is_full=is_currently_full)
                cursor.execute(combined_count_sql, count_params)
                total_hits = cursor.fetchone()[0]

                # Fallback: If no hits in shoppable database, switch to the full database
                if total_hits == 0:
                    print(f"[Hybrid Fallback] 0 hits found in shoppable database for '{query}'. Re-querying full database...")
                    active_dbs = resolve_active_dbs(cursor, selected_states, force_full=True)
                    is_currently_full = True
                    combined_count_sql, count_params = _build_count_query(active_dbs, is_full=True)
                    cursor.execute(combined_count_sql, count_params)
                    total_hits = cursor.fetchone()[0]

                results_count = total_hits

                # 3. Query matching procedures (paginated) ranked by FTS5 BM25 relevance score
                results_parts = []
                results_params = []
                for db in active_dbs:
                    db_prefix = f"{db}_db." if db != "main" else ""
                    price_db_prefix = f"{db}_db." if (db == "main" or is_currently_full) else f"{db}_full_db."
                    
                    db_where_conditions = ["fts_procedures MATCH %s"]
                    db_params = [sqlite_query]
                    
                    if selected_hospitals:
                        h_placeholders = ",".join(["%s"] * len(selected_hospitals))
                        db_where_conditions.append(
                            f"EXISTS (SELECT 1 FROM {price_db_prefix}prices p JOIN {price_db_prefix}hospitals h ON p.hospital_id = h.id WHERE p.procedure_id = {db_prefix}fts_procedures.procedure_id AND h.hospital_hash IN ({h_placeholders}))"
                        )
                        db_params.extend(selected_hospitals)
                        
                    if selected_payer_plans:
                        sub_conditions = []
                        for p_raw, pl_raw in selected_payer_plans:
                            if pl_raw is not None:
                                sub_conditions.append(f"(pay.name = %s AND pl.name = %s)")
                                db_params.extend([p_raw, pl_raw])
                            else:
                                sub_conditions.append(f"(pay.name = %s)")
                                db_params.extend([p_raw])
                        db_where_conditions.append(
                            f"EXISTS (SELECT 1 FROM {price_db_prefix}prices p JOIN {price_db_prefix}plans pl ON p.plan_id = pl.id JOIN {price_db_prefix}payers pay ON pl.payer_id = pay.id WHERE p.procedure_id = {db_prefix}fts_procedures.procedure_id AND ({' OR '.join(sub_conditions)}))"
                        )
                        
                    db_where_clause = " WHERE " + " AND ".join(db_where_conditions)
                    
                    results_parts.append(f"""
                        SELECT {db_prefix}procedures.id, {db_prefix}procedures.description, {db_prefix}procedures.code, {db_prefix}procedures.code_type,
                               {db_prefix}procedures.ms_drg, {db_prefix}procedures.apr_drg, {db_prefix}procedures.rc, {db_prefix}procedures.apc, {db_prefix}procedures.ndc, {db_prefix}procedures.cdm,
                               {db_prefix}procedures.stats_min, {db_prefix}procedures.stats_max, {db_prefix}procedures.stats_avg, {db_prefix}procedures.stats_count,
                               {db_prefix}procedures.is_standard_group,
                               bm25(fts_procedures) as rank,
                               {db_prefix}procedures.all_codes,
                               '{db}' as source_db
                        FROM {db_prefix}fts_procedures
                        JOIN {db_prefix}procedures ON {db_prefix}procedures.id = {db_prefix}fts_procedures.procedure_id
                        {db_where_clause}
                    """)
                    results_params.extend(db_params)
                
                combined_results_sql = f"""
                    SELECT * FROM (
                        {' UNION ALL '.join(results_parts)}
                    )
                    ORDER BY rank ASC
                """
                cursor.execute(combined_results_sql, results_params)
                proc_rows = cursor.fetchall()
                
                settings_by_proc = {}
                hits = []
                if proc_rows:
                    proc_ids = [row[0] for row in proc_rows]
                    price_placeholders = ",".join(["%s"] * len(proc_ids))
                    
                    # Batch fetch all unique settings for each procedure ID from the prices table
                    setting_queries = []
                    setting_params = []
                    for db in active_dbs:
                        p_prefix = f"{db}_db." if (db == "main" or is_currently_full) else f"{db}_full_db."
                        setting_queries.append(f"""
                            SELECT DISTINCT p.procedure_id, s.name 
                            FROM {p_prefix}prices p
                            JOIN {p_prefix}settings s ON p.setting_id = s.id
                            WHERE p.procedure_id IN ({price_placeholders})
                        """)
                        setting_params.extend(proc_ids)
                        
                    cursor.execute(" UNION ALL ".join(setting_queries), setting_params)
                    settings_fetched = cursor.fetchall()

                    settings_by_proc = {}
                    for s_row in settings_fetched:
                        pid, setting = s_row
                        if setting:
                            setting_clean = setting.strip().lower()
                            if setting_clean and 'unknown' not in setting_clean:
                                settings_by_proc.setdefault(pid, set()).add(setting_clean)


                    # Construct hit structures
                    for row in proc_rows:
                        pid = row[0]
                        source_db = row[17]
                        all_codes_raw = row[16]
                        codes = []
                        if all_codes_raw:
                            try:
                                codes = json.loads(all_codes_raw)
                            except Exception:
                                pass
                        
                        hits.append({
                            '_source': {
                                'id': pid,
                                'description': row[1],
                                'code': row[2],
                                'code_type': row[3],
                                'ms_drg': row[4],
                                'apr_drg': row[5],
                                'rc': row[6],
                                'apc': row[7],
                                'ndc': row[8],
                                'cdm': row[9],
                                'stats': {
                                    'min': row[10],
                                    'max': row[11],
                                    'avg': row[12],
                                    'count': row[13]
                                },
                                'is_standard_group': bool(row[14]),
                                'source_db': source_db,
                                'prices': [],
                                'codes': codes
                            }
                        })
            
            # Grouping Dictionary: Code -> List of Variants (same code, different desc → collapsed)
            from collections import OrderedDict, Counter
            import re as _re

            def _normalize_desc(d):
                """Lowercase, strip punctuation/extra whitespace for fuzzy grouping."""
                return _re.sub(r'\s+', ' ', _re.sub(r'[^\w\s]', '', d.lower())).strip()

            matches_by_code = OrderedDict()

            for hit in hits:
                source = hit['_source']
                
                # Extract basic info
                code = source.get('code', '')
                code_type = source.get('code_type', '')
                desc = source.get('description', '') or "Unknown Description"
                rev_code = source.get('rev_code', '')
                
                stats = source.get('stats', {})
                
                # Check if there are actually other procedures sharing the same group key
                ms_drg_val = (source.get('ms_drg') or '').strip()
                apr_drg_val = (source.get('apr_drg') or '').strip()
                apc_val = (source.get('apc') or '').strip()
                source_db = source.get('source_db', 'main')
                db_prefix = f"{source_db}_db." if source_db != "main" else ""

                with connection.cursor() as sub_cursor:
                    if ms_drg_val:
                        sub_cursor.execute(f"SELECT 1 FROM {db_prefix}procedures WHERE ms_drg = %s AND code != %s LIMIT 1", [ms_drg_val, code])
                        if not sub_cursor.fetchone():
                            ms_drg_val = ""

                    if apr_drg_val:
                        sub_cursor.execute(f"SELECT 1 FROM {db_prefix}procedures WHERE apr_drg = %s AND code != %s LIMIT 1", [apr_drg_val, code])
                        if not sub_cursor.fetchone():
                            apr_drg_val = ""

                    if apc_val:
                        sub_cursor.execute(f"SELECT 1 FROM {db_prefix}procedures WHERE apc = %s AND code != %s LIMIT 1", [apc_val, code])
                        if not sub_cursor.fetchone():
                            apc_val = ""

                variant_data = {
                    'code': (code or '').strip(),
                    'code_type': (code_type or '').strip(),
                    'common_setting': 'Unknown', 
                    'items': [],
                    'stats': stats,
                    'rev_code': (source.get('rc') or source.get('rev_code') or '').strip(),
                    'ms_drg':  ms_drg_val,
                    'apr_drg': apr_drg_val,
                    'apc':     apc_val,
                    'procedure_ids': [source.get('id')],
                    'descriptions': [desc],
                }
                
                # Group by code (uppercased); fall back to normalized description if no code
                group_key = code.strip().upper() if code and code.strip() else _normalize_desc(desc)

                if group_key not in matches_by_code:
                    matches_by_code[group_key] = {
                        'description': desc,
                        '_raw_descriptions': [desc],
                        'variants': []
                    }
                else:
                    matches_by_code[group_key]['_raw_descriptions'].append(desc)
                matches_by_code[group_key]['variants'].append(variant_data)

            # Set canonical title-cased description for each group (most common wins)
            for group in matches_by_code.values():
                raw = group.pop('_raw_descriptions')
                canonical = Counter(raw).most_common(1)[0][0]
                group['description'] = canonical.title()

            grouped_results = list(matches_by_code.values())

            # --- Merge overflow variant-parts (same code split across multiple ES docs/types) ---
            # Merge variants sharing the same code. Treats CPT, HCPCS, and CDM as equivalent.
            for group in grouped_results:
                if len(group['variants']) <= 1:
                    continue
                merged_map = {}
                order = []
                for variant in group['variants']:
                    v_code = variant['code'].strip().upper()
                    # If code is present, merge by code. If empty, keep separate using unique procedure ID.
                    key = v_code if v_code else variant['procedure_ids'][0]
                    
                    if key not in merged_map:
                        merged_map[key] = variant
                        order.append(key)
                        # Determine canonical code type for CPT/HCPCS/CDM
                        orig_type = (variant.get('code_type') or '').strip().upper()
                        if v_code and orig_type in ('CPT', 'HCPCS', 'CDM', 'LOCAL', 'CPT/HCPCS', ''):
                            # HCPCS Level II code starts with a letter A-V
                            if v_code[0].isalpha() and v_code[0].upper() in 'ABCDEFGHJKLMNOPQRSTUV':
                                merged_map[key]['code_type'] = 'HCPCS'
                            else:
                                merged_map[key]['code_type'] = 'CPT/HCPCS'
                    else:
                        merged_map[key]['procedure_ids'].extend(variant['procedure_ids'])
                        # Consolidate unique descriptions
                        for d in variant.get('descriptions', []):
                            if d not in merged_map[key]['descriptions']:
                                merged_map[key]['descriptions'].append(d)

                if len(order) < len(group['variants']):
                    # At least one merge happened — recalculate per-variant stats from precomputed stats
                    for key in order:
                        v = merged_map[key]
                        v_code = v['code'].strip().upper()
                        # Find all matching variants from the original group list
                        matching_variants = [
                            orig for orig in group['variants']
                            if (v_code and orig['code'].strip().upper() == v_code) or (not v_code and orig['procedure_ids'][0] == key)
                        ]
                        if len(matching_variants) > 1:
                            mins = [mv['stats']['min'] for mv in matching_variants if mv['stats'].get('min') is not None]
                            maxs = [mv['stats']['max'] for mv in matching_variants if mv['stats'].get('max') is not None]
                            counts = [mv['stats']['count'] for mv in matching_variants if mv['stats'].get('count') is not None]
                            weighted_avgs = [mv['stats']['avg'] * mv['stats']['count'] for mv in matching_variants if mv['stats'].get('avg') is not None and mv['stats'].get('count') is not None]
                            
                            total_count = sum(counts)
                            v['stats'] = {
                                'min': min(mins) if mins else None,
                                'max': max(maxs) if maxs else None,
                                'avg': round(sum(weighted_avgs) / total_count, 2) if total_count > 0 and weighted_avgs else None,
                                'count': total_count,
                                'distribution_svg': None
                            }
                # Assign merged/deduplicated variants list back to group
                group['variants'] = [merged_map[k] for k in order]
            # --- Post-Processing for Groups ---
            for group in grouped_results:
                
                # 1. Calculate Group-Level Aggregate Stats (Min/Max across all variants)
                g_min = float('inf')
                g_max = float('-inf')
                
                for variant in group['variants']:
                    # Resolve common_setting from all procedure_ids in this variant
                    v_settings = set()
                    for pid in variant['procedure_ids']:
                        if pid in settings_by_proc:
                            v_settings.update(settings_by_proc[pid])
                    if len(v_settings) == 1:
                        variant['common_setting'] = list(v_settings)[0]
                    else:
                        variant['common_setting'] = None

                    v_stats = variant.get('stats', {})
                    if v_stats:
                        if v_stats.get('min') is not None: g_min = min(g_min, v_stats['min'])
                        if v_stats.get('max') is not None: g_max = max(g_max, v_stats['max'])
                    
                # Attach Group Stats
                if g_min != float('inf'):
                    group['group_stats'] = {
                        'min': g_min,
                        'max': g_max,
                        'is_range': (g_min != g_max)
                    }
                    # If only one variant, pull its average up
                    if len(group['variants']) == 1:
                        group['group_stats']['avg'] = group['variants'][0]['stats'].get('avg')

            elapsed_time = time.time() - start_time
            
            # Setup Pagination
            results_count = len(grouped_results)
            paginator = Paginator(grouped_results, items_per_page)
            page_obj = paginator.get_page(page_number)
            grouped_results = list(page_obj.object_list)


        except Exception as e:
            print(f"Error searching SQLite: {e}")
            grouped_results = []
            results_count = 0
            
    context = {
        'query': query,
        'grouped_results': grouped_results,
        'results_count': results_count,
        'elapsed_time': f"{elapsed_time:.4f}",
        'page_obj': page_obj,
        'hospital_name': get_hospital_name(),
        'field_tooltips': FIELD_TOOLTIPS,
        'total_records': total_records,
        'payers_list': payers_list,
        'selected_payers': selected_payers_set,
        'selected_states': selected_states_set,
        'hospitals_list': hospitals_list,
        'hospital_cities': hospital_cities,
        'selected_hospitals': selected_hospitals_set,
        'hospitals_json': json.dumps([{'id': h['id'], 'name': h['name'], 'city': h['city'],
                                       'lat': h['lat'], 'lng': h['lng']} for h in hospitals_list]),
        'base_query_string': base_query_string,
        'filter_token': filter_token,
        'token_expired': token_expired,
        'error_message': error_message,
        'turnstile_site_key': getattr(settings, 'TURNSTILE_BASKET_KEY', ''),
        'debug': settings.DEBUG,
        'is_human': request.session.get('is_human', False),
        'is_local': is_local,
    }
    
    response = render(request, 'prices/search.html', context)
    if filter_token and not token_expired and selected_hospitals:
        response.set_cookie('selected_hospitals', ','.join(selected_hospitals), max_age=30*86400, path='/', samesite='Lax')
    return response


@require_GET
def related_procedures(request):
    """Return procedures that share the same DRG (or APC) as the given code."""
    ms_drg   = request.GET.get('ms_drg', '').strip()
    apr_drg  = request.GET.get('apr_drg', '').strip()
    apc      = request.GET.get('apc', '').strip()
    exclude  = request.GET.get('exclude', '').strip()  # the CPT/code to exclude
    state    = request.GET.get('state', 'in').strip().lower()

    # Pick the best grouping key available
    if ms_drg:
        group_field, group_value = 'ms_drg', ms_drg
    elif apr_drg:
        group_field, group_value = 'apr_drg', apr_drg
    elif apc:
        group_field, group_value = 'apc', apc
    else:
        return JsonResponse({'error': 'ms_drg, apr_drg, or apc is required'}, status=400)

    cache_key = f'related_{group_field}_{group_value}_{exclude}_{state}'
    cached = cache.get(cache_key)
    if cached:
        return JsonResponse({'results': cached})

    try:
        with connection.cursor() as cursor:
            # Resolve the correct state database
            active_dbs = resolve_active_dbs(cursor, [state])
            source_db = active_dbs[0]
            db_prefix = f"{source_db}_db." if source_db != "main" else ""
            
            # We want to select up to 8 procedures sharing the same group_field, excluding code 'exclude'
            sql = f"""
                SELECT code, code_type, description, stats_avg, ms_drg, apr_drg, apc 
                FROM {db_prefix}procedures 
                WHERE {group_field} = %s 
            """
            params = [group_value]
            if exclude:
                sql += " AND code != %s"
                params.append(exclude)
            
            sql += " LIMIT 8"
            cursor.execute(sql, params)
            
            results = []
            for row in cursor.fetchall():
                results.append({
                    'code':        row[0] or '',
                    'code_type':   row[1] or '',
                    'description': row[2] or '',
                    'avg_price':   row[3],
                    'ms_drg':      row[4] or '',
                    'apr_drg':     row[5] or '',
                    'apc':         row[6] or '',
                })
        cache.set(cache_key, results, 3600)
        return JsonResponse({'results': results, 'group_field': group_field, 'group_value': group_value})
    except Exception as e:
        return JsonResponse({'error': str(e) if settings.DEBUG else 'Query failed'}, status=500)


@require_GET
def explain_code(request):
    code = request.GET.get('code', '').strip()
    description = request.GET.get('description', '').strip()

    if not code and not description:
        return JsonResponse({'error': 'code or description required'}, status=400)

    cache_key = f'explain_{code}_{description[:80]}'
    cached = cache.get(cache_key)
    if cached:
        return JsonResponse({'explanation': cached})

    project = getattr(settings, 'GOOGLE_CLOUD_PROJECT', '')
    if not project:
        return JsonResponse({'error': 'Google Gen AI not configured'}, status=503)

    try:
        from google import genai
        from google.genai.types import HttpOptions

        client = genai.Client(
            vertexai=True,
            project=project,
            location=getattr(settings, 'GOOGLE_CLOUD_LOCATION', 'global'),
            http_options=HttpOptions(api_version='v1'),
        )

        prompt = (
            f"Explain the medical procedure or service described below in plain English for a patient. "
            f"Be concise (2-3 sentences): what it is, why it's typically done, and what to expect.\n\n"
            f"Code: {code}\n"
            f"Description: {description}"
        )

        response = client.models.generate_content(
            model='gemini-3.1-flash-lite-preview',
            contents=prompt,
        )
        explanation = response.text.strip()

        cache.set(cache_key, explanation, 3600)  # cache 1 hour
        return JsonResponse({'explanation': explanation})

    except Exception as e:
        import traceback
        traceback.print_exc()
        error_detail = str(e) if settings.DEBUG else 'Could not generate explanation'
        return JsonResponse({'error': error_detail}, status=500)


_payer_plan_mapping_cache = None

def _get_payer_plan_mapping():
    global _payer_plan_mapping_cache
    if _payer_plan_mapping_cache is not None:
        return _payer_plan_mapping_cache
        
    mapping = {}
    try:
        from django.conf import settings
        import json
        json_path = os.path.join(settings.BASE_DIR, 'prices', 'static_payers_and_plans.json')
        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as f:
                payers_list = json.load(f)
                
            for group in payers_list:
                parent_name = group.get('parent_name', 'Other / Local Insurers')
                for plan in group.get('plans', []):
                    display = plan.get('display', 'Standard / All Plans')
                    combined_raw = plan.get('combined_raw', '')
                    for pair_str in combined_raw.split(','):
                        if '|' in pair_str:
                            p_raw, pl_raw = pair_str.split('|', 1)
                        else:
                            p_raw, pl_raw = pair_str, ''
                        
                        key = (p_raw.strip().lower(), pl_raw.strip().lower())
                        mapping[key] = (parent_name, display)
    except Exception as e:
        print(f"Error building reverse mapping: {e}")
        
    _payer_plan_mapping_cache = mapping
    return mapping


@require_GET
def prices_details(request):
    """
    AJAX endpoint to fetch pricing details for specific procedure IDs.
    Attaches the full database (GCS FUSE) and queries the prices table.
    Renders prices/price_table.html and returns it.
    """
    from django.http import HttpResponse
    
    proc_ids_str = request.GET.get('ids', '').strip()
    state = request.GET.get('state', 'in').strip().lower()
    selected_payers_str = request.GET.get('payers', '').strip()
    selected_hospitals_str = request.GET.get('hospitals', '').strip()
    
    if not proc_ids_str:
        return HttpResponse("<div class='alert alert-warning'>No procedures selected.</div>")
        
    proc_ids = [pid.strip() for pid in proc_ids_str.split(',') if pid.strip()]
    selected_payers = [p.strip() for p in selected_payers_str.split(',') if p.strip()]
    selected_hospitals = set([h.strip() for h in selected_hospitals_str.split(',') if h.strip()])
    
    selected_payer_plans = set()
    for item in selected_payers:
        for sub_item in item.split(','):
            if '|' in sub_item:
                parts = sub_item.split('|', 1)
                selected_payer_plans.add((parts[0].lower(), parts[1].lower()))
            else:
                selected_payer_plans.add((sub_item.lower(), None))
    
    try:
        with connection.cursor() as cursor:
            # Resolve the full database for the state
            active_dbs = resolve_active_dbs(cursor, [state], force_full=True)
            source_db = active_dbs[0]
            db_prefix = f"{source_db}_db." if source_db != "main" else ""
            
            # Fetch prices
            price_placeholders = ",".join(["%s"] * len(proc_ids))
            sql = f"""
                SELECT 
                    p.procedure_id, 
                    h.hospital_hash AS hospital_id, 
                    h.name AS hospital_name, 
                    pay.name AS payer_name, 
                    pl.name AS plan_name, 
                    s.name AS setting, 
                    p.price 
                FROM {db_prefix}prices p
                LEFT JOIN {db_prefix}hospitals h ON p.hospital_id = h.id
                LEFT JOIN {db_prefix}plans pl ON p.plan_id = pl.id
                LEFT JOIN {db_prefix}payers pay ON pl.payer_id = pay.id
                LEFT JOIN {db_prefix}settings s ON p.setting_id = s.id
                WHERE p.procedure_id IN ({price_placeholders})
            """
            cursor.execute(sql, proc_ids)
            prices_fetched = cursor.fetchall()
            
            # Fetch stats for each procedure ID to determine price_hue correctly
            stats_sql = f"""
                SELECT id, stats_min, stats_max, stats_avg, stats_count
                FROM {db_prefix}procedures
                WHERE id IN ({price_placeholders})
            """
            cursor.execute(stats_sql, proc_ids)
            stats_fetched = cursor.fetchall()
            stats_by_proc = {}
            for row in stats_fetched:
                pid, s_min, s_max, s_avg, s_count = row
                stats_by_proc[pid] = {
                    'min': s_min,
                    'max': s_max,
                    'avg': s_avg,
                    'count': s_count
                }
            
            raw_items = []
            for p_row in prices_fetched:
                pid, h_id, h_name, payer_name, plan_name, setting, price = p_row
                raw_items.append({
                    'procedure_id': pid,
                    'hospital_id': h_id,
                    'hospital_name': h_name,
                    'payer_name': payer_name,
                    'plan_name': plan_name,
                    'setting': setting,
                    'price': price
                })
                
            # Prepare items with hue
            items = []
            for item in raw_items:
                p_name = item.get('payer_name') or 'Unknown'
                if p_name.lower() in ('gross', 'gross charge', 'negotiated dollar'):
                    continue
                h_id = item.get('hospital_id')
                pid = item.get('procedure_id')
                
                # Filter displayed items if payers selected
                if selected_payer_plans:
                    row_payer = (item.get('payer_name') or '').strip().lower()
                    row_plan = (item.get('plan_name') or '').strip().lower()
                    matched = False
                    for sel_payer, sel_plan in selected_payer_plans:
                        if sel_plan is not None:
                            if row_payer == sel_payer and row_plan == sel_plan:
                                matched = True
                                break
                        else:
                            if row_payer == sel_payer:
                                matched = True
                                break
                    if not matched:
                        continue

                # Filter displayed items if hospitals selected
                if selected_hospitals and h_id not in selected_hospitals:
                    continue
                    
                val = float(item.get('price', 0))
                
                # Calculate hue: 120 (Green) for low, 0 (Red) for high
                proc_stats = stats_by_proc.get(pid, {})
                local_min = proc_stats.get('min', 0) or 0
                local_max = proc_stats.get('max', 0) or 0
                
                hue = 120
                if local_max > local_min:
                    ratio = (val - local_min) / (local_max - local_min)
                    hue = int(120 - (ratio * 120))
                
                items.append({
                    'payer_name': p_name,
                    'plan_name': item.get('plan_name') or 'Unknown',
                    'hospital_id': h_id,
                    'hospital_name': item.get('hospital_name') or 'Unnamed Hospital',
                    'standard_charge_negotiated_dollar': val if val > 0 else 0.0,
                    'price_hue': hue,
                    'setting': item.get('setting') or 'Unknown'
                })
                
            # Now consolidate Payer Groups
            from collections import Counter
            import re as _re
            
            mapping = _get_payer_plan_mapping()
            
            # Group by payer first, then plan
            payer_groups = {}
            for item in items:
                raw_p = (item.get('payer_name') or '').strip()
                raw_pl = (item.get('plan_name') or '').strip()
                
                mapped = mapping.get((raw_p.lower(), raw_pl.lower()))
                if mapped:
                    p_name, pl_name = mapped
                else:
                    def _human_payer_name(raw):
                        s = (raw or "Unknown").strip().replace('_', ' ')
                        return s.title()

                    def _normalize_plan_name(raw):
                        s = (raw or "Unknown").strip()
                        s = _re.sub(r'[-/]', ' ', s)
                        s = _re.sub(r'\s+', ' ', s).strip()
                        return s.title()
                    
                    p_name = _human_payer_name(raw_p or "Unknown")
                    pl_name = _normalize_plan_name(raw_pl or "Unknown")
                
                if p_name not in payer_groups:
                    payer_groups[p_name] = {}
                if pl_name not in payer_groups[p_name]:
                    payer_groups[p_name][pl_name] = []
                payer_groups[p_name][pl_name].append(item)
            
            consolidated_payers = []
            all_price_values = []
            
            for p_name, plans_dict in payer_groups.items():
                payer_plans = []
                payer_all_prices = []
                
                for pl_name, rows in plans_dict.items():
                    prices = [r['standard_charge_negotiated_dollar'] for r in rows if r.get('standard_charge_negotiated_dollar') is not None]
                    if not prices:
                        continue
                    all_price_values.extend(prices)
                    payer_all_prices.extend(prices)
                    
                    c_min = min(prices)
                    c_max = max(prices)
                    c_avg = sum(prices) / len(prices)
                    
                    hosps = sorted(list(set([r['hospital_name'] or r['hospital_id'] for r in rows])))
                    hosp_display = ", ".join(hosps) if len(hosps) <= 2 else f"{len(hosps)} Hospitals"

                    hosp_prices = {}
                    for r in rows:
                        h = r['hospital_name'] or r['hospital_id']
                        if h not in hosp_prices:
                            hosp_prices[h] = r['standard_charge_negotiated_dollar']
                    hosp_list = [{'name': h, 'price': p} for h, p in sorted(hosp_prices.items())]

                    payer_plans.append({
                        'plan_name': pl_name,
                        'hospital_display': hosp_display,
                        'hospitals': hosp_list,
                        'price_min': c_min,
                        'price_max': c_max,
                        'price_avg': c_avg,
                        'count': len(prices),
                        'price_hue': rows[0]['price_hue']
                    })
                
                if not payer_plans:
                    continue
                
                # Sort plans under parent alphabetically
                payer_plans.sort(key=lambda x: x['plan_name'].lower())
                
                p_min = min(payer_all_prices)
                p_max = max(payer_all_prices)
                p_avg = sum(payer_all_prices) / len(payer_all_prices)
                p_count = len(payer_all_prices)
                
                # Compute aggregate hue for the payer based on its average price
                p_hue = 120
                if proc_ids:
                    proc_stats = stats_by_proc.get(proc_ids[0], {})
                    local_min = proc_stats.get('min', 0) or 0
                    local_max = proc_stats.get('max', 0) or 0
                    if local_max > local_min:
                        ratio = (p_avg - local_min) / (local_max - local_min)
                        p_hue = int(120 - (ratio * 120))
                
                consolidated_payers.append({
                    'payer_name': p_name,
                    'plans': payer_plans,
                    'price_min': p_min,
                    'price_max': p_max,
                    'price_avg': p_avg,
                    'count': p_count,
                    'price_hue': p_hue
                })
            
            _pinned = ['cash / self-pay', 'cash', 'negotiated dollar', 'cash price', 'gross charge', 'gross']
            consolidated_payers.sort(key=lambda x: (
                next((i for i, p in enumerate(_pinned) if x['payer_name'].lower().startswith(p)), len(_pinned)),
                x['payer_name'].lower()
            ))
            
            # Generate distribution SVG for the whole variant
            distribution_svg = generate_distribution_svg(all_price_values)
            
            # Generate unique identifier for nested accordion to prevent ID collisions
            unique_id = proc_ids_str.replace(',', '-')
            
            context = {
                'payers': consolidated_payers,
                'distribution_svg': distribution_svg,
                'unique_id': unique_id,
            }
            return render(request, 'prices/price_table.html', context)
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        return HttpResponse(f"<div class='alert alert-danger'>Error loading prices: {e}</div>")
