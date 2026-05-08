"""
Script: load_to_es.py

Description:
      This script is responsible for loading processed hospital pricing data into an Elasticsearch index.
      It reads data (typically from CSV, JSON, or a database), transforms it into the appropriate
      document structure, and uses the Elasticsearch bulk API for efficient indexing.

Usage:
      python load_to_es.py [--input_file DATA_PATH] [--index_name HOSPITAL_PRICES] [--host ES_HOST]

Arguments:
      --input_file (str): Path to the source file containing processed hospital data.
      --index_name (str): Name of the Elasticsearch index to populate. Defaults to 'hospital-prices'.
      --host (str): Elasticsearch host URL. Defaults to 'localhost'.

Dependencies:
      - elasticsearch (Python client)
      - pandas (if used for data manipulation)

Notes:
      - Ensure the Elasticsearch service is running before executing this script.
      - Existing data in the target index may be overwritten depending on the script's configuration.
"""
import csv
import gzip
import hashlib
import json
import os
import re
import sys
import argparse
import django
from elasticsearch import Elasticsearch, helpers
from elasticsearch.helpers import parallel_bulk
from tqdm import tqdm

# Setup Django environment
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.conf import settings

# Configuration
ES_URL = settings.ELASTICSEARCH_URL
ES_USER = getattr(settings, 'ELASTICSEARCH_USERNAME', None)
ES_PASS = getattr(settings, 'ELASTICSEARCH_PASSWORD', None)
INDEX_NAME = getattr(settings, 'ELASTICSEARCH_INDEX', 'hospital_prices')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
REFERENCE_DIR = os.path.join(BASE_DIR, 'reference')

print(f"Configuration:")
print(f"  ES_URL: {ES_URL}")
print(f"  ES_USER: {ES_USER}")
print(f"  ES_PASS: {'******' if ES_PASS else None}")
print(f"  INDEX_NAME: {INDEX_NAME}")
print(f"  DATA_DIR: {DATA_DIR}")

def load_shoppable_codes(csv_path=None):
    """Load the CMS shoppable services code list and return a set of code values."""
    if csv_path is None:
        csv_path = os.path.join(REFERENCE_DIR, 'shoppable_codes.csv')
    codes = set()
    if not os.path.exists(csv_path):
        print(f"WARNING: Shoppable codes file not found at {csv_path}")
        return codes
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            codes.add(row['code'].strip())
    print(f"Loaded {len(codes)} shoppable codes from {os.path.basename(csv_path)}")
    return codes


def normalize_payer_name(name):
    """Normalize payer name to deduplicate variants like 'J&J' vs 'J and J'."""
    if not name:
        return name
    # Replace underscores with spaces (e.g. negotiated_dollar -> negotiated dollar)
    name = name.replace('_', ' ')
    name = re.sub(r'\s*&\s*', ' and ', name)
    name = re.sub(r'\s+', ' ', name).strip()
    # Title-case known pinned values for display consistency
    _title_map = {
        'negotiated dollar': 'Negotiated Dollar',
        'discounted cash': 'Discounted Cash',
        'gross charge': 'Gross Charge',
        'cash': 'Cash',
        'gross': 'Gross',
    }
    return _title_map.get(name.lower(), name)

def parse_currency(value):
    if not value or value.strip() == '':
        return None
    try:
        return float(value.replace('$', '').replace(',', ''))
    except ValueError:
        return None

def generate_id(text_parts):
    """Generates a consistent hash ID from a list of strings."""
    combined = "".join([str(p).strip().lower() for p in text_parts if p])
    return hashlib.md5(combined.encode('utf-8')).hexdigest()

def create_index(es, clean=False):
    """Creates the index with appropriate mappings."""
    if es.indices.exists(index=INDEX_NAME):
        if clean:
            print(f"Index '{INDEX_NAME}' exists. Deleting it as requested...")
            es.indices.delete(index=INDEX_NAME)
        else:
            print(f"Index '{INDEX_NAME}' already exists. Skipping creation to preserve text.")
            return

    settings = {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "index.mapping.nested_objects.limit": 10000
        },
        "mappings": {
            "properties": {
                "id": {"type": "keyword"},
                "group_key": {"type": "keyword"},
                "description": {"type": "text", "analyzer": "english"},
                "code": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                "code_type": {"type": "keyword"},
                "ms_drg": {"type": "keyword"},
                "apr_drg": {"type": "keyword"},
                "rc": {"type": "keyword"},
                "apc": {"type": "keyword"},
                "ndc": {"type": "keyword"},
                "cdm": {"type": "keyword"},
                "codes": {
                    "type": "nested",
                    "properties": {
                        "value": {"type": "keyword"},
                        "type": {"type": "keyword"}
                    }
                },
                "is_standard_group": {"type": "boolean"},
                "stats": {
                    "properties": {
                        "min": {"type": "float"},
                        "max": {"type": "float"},
                        "avg": {"type": "float"},
                        "count": {"type": "integer"}
                    }
                },
                "prices": {
                    "type": "nested",
                    "properties": {
                        "hospital_id": {"type": "keyword"},
                        "payer_name": {"type": "keyword"},
                        "plan_name": {"type": "keyword"},
                        "setting": {"type": "keyword"},
                        "price": {"type": "float"}
                    }
                }
            }
        }
    }

    es.indices.create(index=INDEX_NAME, body=settings)
    print(f"Index '{INDEX_NAME}' created.")

def generate_actions(procedures):
    """Generates actions for the bulk API."""
    for procedure in procedures:
        yield {
            "_index": INDEX_NAME,
            "_id": procedure['id'],
            "_source": procedure
        }

def clean_hospital_name(raw_name):
    """
    Cleans a hospital name by removing ID prefixes (digits/underscores)
    and formatting it to be human-readable.
    Example: 351720796_Indiana-University... -> Indiana University...
    """
    if not raw_name:
        return "Unknown Hospital"
    
    # Remove leading tax IDs/numbers and separator (e.g. "123456_" or "123456 ")
    s = re.sub(r'^\d+[\W_]+', '', raw_name)
    
    # Replace filename separators with spaces
    s = s.replace('_', ' ').replace('-', ' ').replace('.', ' ')
    
    # Clean up multiple spaces
    s = re.sub(r'\s+', ' ', s).strip()
    
    return s.title()

def parse_csv_into_map(csv_path, procedures_map, active_group_tracker, shoppable_codes=None):
    print(f"Parsing {os.path.basename(csv_path)}{'  [shoppable-only filter active]' if shoppable_codes else ''}...")
    LIMIT_PER_DOC = 5000

    try:
        with open(csv_path, 'r', encoding='utf-8', errors='replace') as f:
            # 1. Analyze first few lines to find the real header row
            sample_lines = []
            for _ in range(10):
                line = f.readline()
                if not line: break
                sample_lines.append(line)
            
            f.seek(0) # Reset file pointer
            reader = csv.reader(f)

            header_row_idx = 0
            headers = []
            Hospital_Name_From_Meta = None

            # Keywords to identify the header row
            header_keywords = ['description', 'code', 'standard_charge', 'price', 'plan', 'payer']
            max_matches = 0

            # Temporary reader to analyze structure
            temp_reader = csv.reader(sample_lines)
            for idx, row in enumerate(temp_reader):
                if not row: continue
                # Count how many keywords appear in this row (case insensitive)
                row_str = " ".join(row).lower()
                matches = sum(1 for k in header_keywords if k in row_str)

                # Heuristic: logical header usually has at least 3 matching keywords
                if matches > max_matches and matches >= 2:
                    max_matches = matches
                    header_row_idx = idx
                    headers = row
                    
                    # Look for hospital name in previous rows
                    if idx > 0:
                        # Check strictly the row before (idx-1) or 2 rows before
                        # Simple rule: Look at the very first row or the row just before headers
                        try:
                            # Re-read strictly for meta analysis
                            meta_row_1 = next(csv.reader([sample_lines[0]]))
                            meta_row_2 = next(csv.reader([sample_lines[1]])) if len(sample_lines) > 1 else []
                            
                            # Check specifically for "hospital_name" key in first row
                            meta_map = {h.strip().lower(): i for i, h in enumerate(meta_row_1)}
                            if "hospital_name" in meta_map and len(meta_row_2) > meta_map["hospital_name"]:
                                Hospital_Name_From_Meta = meta_row_2[meta_map["hospital_name"]]
                            elif len(meta_row_2) > 0 and idx >= 2:
                                # Fallback: assume first col of 2nd row is name (CMS style)
                                Hospital_Name_From_Meta = meta_row_2[0]
                        except Exception:
                            pass

            if not headers:
                # Fallback: Assume first row is header if dynamic detection failed
                f.seek(0)
                headers = next(reader)
                header_row_idx = 0

            # Advance the real reader to the data payload (skip metadata + header)
            f.seek(0)
            reader = csv.reader(f)
            for _ in range(header_row_idx + 1):
                next(reader, None)

            # Update Hospital ID if found in metadata
            if Hospital_Name_From_Meta:
                print(f"  [Meta] Detected Hospital Name: {Hospital_Name_From_Meta}")

            # Prepare Header Map
            header_map = {h.strip(): i for i, h in enumerate(headers)}
            
            col_desc = header_map.get('description')
            col_code = header_map.get('code|1') or header_map.get('code')
            col_code_type = header_map.get('code|1|type') or header_map.get('code_type')
            col_setting = header_map.get('setting')

            # --- DYNAMIC PRICE COLUMN DETECTION ---
            col_payer_generic = header_map.get('payer_name')
            col_plan_generic = header_map.get('plan_name')
            col_price_generic = header_map.get('standard_charge|negotiated_dollar')

            # Identify "Wide" columns (CMS format: standard_charge | payer | plan | type)
            wide_price_cols = []
            for h, idx in header_map.items():
                parts = h.split('|')
                
                # Check for standard_charge | ... | negotiated_dollar
                if len(parts) >= 2 and parts[0] == 'standard_charge':
                    last_part = parts[-1] 
                    if last_part == 'negotiated_dollar':
                        # Generic column with no payer embedded (e.g. standard_charge|negotiated_dollar)
                        if len(parts) == 2:
                            payer = 'Negotiated Dollar'
                            plan = 'Negotiated Dollar'
                        # Try to extract payer/plan from middle parts
                        elif len(parts) == 4:
                            payer = normalize_payer_name(parts[1])
                            plan = normalize_payer_name(parts[2])
                        elif len(parts) == 3:
                            payer = normalize_payer_name(parts[1])
                            plan = "Standard"
                        else:
                            # Fallback for complex headers
                            payer = normalize_payer_name(parts[1])
                            plan = " / ".join(normalize_payer_name(p) for p in parts[2:-1])
                        
                        wide_price_cols.append((idx, payer, plan))

                    elif h == 'standard_charge|discounted_cash':
                         wide_price_cols.append((idx, 'Cash', 'Discounted Cash'))
                    elif h == 'standard_charge|gross':
                         wide_price_cols.append((idx, 'Gross', 'Gross Charge'))

            is_wide_format = len(wide_price_cols) > 0
            if is_wide_format:
                print(f"  Detected Wide/CMS Format with {len(wide_price_cols)} price columns.")

            # --- ID/Name Source Logic ---
            final_h_name = Hospital_Name_From_Meta if Hospital_Name_From_Meta else "Unknown Hospital"
            final_h_id = generate_id([final_h_name])
            final_h_name = clean_hospital_name(final_h_name)

            records_processed = 0
            MAX_RECORDS = 999999999

            for row in tqdm(reader, desc=f"Parsing {final_h_name}", unit="rows"):
                if records_processed >= MAX_RECORDS:
                    break

                if not row or len(row) < 3: # Basic sanity check for empty lines
                    continue
                
                # Safe description extraction
                description = row[col_desc] if col_desc is not None and col_desc < len(row) else "Unknown"
                
                # Extract all codes from all code|N / code|N|type columns
                all_codes = []
                primary_code = ""
                primary_code_type = ""
                found_primary = False
                
                for i in range(1, 7):
                    c_key = f"code|{i}"
                    t_key = f"code|{i}|type"
                    
                    if c_key in header_map and t_key in header_map:
                        idx_c = header_map[c_key]
                        idx_t = header_map[t_key]
                        
                        if idx_c < len(row) and idx_t < len(row):
                            c_val = row[idx_c].strip()
                            t_val = row[idx_t].strip().upper()
                            
                            if not c_val:
                                continue
                            
                            all_codes.append({"value": c_val, "type": t_val})
                            
                            if not found_primary:
                                if t_val in ("CPT", "HCPCS"):
                                    primary_code = c_val; primary_code_type = t_val; found_primary = True
                                elif "DRG" in t_val:
                                    primary_code = c_val; primary_code_type = t_val; found_primary = True

                if not primary_code:
                    primary_code = row[col_code] if col_code is not None and col_code < len(row) else ""
                    primary_code_type = row[col_code_type] if col_code_type is not None and col_code_type < len(row) else ""
                    if primary_code and primary_code_type:
                        all_codes.insert(0, {"value": primary_code, "type": primary_code_type.strip().upper()})

                # Promote frequently-queried code types to flat fields
                flat_codes = {}
                _type_to_field = {
                    "MS-DRG": "ms_drg", "DRG": "ms_drg",
                    "APR-DRG": "apr_drg", "TRIS-DRG": "apr_drg",
                    "RC": "rc",
                    "APC": "apc",
                    "NDC": "ndc",
                    "CDM": "cdm",
                }
                for c in all_codes:
                    field = _type_to_field.get(c["type"])
                    if field and field not in flat_codes:
                        flat_codes[field] = c["value"]

                # --- EXTRACT PRICES FOR THIS ROW ---
                row_prices = [] # (price_val, payer_name, plan_name, setting_val)
                setting_val = row[col_setting] if col_setting is not None and col_setting < len(row) else "Unknown"

                if is_wide_format:
                    for idx, payer, plan in wide_price_cols:
                        if idx < len(row):
                            p_val = parse_currency(row[idx])
                            if p_val is not None:
                                row_prices.append((p_val, payer, plan, setting_val))
                else:
                    # Generic / Tall format
                    price = parse_currency(row[col_price_generic]) if col_price_generic is not None and col_price_generic < len(row) else None
                    if price is not None:
                        # Ensure payer/plan are strings
                        payer = normalize_payer_name(row[col_payer_generic]) if col_payer_generic is not None and col_payer_generic < len(row) else "Unknown"
                        plan = normalize_payer_name(row[col_plan_generic]) if col_plan_generic is not None and col_plan_generic < len(row) else "Unknown"
                        # setting_val already extracted
                        row_prices.append((price, payer, plan, setting_val))

                if not row_prices:
                    continue

                # Apply shoppable-only filter if requested
                if shoppable_codes is not None:
                    row_code_values = {c['value'] for c in all_codes}
                    if primary_code:
                        row_code_values.add(primary_code)
                    if not row_code_values.intersection(shoppable_codes):
                        continue

                records_processed += 1

                # Create Group Key
                if primary_code:
                    prefix = primary_code_type if primary_code_type else "CODE"
                    group_key = f"{prefix}_{primary_code}"
                    is_standard_group = True
                else:
                    group_key = description
                    is_standard_group = False

                # --- Splitting Logic ---
                if group_key not in active_group_tracker:
                    active_group_tracker[group_key] = {
                        'current_doc_id': generate_id([group_key]),
                        'part_count': 0
                    }
                
                tracker = active_group_tracker[group_key]
                current_doc_id = tracker['current_doc_id']

                if current_doc_id not in procedures_map:
                    procedures_map[current_doc_id] = {
                        'id': current_doc_id,
                        'is_standard_group': is_standard_group,
                        'group_key': group_key,
                        'description': description,
                        'code': primary_code,
                        'code_type': primary_code_type,
                        **flat_codes,
                        'codes': all_codes,
                        'prices': [], 
                        'price_values': []
                    }
                else:
                    # Merge flat code fields if not yet set
                    for field, val in flat_codes.items():
                        if not procedures_map[current_doc_id].get(field):
                            procedures_map[current_doc_id][field] = val
                    # Merge any new code entries not already present
                    existing_codes = {(c['value'], c['type']) for c in procedures_map[current_doc_id].get('codes', [])}
                    for c in all_codes:
                        if (c['value'], c['type']) not in existing_codes:
                            procedures_map[current_doc_id].setdefault('codes', []).append(c)
                            existing_codes.add((c['value'], c['type']))

                # Check for Overflow
                if len(procedures_map[current_doc_id]['prices']) + len(row_prices) >= LIMIT_PER_DOC:
                    if tracker['part_count'] == 0:
                        # Only log once per group to keep noise down
                        pass 

                    tracker['part_count'] += 1
                    new_doc_id = f"{generate_id([group_key])}_{tracker['part_count']}"
                    tracker['current_doc_id'] = new_doc_id
                    current_doc_id = new_doc_id
                    
                    procedures_map[current_doc_id] = {
                        'id': current_doc_id,
                        'is_standard_group': is_standard_group,
                        'group_key': group_key,
                        'description': description + f" (Part {tracker['part_count'] + 1})", 
                        'code': primary_code,
                        'code_type': primary_code_type,
                        **flat_codes,
                        'codes': all_codes,
                        'prices': [], 
                        'price_values': [] 
                    }
                
                for p_val, p_payer, p_plan, p_setting in row_prices:
                    price_record = {
                        'hospital_id': final_h_id,
                        'hospital_name': final_h_name,
                        'payer_name': p_payer,
                        'plan_name': p_plan,
                        'setting': p_setting,
                        'price': p_val
                    }

                    procedures_map[current_doc_id]['prices'].append(price_record)
                    procedures_map[current_doc_id]['price_values'].append(p_val)

    except Exception as e:
        print(f"Error parsing {csv_path}: {e}")
        import traceback
        traceback.print_exc()
    
    return records_processed

def main():
    parser = argparse.ArgumentParser(description='Load hospital price data into Elasticsearch')
    parser.add_argument('--input_file', type=str, help='Path to a specific CSV file to process')
    parser.add_argument('--mock', action='store_true', help='Skip indexing and print parsed records instead')
    parser.add_argument('--clean', action='store_true', help='Delete existing index before adding data')
    parser.add_argument('--shoppable-only', action='store_true',
                        help='Only index the ~70 CMS shoppable services (reference/shoppable_codes.csv). '
                             'Reduces index size by ~97%% for large hospital files.')
    parser.add_argument('--cached-file', type=str, metavar='PATH',
                        help='Load from a pre-built shoppable_cache.json.gz (created by extract_shoppable.py). '
                             'Skips all CSV parsing.')
    args = parser.parse_args()

    try:
        es_params = {'hosts': ES_URL}
        if ES_USER and ES_PASS:
            es_params['basic_auth'] = (ES_USER, ES_PASS)
        
        if ES_URL.startswith('https'):
             es_params['verify_certs'] = False
             es_params['ssl_show_warn'] = False

        es = Elasticsearch(**es_params)

        if not args.mock:
            if not es.ping():
                print(f"Could not connect to Elasticsearch at {ES_URL}")
                return
            
            # 1. Clean/Init Index
            create_index(es, clean=args.clean)
        else:
            print("[MOCK MODE] Skipping connection check and index creation.")

        # 2. Parse All CSVs (or load from cache)
        final_procedures = []

        if args.cached_file:
            # Fast path: load pre-extracted shoppable cache
            cache_path = args.cached_file
            if not os.path.exists(cache_path):
                print(f"ERROR: Cached file not found: {cache_path}")
                return
            print(f"Loading from cache: {cache_path} ...")
            with gzip.open(cache_path, 'rt', encoding='utf-8') as f:
                final_procedures = json.load(f)
            print(f"Loaded {len(final_procedures)} documents from cache.")
            # Normalize payer names in cached data (cache may pre-date normalization)
            for doc in final_procedures:
                for price in doc.get('prices', []):
                    if 'payer_name' in price:
                        price['payer_name'] = normalize_payer_name(price['payer_name'])
                    if 'plan_name' in price:
                        price['plan_name'] = normalize_payer_name(price['plan_name'])
        else:
            procedures_map = {}
            active_group_tracker = {}  # Global tracker for splits
            files_to_process = []

            shoppable_codes = None
            if args.shoppable_only:
                shoppable_codes = load_shoppable_codes()
                if not shoppable_codes:
                    print("ERROR: Could not load shoppable codes. Aborting.")
                    return

            if args.input_file:
                if os.path.exists(args.input_file):
                    files_to_process.append(args.input_file)
                else:
                    print(f"Input file not found: {args.input_file}")
                    return
            elif os.path.exists(DATA_DIR):
                for filename in os.listdir(DATA_DIR):
                    if filename.lower().endswith(".csv"):
                        files_to_process.append(os.path.join(DATA_DIR, filename))
            else:
                print(f"Data directory not found: {DATA_DIR}")
                return

            if not files_to_process:
                print("No CSV files found to process.")
                return

            total_records = 0
            for csv_path in files_to_process:
                count = parse_csv_into_map(csv_path, procedures_map, active_group_tracker, shoppable_codes)
                print(f"  > File '{os.path.basename(csv_path)}' -> {count} records extracted.")
                total_records += count

            print(f"Finished parsing. Found {len(procedures_map)} unique procedure group documents from {total_records} total price records.")

            # Calculate Stats
            for pid, data in procedures_map.items():
                values = data.pop('price_values')
                if values:
                    data['stats'] = {
                        'min': min(values),
                        'max': max(values),
                        'avg': round(sum(values) / len(values), 2),
                        'count': len(values)
                    }
                final_procedures.append(data)

        # 3. Bulk Index
        if args.mock:
            print("\n[MOCK MODE] Documents that would be indexed:")
            print("-" * 50)
            # Print first 3 documents as sample
            sample_count = 3
            for i, doc in enumerate(final_procedures[:sample_count]):
                print(f"Document {i+1}:")
                print(json.dumps(doc, indent=2))
                print("-" * 50)
            print(f"... and {len(final_procedures) - sample_count} more documents.")
        else:
            print("Indexing documents...")

            # Speed up indexing: disable refresh and replicas during bulk load
            es.indices.put_settings(index=INDEX_NAME, body={
                "index": {
                    "refresh_interval": "-1",
                    "number_of_replicas": 0
                }
            })

            success = 0
            failed = []
            try:
                actions = generate_actions(tqdm(final_procedures, desc="Indexing", unit="docs"))
                for ok, result in parallel_bulk(
                    es,
                    actions,
                    thread_count=4,
                    chunk_size=500,
                    max_chunk_bytes=20 * 1024 * 1024,
                    raise_on_error=False,
                ):
                    if ok:
                        success += 1
                    else:
                        failed.append(result)
            finally:
                # Always restore refresh — ignore errors if connection dropped after bulk load
                try:
                    es.indices.put_settings(index=INDEX_NAME, body={
                        "index": {
                            "refresh_interval": "1s",
                            "number_of_replicas": 0
                        }
                    })
                    es.indices.refresh(index=INDEX_NAME)
                    print("Index refreshed.")
                except Exception as refresh_err:
                    print(f"Warning: could not restore index settings after indexing ({refresh_err}). "
                          "Data was indexed successfully — run: "
                          f"curl -X POST {ES_URL}/{INDEX_NAME}/_refresh to refresh manually.")

            print(f"Successfully indexed {success} documents.")
            if failed:
                print(f"Failed to index {len(failed)} documents.")
                print(f"Sample failure: {failed[0]}")

    except Exception as e:
        print(f"Operation failed: {e}")
        return


if __name__ == "__main__":
    main()
