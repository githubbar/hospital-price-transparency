import sqlite3

try:
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print(f"Tables: {tables}")
    
    for table in tables:
        t_name = table[0]
        if 'cpt' in t_name.lower() or 'cdm' in t_name.lower() or 'desc' in t_name.lower() or 'code' in t_name.lower():
            print(f"--- Schema for {t_name} ---")
            cursor.execute(f"PRAGMA table_info({t_name})")
            columns = cursor.fetchall()
            for col in columns:
                print(col)
except Exception as e:
    print(e)
