import sqlite3
import os

db_path = 'in_shoppable.sqlite3'
print(f"Original size: {os.path.getsize(db_path)} bytes")

conn = sqlite3.connect(db_path)
cursor = conn.cursor()
try:
    print("Running vacuum...")
    cursor.execute("VACUUM")
    conn.commit()
    print("Vacuum completed successfully.")
except Exception as e:
    print(f"Error during vacuum: {e}")
finally:
    conn.close()

print(f"New size: {os.path.getsize(db_path)} bytes")
