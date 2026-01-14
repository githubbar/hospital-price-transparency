from django.shortcuts import render
from django.db.models import Q
from django.conf import settings
from django.core.paginator import Paginator
from itertools import groupby
from .models import HospitalPrices
import csv
import os
import time

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

def search(request):
    query = request.GET.get('q', '')
    results = []
    grouped_results = []
    results_count = 0
    elapsed_time = 0
    
    if query:
        start_time = time.time()
        # Search using Full Text Search (MySQL)
        # Note: This requires the Full Text Index created in migration 0002
        sql_query = """
            SELECT * FROM hospital_prices 
            WHERE MATCH(description, code_1, code_2, code_3)
            AGAINST (%s IN NATURAL LANGUAGE MODE)
            ORDER BY description, payer_name
            LIMIT 5000
        """
        results_qs = HospitalPrices.objects.raw(sql_query, [query])
        
        # Force evaluation and convert to list for groupby
        results = list(results_qs)
        results_count = len(results)
        
        # Group results by description
        for description, items in groupby(results, key=lambda x: x.description):
            # Convert iterator to list to use multiple times
            item_list = list(items)
            
            # Calculate stats and sort
            stats = None
            priced_items = [i for i in item_list if i.standard_charge_negotiated_dollar is not None]
            
            if priced_items:
                prices = [float(i.standard_charge_negotiated_dollar) for i in priced_items]
                min_price = min(prices)
                max_price = max(prices)
                avg_price = sum(prices) / len(prices)
                
                stats = {
                    'min': min_price,
                    'max': max_price,
                    'avg': avg_price,
                    'count': len(prices)
                }
                
                # Generate distribution plot
                stats['distribution_svg'] = generate_distribution_svg(prices)

                # Sort item_list: items with price (asc) first, then None
                item_list.sort(key=lambda x: float(x.standard_charge_negotiated_dollar) if x.standard_charge_negotiated_dollar is not None else float('inf'))

                # Calculate Hue for Heatmap (Green 120 -> Red 0)
                price_range = max_price - min_price
                for item in item_list:
                    if item.standard_charge_negotiated_dollar is not None:
                        current_price = float(item.standard_charge_negotiated_dollar)
                        if price_range > 0:
                            ratio = (current_price - min_price) / price_range
                            hue = 120 - (ratio * 120)
                        else:
                            hue = 120 # Default green if single price
                        
                        # Attach attribute dynamically
                        item.price_hue = int(hue)

            # Take the first item to get common fields like code/setting if we want to display them in the header
            # For now, just passing the description and the list of items
            first_item = item_list[0]
            grouped_results.append({
                'description': description,
                'common_code': first_item.code_1,
                'common_code_type': first_item.code_1_type,
                'common_setting': first_item.setting,
                'items': item_list,
                'stats': stats
            })

        elapsed_time = time.time() - start_time

    # Pagination
    paginator = Paginator(grouped_results, 10) # 10 groups per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    total_records = HospitalPrices.objects.count()
    hospital_name = get_hospital_name()
    
    context = {
        'query': query,
        'results': results, # Keep for backward compatibility if needed, but we mostly use grouped_results now
        'grouped_results': page_obj, # Pass the page object as grouped_results so the template loop works
        'page_obj': page_obj,
        'results_count': results_count,
        'tooltips': FIELD_TOOLTIPS,
        'hospital_name': hospital_name,
        'total_records': total_records,
        'elapsed_time': round(elapsed_time, 4)
    }
    return render(request, 'prices/search.html', context)