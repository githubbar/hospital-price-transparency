import os
import sys

# Ensure stdout encodes correctly to avoid UnicodeEncodeError in Windows terminals
sys.stdout.reconfigure(encoding='utf-8')

brain_dir = r"C:\Users\oleyk\.gemini\antigravity-ide\brain"
print("Scanning brain walkthroughs and plans...")

for root, dirs, files in os.walk(brain_dir):
    for f in files:
        if f.endswith('.md') or f.endswith('.txt'):
            path = os.path.join(root, f)
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as file:
                    content = file.read()
                    if 'procedure' in content.lower() or 'code' in content.lower():
                        # Find occurrences of numbers or lists
                        lines = content.splitlines()
                        for idx, line in enumerate(lines):
                            if any(w in line.lower() for w in ['total', 'count', 'unique']) and any(w in line.lower() for w in ['procedure', 'code']):
                                print(f"[{path}:{idx+1}] {line.strip()}")
            except Exception:
                pass
