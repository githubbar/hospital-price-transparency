import sqlite3
import os

db_path = r"x:\Hospital Price Transparency\in_full.sqlite3"
print(f"Checking {db_path}...")
if not os.path.exists(db_path):
    print("Database not found!")
    # Try fallback
    db_path = r"x:\Hospital Price Transparency\db.sqlite3"
    print(f"Checking fallback: {db_path}...")

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

try:
    print("\n--- TABLES ---")
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    for row in cursor.fetchall():
        print(row[0])
        
    print("\n--- INDEXES on table 'prices' ---")
    cursor.execute("PRAGMA index_list('prices')")
    indexes = cursor.fetchall()
    for idx in indexes:
        print(idx)
        # Get columns of the index
        idx_name = idx[1]
        cursor.execute(f"PRAGMA index_info('{idx_name}')")
        cols = cursor.fetchall()
        print(f"  Columns: {cols}")
        
    print("\n--- EXPLAIN QUERY PLAN ---")
    # Let's see how SQLite would execute the query
    cursor.execute("EXPLAIN QUERY PLAN SELECT procedure_id, hospital_id, hospital_name, payer_name, plan_name, setting, price FROM prices WHERE procedure_id = 'test_id'")
    for row in cursor.fetchall():
        print(row)
        
except Exception as e:
    print(f"Error: {e}")
finally:
    conn.close()
