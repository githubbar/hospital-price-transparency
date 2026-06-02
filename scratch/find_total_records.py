with open(r"x:\Hospital Price Transparency\prices\views.py", 'r', encoding='utf-8') as f:
    for idx, line in enumerate(f):
        if 'total_records' in line:
            print(f"Line {idx+1}: {line.strip()}")
