import sqlite3
import json
import os
import argparse

SYNONYMS_JSON_PATH = os.path.join('reference', 'default_synonyms.json')

def update_db_synonyms(db_path, default_synonyms):
    if not os.path.exists(db_path):
        print(f"ERROR: Database file {db_path} not found.")
        return False

    print(f"Opening database: {db_path}")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 1. Create table if not exists
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS synonyms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phrase TEXT UNIQUE NOT NULL,
            expansions TEXT NOT NULL
        );
        """)
        conn.commit()
        
        # 2. Insert or update synonyms from JSON
        inserted = 0
        updated = 0
        for phrase, expansions in default_synonyms.items():
            exp_json = json.dumps(expansions)
            # Check if phrase exists to distinguish between insert and update
            cursor.execute("SELECT id FROM synonyms WHERE phrase = ?", (phrase,))
            row = cursor.fetchone()
            if row:
                cursor.execute("UPDATE synonyms SET expansions = ? WHERE phrase = ?", (exp_json, phrase))
                updated += 1
            else:
                cursor.execute("INSERT INTO synonyms (phrase, expansions) VALUES (?, ?)", (phrase, exp_json))
                inserted += 1
                
        conn.commit()
        print(f"Synonyms database update complete for {db_path}: {inserted} inserted, {updated} updated.")
        conn.close()
        return True
    except Exception as e:
        print(f"ERROR: Failed to update synonyms in {db_path}: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Initialize or update synonyms in SQLite database(s).")
    parser.add_argument("--db", type=str, help="Path to specific SQLite database file to update.")
    args = parser.parse_args()

    if not os.path.exists(SYNONYMS_JSON_PATH):
        print(f"ERROR: Synonyms reference file {SYNONYMS_JSON_PATH} not found.")
        return

    print(f"Loading synonyms from: {SYNONYMS_JSON_PATH}")
    try:
        with open(SYNONYMS_JSON_PATH, 'r', encoding='utf-8') as f:
            default_synonyms = json.load(f)
    except Exception as e:
        print(f"ERROR: Failed to load synonyms JSON: {e}")
        return

    if args.db:
        update_db_synonyms(args.db, default_synonyms)
    else:
        # Update db.sqlite3
        update_db_synonyms('db.sqlite3', default_synonyms)
        
        # Also scan and update any other *.sqlite3 files in the current directory
        for file in os.listdir('.'):
            if file.endswith('.sqlite3') and file != 'db.sqlite3':
                update_db_synonyms(file, default_synonyms)

if __name__ == '__main__':
    main()
