import json
import sys

# Ensure stdout encodes correctly to avoid UnicodeEncodeError in Windows terminals
sys.stdout.reconfigure(encoding='utf-8')

path = r"C:\Users\oleyk\.gemini\antigravity-ide\brain\f7d24124-f658-4916-81dd-51dc53b8c042\.system_generated\logs\transcript.jsonl"
print(f"Reading f7d24124 transcript...")

with open(path, 'r', encoding='utf-8') as f:
    for idx, line in enumerate(f):
        if idx == 24:
            data = json.loads(line)
            content = data.get('content')
            print(f"Step 24 Content:")
            print(content)
            break
