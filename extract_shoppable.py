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
import functools
import zipfile
import io
from collections import Counter

# Some hospital CSVs embed very long compliance attestation text in their
# header rows (e.g. South_Campus_Surgery_Center.csv). Raise the limit to
# the largest safe value on Windows (2^31-1) and sys.maxsize on Unix.
csv.field_size_limit(min(sys.maxsize, 2 ** 31 - 1))
from tqdm import tqdm

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
REFERENCE_DIR = os.path.join(BASE_DIR, 'reference')

# doc id -> set of price tuples already stored, to drop exact repeats (spans all files,
# since one procedure document collects prices from many hospitals)
seen_prices = {}

# doc id -> set of (hospital id, description) already tallied
seen_descriptions = {}
# doc id -> Counter of descriptions (one count per hospital) / of their words
description_counts = {}
word_counts = {}
# Most common words kept as a document's hidden 'search_terms'
MAX_SEARCH_WORDS = 400
CPT_FAMILY = {'CPT', 'HCPCS', 'CPT/HCPCS'}


def code_family(code_type):
    """CPT and HCPCS (and an unlabelled code) are one family; MS-DRG and DRG are one family."""
    t = (code_type or '').strip().upper()
    if t in CPT_FAMILY or not t:
        return 'CPT'
    if t in ('MS-DRG', 'DRG'):
        return 'MS-DRG'
    return t


def code_key(code_type, code):
    """(family, normalized code). Numeric CPT codes are zero-padded to 5 digits: spreadsheets
    strip leading zeros, and the shoppable list itself has anesthesia codes like '192' for 00192."""
    family = code_family(code_type)
    code = (code or '').strip().upper()
    if family == 'CPT' and code.isdigit() and len(code) < 5:
        code = code.zfill(5)
    return family, code
# (code family, code) -> plain-language title from reference/shoppable_codes.csv
standard_titles = {}


def load_standard_titles(csv_path=None):
    """Titles by (family, code). The CPT-labelled row wins over the HCPCS one (shorter, closer to
    what people type); placeholder 'Unknown' rows are skipped."""
    csv_path = csv_path or os.path.join(REFERENCE_DIR, 'shoppable_codes.csv')
    titles = {}
    if not os.path.exists(csv_path):
        return titles
    with open(csv_path, 'r', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            code, c_type, desc = row['code'].strip().upper(), row['code_type'].strip().upper(), row['description'].strip()
            if not code or not desc or desc.lower() == 'unknown':
                continue
            key = code_key(c_type, code)
            if c_type == 'CPT':
                titles[key] = desc
            else:
                titles.setdefault(key, desc)
    return titles


@functools.lru_cache(maxsize=500_000)
def description_words(description):
    """Lowercase alphanumeric words (2+ chars) of a description, cached since rows repeat wording."""
    return tuple(dict.fromkeys(re.findall(r'[a-z0-9]{2,}', description.lower())))


# Standard CMS column names, matched case-insensitively
CMS_FIXED_COLUMNS = {
    'description', 'code', 'code_type', 'setting', 'modifiers', 'payer_name', 'plan_name',
    'standard_charge|negotiated_dollar', 'standard_charge|gross', 'standard_charge|discounted_cash',
}
# doc id -> set of (code, type) already in the document's 'codes' list
seen_codes = {}


def load_shoppable_codes(csv_path=None):
    """Load the CMS shoppable services code list as a set of (code family, code)."""
    if csv_path is None:
        csv_path = os.path.join(REFERENCE_DIR, 'shoppable_codes.csv')
    codes = set()
    if not os.path.exists(csv_path):
        print(f"ERROR: Shoppable codes file not found at {csv_path}")
        return codes
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            codes.add(code_key(row['code_type'], row['code']))
    print(f"Loaded {len(codes)} shoppable codes from {os.path.basename(csv_path)}")
    return codes


# Lines a hospital marks as cash-only, e.g. IU Health's "SCREEN HEART CALCIUM CASH ONLY"
CASH_ONLY_RE = re.compile(r'\b(cash|self[\s-]*pay)[\s-]*only\b', re.IGNORECASE)
cash_only_rows = Counter()


def cash_only_prices(description, row_prices, label=''):
    """For a cash-only line, the flat gross charge is the cash price. Some files still run it
    through the general self-pay discount and list payer rates (IU Health: $49 scan shown as
    $12.06 cash), so keep only the gross charge, as both Cash and Gross. Rows with no gross
    charge are left as published."""
    if not CASH_ONLY_RE.search(description or ''):
        return row_prices
    gross = [p for p in row_prices if p[1] == 'Gross']
    if not gross:
        return row_prices
    cash_only_rows[label] += 1
    price, _, _, setting = gross[0]
    return [(price, 'Cash', 'Discounted Cash', setting), (price, 'Gross', 'Gross Charge', setting)]


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


def parse_csv_into_map(stream, label, procedures_map, active_group_tracker, shoppable_codes):
    """Parse a single hospital CSV and merge shoppable rows into procedures_map."""
    print(f"Parsing {label}  [shoppable-only filter active]...")

    shoppable_descriptions = {}
    shoppable_csv_path = os.path.join(REFERENCE_DIR, 'shoppable_codes.csv')
    if os.path.exists(shoppable_csv_path):
        with open(shoppable_csv_path, 'r', encoding='utf-8') as sf:
            s_reader = csv.DictReader(sf)
            for s_row in s_reader:
                s_desc = s_row['description'].strip().lower()
                # Some reference rows have the placeholder description "Unknown"; matching on it
                # would tag every row that lacks a description as shoppable
                if s_desc and s_desc != 'unknown':
                    shoppable_descriptions[s_desc] = (s_row['code'].strip(), s_row['code_type'].strip())

    try:
        with stream as f:
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
            # Some hospitals capitalize the standard CMS columns (IU Health: Payer_Name, Description).
            # Alias only those fixed names; per-payer wide columns keep their original spelling.
            for h, i in list(header_map.items()):
                if h.lower() in CMS_FIXED_COLUMNS or re.fullmatch(r'code\|\d+(\|type)?', h.lower()):
                    header_map.setdefault(h.lower(), i)

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
                            if col_payer_generic is not None:
                                continue
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

                # Tall/hybrid rows carry a payer column AND gross/cash columns (the CMS tall
                # template always has both), so read the payer price first, then any wide columns.
                # Mirrors load_to_sqlite.parse_csv_into_map.
                if col_payer_generic is not None:
                    price = parse_currency(row[col_price_generic]) if col_price_generic is not None and col_price_generic < len(row) else None
                    if price is not None:
                        payer = row[col_payer_generic] if col_payer_generic < len(row) else "Unknown"
                        plan = row[col_plan_generic] if col_plan_generic is not None and col_plan_generic < len(row) else "Unknown"
                        row_prices.append((price, payer, plan, setting_val))
                for idx, payer, plan in wide_price_cols:
                    if idx < len(row):
                        p_val = parse_currency(row[idx])
                        if p_val is not None:
                            row_prices.append((p_val, payer, plan, setting_val))

                if not row_prices:
                    continue

                # Shoppable filter
                # Match code AND code family: hospital chargemaster and revenue codes collide with
                # real codes (CDM 35301 "post surgical care" vs CPT 35301; RC 460 vs MS-DRG 460)
                row_codes = {code_key(c['type'], c['value']) for c in all_codes}
                if primary_code:
                    row_codes.add(code_key(primary_code_type, primary_code))

                is_shoppable = not row_codes.isdisjoint(shoppable_codes)
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
                row_prices = cash_only_prices(description, row_prices, final_h_name)

                records_processed += 1

                if primary_code:
                    # CPT and HCPCS are one code set (the site already shows them as one)
                    group_key = "_".join(code_key(primary_code_type, primary_code))
                    is_standard_group = True
                else:
                    group_key = description
                    is_standard_group = False

                # One document per code. The old split at 5,000 prices (an Elasticsearch document
                # size limit) scattered a code over several documents, so a search matching one of
                # them showed only some hospitals' prices. SQLite stores prices as rows; no limit.
                current_doc_id = active_group_tracker.get(group_key)
                if current_doc_id is None:
                    current_doc_id = active_group_tracker[group_key] = generate_id([group_key])

                if current_doc_id not in procedures_map:
                    procedures_map[current_doc_id] = {
                        'id': current_doc_id,
                        'is_standard_group': is_standard_group,
                        'group_key': group_key,
                        'description': description,
                        'code': code_key(primary_code_type, primary_code)[1] if primary_code else primary_code,
                        'code_type': primary_code_type,
                        **flat_codes,
                        'codes': all_codes,
                        'prices': []
                    }
                else:
                    for field, val in flat_codes.items():
                        if not procedures_map[current_doc_id].get(field):
                            procedures_map[current_doc_id][field] = val
                    # Cached per document: rebuilding this set on every row is quadratic once a
                    # document collects thousands of codes (seen on large system files)
                    existing_codes = seen_codes.get(current_doc_id)
                    if existing_codes is None:
                        existing_codes = seen_codes[current_doc_id] = {
                            (c['value'], c['type']) for c in procedures_map[current_doc_id].get('codes', [])}
                    for c in all_codes:
                        if (c['value'], c['type']) not in existing_codes:
                            procedures_map[current_doc_id].setdefault('codes', []).append(c)
                            existing_codes.add((c['value'], c['type']))

                # Tally each hospital's wording once: the most common wording is the fallback title,
                # and the most common words become hidden search terms, so any hospital's phrasing
                # finds the code (e.g. "knee mri" -> 73721, "MRI scan of leg joint")
                pair = (final_h_id, description)
                seen_desc = seen_descriptions.setdefault(current_doc_id, set())
                if pair not in seen_desc:
                    seen_desc.add(pair)
                    description_counts.setdefault(current_doc_id, Counter())[description] += 1
                    word_counts.setdefault(current_doc_id, Counter()).update(description_words(description))

                # Prices are held as compact tuples of shared strings (~4x less RAM than a dict
                # per price); they're expanded back to dicts one document at a time when saving.
                # Exact repeats are dropped: tall files repeat an item's gross/cash columns on
                # every payer row, and hospitals often list one code several times at one price.
                doc_prices = procedures_map[current_doc_id]['prices']
                seen = seen_prices.setdefault(current_doc_id, set())
                for p_val, p_payer, p_plan, p_setting in row_prices:
                    t = (final_h_id, final_h_name, sys.intern(p_payer),
                         sys.intern(p_plan), sys.intern(p_setting), p_val)
                    if t not in seen:
                        seen.add(t)
                        doc_prices.append(t)

    except Exception as e:
        print(f"Error parsing {label}: {e}")
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
    standard_titles.update(load_standard_titles())

    data_files = [
        os.path.join(args.data_dir, f)
        for f in os.listdir(args.data_dir)
        if f.lower().endswith(('.csv', '.zip'))
    ]

    if not data_files:
        print(f"No files (.csv or .zip) found in {args.data_dir}")
        sys.exit(1)

    print(f"Found {len(data_files)} files (.csv or .zip) to process.")

    procedures_map = {}
    active_group_tracker = {}
    total_records = 0

    for fpath in data_files:
        fname = os.path.basename(fpath)
        if fpath.lower().endswith('.zip'):
            try:
                with zipfile.ZipFile(fpath, 'r') as zf:
                    for name in (n for n in zf.namelist() if n.lower().endswith('.csv')):
                        with zf.open(name) as raw:
                            stream = io.TextIOWrapper(raw, encoding='utf-8', errors='replace', newline='')
                            count = parse_csv_into_map(stream, f"{fname} / {name}", procedures_map, active_group_tracker, shoppable_codes)
                            print(f"  > {fname} / {name} -> {count} shoppable records")
                            total_records += count
            except Exception as e:
                print(f"Error reading zip {fpath}: {e}")
        else:
            try:
                with open(fpath, 'r', encoding='utf-8', errors='replace') as stream:
                    count = parse_csv_into_map(stream, fname, procedures_map, active_group_tracker, shoppable_codes)
                    print(f"  > {fname} -> {count} shoppable records")
                    total_records += count
            except Exception as e:
                print(f"Error reading {fpath}: {e}")

    print(f"\nParsed {total_records} total price records into {len(procedures_map)} procedure documents.")
    if cash_only_rows:
        print(f"Cash-only lines priced at their gross charge: {sum(cash_only_rows.values())} rows")
        for name, n in cash_only_rows.most_common():
            print(f"  {name}: {n}")
    seen_prices.clear()  # only needed while parsing; frees RAM before saving
    seen_codes.clear()
    seen_descriptions.clear()

    def finalize(data):
        """Expand price tuples back to the cache's dict format, pick the title, and add stats."""
        counts = description_counts.pop(data['id'], None)
        words = word_counts.pop(data['id'], None)
        title = None
        if data['is_standard_group']:
            family, _, code = data['group_key'].partition('_')
            title = standard_titles.get((family, code))
        if not title and counts:
            title = counts.most_common(1)[0][0]
        if title:
            data['description'] = title
        data['search_terms'] = ' '.join(w for w, _ in words.most_common(MAX_SEARCH_WORDS)) if words else ''
        values = [t[5] for t in data['prices']]
        data['prices'] = [
            {'hospital_id': h_id, 'hospital_name': h_name, 'payer_name': payer,
             'plan_name': plan, 'setting': setting, 'price': price}
            for h_id, h_name, payer, plan, setting, price in data['prices']
        ]
        if values:
            data['stats'] = {
                'min': min(values),
                'max': max(values),
                'avg': round(sum(values) / len(values), 2),
                'count': len(values)
            }
        return data

    # Save as gzip-compressed NDJSON (one JSON object per line).
    # This format lets check_and_reload.py read docs line-by-line with
    # json.loads(), which is ~10x faster than ijson streaming and uses only
    # one doc of memory at a time (vs loading the full 2.6 GB array).
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    print(f"\nSaving cache to {args.output} ...")
    # Write one document at a time and drop it from memory as we go, so RAM falls while saving.
    # Written to a temp name first so a crash can't leave a truncated-but-valid cache behind.
    tmp_output = args.output + '.tmp'
    doc_count = 0
    with gzip.open(tmp_output, 'wt', encoding='utf-8') as f:
        for pid in list(procedures_map):
            doc = finalize(procedures_map.pop(pid))
            f.write(json.dumps(doc, separators=(',', ':')) + '\n')
            doc_count += 1
    os.replace(tmp_output, args.output)

    raw_size = os.path.getsize(args.output)
    print(f"Done. Cache file size: {raw_size / 1024 / 1024:.2f} MB  ({doc_count} documents)")
    print(f"\nTo load into Elasticsearch, run:")
    print(f"  python load_to_es.py --cached-file \"{args.output}\"")


if __name__ == '__main__':
    main()
