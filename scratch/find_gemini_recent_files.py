import os
import sys
from datetime import datetime, timezone

# Ensure stdout encodes correctly to avoid UnicodeEncodeError in Windows terminals
sys.stdout.reconfigure(encoding='utf-8')

gemini_dir = r"C:\Users\oleyk\.gemini"
cutoff = datetime(2026, 5, 28, 0, 0, 0, tzinfo=timezone.utc)

print(f"Searching for files modified after {cutoff} in {gemini_dir}...")

recent_files = []
for root, dirs, files in os.walk(gemini_dir):
    # Avoid listing too many files in brain system_generated logs unless they are ours
    # skip some very common heavy paths if needed
    for f in files:
        path = os.path.join(root, f)
        try:
            mtime = os.path.getmtime(path)
            dt = datetime.fromtimestamp(mtime, timezone.utc)
            if dt > cutoff:
                recent_files.append((path, dt, os.path.getsize(path)))
        except Exception:
            pass

recent_files.sort(key=lambda x: x[1], reverse=True)
print(f"Found {len(recent_files)} files modified after {cutoff}:")
for path, dt, size in recent_files[:100]:  # limit to top 100
    print(f"  {dt.isoformat()} - {size} bytes - {path}")
