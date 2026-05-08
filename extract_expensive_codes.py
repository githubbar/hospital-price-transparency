"""
Script: extract_expensive_codes.py

Scans all Indiana hospital CSV/ZIP files in the data/ directory, collects the
mean negotiated price for every CPT/HCPCS code seen across all hospitals, then
appends the top N% most expensive codes to reference/shoppable_codes.csv
(deduplicating against codes already present).

Usage:
    python extract_expensive_codes.py [--data-dir PATH] [--out PATH] [--percentile N]
"""

import argparse
import csv
import io
import os
import zipfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
REFERENCE_DIR = os.path.join(BASE_DIR, 'reference')
SHOPPABLE_CSV = os.path.join(REFERENCE_DIR, 'shoppable_codes.csv')

CPT_TYPES = frozenset(('CPT', 'HCPCS'))


def parse_currency(value):
    if not value:
        return None
    try:
        return float(value.replace('$', '').replace(',', ''))
    except ValueError:
        return None


def detect_header_row(sample_lines):
    """Return (header_row_index, headers_list)."""
    header_keywords = ['description', 'code', 'standard_charge', 'price', 'plan', 'payer']
    max_matches = 0
    header_row_idx = 0
    headers = []
    for idx, line in enumerate(sample_lines):
        row = next(csv.reader([line]))
        row_str = ' '.join(row).lower()
        matches = sum(1 for k in header_keywords if k in row_str)
        if matches > max_matches and matches >= 2:
            max_matches = matches
            header_row_idx = idx
            headers = row
    return header_row_idx, headers


def scan_stream(stream, label, code_data):
    """
    Scan a text stream and accumulate into code_data.
    code_data[code] = [price_sum, price_count, description]

    Optimizations vs. original:
    - Running sum+count instead of appending to lists (O(1) memory per code)
    - Code/type column pairs precomputed before the row loop
    - ZIP members streamed directly via TextIOWrapper (no temp file)
    - No tqdm per-row overhead
    - Larger read buffer on file open
    """
    sample_lines = []
    for _ in range(10):
        line = stream.readline()
        if not line:
            break
        sample_lines.append(line)

    header_row_idx, headers = detect_header_row(sample_lines)
    if not headers:
        return 0

    header_map = {h.strip(): i for i, h in enumerate(headers)}
    col_desc = header_map.get('description')

    # Precompute (code_col, type_col) pairs — avoid rebuilding strings per row
    code_col_pairs = []
    for i in range(1, 7):
        ci = header_map.get(f'code|{i}')
        ti = header_map.get(f'code|{i}|type')
        if ci is not None and ti is not None:
            code_col_pairs.append((ci, ti))

    fallback_code_col = header_map.get('code|1') or header_map.get('code')
    fallback_type_col = header_map.get('code|1|type') or header_map.get('code_type')

    # Negotiated price columns only (skip gross/cash — they inflate averages)
    wide_price_cols = []
    for h, idx in header_map.items():
        parts = h.split('|')
        if parts[0] == 'standard_charge' and parts[-1] == 'negotiated_dollar':
            wide_price_cols.append(idx)

    col_price_tall = header_map.get('standard_charge|negotiated_dollar')
    is_wide = bool(wide_price_cols)

    # Fall back to cash/gross only when nothing else is available
    if not is_wide and col_price_tall is None:
        cash_col = header_map.get('standard_charge|discounted_cash')
        gross_col = header_map.get('standard_charge|gross')
        if cash_col is not None:
            wide_price_cols = [cash_col]
            is_wide = True
        elif gross_col is not None:
            wide_price_cols = [gross_col]
            is_wide = True

    stream.seek(0)
    reader = csv.reader(stream)
    for _ in range(header_row_idx + 1):
        next(reader, None)

    rows_processed = 0

    for row in reader:
        row_len = len(row)
        if row_len < 3:
            continue

        # Extract CPT/HCPCS codes
        codes = []
        if code_col_pairs:
            for ci, ti in code_col_pairs:
                if ci < row_len and ti < row_len:
                    cv = row[ci]
                    if cv and row[ti].upper() in CPT_TYPES:
                        codes.append(cv.strip())
        if not codes and fallback_code_col is not None and fallback_code_col < row_len:
            cv = row[fallback_code_col].strip()
            if cv:
                tv = row[fallback_type_col].upper() if fallback_type_col and fallback_type_col < row_len else ''
                if not tv or tv in CPT_TYPES:
                    codes.append(cv)

        if not codes:
            continue

        # Gather prices
        price_sum = 0.0
        price_count = 0
        if is_wide:
            for idx in wide_price_cols:
                if idx < row_len:
                    raw = row[idx]
                    if raw:
                        p = parse_currency(raw)
                        if p and p > 0:
                            price_sum += p
                            price_count += 1
        elif col_price_tall is not None and col_price_tall < row_len:
            p = parse_currency(row[col_price_tall])
            if p and p > 0:
                price_sum = p
                price_count = 1

        if price_count == 0:
            continue

        avg = price_sum / price_count
        desc = row[col_desc].strip() if col_desc is not None and col_desc < row_len else ''

        for code in codes:
            entry = code_data.get(code)
            if entry is None:
                code_data[code] = [avg, 1, desc]
            else:
                entry[0] += avg
                entry[1] += 1
                if not entry[2] and desc:
                    entry[2] = desc  # capture first non-empty description seen

        rows_processed += 1

    return rows_processed


def scan_file(path, code_data):
    fname = os.path.basename(path)
    if path.lower().endswith('.zip'):
        try:
            with zipfile.ZipFile(path, 'r') as zf:
                for name in (n for n in zf.namelist() if n.lower().endswith('.csv')):
                    with zf.open(name) as raw:
                        # Stream directly — no temp file extraction
                        stream = io.TextIOWrapper(raw, encoding='utf-8', errors='replace', newline='')
                        n = scan_stream(stream, name, code_data)
                        print(f"  {fname} / {os.path.basename(name)}: {n:,} rows")
        except Exception as e:
            print(f"  Warning: could not open zip {path}: {e}")
    else:
        try:
            with open(path, 'r', encoding='utf-8', errors='replace', buffering=1 << 20) as f:
                n = scan_stream(f, fname, code_data)
                print(f"  {fname}: {n:,} rows")
        except Exception as e:
            print(f"  Warning: error scanning {path}: {e}")


def main():
    parser = argparse.ArgumentParser(description='Extract top-N% most expensive CPT codes from Indiana hospital files')
    parser.add_argument('--data-dir', default=DATA_DIR)
    parser.add_argument('--out', default=SHOPPABLE_CSV)
    parser.add_argument('--percentile', type=float, default=95.0,
                        help='Keep codes at or above this percentile of mean price (default: 95)')
    args = parser.parse_args()

    data_files = [
        os.path.join(args.data_dir, fname)
        for fname in sorted(os.listdir(args.data_dir))
        if fname.lower().endswith(('.csv', '.zip'))
    ]
    print(f"Found {len(data_files)} files in {args.data_dir}\n")

    # code_data[code] = [price_sum, price_count, description]
    code_data = {}
    for fpath in data_files:
        scan_file(fpath, code_data)

    if not code_data:
        print("No codes/prices found. Exiting.")
        return

    print(f"\nCollected data for {len(code_data):,} distinct codes.")

    code_mean = {code: v[0] / v[1] for code, v in code_data.items()}

    all_means = sorted(code_mean.values())
    threshold_idx = min(int(len(all_means) * args.percentile / 100), len(all_means) - 1)
    threshold = all_means[threshold_idx]
    print(f"Percentile {args.percentile}% threshold: ${threshold:,.2f} mean price")

    expensive_codes = {code for code, mean in code_mean.items() if mean >= threshold}
    print(f"Codes at or above threshold: {len(expensive_codes)}")

    # Load existing shoppable codes
    existing_codes = set()
    existing_rows = []
    if os.path.exists(args.out):
        with open(args.out, newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                existing_rows.append(row)
                existing_codes.add(row['code'].strip())
        print(f"Existing codes in {os.path.basename(args.out)}: {len(existing_codes)}")

    new_rows = []
    for code in sorted(expensive_codes):
        if code not in existing_codes:
            mean = code_mean[code]
            desc = code_data[code][2] or code
            new_rows.append({
                'code': code,
                'code_type': 'CPT',
                'description': f'{desc} (mean ${mean:,.0f})',
            })

    print(f"New codes to add: {len(new_rows)}")
    print(f"Total after merge: {len(existing_rows) + len(new_rows)}")

    all_rows = existing_rows + new_rows
    with open(args.out, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['code', 'code_type', 'description'])
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\nUpdated {args.out} with {len(all_rows)} total codes.")


if __name__ == '__main__':
    main()
