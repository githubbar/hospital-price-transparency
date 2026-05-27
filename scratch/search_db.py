import sqlite3

def test_code(cursor, code):
    cursor.execute("SELECT id, description, code, code_type FROM procedures WHERE code = ?", [code])
    rows = cursor.fetchall()
    print(f"Code: {code} -> Matches: {len(rows)}")
    for r in rows[:3]:
        print(f"  {r}")

def main():
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    
    test_code(cursor, '43239') # EGD/upper endoscopy with biopsy
    test_code(cursor, '66984') # Cataract surgery
    test_code(cursor, '47562') # Gallbladder removal laparoscopic
    test_code(cursor, '42820') # Tonsillectomy under age 12
    test_code(cursor, '77080') # Bone density scan (DEXA)
    test_code(cursor, '93306') # Echocardiogram
    test_code(cursor, '93015') # Cardiovascular stress test
    test_code(cursor, '27130') # Total hip replacement
    test_code(cursor, '64721') # Carpal tunnel surgery
    test_code(cursor, '49505') # Inguinal hernia repair (tested before, matches 7)
    
    conn.close()

if __name__ == '__main__':
    main()
