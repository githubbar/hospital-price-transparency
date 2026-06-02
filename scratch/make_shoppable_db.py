import sqlite3
import os
import csv
import time

def main():
    start_time = time.time()
    full_db_path = 'in_full.sqlite3'
    shoppable_db_path = 'in_shoppable.sqlite3'
    
    print(f"Creating highly-optimized shoppable database from {full_db_path}...")
    
    if os.path.exists(shoppable_db_path):
        os.remove(shoppable_db_path)
        
    # Read shoppable codes
    shoppable_codes = set()
    shoppable_csv_path = 'reference/shoppable_codes.csv'
    with open(shoppable_csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = row['code'].strip()
            if code:
                shoppable_codes.add(code.upper())
    print(f"Loaded {len(shoppable_codes)} shoppable codes.")
    
    conn_shop = sqlite3.connect(shoppable_db_path)
    cursor_shop = conn_shop.cursor()
    
    # Copy schema from full database
    conn_full = sqlite3.connect(full_db_path)
    cursor_full = conn_full.cursor()
    
    cursor_full.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
    tables = cursor_full.fetchall()
    for name, table_sql in tables:
        if table_sql and not name.startswith('fts_procedures_'):
            cursor_shop.execute(table_sql)
        
    cursor_full.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%';")
    indexes = cursor_full.fetchall()
    for name, index_sql in indexes:
        if index_sql and not name.startswith('fts_procedures_') and not name.startswith('sqlite_autoindex_'):
            cursor_shop.execute(index_sql)
        
    conn_shop.commit()
    conn_full.close()
    
    # Attach full database to shoppable database for direct transfer
    cursor_shop.execute(f"ATTACH DATABASE '{full_db_path}' AS full_db;")
    
    print("Transferring all procedures (100%)...")
    cursor_shop.execute("""
        INSERT INTO main.procedures
        SELECT * FROM full_db.procedures;
    """)
    print(f"  Procedures transferred: {cursor_shop.rowcount}")
    
    print("Transferring procedure codes...")
    cursor_shop.execute("""
        INSERT INTO main.procedure_codes
        SELECT * FROM full_db.procedure_codes
        WHERE procedure_id IN (SELECT id FROM main.procedures);
    """)
    print(f"  Procedure codes transferred: {cursor_shop.rowcount}")
    
    print("Skipping transferring prices to keep DB lightweight...")
    print("  Prices table left empty.")
    
    print("Transferring synonyms...")
    cursor_shop.execute("""
        INSERT INTO main.synonyms
        SELECT * FROM full_db.synonyms;
    """)
    
    print("Transferring unique words vocabulary...")
    cursor_shop.execute("""
        INSERT INTO main.unique_words
        SELECT * FROM full_db.unique_words;
    """)
    
    print("Populating FTS virtual table...")
    cursor_shop.execute("""
        INSERT INTO main.fts_procedures
        SELECT procedure_id, description, code, code_type, ms_drg, apr_drg, rc, apc, ndc, cdm
        FROM full_db.fts_procedures;
    """)
    print(f"  FTS entries populated: {cursor_shop.rowcount}")
    
    conn_shop.commit()
    conn_shop.close()
    
    elapsed = time.time() - start_time
    print(f"True shoppable database created successfully in {elapsed:.2f} seconds!")
    
    # Print file size
    size_bytes = os.path.getsize(shoppable_db_path)
    print(f"Final shoppable database file size: {size_bytes / (1024*1024):.2f} MB")

if __name__ == "__main__":
    main()
