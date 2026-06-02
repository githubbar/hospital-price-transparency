import sqlite3

def main():
    conn = sqlite3.connect('in_shoppable.sqlite3')
    cursor = conn.cursor()
    
    query = '("magnetic resonance imaging" OR "magnetic resonance" OR "mri" OR mri)'
    print(f"Executing MATCH query: {query}")
    
    try:
        cursor.execute("SELECT COUNT(*) FROM fts_procedures WHERE fts_procedures MATCH ?", [query])
        print(f"Result count: {cursor.fetchone()[0]}")
    except Exception as e:
        print(f"Error: {e}")
        
    conn.close()

if __name__ == '__main__':
    main()
