import sqlite3

def check_prices():
    conn = sqlite3.connect('in_full.sqlite3')
    cursor = conn.cursor()
    
    print("Top 10 procedures with the most prices:")
    cursor.execute("""
        SELECT p.id, p.code, p.code_type, p.description, COUNT(pr.id) as price_count
        FROM procedures p
        LEFT JOIN prices pr ON p.id = pr.procedure_id
        GROUP BY p.id, p.code, p.code_type, p.description
        ORDER BY price_count DESC
        LIMIT 10
    """)
    for row in cursor.fetchall():
        print(row)
        
    print("\nProcedures where code is empty or NULL:")
    cursor.execute("""
        SELECT COUNT(*), SUM(stats_count)
        FROM procedures
        WHERE code IS NULL OR trim(code) = ''
    """)
    print(cursor.fetchone())

    conn.close()

if __name__ == '__main__':
    check_prices()
