import sqlite3
import json
import os

DB_PATH = 'db.sqlite3'

DEFAULT_SYNONYMS = [
    ("allergy skin test", ["95004", "95024", '"percutaneous allergy skin"', '"percut allergy skin"']),
    ("allergy skin tests", ["95004", "95024", '"percutaneous allergy skin"', '"percut allergy skin"']),
    ("allergy shot", ["95115", "95117", '"immunotherapy injection"', '"allergen injection"']),
    ("allergy shots", ["95115", "95117", '"immunotherapy injection"', '"allergen injection"']),
    ("scratch test", ["95004", "95024", '"percutaneous allergy skin"', '"percut allergy skin"']),
    ("scratch tests", ["95004", "95024", '"percutaneous allergy skin"', '"percut allergy skin"']),
    ("allergy test", ["95004", "95024", "95044", '"allergy skin test"', '"allergy patch test"']),
    ("allergy tests", ["95004", "95024", "95044", '"allergy skin test"', '"allergy patch test"']),
    ("allergy testing", ["95004", "95024", "95044", '"allergy skin test"', '"allergy patch test"']),
    ("ct scan", ['"computed tomography"', '"ct scan"']),
    ("ct scans", ['"computed tomography"', '"ct scan"']),
    ("cat scan", ['"computed tomography"', '"cat scan"']),
    ("cat scans", ['"computed tomography"', '"cat scan"']),
    ("mri", ['"magnetic resonance imaging"', '"magnetic resonance"']),
    ("mris", ['"magnetic resonance imaging"', '"magnetic resonance"']),
    ("x-ray", ['"radiologic examination"', "xray", "x-ray"]),
    ("x-rays", ['"radiologic examination"', "xray", "x-ray"]),
    ("xray", ['"radiologic examination"', "xray", "x-ray"]),
    ("xrays", ['"radiologic examination"', "xray", "x-ray"]),
    ("ultrasound", ["ultrasound", "echography", "sonogram"]),
    ("ultrasounds", ["ultrasound", "echography", "sonogram"]),
    ("sonogram", ["ultrasound", "echography", "sonogram"]),
    ("sonograms", ["ultrasound", "echography", "sonogram"]),
    ("ekg", ['"electrocardiogram"', "ecg", "ekg"]),
    ("ekgs", ['"electrocardiogram"', "ecg", "ekg"]),
    ("ecg", ['"electrocardiogram"', "ecg", "ekg"]),
    ("ecgs", ['"electrocardiogram"', "ecg", "ekg"]),
    ("eeg", ['"electroencephalogram"', "eeg"]),
    ("eegs", ['"electroencephalogram"', "eeg"]),
    ("colonoscopy", ['"screening colonoscopy"', '"colonoscopy flexible"', "colonoscopy"]),
    ("colonoscopies", ['"screening colonoscopy"', '"colonoscopy flexible"', "colonoscopy"]),
    ("mammogram", ['"mammography"', "mammogram"]),
    ("mammograms", ['"mammography"', "mammogram"]),
    ("blood test", ['"blood draw"', "venipuncture", '"basic metabolic"', '"comprehensive metabolic"']),
    ("blood tests", ['"blood draw"', "venipuncture", '"basic metabolic"', '"comprehensive metabolic"']),
    ("lab work", ['"blood draw"', "venipuncture", '"basic metabolic"', '"comprehensive metabolic"']),
    ("labs", ['"blood draw"', "venipuncture", '"basic metabolic"', '"comprehensive metabolic"']),
    ("physical", ['"preventive medicine"', '"annual exam"', '"annual physical"']),
    ("physicals", ['"preventive medicine"', '"annual exam"', '"annual physical"']),
    ("checkup", ['"preventive medicine"', '"annual exam"', "checkup"]),
    ("checkups", ['"preventive medicine"', '"annual exam"', "checkup"]),
    ("er visit", ['"emergency department"', '"emergency room"', '"er visit"']),
    ("er visits", ['"emergency department"', '"emergency room"', '"er visit"']),
    ("emergency room", ['"emergency department"', '"emergency room"']),
    ("emergency rooms", ['"emergency department"', '"emergency room"']),
    ("delivery", ['"vaginal delivery"', '"cesarean delivery"', '"obstetrical care"']),
    ("deliveries", ['"vaginal delivery"', '"cesarean delivery"', '"obstetrical care"']),
    ("birth", ['"vaginal delivery"', '"cesarean delivery"', '"obstetrical care"']),
    ("births", ['"vaginal delivery"', '"cesarean delivery"', '"obstetrical care"']),
    ("childbirth", ['"vaginal delivery"', '"cesarean delivery"', '"obstetrical care"']),
    ("c-section", ['"cesarean delivery"', '"c-section"']),
    ("c-sections", ['"cesarean delivery"', '"c-section"']),
    
    # ── NEW Synonyms added from recent 30-day search logs (download.csv) ──
    ("allergy vials shot", ["95115", "95117", "95165", '"immunotherapy injection"', '"allergen injection"', '"antigen therapy"']),
    ("allergy vials", ["95165", '"antigen therapy"', '"allergy antigen"']),
    ("allergy vial", ["95165", '"antigen therapy"', '"allergy antigen"']),
    ("c secion", ['"cesarean delivery"', '"c-section"']),
    ("cesarian section", ['"cesarean delivery"', '"c-section"']),
    ("sleep apnea", ["95810", "95811", '"sleep study"', '"polysom"']),
    ("tubal", ["58670", "58671", '"tubal ligation"', '"tubal block"', '"tubal cautery"']),
    ("tubal ligation", ["58670", "58671", '"tubal ligation"', '"tubal block"', '"tubal cautery"']),
    ("tubal block", ["58670", "58671", '"tubal ligation"', '"tubal block"', '"tubal cautery"']),
    ("penile implant", ["54405", "C1813", '"penile pump"', '"penile prosthesis"', '"penis pros"']),
    ("ankle tendon repair", ["27658", "27659", "27695", "27696", '"ankle ligament"', '"leg tendon"']),
    ("ankle repair", ["27658", "27659", "27695", "27696", '"ankle ligament"', '"leg tendon"']),
    ("peroneus brevis", ["27658", "27659", "27695", "27696", '"leg tendon"']),
    ("peroneus tendon", ["27658", "27659", "27695", "27696", '"leg tendon"']),
    ("knee replacement", ["27447", '"knee replacement"', '"knee arthroplasty"']),
    ("knee replacements", ["27447", '"knee replacement"', '"knee arthroplasty"']),
]

def main():
    if not os.path.exists(DB_PATH):
        print(f"ERROR: Database file {DB_PATH} not found.")
        return
        
    print(f"Opening database: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
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
    
    # 2. Insert standard & new synonyms using INSERT OR REPLACE to update existing phrases with new expansions
    inserted = 0
    updated = 0
    for phrase, expansions in DEFAULT_SYNONYMS:
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
    print(f"Synonyms database update complete: {inserted} inserted, {updated} updated.")
    conn.close()

if __name__ == '__main__':
    main()
