import sqlite3
import os

def check_db(db_path, label):
    print(f"\n==========================================")
    print(f"Checking DB: {db_path} ({label})")
    print(f"==========================================")
    if not os.path.exists(db_path):
        print("File does not exist!")
        return
        
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 1. Total rows
    cursor.execute("SELECT COUNT(*) FROM procedures")
    print(f"Total procedures: {cursor.fetchone()[0]}")
    cursor.execute("SELECT COUNT(*) FROM prices")
    print(f"Total prices: {cursor.fetchone()[0]}")
    
    # 2. LIKE query
    cursor.execute("SELECT COUNT(*) FROM procedures WHERE description LIKE '%mri%'")
    print(f"LIKE '%mri%' count: {cursor.fetchone()[0]}")
    
    # 3. FTS5 MATCH query
    try:
        cursor.execute("SELECT COUNT(*) FROM fts_procedures WHERE fts_procedures MATCH 'mri'")
        print(f"FTS5 MATCH 'mri' count: {cursor.fetchone()[0]}")
    except Exception as e:
        print(f"FTS5 MATCH 'mri' failed: {e}")
        
    try:
        cursor.execute("SELECT COUNT(*) FROM fts_procedures WHERE fts_procedures MATCH 'mri*'")
        print(f"FTS5 MATCH 'mri*' count: {cursor.fetchone()[0]}")
    except Exception as e:
        print(f"FTS5 MATCH 'mri*' failed: {e}")
        
    # 4. Check a few actual matched rows
    cursor.execute("SELECT id, description, code FROM procedures WHERE description LIKE '%mri%' LIMIT 3")
    print("Sample LIKE matches:")
    for row in cursor.fetchall():
        print("  ", row)
        
    conn.close()

def main():
    check_db('in_shoppable.sqlite3', 'Shoppable')
    check_db('in_full.sqlite3', 'Full GCS/Local Fallback')
    check_db('db.sqlite3', 'Main DB')

if __name__ == '__main__':
    main()
