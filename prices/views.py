from django.shortcuts import render
from django.conf import settings
from django.core.paginator import Paginator
from elasticsearch import Elasticsearch
import csv
import os
import time
import requests

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

def search(request):
    query = request.GET.get('q', '')
    try:
        page_number = int(request.GET.get('page', 1))
    except (ValueError, TypeError):
        page_number = 1

    # Turnstile Verification
    error_message = None
    if query:
        # If already verified in session, skip
        if not request.session.get('is_human', False):
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
                "prices": {
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
        if 'aggregations' in agg_res and 'prices' in agg_res['aggregations']:
            buckets = agg_res['aggregations']['prices']['unique_payers']['buckets']
            payers_list = [b['key'] for b in buckets]
    except Exception as e:
        print(f"Error fetching payers: {e}")

    selected_payer = request.GET.get('payer', '')

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
        if selected_payer:
            filter_clauses.append({
                "nested": {
                    "path": "prices",
                    "query": {
                        "term": { "prices.payer_name": selected_payer }
                    }
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
            
            # Grouping Dictionary: Description -> List of Variants
            from collections import OrderedDict
            matches_by_desc = OrderedDict()

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
                    
                    # Filter displayed items if payer selected
                    if selected_payer and p_name != selected_payer:
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
                        'hospital_name': p.get('hospital_name', ''),
                        'standard_charge_negotiated_dollar': val,
                        'price_hue': hue,
                        'setting': p.get('setting', 'Unknown')
                    })
                
                # Determine Stats (Recalculate if filtered)
                if selected_payer and items:
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
                    'rev_code': rev_code
                }
                
                if desc not in matches_by_desc:
                    matches_by_desc[desc] = {
                        'description': desc,
                        'variants': []
                    }
                matches_by_desc[desc]['variants'].append(variant_data)

            grouped_results = list(matches_by_desc.values())

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
                    raw_items = variant['items']
                    payer_map = {}
                    
                    for item in raw_items:
                        # Normalize keys
                        p_name = (item.get('payer_name') or "Unknown").strip()
                        pl_name = (item.get('plan_name') or "Unknown").strip()
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
                        
                        consolidated_items.append({
                            'payer_name': p_name,
                            'plan_name': pl_name,
                            'hospital_display': hosp_display,
                            'price_min': c_min,
                            'price_max': c_max,
                            'price_avg': c_avg,
                            'count': len(prices),
                            # Use hue logic based on VARIANT stats, not global
                            'price_hue': rows[0]['price_hue'] # Just take the first one's hue as approximation
                        })
                    
                    # 3. Sort by Payer Name
                    consolidated_items.sort(key=lambda x: x['payer_name'].lower())
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
        'selected_payer': selected_payer,
        'error_message': error_message,
        'turnstile_site_key': getattr(settings, 'TURNSTILE_BASKET_KEY', '')
    }
    
    return render(request, 'prices/search.html', context)