with open(r"x:\Hospital Price Transparency\scratch\server_logs_full.txt", 'r', encoding='utf-8', errors='ignore') as f:
    for idx, line in enumerate(f):
        # search for any 4-digit or 5-digit number or any number like 9502 or 27891
        # or search for "records" or "total"
        if any(w in line.lower() for w in ['total', 'record', 'procedure']) and not any(w in line.lower() for w in ['attach', 'gcsfuse', 'config']):
            print(f"Line {idx+1}: {line.strip()}")
