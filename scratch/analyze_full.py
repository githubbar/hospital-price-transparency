import re
from datetime import datetime

def parse_time(ts_str):
    ts_str = ts_str.rstrip('Z')
    if '.' in ts_str:
        return datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S.%f")
    else:
        return datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S")

with open(r"x:\Hospital Price Transparency\scratch\server_logs_full.txt", 'r', encoding='utf-8-sig') as f:
    lines = f.readlines()

print(f"Total lines in server_logs_full.txt: {len(lines)}")

events = []
for i, line in enumerate(lines):
    line = line.strip()
    if not line: continue
    parts = line.split(None, 1)
    if len(parts) < 2: continue
    ts_str = parts[0]
    payload = parts[1]
    try:
        ts = parse_time(ts_str)
        events.append({'ts': ts, 'ts_str': ts_str, 'payload': payload, 'line': i+1})
    except Exception:
        continue

# Sort events chronologically
events.sort(key=lambda x: x['ts'])

print(f"Parsed {len(events)} events chronologically from {events[0]['ts_str']} to {events[-1]['ts_str']}")

startups = [e for e in events if "Starting new instance" in e['payload']]
attaches = [e for e in events if "[SQLite Attach]" in e['payload']]
spellchecks = [e for e in events if "[spellcheck]" in e['payload']]

print(f"Startups: {len(startups)}")
print(f"SQLite Attaches: {len(attaches)}")
print(f"Spellchecks: {len(spellchecks)}")

print("\n--- Spellcheck Events ---")
for sp in spellchecks:
    print(f"[{sp['ts_str']}] {sp['payload']}")

print("\n--- SQLite Attaches (first 10) ---")
for a in attaches[:10]:
    print(f"[{a['ts_str']}] {a['payload']}")

# Calculate cold start time for spellcheck searches
# For each spellcheck search, trace back to the closest preceding startup
print("\n--- Cold Start Analysis for Searches ---")
for sp in spellchecks:
    # Find the closest preceding startup
    preceding_startups = [s for s in startups if s['ts'] < sp['ts']]
    if not preceding_startups:
        print(f"Spellcheck [{sp['ts_str']}] {sp['payload']}: No preceding startup found")
        continue
    closest_startup = preceding_startups[-1]
    
    # Find any SQLite attaches between closest_startup and this spellcheck
    between_attaches = [a for a in attaches if closest_startup['ts'] <= a['ts'] <= sp['ts']]
    
    duration = (sp['ts'] - closest_startup['ts']).total_seconds()
    print(f"Search: {sp['payload']}")
    print(f"  Timestamp: {sp['ts_str']}")
    print(f"  Closest Startup: {closest_startup['ts_str']} (Reason: {closest_startup['payload']})")
    print(f"  SQLite attaches between them:")
    for a in between_attaches:
        print(f"    - [{a['ts_str']}] {a['payload']}")
    print(f"  Cold Start Search Time: {duration:.2f} seconds")
    print("-" * 50)
