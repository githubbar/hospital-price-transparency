import re
from datetime import datetime, timezone

def analyze_file(log_path):
    print(f"\n==========================================")
    print(f"Analyzing: {log_path}")
    print(f"==========================================")
    
    with open(log_path, 'r', encoding='utf-8-sig', errors='ignore') as f:
        lines = f.readlines()
        
    print(f"Total lines: {len(lines)}")
    
    # Let's inspect the first and last line timestamps
    def get_timestamp(line):
        # Match standard ISO timestamp
        m = re.match(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)', line)
        if m:
            return m.group(1)
        # Maybe brackets?
        m = re.search(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[^\]]*)\]', line)
        if m:
            return m.group(1)
        return None

    valid_timestamps = []
    for line in lines:
        ts = get_timestamp(line)
        if ts:
            valid_timestamps.append(ts)
            
    if valid_timestamps:
        print(f"Min timestamp: {valid_timestamps[-1]}") # They seem to be in reverse chronological order based on quick_parse!
        print(f"Max timestamp: {valid_timestamps[0]}")
    else:
        print("No timestamps found.")

    # Search for "mri" in the file
    mri_lines = []
    for idx, line in enumerate(lines):
        if 'mri' in line.lower():
            mri_lines.append((idx + 1, line.strip()))
            
    print(f"Found {len(mri_lines)} lines containing 'mri' (case-insensitive):")
    for idx, l in mri_lines[:100]:
        print(f"Line {idx}: {l}")

analyze_file(r"x:\Hospital Price Transparency\scratch\server_logs.txt")
analyze_file(r"x:\Hospital Price Transparency\scratch\server_logs_full.txt")
