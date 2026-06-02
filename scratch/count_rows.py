import sqlite3

def count_rows(db_path):
    print(f"Inspecting {db_path}...")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Get list of tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [t[0] for t in cursor.fetchall()]
        
        for table in tables:
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                count = cursor.fetchone()[0]
                print(f"Table: {table:25} | Rows: {count:,}")
            except Exception as e:
                print(f"Table: {table:25} | Error: {e}")
                
        conn.close()
    except Exception as e:
        print(f"Failed to connect or query: {e}")

count_rows("db.sqlite3")
