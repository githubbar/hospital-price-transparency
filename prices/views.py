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


def _load_hospitals():
    """Load Indiana hospital list from reference file with computed ES-compatible IDs."""
    cache_key = 'indiana_hospitals_list_v1'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        ref_path = os.path.join(settings.BASE_DIR, 'reference', 'indiana_hospitals.json')
        with open(ref_path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
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

    # Get total records count (best effort)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM procedures")
            total_records = cursor.fetchone()[0]
    except Exception as e:
        print(f"Error fetching total records: {e}")

    # Fetch unique Payers for the dropdown (pre-computed statically to avoid 14.4M row scans in serverless)
    try:
        json_path = os.path.join(settings.BASE_DIR, 'prices', 'static_payers.json')
        with open(json_path, 'r', encoding='utf-8') as f:
            payers_list = json.load(f)
    except Exception as e:
        print(f"Error loading static payers: {e}")
        payers_list = []

    selected_payers = request.GET.getlist('payer')  # multi-select list; always kept in URL
    selected_payers_set = set(selected_payers)
    hospitals_list = _load_hospitals()
    hospital_cities = sorted(set(h['city'] for h in hospitals_list if h['city']))

    # ── Hospital filter via shareable token ───────────────────────────────────
    # Flow A – ?hospital=<md5>&... submitted (fresh form submit): create token, redirect.
    # Flow B – ?s=<token> present: restore hospital IDs from cache.
    # Payers always stay in the URL (?payer=X&payer=Y); only hospitals go into the token.
    filter_token = request.GET.get('s', '')
    token_expired = False
    raw_hospital_ids = request.GET.getlist('hospital')

    if raw_hospital_ids:
        # Flow A: pack hospitals into a token and redirect to clean URL
        token = _save_filter_token(raw_hospital_ids)
        redirect_params = [('q', query), ('s', token)] + [('payer', p) for p in selected_payers]
        return HttpResponseRedirect(f"{request.path}?{_urlencode(redirect_params)}")
    elif filter_token:
        # Flow B: restore from cache
        cached_ids = _load_filter_token(filter_token)
        if cached_ids is not None:
            selected_hospitals = cached_ids
        else:
            token_expired = True
            selected_hospitals = []
    else:
        selected_hospitals = []

    selected_hospitals_set = set(selected_hospitals)  # MD5 IDs used for filtering

    # Build base query string for pagination (preserves all filters except page)
    _params = request.GET.copy()
    _params.pop('page', None)
    base_query_string = _params.urlencode()

    if query and not error_message:
        start_time = time.time()
        
        # --- Spelling Auto-Correction (Typo Tolerance) ---
        corrected_query = query
        words_to_correct = re.findall(r'\b[a-zA-Z]{3,}\b', query) # Only correct words of length >= 3 containing letters
        if words_to_correct:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT word FROM unique_words")
                    vocab = [row[0] for row in cursor.fetchall()]
                    vocab_set = set(vocab)
                    
                    query_changed = False
                    for word in words_to_correct:
                        clean_word = word.lower()
                        if clean_word not in vocab_set:
                            # Not found, search closest matches
                            matches = difflib.get_close_matches(clean_word, vocab, n=1, cutoff=0.75)
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
                if not t_clean.endswith('*'):
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

        # Prepare parameters & placeholders for SQL filters
        sql_where_fts = ["fts_procedures MATCH %s"]
        sql_params = [sqlite_query]

        if selected_hospitals:
            h_placeholders = []
            for h_id in selected_hospitals:
                h_placeholders.append("%s")
                sql_params.append(h_id)
            sql_where_fts.append(f"EXISTS (SELECT 1 FROM prices WHERE prices.procedure_id = fts_procedures.procedure_id AND prices.hospital_id IN ({','.join(h_placeholders)}))")

        if selected_payers:
            p_placeholders = []
            for p_name in selected_payers:
                p_placeholders.append("%s")
                sql_params.append(p_name)
            sql_where_fts.append(f"EXISTS (SELECT 1 FROM prices WHERE prices.procedure_id = fts_procedures.procedure_id AND prices.payer_name IN ({','.join(p_placeholders)}))")

        where_clause_fts = " WHERE " + " AND ".join(sql_where_fts) if sql_where_fts else ""

        try:
            with connection.cursor() as cursor:
                # Count total matching records
                count_sql = f"SELECT COUNT(DISTINCT procedure_id) FROM fts_procedures {where_clause_fts}"
                cursor.execute(count_sql, sql_params)
                total_hits = cursor.fetchone()[0]
                results_count = total_hits

                # Query matching procedures (paginated) ranked by FTS5 BM25 relevance score
                results_sql = f"""
                    SELECT procedures.id, procedures.description, procedures.code, procedures.code_type,
                           procedures.ms_drg, procedures.apr_drg, procedures.rc, procedures.apc, procedures.ndc, procedures.cdm,
                           procedures.stats_min, procedures.stats_max, procedures.stats_avg, procedures.stats_count,
                           procedures.is_standard_group,
                           bm25(fts_procedures) as rank
                    FROM fts_procedures
                    JOIN procedures ON procedures.id = fts_procedures.procedure_id
                    {where_clause_fts}
                    ORDER BY rank ASC
                    LIMIT {items_per_page} OFFSET {(page_number - 1) * items_per_page}
                """
                cursor.execute(results_sql, sql_params)
                proc_rows = cursor.fetchall()
                
                hits = []
                if proc_rows:
                    proc_ids = [row[0] for row in proc_rows]
                    
                    # Batch fetch all prices for these procedures
                    price_placeholders = ",".join(["%s"] * len(proc_ids))
                    cursor.execute(f"""
                        SELECT procedure_id, hospital_id, hospital_name, payer_name, plan_name, setting, price 
                        FROM prices 
                        WHERE procedure_id IN ({price_placeholders})
                    """, proc_ids)
                    prices_fetched = cursor.fetchall()
                    
                    # Batch fetch all auxiliary codes
                    cursor.execute(f"""
                        SELECT procedure_id, code_value, code_type 
                        FROM procedure_codes 
                        WHERE procedure_id IN ({price_placeholders})
                    """, proc_ids)
                    codes_fetched = cursor.fetchall()

                    # Map prices & codes to their respective procedure IDs
                    prices_by_proc = {}
                    for p_row in prices_fetched:
                        pid, h_id, h_name, payer_name, plan_name, setting, price = p_row
                        prices_by_proc.setdefault(pid, []).append({
                            'hospital_id': h_id,
                            'hospital_name': h_name,
                            'payer_name': payer_name,
                            'plan_name': plan_name,
                            'setting': setting,
                            'price': price
                        })

                    codes_by_proc = {}
                    for c_row in codes_fetched:
                        pid, val, c_type = c_row
                        codes_by_proc.setdefault(pid, []).append({
                            'value': val,
                            'type': c_type
                        })

                    # Construct Elasticsearch-compatible hit structures
                    for row in proc_rows:
                        pid = row[0]
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
                                'prices': prices_by_proc.get(pid, []),
                                'codes': codes_by_proc.get(pid, [])
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
                
                prices_data = source.get('prices', [])
                stats = source.get('stats', {})
                
                # Prepare items with hue
                price_values = [float(p.get('price', 0)) for p in prices_data]
                
                # Determine min/max for coloring this specific group
                local_min = stats.get('min', 0)
                local_max = stats.get('max', 0)
                
                items = []
                for p in prices_data:
                    p_name = p.get('payer_name', 'Unknown')
                    
                    # Filter displayed items if payers selected
                    if selected_payers_set and p_name not in selected_payers_set:
                        continue

                    # Filter displayed items if hospitals selected
                    if selected_hospitals_set and p.get('hospital_id') not in selected_hospitals_set:
                        continue
                        
                    val = float(p.get('price', 0))
                    
                    # Calculate hue: 120 (Green) for low, 0 (Red) for high
                    hue = 120
                    if local_max > local_min:
                        ratio = (val - local_min) / (local_max - local_min)
                        hue = int(120 - (ratio * 120))
                    
                    items.append({
                        'payer_name': p_name,
                        'plan_name': p.get('plan_name', 'Unknown'),
                        'hospital_id': p.get('hospital_id', 'Unknown'),
                        'hospital_name': p.get('hospital_name', 'Unnamed Hospital'),  # Fallback for missing hospital_name
                        'standard_charge_negotiated_dollar': val if val > 0 else 0.0,  # Fallback for missing or invalid price
                        'price_hue': hue,
                        'setting': p.get('setting', 'Unknown')
                    })
                
                # Determine Stats (Recalculate if filtered)
                if (selected_payers_set or selected_hospitals_set) and items:
                   filtered_vals = [i['standard_charge_negotiated_dollar'] for i in items]
                   stats = {
                       'min': min(filtered_vals),
                       'max': max(filtered_vals),
                       'avg': sum(filtered_vals) / len(filtered_vals),
                       'count': len(filtered_vals)
                   }
                   # Regenerate SVG for filtered subset
                   dist_svg = generate_distribution_svg(filtered_vals)
                else:
                    # Generate Dist SVG
                    dist_svg = generate_distribution_svg(price_values)
                
                stats['distribution_svg'] = dist_svg
                
                # Common setting from first item
                common_setting = items[0]['setting'] if items else 'Unknown'
                
                # Check if there are actually other procedures sharing the same group key
                ms_drg_val = (source.get('ms_drg') or '').strip()
                apr_drg_val = (source.get('apr_drg') or '').strip()
                rc_val = (source.get('rc') or '').strip()

                with connection.cursor() as sub_cursor:
                    if ms_drg_val:
                        sub_cursor.execute("SELECT 1 FROM procedures WHERE ms_drg = %s AND code != %s LIMIT 1", [ms_drg_val, code])
                        if not sub_cursor.fetchone():
                            ms_drg_val = ""

                    if apr_drg_val:
                        sub_cursor.execute("SELECT 1 FROM procedures WHERE apr_drg = %s AND code != %s LIMIT 1", [apr_drg_val, code])
                        if not sub_cursor.fetchone():
                            apr_drg_val = ""

                    if rc_val:
                        sub_cursor.execute("SELECT 1 FROM procedures WHERE rc = %s AND code != %s LIMIT 1", [rc_val, code])
                        if not sub_cursor.fetchone():
                            rc_val = ""

                variant_data = {
                    'code': (code or '').strip(),
                    'code_type': (code_type or '').strip(),
                    'common_setting': common_setting, 
                    'items': items,
                    'stats': stats,
                    'rev_code': (source.get('rc') or source.get('rev_code') or '').strip(),
                    'ms_drg':  ms_drg_val,
                    'apr_drg': apr_drg_val,
                    'rc':      rc_val,
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

            # --- Merge overflow variant-parts (same code+code_type split across multiple ES docs) ---
            # When a code's prices exceed LIMIT_PER_DOC they become separate ES documents,
            # each appearing as a separate variant. Re-merge them so payer rows are not duplicated.
            for group in grouped_results:
                if len(group['variants']) <= 1:
                    continue
                merged_map = {}
                order = []
                for variant in group['variants']:
                    key = (variant['code'].strip().upper(), variant['code_type'])
                    if key not in merged_map:
                        merged_map[key] = variant
                        order.append(key)
                    else:
                        merged_map[key]['items'].extend(variant['items'])

                if len(order) < len(group['variants']):
                    # At least one merge happened — recalculate per-variant stats
                    for key in order:
                        v = merged_map[key]
                        all_vals = [i['standard_charge_negotiated_dollar'] for i in v['items']
                                    if i.get('standard_charge_negotiated_dollar')]
                        if all_vals:
                            v['stats'] = {
                                'min': min(all_vals),
                                'max': max(all_vals),
                                'avg': sum(all_vals) / len(all_vals),
                                'count': len(all_vals),
                                'distribution_svg': generate_distribution_svg(all_vals),
                            }
                    group['variants'] = [merged_map[k] for k in order]

            # --- Post-Processing for Groups ---
            for group in grouped_results:
                
                # 1. Calculate Group-Level Aggregate Stats (Min/Max across all variants)
                g_min = float('inf')
                g_max = float('-inf')
                g_prices = []
                
                for variant in group['variants']:
                    v_stats = variant.get('stats', {})
                    if v_stats:
                        if v_stats.get('min') is not None: g_min = min(g_min, v_stats['min'])
                        if v_stats.get('max') is not None: g_max = max(g_max, v_stats['max'])
                    
                    # Also collect all prices for a potential group-level chart (optional, maybe overkill)
                    # We will just use the stats for the header for now.
                    
                    # 2. Group Items by Payer/Plan within Variant
                    def _human_payer_name(raw):
                        """Make raw payer names human-readable (e.g. negotiated_dollar → Negotiated Dollar)."""
                        s = (raw or "Unknown").strip().replace('_', ' ')
                        return s.title()

                    def _normalize_plan_name(raw):
                        """Collapse punctuation/hyphen variants (e.g. Non-Par → Non Par)."""
                        s = (raw or "Unknown").strip()
                        s = _re.sub(r'[-/]', ' ', s)          # hyphens/slashes → space
                        s = _re.sub(r'\s+', ' ', s).strip()   # collapse whitespace
                        return s.title()

                    raw_items = variant['items']
                    payer_map = {}
                    
                    for item in raw_items:
                        # Normalize keys
                        p_name = _human_payer_name(item.get('payer_name') or "Unknown")
                        pl_name = _normalize_plan_name(item.get('plan_name') or "Unknown")
                        key = (p_name, pl_name)
                        
                        if key not in payer_map:
                            payer_map[key] = []
                        payer_map[key].append(item)
                    
                    # Consolidate Payer Groups
                    consolidated_items = []
                    for (p_name, pl_name), rows in payer_map.items():
                        prices = [r['standard_charge_negotiated_dollar'] for r in rows if r.get('standard_charge_negotiated_dollar') is not None]
                        
                        if not prices:
                            continue
                            
                        c_min = min(prices)
                        c_max = max(prices)
                        c_avg = sum(prices) / len(prices)
                        
                        # Gather hospital info (unique list)
                        hosps = sorted(list(set([r['hospital_name'] or r['hospital_id'] for r in rows])))
                        hosp_display = ", ".join(hosps) if len(hosps) <= 2 else f"{len(hosps)} Hospitals"

                        # Build per-hospital breakdown for tooltip
                        hosp_prices = {}
                        for r in rows:
                            h = r['hospital_name'] or r['hospital_id']
                            if h not in hosp_prices:
                                hosp_prices[h] = r['standard_charge_negotiated_dollar']
                        hosp_list = [{'name': h, 'price': p} for h, p in sorted(hosp_prices.items())]

                        consolidated_items.append({
                            'payer_name': p_name,
                            'plan_name': pl_name,
                            'hospital_display': hosp_display,
                            'hospitals': hosp_list,
                            'price_min': c_min,
                            'price_max': c_max,
                            'price_avg': c_avg,
                            'count': len(prices),
                            # Use hue logic based on VARIANT stats, not global
                            'price_hue': rows[0]['price_hue'] # Just take the first one's hue as approximation
                        })
                    
                    # 3. Sort by Payer Name (pin Negotiated Dollar/Cash/Gross first)
                    _pinned = ['negotiated dollar', 'cash price', 'cash', 'gross charge', 'gross']
                    consolidated_items.sort(key=lambda x: (
                        next((i for i, p in enumerate(_pinned) if x['payer_name'].lower().startswith(p)), len(_pinned)),
                        x['payer_name'].lower()
                    ))
                    variant['items'] = consolidated_items

                # Attach Group Stats
                if g_min != float('inf'):
                    group['group_stats'] = {
                        'min': g_min,
                        'max': g_max,
                        'is_range': (g_min != g_max)
                    }
                    # If only one variant, pull its SVG up
                    if len(group['variants']) == 1:
                        group['group_stats']['distribution_svg'] = group['variants'][0]['stats'].get('distribution_svg')
                        group['group_stats']['avg'] = group['variants'][0]['stats'].get('avg')

            elapsed_time = time.time() - start_time
            
            # Setup Pagination
            paginator = Paginator(range(results_count), items_per_page)
            page_obj = paginator.get_page(page_number)
            page_obj.object_list = grouped_results


        except Exception as e:
            print(f"Error searching Elastic: {e}")
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
    
    return render(request, 'prices/search.html', context)


@require_GET
def related_procedures(request):
    """Return procedures that share the same DRG (or RC) as the given code."""
    ms_drg   = request.GET.get('ms_drg', '').strip()
    apr_drg  = request.GET.get('apr_drg', '').strip()
    rc       = request.GET.get('rc', '').strip()
    exclude  = request.GET.get('exclude', '').strip()  # the CPT/code to exclude

    # Pick the best grouping key available
    if ms_drg:
        group_field, group_value = 'ms_drg', ms_drg
    elif apr_drg:
        group_field, group_value = 'apr_drg', apr_drg
    elif rc:
        group_field, group_value = 'rc', rc
    else:
        return JsonResponse({'error': 'ms_drg, apr_drg, or rc is required'}, status=400)

    cache_key = f'related_{group_field}_{group_value}_{exclude}'
    cached = cache.get(cache_key)
    if cached:
        return JsonResponse({'results': cached})

    try:
        with connection.cursor() as cursor:
            # We want to select up to 8 procedures sharing the same group_field, excluding code 'exclude'
            sql = f"""
                SELECT code, code_type, description, stats_avg, ms_drg, apr_drg, rc 
                FROM procedures 
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
                    'rc':          row[6] or '',
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