"""
Script: load_to_sqlite.py

Description:
      This script replaces load_to_es.py. It initializes a relational SQLite schema
      inside db.sqlite3, indexes all hospital pricing records, configures an FTS5 
      stemmed virtual table, and extracts a unique vocabulary for typo auto-correction.

Usage:
      python load_to_sqlite.py [--input_file DATA_PATH] [--shoppable-only] [--cached-file PATH]
"""
import sqlite3
import csv
import gzip
import hashlib
import json
import os
import re
import sys
import argparse
import django
from tqdm import tqdm

# Raise CSV field size limits for oversized hospital sheets
csv.field_size_limit(min(sys.maxsize, 2 ** 31 - 1))

# Setup Django environment
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.conf import settings

# Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = str(settings.DATABASES['default']['NAME'])
DATA_DIR = os.path.join(BASE_DIR, 'data')
REFERENCE_DIR = os.path.join(BASE_DIR, 'reference')

print(f"Configuration:")
print(f"  DB_PATH: {DB_PATH}")
print(f"  DATA_DIR: {DATA_DIR}")

def init_db(conn, clean=False):
    """Creates the SQLite schema with relational and FTS5 tables."""
    cursor = conn.cursor()
    
    if clean:
        print("Cleaning existing database tables...")
        cursor.execute("DROP TABLE IF EXISTS fts_procedures;")
        cursor.execute("DROP TABLE IF EXISTS prices;")
        cursor.execute("DROP TABLE IF EXISTS procedure_codes;")
        cursor.execute("DROP TABLE IF EXISTS unique_words;")
        cursor.execute("DROP TABLE IF EXISTS synonyms;")
        cursor.execute("DROP TABLE IF EXISTS procedures;")
        conn.commit()

    # Create tables
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS procedures (
        id TEXT PRIMARY KEY,
        is_standard_group INTEGER,
        group_key TEXT,
        description TEXT,
        code TEXT,
        code_type TEXT,
        ms_drg TEXT,
        apr_drg TEXT,
        rc TEXT,
        apc TEXT,
        ndc TEXT,
        cdm TEXT,
        stats_min REAL,
        stats_max REAL,
        stats_avg REAL,
        stats_count INTEGER
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS procedure_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        procedure_id TEXT,
        code_value TEXT,
        code_type TEXT,
        FOREIGN KEY (procedure_id) REFERENCES procedures(id) ON DELETE CASCADE
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS prices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        procedure_id TEXT,
        hospital_id TEXT,
        hospital_name TEXT,
        payer_name TEXT,
        plan_name TEXT,
        setting TEXT,
        price REAL,
        FOREIGN KEY (procedure_id) REFERENCES procedures(id) ON DELETE CASCADE
    );
    """)

    # FTS5 Virtual Table for searching with Porter stemming
    cursor.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS fts_procedures USING fts5(
        procedure_id UNINDEXED,
        description,
        code,
        code_type,
        ms_drg,
        apr_drg,
        rc,
        apc,
        ndc,
        cdm,
        tokenize = 'porter'
    );
    """)

    # Unique vocabulary words for typo auto-correction
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS unique_words (
        word TEXT PRIMARY KEY
    );
    """)

    # Synonyms table for mapping consumer keywords
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS synonyms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phrase TEXT UNIQUE NOT NULL,
        expansions TEXT NOT NULL
    );
    """)

    # Relational search performance indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_prices_procedure_id ON prices(procedure_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_procedure_codes_procedure_id ON procedure_codes(procedure_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_prices_proc_payer ON prices(procedure_id, payer_name);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_prices_proc_hosp ON prices(procedure_id, hospital_id);")

    conn.commit()
    print("Database tables initialized successfully.")


def normalize_payer_name(name):
    """Normalize payer name to deduplicate variants."""
    if not name:
        return name
    name = name.replace('_', ' ')
    name = re.sub(r'\s*&\s*', ' and ', name)
    name = re.sub(r'\s+', ' ', name).strip()
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
    combined = "".join([str(p).strip().lower() for p in text_parts if p])
    return hashlib.md5(combined.encode('utf-8')).hexdigest()


def extract_vocabulary_words(description):
    """Extract individual clean alphanumeric words from descriptions."""
    if not description:
        return []
    # Replace punctuation and special characters with spaces, keep letters and digits
    clean_text = re.sub(r'[^\w\s-]', ' ', description.lower())
    # Split, clean, filter out purely numeric values or very short strings (length < 3)
    words = []
    for w in clean_text.split():
        w = w.strip()
        # Keep words that are not purely digits and length >= 3
        if w and not w.isdigit() and len(w) >= 3:
            words.append(w)
    return list(set(words))


def load_shoppable_codes(csv_path=None):
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


def clean_hospital_name(raw_name):
    if not raw_name:
        return "Unknown Hospital"
    s = re.sub(r'^\d+[\W_]+', '', raw_name)
    s = s.replace('_', ' ').replace('-', ' ').replace('.', ' ')
    s = re.sub(r'\s+', ' ', s).strip()
    return s.title()


def parse_csv_into_map(csv_path, procedures_map, active_group_tracker, shoppable_codes=None):
    print(f"Parsing {os.path.basename(csv_path)}{'  [shoppable-only filter active]' if shoppable_codes else ''}...")
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
                if not line: break
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
                if not row: continue
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
                        if len(parts) == 2:
                            payer = 'Negotiated Dollar'
                            plan = 'Negotiated Dollar'
                        elif len(parts) == 4:
                            payer = normalize_payer_name(parts[1])
                            plan = normalize_payer_name(parts[2])
                        elif len(parts) == 3:
                            payer = normalize_payer_name(parts[1])
                            plan = "Standard"
                        else:
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
                                    primary_code = c_val; primary_code_type = t_val; found_primary = True
                                elif "DRG" in t_val:
                                    primary_code = c_val; primary_code_type = t_val; found_primary = True

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

                row_prices = []
                setting_val = row[col_setting] if col_setting is not None and col_setting < len(row) else "Unknown"

                if is_wide_format:
                    for idx, payer, plan in wide_price_cols:
                        if idx < len(row):
                            p_val = parse_currency(row[idx])
                            if p_val is not None:
                                row_prices.append((p_val, payer, plan, setting_val))
                else:
                    price = parse_currency(row[col_price_generic]) if col_price_generic is not None and col_price_generic < len(row) else None
                    if price is not None:
                        payer = normalize_payer_name(row[col_payer_generic]) if col_payer_generic is not None and col_payer_generic < len(row) else "Unknown"
                        plan = normalize_payer_name(row[col_plan_generic]) if col_plan_generic is not None and col_plan_generic < len(row) else "Unknown"
                        row_prices.append((price, payer, plan, setting_val))

                if not row_prices:
                    continue

                if shoppable_codes is not None:
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
    
    return records_processed


def save_to_sqlite(conn, final_procedures):
    """Inserts processed procedures, codes, prices, FTS, and vocabulary words into SQLite."""
    print("Beginning SQLite bulk inserts...")
    cursor = conn.cursor()
    
    # Disable synchronous writes & configure journaling for speed during index builds
    cursor.execute("PRAGMA synchronous = OFF;")
    cursor.execute("PRAGMA journal_mode = MEMORY;")

    procedure_rows = []
    code_rows = []
    price_rows = []
    fts_rows = []
    unique_words = set()

    for doc in tqdm(final_procedures, desc="Preparing DB rows"):
        pid = doc['id']
        stats = doc.get('stats', {})
        
        # 1. Main Procedure Row
        procedure_rows.append((
            pid,
            1 if doc.get('is_standard_group') else 0,
            doc.get('group_key'),
            doc.get('description'),
            doc.get('code'),
            doc.get('code_type'),
            doc.get('ms_drg'),
            doc.get('apr_drg'),
            doc.get('rc'),
            doc.get('apc'),
            doc.get('ndc'),
            doc.get('cdm'),
            stats.get('min'),
            stats.get('max'),
            stats.get('avg'),
            stats.get('count')
        ))

        # 2. Auxiliary Code Rows
        for code_record in doc.get('codes', []):
            code_rows.append((pid, code_record.get('value'), code_record.get('type')))

        # 3. Hospital Pricing Rows
        for price_record in doc.get('prices', []):
            price_rows.append((
                pid,
                price_record.get('hospital_id'),
                price_record.get('hospital_name'),
                price_record.get('payer_name'),
                price_record.get('plan_name'),
                price_record.get('setting'),
                price_record.get('price')
            ))

        # 4. FTS Virtual Table Row
        fts_rows.append((
            pid,
            doc.get('description'),
            doc.get('code'),
            doc.get('code_type'),
            doc.get('ms_drg'),
            doc.get('apr_drg'),
            doc.get('rc'),
            doc.get('apc'),
            doc.get('ndc'),
            doc.get('cdm')
        ))

        # 5. Vocabulary Words (for spelling suggestions)
        words = extract_vocabulary_words(doc.get('description'))
        # Also extract words from the code itself if it has letters
        if doc.get('code'):
            words.extend(extract_vocabulary_words(doc.get('code')))
        
        for w in words:
            unique_words.add(w)

    # Database writes in a single unified transaction
    try:
        print(f"Inserting {len(procedure_rows)} procedures...")
        cursor.executemany("""
            INSERT OR REPLACE INTO procedures (
                id, is_standard_group, group_key, description, code, code_type,
                ms_drg, apr_drg, rc, apc, ndc, cdm, stats_min, stats_max, stats_avg, stats_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, procedure_rows)

        print(f"Inserting {len(code_rows)} auxiliary code associations...")
        cursor.executemany("""
            INSERT INTO procedure_codes (procedure_id, code_value, code_type)
            VALUES (?, ?, ?)
        """, code_rows)

        print(f"Inserting {len(price_rows)} prices...")
        cursor.executemany("""
            INSERT INTO prices (procedure_id, hospital_id, hospital_name, payer_name, plan_name, setting, price)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, price_rows)

        print(f"Populating FTS5 Virtual table...")
        cursor.executemany("""
            INSERT INTO fts_procedures (
                procedure_id, description, code, code_type, ms_drg, apr_drg, rc, apc, ndc, cdm
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, fts_rows)

        # 5. Enrichment of spelling vocabulary (clinical terms & default synonyms)
        standard_clinical_words = {
            'radiography', 'radiological', 'radiologic', 'ultrasound', 'echography', 
            'sonogram', 'electrocardiogram', 'electroencephalogram', 'tomography', 
            'magnetic', 'resonance', 'imaging', 'colonoscopy', 'mammography', 
            'mammogram', 'ligation', 'prosthesis', 'ligament', 'arthroplasty', 
            'cesarean', 'obstetrical', 'vaginal', 'immunotherapy', 'allergen', 
            'antigen', 'injection', 'venipuncture', 'metabolic', 'preventive', 
            'emergency', 'orthopedic', 'cruciate', 'anterior', 'acl', 'mri', 'ct', 
            'cbc', 'ekg', 'ecg', 'eeg', 'cardiac', 'neurological', 'pediatric', 
            'obstetrics', 'gynecology', 'therapy', 'rehabilitation', 'clinical', 
            'laboratory', 'pathology', 'radiology', 'oncology', 'anesthesia', 
            'surgical', 'outpatient', 'inpatient', 'emergency', 'shoppable', 
            'transparency', 'allergy', 'vials', 'vial', 'shots', 'shot', 'testing', 
            'tests', 'test', 'scratch', 'apnea', 'sleep', 'tubal', 'penile', 
            'implant', 'tendon', 'repair', 'knee', 'replacement', 'vessel', 
            'angiography', 'pulmonary', 'coronary', 'renal', 'hepatic', 'cerebral'
        }
        for w in standard_clinical_words:
            unique_words.add(w)

        try:
            synonyms_path = os.path.join(REFERENCE_DIR, 'default_synonyms.json')
            if os.path.exists(synonyms_path):
                with open(synonyms_path, 'r', encoding='utf-8') as f:
                    synonyms_data = json.load(f)
                for phrase, expansions in synonyms_data.items():
                    for word in extract_vocabulary_words(phrase):
                        unique_words.add(word)
                    for exp in expansions:
                        clean_exp = exp.replace('"', '').replace('(', '').replace(')', '').replace('*', '')
                        for word in extract_vocabulary_words(clean_exp):
                            unique_words.add(word)
                print(f"Enriched spelling vocabulary with default_synonyms.json terms.")
        except Exception as e:
            print(f"Warning: Could not enrich spelling vocabulary from default synonyms: {e}")

        print(f"Indexing {len(unique_words)} words in spelling vocabulary...")
        vocab_rows = [(w,) for w in unique_words]
        cursor.executemany("INSERT OR IGNORE INTO unique_words (word) VALUES (?)", vocab_rows)

        conn.commit()
        print("SQLite Database successfully populated and indexed!")
    except Exception as e:
        conn.rollback()
        print(f"ERROR: SQLite bulk inserts failed, transaction rolled back. Detail: {e}")
        raise e


def main():
    parser = argparse.ArgumentParser(description='Load hospital price data into SQLite')
    parser.add_argument('--input_file', type=str, help='Path to a specific CSV file to process')
    parser.add_argument('--clean', action='store_true', help='Wipe existing SQLite tables first')
    parser.add_argument('--shoppable-only', action='store_true',
                        help='Only index CMS shoppable services')
    parser.add_argument('--cached-file', type=str, metavar='PATH',
                        help='Load from a pre-built shoppable_cache.json.gz (skips CSV parsing)')
    parser.add_argument('--output-db', type=str, help='Custom filename for the output SQLite database (e.g., ca.sqlite3)')
    args = parser.parse_args()

    db_path = DB_PATH
    if args.output_db:
        # Resolve path in the same directory as settings default database
        db_path = os.path.join(os.path.dirname(DB_PATH), args.output_db)

    print(f"Dynamic DB Resolution:")
    print(f"  Target DB Path: {db_path}")

    # Establish local SQLite database connection
    conn = sqlite3.connect(db_path)
    
    try:
        # 1. Initialize tables
        init_db(conn, clean=args.clean)

        final_procedures = []

        # 2. Index from cache or parse CSVs
        if args.cached_file:
            cache_path = args.cached_file
            if not os.path.exists(cache_path):
                print(f"ERROR: Cached file not found: {cache_path}")
                return
            print(f"Loading from cache: {cache_path} ...")
            with gzip.open(cache_path, 'rt', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        doc = json.loads(line)
                        final_procedures.append(doc)
            print(f"Loaded {len(final_procedures)} documents from cache.")
            
            # Payer normalizations
            for doc in final_procedures:
                for price in doc.get('prices', []):
                    if 'payer_name' in price:
                        price['payer_name'] = normalize_payer_name(price['payer_name'])
                    if 'plan_name' in price:
                        price['plan_name'] = normalize_payer_name(price['plan_name'])
        else:
            procedures_map = {}
            active_group_tracker = {}
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

            print(f"Finished parsing. Found {len(procedures_map)} unique procedure group documents.")

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

        # 3. Write data to SQLite
        if final_procedures:
            save_to_sqlite(conn, final_procedures)
        else:
            print("No records available to save to SQLite.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
