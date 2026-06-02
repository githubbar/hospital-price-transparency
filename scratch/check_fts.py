import sqlite3

def check_fts():
    conn = sqlite3.connect('in_shoppable.sqlite3')
    cursor = conn.cursor()
    
    # 1. Search in procedures
    cursor.execute("SELECT id, code, description FROM procedures WHERE code = '94621'")
    row = cursor.fetchone()
    print("Procedures query for 94621:", row)
    
    # 2. Search in FTS procedures
    try:
        cursor.execute("SELECT procedure_id, code, description FROM fts_procedures WHERE fts_procedures MATCH '94621'")
        print("FTS match for 94621 (no asterisk):", cursor.fetchall())
    except Exception as e:
        print("FTS MATCH error:", e)
        
    try:
        cursor.execute("SELECT procedure_id, code, description FROM fts_procedures WHERE fts_procedures MATCH '94621*'")
        print("FTS match for 94621*:", cursor.fetchall())
    except Exception as e:
        print("FTS MATCH error:", e)

    conn.close()

if __name__ == '__main__':
    check_fts()
