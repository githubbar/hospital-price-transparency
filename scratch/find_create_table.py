with open(r"x:\Hospital Price Transparency\load_to_sqlite.py", 'r', encoding='utf-8') as f:
    for idx, line in enumerate(f):
        if 'create table' in line.lower():
            print(f"Line {idx+1}: {line.strip()}")
