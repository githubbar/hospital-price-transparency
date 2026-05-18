from django.shortcuts import render
from django.conf import settings
from django.core.paginator import Paginator
from django.http import JsonResponse, HttpResponseRedirect
from django.views.decorators.http import require_GET
from django.core.cache import cache
from elasticsearch import Elasticsearch
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

    # es = Elasticsearch(settings.ELASTICSEARCH_URL)
    
    # Support for Auth
    es_params = {'hosts': settings.ELASTICSEARCH_URL}
    if hasattr(settings, 'ELASTICSEARCH_USERNAME') and settings.ELASTICSEARCH_USERNAME:
        es_params['basic_auth'] = (settings.ELASTICSEARCH_USERNAME, settings.ELASTICSEARCH_PASSWORD)

    if settings.ELASTICSEARCH_URL.startswith('https'):
        es_params['verify_certs'] = False
        es_params['ssl_show_warn'] = False

    es = Elasticsearch(**es_params)

    # Get total records count (best effort)
    try:
        # Check if index exists first to avoid 404
        if es.indices.exists(index=settings.ELASTICSEARCH_INDEX):
            total_records = es.count(index=settings.ELASTICSEARCH_INDEX)['count']
    except Exception:
        pass

    # Fetch unique Payers for the dropdown
    payers_list = []
    try:
        aggs_body = {
            "size": 0,
            "aggs": {
                "nested_prices": {
                    "nested": {"path": "prices"},
                    "aggs": {
                        "unique_payers": {
                            "terms": {"field": "prices.payer_name", "size": 1000, "order": {"_key": "asc"}}
                        }
                    }
                }
            }
        }
        agg_res = es.search(index=settings.ELASTICSEARCH_INDEX, body=aggs_body)
        if 'aggregations' in agg_res and 'nested_prices' in agg_res['aggregations']:
            buckets = agg_res['aggregations']['nested_prices']['unique_payers']['buckets']
            all_payers_raw = [b['key'] for b in buckets if b['key'].strip()]

            def _humanize_payer(raw):
                """Turn raw ES values like negotiated_dollar into Negotiated Dollar."""
                if '_' in raw:
                    return raw.replace('_', ' ').title()
                return raw  # Already human-readable (e.g. 'Gross', 'BCBS PPO')

            # Deduplicate near-identical names (e.g. 'J&J' vs 'J and J')
            def _norm_key(n):
                import re as _re
                return _re.sub(r'\s+', ' ', _re.sub(r'\s*&\s*', ' and ', n.replace('_', ' '))).strip().lower()

            seen_keys = {}
            all_payers = []  # list of {'raw': ..., 'display': ...}
            for p in all_payers_raw:
                k = _norm_key(p)
                if k not in seen_keys:
                    seen_keys[k] = p
                    all_payers.append({'raw': p, 'display': _humanize_payer(p)})

            _pinned_display = {'negotiated dollar', 'cash', 'gross', 'gross charge', 'discounted cash'}
            def _pin_order(item):
                d = item['display'].lower()
                order = ['negotiated dollar', 'cash', 'gross charge', 'gross', 'discounted cash']
                return order.index(d) if d in order else len(order)

            pinned_items = sorted([i for i in all_payers if i['display'].lower() in _pinned_display], key=_pin_order)
            other_items  = sorted([i for i in all_payers if i['display'].lower() not in _pinned_display], key=lambda x: x['display'].lower())
            payers_list = pinned_items + other_items
    except Exception as e:
        print(f"Error fetching payers: {e}")

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

    selected_hospitals_set = set(selected_hospitals)  # MD5 IDs used for ES filtering

    # Build base query string for pagination (preserves all filters except page)
    _params = request.GET.copy()
    _params.pop('page', None)
    base_query_string = _params.urlencode()

    if query and not error_message:
        start_time = time.time()
        
        # Build Query
        must_clauses = [
            {
                "multi_match": {
                    "query": query,
                    "fields": ["description", "code^2", "code_type", "rev_code"],
                    "type": "best_fields",
                    "fuzziness": "AUTO"
                }
            }
        ]
        
        filter_clauses = []
        if selected_payers and selected_hospitals:
            filter_clauses.append({
                "nested": {
                    "path": "prices",
                    "query": {
                        "bool": {
                            "must": [
                                {"terms": {"prices.payer_name": selected_payers}},
                                {"terms": {"prices.hospital_id": selected_hospitals}},
                            ]
                        }
                    }
                }
            })
        elif selected_payers:
            filter_clauses.append({
                "nested": {
                    "path": "prices",
                    "query": {"terms": {"prices.payer_name": selected_payers}}
                }
            })
        elif selected_hospitals:
            filter_clauses.append({
                "nested": {
                    "path": "prices",
                    "query": {"terms": {"prices.hospital_id": selected_hospitals}}
                }
            })

        body = {
            "from": (page_number - 1) * items_per_page,
            "size": items_per_page,
            "query": {
                "bool": {
                    "must": must_clauses,
                    "filter": filter_clauses
                }
            }
        }
        
        try:
            res = es.search(index=settings.ELASTICSEARCH_INDEX, body=body)
            hits = res['hits']['hits']
            total_hits = res['hits']['total']['value']
            
            results_count = total_hits
            
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
                
                variant_data = {
                    'code': code,
                    'code_type': code_type,
                    'common_setting': common_setting, 
                    'items': items,
                    'stats': stats,
                    'rev_code': source.get('rc', '') or source.get('rev_code', ''),
                    'ms_drg':  source.get('ms_drg', ''),
                    'apr_drg': source.get('apr_drg', ''),
                    'rc':      source.get('rc', ''),
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

    es_params = {'hosts': settings.ELASTICSEARCH_URL}
    if getattr(settings, 'ELASTICSEARCH_USERNAME', None):
        es_params['basic_auth'] = (settings.ELASTICSEARCH_USERNAME, settings.ELASTICSEARCH_PASSWORD)
    if settings.ELASTICSEARCH_URL.startswith('https'):
        es_params['verify_certs'] = False
        es_params['ssl_show_warn'] = False
    es = Elasticsearch(**es_params)

    query_body = {
        "size": 8,
        "_source": ["code", "code_type", "description", "stats", "ms_drg", "apr_drg", "rc"],
        "query": {
            "bool": {
                "must": [{"term": {group_field: group_value}}],
                "must_not": (
                    [{"term": {"code.keyword": exclude}}] if exclude else []
                )
            }
        }
    }

    try:
        res = es.search(index=settings.ELASTICSEARCH_INDEX, body=query_body)
        results = []
        for hit in res['hits']['hits']:
            s = hit['_source']
            results.append({
                'code':        s.get('code', ''),
                'code_type':   s.get('code_type', ''),
                'description': s.get('description', ''),
                'avg_price':   s.get('stats', {}).get('avg'),
                'ms_drg':      s.get('ms_drg', ''),
                'apr_drg':     s.get('apr_drg', ''),
                'rc':          s.get('rc', ''),
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