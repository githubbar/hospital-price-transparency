import os
import json
import re
import sys

# Ensure stdout encodes correctly to avoid UnicodeEncodeError in Windows terminals
sys.stdout.reconfigure(encoding='utf-8')

brain_dir = r"C:\Users\oleyk\.gemini\antigravity-ide\brain"
print("Scanning brain conversations for procedure counts...")

for folder in os.listdir(brain_dir):
    path = os.path.join(brain_dir, folder, ".system_generated", "logs", "transcript.jsonl")
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if 'Total Procedure' in line or 'unique procedure' in line or 'procedures in' in line:
                        print(f"[{folder}] Found match:")
                        # print the line
                        print(f"  {line.strip()[:200]}")
        except Exception:
            pass
