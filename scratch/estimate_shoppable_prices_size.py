import sqlite3
import os
import csv

full_db_path = r"x:\Hospital Price Transparency\in_full.sqlite3"
shoppable_csv_path = r"x:\Hospital Price Transparency\reference\shoppable_codes.csv"

if not os.path.exists(full_db_path):
    full_db_path = r"x:\Hospital Price Transparency\db.sqlite3"

print(f"Reading from: {full_db_path}")

shoppable_codes = set()
with open(shoppable_csv_path, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        code = row['code'].strip()
        if code:
            shoppable_codes.add(code.upper())

print(f"Loaded {len(shoppable_codes)} shoppable codes.")

conn = sqlite3.connect(full_db_path)
cursor = conn.cursor()

try:
    # Get total procedures count
    cursor.execute("SELECT COUNT(*) FROM procedures")
    total_procs = cursor.fetchone()[0]
    print(f"Total procedures in full DB: {total_procs}")
    
    # Get total prices count
    cursor.execute("SELECT COUNT(*) FROM prices")
    total_prices = cursor.fetchone()[0]
    print(f"Total prices in full DB: {total_prices}")
    
    # Let's count how many procedures match the shoppable codes
    # We can check by joining with procedure_codes or checking code directly on procedures table
    # Wait, let's see how many procedures have their code in shoppable_codes
    placeholders = ",".join(["?"] * len(shoppable_codes))
    cursor.execute(f"SELECT id FROM procedures WHERE UPPER(code) IN ({placeholders})", list(shoppable_codes))
    matched_proc_ids = [row[0] for row in cursor.fetchall()]
    print(f"Procedures matching shoppable codes directly: {len(matched_proc_ids)}")
    
    # Let's check how many prices are associated with these matched procedures
    if matched_proc_ids:
        # Since the list of matched_proc_ids might be large, let's count using a subquery
        cursor.execute(f"""
            SELECT COUNT(*) FROM prices 
            WHERE procedure_id IN (
                SELECT id FROM procedures WHERE UPPER(code) IN ({placeholders})
            )
        """, list(shoppable_codes))
        matched_prices_count = cursor.fetchone()[0]
        print(f"Prices associated with these shoppable procedures: {matched_prices_count}")
        print(f"This is {matched_prices_count / total_prices * 100:.2f}% of total prices.")
    else:
        print("No matched procedures found.")
        
except Exception as e:
    print(f"Error: {e}")
finally:
    conn.close()
