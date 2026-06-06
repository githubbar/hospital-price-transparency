import sqlite3
import os
import csv
import time

def main():
    start_time = time.time()
    full_db_path = 'in_full.sqlite3'
    aggregate_db_path = 'in_aggregate.sqlite3'
    
    print(f"Creating highly-optimized aggregate database from {full_db_path}...")
    
    if os.path.exists(aggregate_db_path):
        os.remove(aggregate_db_path)
        
    conn_agg = sqlite3.connect(aggregate_db_path)
    cursor_agg = conn_agg.cursor()
    
    # Copy schema from full database
    conn_full = sqlite3.connect(full_db_path)
    cursor_full = conn_full.cursor()
    
    cursor_full.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
    tables = cursor_full.fetchall()
    for name, table_sql in tables:
        if table_sql and not name.startswith('fts_procedures_'):
            cursor_agg.execute(table_sql)
        
    cursor_full.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%';")
    indexes = cursor_full.fetchall()
    for name, index_sql in indexes:
        if index_sql and not name.startswith('fts_procedures_') and not name.startswith('sqlite_autoindex_'):
            cursor_agg.execute(index_sql)
        
    conn_agg.commit()
    conn_full.close()
    
    # Attach full database to aggregate database for direct transfer
    cursor_agg.execute(f"ATTACH DATABASE '{full_db_path}' AS full_db;")
    
    print("Transferring all procedures (100%)...")
    cursor_agg.execute("""
        INSERT INTO main.procedures
        SELECT * FROM full_db.procedures;
    """)
    print(f"  Procedures transferred: {cursor_agg.rowcount}")
    
    print("Skipping transferring prices to keep DB lightweight...")
    print("  Prices table left empty.")
    
    print("Transferring synonyms...")
    cursor_agg.execute("""
        INSERT INTO main.synonyms
        SELECT * FROM full_db.synonyms;
    """)
    
    print("Transferring unique words vocabulary...")
    cursor_agg.execute("""
        INSERT INTO main.unique_words
        SELECT * FROM full_db.unique_words;
    """)
    
    print("Populating FTS virtual table...")
    cursor_agg.execute("""
        INSERT INTO main.fts_procedures (procedure_id, description, code, code_type, ms_drg, apr_drg, rc, apc, ndc, cdm, all_codes)
        SELECT procedure_id, description, code, code_type, ms_drg, apr_drg, rc, apc, ndc, cdm, all_codes
        FROM full_db.fts_procedures;
    """)
    print(f"  FTS entries populated: {cursor_agg.rowcount}")
    
    conn_agg.commit()
    conn_agg.close()
    
    elapsed = time.time() - start_time
    print(f"True aggregate database created successfully in {elapsed:.2f} seconds!")
    
    # Print file size
    size_bytes = os.path.getsize(aggregate_db_path)
    print(f"Final aggregate database file size: {size_bytes / (1024*1024):.2f} MB")

if __name__ == "__main__":
    main()
