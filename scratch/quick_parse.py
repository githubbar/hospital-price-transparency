import re
from datetime import datetime, timezone

def analyze(log_path):
    print(f"=== Analyzing {log_path} ===")
    with open(log_path, 'r', encoding='utf-8-sig') as f:
        lines = f.readlines()
        
    for i, line in enumerate(lines[:10]):
        try:
            print(f"Line {i+1}: {line.strip()}")
        except Exception as e:
            print(f"Line {i+1} print error: {e}")
        
    mri_matches = []
    
    for i, line in enumerate(lines):
        if 'mri' in line.lower():
            mri_matches.append((i+1, line.strip()))
        elif 'search' in line.lower():
            mri_matches.append((i+1, line.strip()))
            
    print(f"Found {len(mri_matches)} matching lines for 'mri' or 'search':")
    for idx, l in mri_matches[:30]:
        try:
            print(f"Line {idx}: {l}")
        except Exception as e:
            print(f"Line {idx} print error: {e}")

    # Let's print out what the logs look like
    categories = {}
    for line in lines:
        stripped = line.strip()
        if not stripped: continue
        parts = stripped.split(None, 2)
        if len(parts) >= 3:
            payload = parts[2]
            # Classify
            prefix = payload[:60]
            categories[prefix] = categories.get(prefix, 0) + 1
            
    print("\nTop log prefixes:")
    for k, v in sorted(categories.items(), key=lambda x: x[1], reverse=True)[:35]:
        try:
            print(f"  {v} occurrences: {k}")
        except Exception as e:
            print(f"  {v} occurrences print error: {e}")

analyze(r"x:\Hospital Price Transparency\scratch\server_logs.txt")
analyze(r"x:\Hospital Price Transparency\scratch\server_logs_full.txt")
