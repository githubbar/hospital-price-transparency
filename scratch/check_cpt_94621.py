import sqlite3

def check_94621():
    conn = sqlite3.connect('in_full.sqlite3')
    cursor = conn.cursor()
    
    # Check procedure details
    cursor.execute("SELECT id, code, description FROM procedures WHERE code = '94621'")
    proc = cursor.fetchone()
    print("Procedure 94621:", proc)
    
    if proc:
        proc_id = proc[0]
        # Count total prices
        cursor.execute("SELECT COUNT(*) FROM prices WHERE procedure_id = ?", (proc_id,))
        print("Total price count in full DB:", cursor.fetchone()[0])
        
        # Check a few price records
        cursor.execute("""
            SELECT hospital_name, payer_name, plan_name, setting, price 
            FROM prices 
            WHERE procedure_id = ? 
            LIMIT 5
        """, (proc_id,))
        print("Sample prices:")
        for row in cursor.fetchall():
            print("  ", row)
            
    conn.close()

if __name__ == '__main__':
    check_94621()
