import os
from datetime import datetime, timezone

workspace = r"x:\Hospital Price Transparency"
cutoff = datetime(2026, 5, 28, 0, 0, 0, tzinfo=timezone.utc)

print(f"Searching for files modified after {cutoff} in {workspace}...")

recent_files = []
for root, dirs, files in os.walk(workspace):
    # Skip .git and .venv
    if '.git' in root or '.venv' in root:
        continue
    for f in files:
        path = os.path.join(root, f)
        try:
            mtime = os.path.getmtime(path)
            dt = datetime.fromtimestamp(mtime, timezone.utc)
            if dt > cutoff:
                recent_files.append((path, dt, os.path.getsize(path)))
        except Exception as e:
            pass

recent_files.sort(key=lambda x: x[1], reverse=True)
print(f"Found {len(recent_files)} files modified after {cutoff}:")
for path, dt, size in recent_files:
    print(f"  {dt.isoformat()} - {size} bytes - {path}")
