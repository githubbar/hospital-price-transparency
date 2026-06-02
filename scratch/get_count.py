import sqlite3

conn = sqlite3.connect('db.sqlite3')
cursor = conn.cursor()

try:
    cursor.execute("SELECT COUNT(*) FROM procedures")
    count = cursor.fetchone()[0]
    print(f"Total procedures in db.sqlite3: {count}")
except Exception as e:
    print(f"Error: {e}")

try:
    cursor.execute("PRAGMA database_list")
    print(f"Databases: {cursor.fetchall()}")
except Exception as e:
    print(f"Error listing databases: {e}")

conn.close()
