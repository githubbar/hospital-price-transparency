import re

def search_logs(filepath):
    print(f"\n--- Searching {filepath} ---")
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        for idx, line in enumerate(f):
            # Let's print any line containing "spellcheck" or "Original:" or "Corrected:" or "search" or "query" or "/search"
            lower_line = line.lower()
            if 'spellcheck' in lower_line or 'original:' in lower_line or 'corrected:' in lower_line or 'searching' in lower_line or '/search' in lower_line or '?q=' in lower_line:
                print(f"Line {idx+1}: {line.strip()}")

search_logs(r"x:\Hospital Price Transparency\scratch\server_logs.txt")
search_logs(r"x:\Hospital Price Transparency\scratch\server_logs_full.txt")
