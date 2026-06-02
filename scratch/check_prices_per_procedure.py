import sqlite3
import os

full_db_path = r"x:\Hospital Price Transparency\in_full.sqlite3"
if not os.path.exists(full_db_path):
    full_db_path = r"x:\Hospital Price Transparency\db.sqlite3"

conn = sqlite3.connect(full_db_path)
cursor = conn.cursor()

try:
    # Let's see the distribution of prices per procedure
    cursor.execute("""
        SELECT p.id, p.code, p.description, COUNT(pr.id) as price_count
        FROM procedures p
        JOIN prices pr ON pr.procedure_id = p.id
        GROUP BY p.id
        ORDER BY price_count DESC
        LIMIT 20
    """)
    print("Top 20 procedures by price count:")
    rows = cursor.fetchall()
    for row in rows:
        print(f"ID: {row[0]}, Code: {row[1]}, Desc: {row[2][:50]}, Count: {row[3]}")
        
    # Let's check some common ones (like CPT 70551 - brain mri)
    cursor.execute("SELECT id, description FROM procedures WHERE code = '70551'")
    mri_procs = cursor.fetchall()
    print("\nMRI procs (code 70551):")
    for p in mri_procs:
        cursor.execute("SELECT COUNT(*) FROM prices WHERE procedure_id = ?", (p[0],))
        count = cursor.fetchone()[0]
        print(f"  ID: {p[0]}, Desc: {p[1]}, Prices Count: {count}")
        
    # Let's get general stats on price counts
    cursor.execute("""
        SELECT AVG(cnt), MIN(cnt), MAX(cnt)
        FROM (
            SELECT COUNT(*) as cnt 
            FROM prices 
            GROUP BY procedure_id
        )
    """)
    avg_cnt, min_cnt, max_cnt = cursor.fetchone()
    print(f"\nGeneral Stats on Prices per Procedure:")
    print(f"  Average: {avg_cnt:.1f}")
    print(f"  Minimum: {min_cnt}")
    print(f"  Maximum: {max_cnt}")

except Exception as e:
    print(f"Error: {e}")
finally:
    conn.close()
