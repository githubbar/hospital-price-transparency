import csv
import collections
from collections import defaultdict

filename = r"x:\Hospital Price Transparency\data\351720796_indiana-university-health-bloomington-inc._standardcharges.csv"

desc_to_cpt = defaultdict(set)
rows_to_check = 5000

with open(filename, 'r', encoding='utf-8') as f:
    # Skip first 2 lines of metadata
    for _ in range(2):
        next(f)
    
    reader = csv.DictReader(f)
    print(f"Analyzing first {rows_to_check} rows for Description vs CPT collisions...")
    
    for i, row in enumerate(reader):
        if i >= rows_to_check:
            break
            
        description = row.get('description', '').strip()
        if not description:
            continue

        # Find CPT code
        cpt_code = None
        # Check all code columns (csv has code|1..code|6 based on header inspection)
        # We need to perform a dynamic check similar to previous run or just hardcode loop
        # Based on previous run, fields are 'code|1', 'code|1|type' etc
        
        for k in range(1, 10):
            type_col = f"code|{k}|type"
            code_col = f"code|{k}"
            
            if type_col not in row: 
                break # No more code columns
                
            if row[type_col] == 'CPT':
                cpt_code = row[code_col]
                break # Found the CPT for this row
        
        if cpt_code:
            desc_to_cpt[description].add(cpt_code)

print("\n--- Results: Descriptions with multiple CPT codes ---")
count = 0
for desc, codes in desc_to_cpt.items():
    if len(codes) > 1:
        count += 1
        print(f"'{desc}' has {len(codes)} CPTs: {codes}")
        if count >= 10:
            print("... and more ...")
            break

if count == 0:
    print("No descriptions found with multiple CPT codes in the sampled data.")

