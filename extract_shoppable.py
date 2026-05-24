"""
Script: extract_shoppable.py

Description:
      Reads all hospital CSV files from the data/ directory, filters rows to only those
      matching codes in reference/shoppable_codes.csv, builds the same document structure
      used by load_to_es.py, and saves the result as a gzip-compressed JSON file.

      This produces a small cache file (~few MB vs GB of raw CSVs) that can be stored on
      Google Cloud and loaded by load_to_es.py via --cached-file.

Usage:
      python extract_shoppable.py [--output PATH] [--data-dir PATH]

Arguments:
      --output (str): Path for the output .json.gz file. Defaults to data/shoppable_cache.json.gz
      --data-dir (str): Directory containing hospital CSV files. Defaults to data/

Output:
      A gzip-compressed JSON file containing a list of procedure documents (same shape as
      what load_to_es.py indexes), with stats pre-calculated.
"""
import csv
import ctypes
import gzip
import hashlib
import json
import os
import re
import sys
import argparse

# Some hospital CSVs embed very long compliance attestation text in their
# header rows (e.g. South_Campus_Surgery_Center.csv). Raise the limit to
# the largest safe value on Windows (2^31-1) and sys.maxsize on Unix.
csv.field_size_limit(min(sys.maxsize, 2 ** 31 - 1))
from tqdm import tqdm

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
REFERENCE_DIR = os.path.join(BASE_DIR, 'reference')


def load_shoppable_codes(csv_path=None):
    """Load the CMS shoppable services code list and return a set of code values."""
    if csv_path is None:
        csv_path = os.path.join(REFERENCE_DIR, 'shoppable_codes.csv')
    codes = set()
    if not os.path.exists(csv_path):
        print(f"ERROR: Shoppable codes file not found at {csv_path}")
        return codes
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            codes.add(row['code'].strip())
    print(f"Loaded {len(codes)} shoppable codes from {os.path.basename(csv_path)}")
    return codes


def parse_currency(value):
    if not value or value.strip() == '':
        return None
    try:
        return float(value.replace('$', '').replace(',', ''))
    except ValueError:
        return None


def generate_id(text_parts):
    combined = "".join([str(p).strip().lower() for p in text_parts if p])
    return hashlib.md5(combined.encode('utf-8')).hexdigest()


def clean_hospital_name(raw_name):
    if not raw_name:
        return "Unknown Hospital"
    s = re.sub(r'^\d+[\W_]+', '', raw_name)
    s = s.replace('_', ' ').replace('-', ' ').replace('.', ' ')
    s = re.sub(r'\s+', ' ', s).strip()
    return s.title()


def parse_csv_into_map(csv_path, procedures_map, active_group_tracker, shoppable_codes):
    """Parse a single hospital CSV and merge shoppable rows into procedures_map."""
    print(f"Parsing {os.path.basename(csv_path)}  [shoppable-only filter active]...")
    LIMIT_PER_DOC = 5000

    shoppable_descriptions = {}
    shoppable_csv_path = os.path.join(REFERENCE_DIR, 'shoppable_codes.csv')
    if os.path.exists(shoppable_csv_path):
        with open(shoppable_csv_path, 'r', encoding='utf-8') as sf:
            s_reader = csv.DictReader(sf)
            for s_row in s_reader:
                s_desc = s_row['description'].strip().lower()
                shoppable_descriptions[s_desc] = (s_row['code'].strip(), s_row['code_type'].strip())

    try:
        with open(csv_path, 'r', encoding='utf-8', errors='replace') as f:
            sample_lines = []
            for _ in range(10):
                line = f.readline()
                if not line:
                    break
                sample_lines.append(line)

            f.seek(0)
            reader = csv.reader(f)

            header_row_idx = 0
            headers = []
            Hospital_Name_From_Meta = None

            header_keywords = ['description', 'code', 'standard_charge', 'price', 'plan', 'payer']
            max_matches = 0

            temp_reader = csv.reader(sample_lines)
            for idx, row in enumerate(temp_reader):
                if not row:
                    continue
                row_str = " ".join(row).lower()
                matches = sum(1 for k in header_keywords if k in row_str)

                if matches > max_matches and matches >= 2:
                    max_matches = matches
                    header_row_idx = idx
                    headers = row

                    if idx > 0:
                        try:
                            meta_row_1 = next(csv.reader([sample_lines[0]]))
                            meta_row_2 = next(csv.reader([sample_lines[1]])) if len(sample_lines) > 1 else []
                            meta_map = {h.strip().lower(): i for i, h in enumerate(meta_row_1) if len(h) < 200}
                            if "hospital_name" in meta_map and len(meta_row_2) > meta_map["hospital_name"]:
                                Hospital_Name_From_Meta = meta_row_2[meta_map["hospital_name"]]
                            elif len(meta_row_2) > 0 and idx >= 2:
                                Hospital_Name_From_Meta = meta_row_2[0]
                        except Exception:
                            pass

            if not headers:
                f.seek(0)
                headers = next(reader)
                header_row_idx = 0

            f.seek(0)
            reader = csv.reader(f)
            for _ in range(header_row_idx + 1):
                next(reader, None)

            if Hospital_Name_From_Meta:
                print(f"  [Meta] Detected Hospital Name: {Hospital_Name_From_Meta}")

            header_map = {h.strip(): i for i, h in enumerate(headers)}

            col_desc = header_map.get('description')
            col_code = header_map.get('code|1') or header_map.get('code')
            col_code_type = header_map.get('code|1|type') or header_map.get('code_type')
            col_setting = header_map.get('setting')

            col_payer_generic = header_map.get('payer_name')
            col_plan_generic = header_map.get('plan_name')
            col_price_generic = header_map.get('standard_charge|negotiated_dollar')

            wide_price_cols = []
            for h, idx in header_map.items():
                parts = h.split('|')
                if len(parts) >= 2 and parts[0] == 'standard_charge':
                    last_part = parts[-1]
                    if last_part == 'negotiated_dollar':
                        if len(parts) == 4:
                            payer = parts[1]
                            plan = parts[2]
                        elif len(parts) == 3:
                            payer = parts[1]
                            plan = "Standard"
                        else:
                            payer = parts[1]
                            plan = " / ".join(parts[2:-1])
                        wide_price_cols.append((idx, payer, plan))
                    elif h == 'standard_charge|discounted_cash':
                        wide_price_cols.append((idx, 'Cash', 'Discounted Cash'))
                    elif h == 'standard_charge|gross':
                        wide_price_cols.append((idx, 'Gross', 'Gross Charge'))

            is_wide_format = len(wide_price_cols) > 0
            if is_wide_format:
                print(f"  Detected Wide/CMS Format with {len(wide_price_cols)} price columns.")

            final_h_name = Hospital_Name_From_Meta if Hospital_Name_From_Meta else "Unknown Hospital"
            final_h_id = generate_id([final_h_name])
            final_h_name = clean_hospital_name(final_h_name)

            records_processed = 0

            for row in tqdm(reader, desc=f"Parsing {final_h_name}", unit="rows"):
                if not row or len(row) < 3:
                    continue

                description = row[col_desc] if col_desc is not None and col_desc < len(row) else "Unknown"

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
                                    primary_code = c_val
                                    primary_code_type = t_val
                                    found_primary = True
                                elif "DRG" in t_val:
                                    primary_code = c_val
                                    primary_code_type = t_val
                                    found_primary = True

                if not primary_code:
                    primary_code = row[col_code] if col_code is not None and col_code < len(row) else ""
                    primary_code_type = row[col_code_type] if col_code_type is not None and col_code_type < len(row) else ""
                    if primary_code and primary_code_type:
                        all_codes.insert(0, {"value": primary_code, "type": primary_code_type.strip().upper()})

                flat_codes = {}
                _type_to_field = {
                    "MS-DRG": "ms_drg", "DRG": "ms_drg",
                    "APR-DRG": "apr_drg", "TRIS-DRG": "apr_drg",
                    "RC": "rc", "APC": "apc", "NDC": "ndc", "CDM": "cdm",
                }
                for c in all_codes:
                    field = _type_to_field.get(c["type"])
                    if field and field not in flat_codes:
                        flat_codes[field] = c["value"]

                setting_val = row[col_setting] if col_setting is not None and col_setting < len(row) else "Unknown"
                row_prices = []

                if is_wide_format:
                    for idx, payer, plan in wide_price_cols:
                        if idx < len(row):
                            p_val = parse_currency(row[idx])
                            if p_val is not None:
                                row_prices.append((p_val, payer, plan, setting_val))
                else:
                    price = parse_currency(row[col_price_generic]) if col_price_generic is not None and col_price_generic < len(row) else None
                    if price is not None:
                        payer = row[col_payer_generic] if col_payer_generic is not None and col_payer_generic < len(row) else "Unknown"
                        plan = row[col_plan_generic] if col_plan_generic is not None and col_plan_generic < len(row) else "Unknown"
                        row_prices.append((price, payer, plan, setting_val))

                if not row_prices:
                    continue

                # Shoppable filter
                row_code_values = {c['value'] for c in all_codes}
                if primary_code:
                    row_code_values.add(primary_code)
                
                is_shoppable = bool(row_code_values.intersection(shoppable_codes))
                if not is_shoppable:
                    clean_desc = description.strip().lower()
                    if clean_desc in shoppable_descriptions:
                        matched_code, matched_type = shoppable_descriptions[clean_desc]
                        if not primary_code or primary_code_type in ("CDM", "LOCAL"):
                            primary_code = matched_code
                            primary_code_type = matched_type
                            all_codes.insert(0, {"value": matched_code, "type": matched_type})
                        is_shoppable = True
                
                if not is_shoppable:
                    continue

                records_processed += 1

                if primary_code:
                    prefix = primary_code_type if primary_code_type else "CODE"
                    group_key = f"{prefix}_{primary_code}"
                    is_standard_group = True
                else:
                    group_key = description
                    is_standard_group = False

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
                    for field, val in flat_codes.items():
                        if not procedures_map[current_doc_id].get(field):
                            procedures_map[current_doc_id][field] = val
                    existing_codes = {(c['value'], c['type']) for c in procedures_map[current_doc_id].get('codes', [])}
                    for c in all_codes:
                        if (c['value'], c['type']) not in existing_codes:
                            procedures_map[current_doc_id].setdefault('codes', []).append(c)
                            existing_codes.add((c['value'], c['type']))

                if len(procedures_map[current_doc_id]['prices']) + len(row_prices) >= LIMIT_PER_DOC:
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
                    procedures_map[current_doc_id]['prices'].append({
                        'hospital_id': final_h_id,
                        'hospital_name': final_h_name,
                        'payer_name': p_payer,
                        'plan_name': p_plan,
                        'setting': p_setting,
                        'price': p_val
                    })
                    procedures_map[current_doc_id]['price_values'].append(p_val)

    except Exception as e:
        print(f"Error parsing {csv_path}: {e}")
        import traceback
        traceback.print_exc()

    return records_processed


def main():
    parser = argparse.ArgumentParser(
        description='Extract shoppable-code rows from hospital CSVs and save as a compact gzip JSON cache.'
    )
    parser.add_argument(
        '--output', type=str,
        default=os.path.join(DATA_DIR, 'shoppable_cache.json.gz'),
        help='Output path for the .json.gz cache file (default: data/shoppable_cache.json.gz)'
    )
    parser.add_argument(
        '--data-dir', type=str, default=DATA_DIR,
        help='Directory containing hospital CSV files (default: data/)'
    )
    args = parser.parse_args()

    shoppable_codes = load_shoppable_codes()
    if not shoppable_codes:
        print("ERROR: Could not load shoppable codes. Aborting.")
        sys.exit(1)

    csv_files = [
        os.path.join(args.data_dir, f)
        for f in os.listdir(args.data_dir)
        if f.lower().endswith('.csv')
    ]

    if not csv_files:
        print(f"No CSV files found in {args.data_dir}")
        sys.exit(1)

    print(f"Found {len(csv_files)} CSV files to process.")

    procedures_map = {}
    active_group_tracker = {}
    total_records = 0

    for csv_path in csv_files:
        count = parse_csv_into_map(csv_path, procedures_map, active_group_tracker, shoppable_codes)
        print(f"  > {os.path.basename(csv_path)} -> {count} shoppable records")
        total_records += count

    print(f"\nParsed {total_records} total price records into {len(procedures_map)} procedure documents.")

    # Calculate stats (same as load_to_es.py)
    final_procedures = []
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

    # Save as gzip-compressed NDJSON (one JSON object per line).
    # This format lets check_and_reload.py read docs line-by-line with
    # json.loads(), which is ~10x faster than ijson streaming and uses only
    # one doc of memory at a time (vs loading the full 2.6 GB array).
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    print(f"\nSaving cache to {args.output} ...")
    with gzip.open(args.output, 'wt', encoding='utf-8') as f:
        for doc in final_procedures:
            f.write(json.dumps(doc, separators=(',', ':')) + '\n')

    raw_size = os.path.getsize(args.output)
    print(f"Done. Cache file size: {raw_size / 1024 / 1024:.2f} MB  ({len(final_procedures)} documents)")
    print(f"\nTo load into Elasticsearch, run:")
    print(f"  python load_to_es.py --cached-file \"{args.output}\"")


if __name__ == '__main__':
    main()
