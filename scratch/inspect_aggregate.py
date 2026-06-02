import sqlite3
import os

db_path = 'in_aggregate.sqlite3'
if not os.path.exists(db_path):
    print(f"File not found: {db_path}")
else:
    print(f"Database file size: {os.path.getsize(db_path)} bytes")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM procedures")
        proc_count = cursor.fetchone()[0]
        print(f"Procedures count: {proc_count}")
        
        cursor.execute("SELECT COUNT(*) FROM prices")
        price_count = cursor.fetchone()[0]
        print(f"Prices count: {price_count}")
        
        cursor.execute("SELECT COUNT(*) FROM fts_procedures")
        fts_count = cursor.fetchone()[0]
        print(f"FTS procedures count: {fts_count}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()
