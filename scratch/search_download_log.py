import os
import json
import sys

# Ensure stdout encodes correctly to avoid UnicodeEncodeError in Windows terminals
sys.stdout.reconfigure(encoding='utf-8')

log_path = r"x:\Hospital Price Transparency\data\download_log.json"
if os.path.exists(log_path):
    print("Reading download_log.json...")
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # print first 500 characters of the JSON or key info
            print(json.dumps(data, indent=2)[:1000])
    except Exception as e:
        print(f"Error: {e}")
else:
    print("download_log.json not found")
