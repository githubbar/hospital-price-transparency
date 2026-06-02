with open(r"x:\Hospital Price Transparency\scratch\server_logs_full.txt", 'r', encoding='utf-8', errors='ignore') as f:
    for idx, line in enumerate(f):
        if 'procedures' in line.lower() or 'count' in line.lower() or 'total_records' in line.lower():
            if 'attach' not in line.lower() and 'gcsfuse' not in line.lower():
                print(f"Line {idx+1}: {line.strip()}")
