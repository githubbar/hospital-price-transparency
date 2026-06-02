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

print("=== Anomalies, Errors, and Warnings in server_logs_full.txt ===")

errors_and_warnings = []
sqlite_errors = []
not_founds = []

for e in events:
    payload_lower = e['payload'].lower()
    if "error" in payload_lower or "warning" in payload_lower or "fail" in payload_lower or "exception" in payload_lower:
        errors_and_warnings.append(e)
    if "syntax error" in payload_lower or "no such column" in payload_lower or "no such table" in payload_lower:
        sqlite_errors.append(e)
    if "not found" in payload_lower:
        not_founds.append(e)

print(f"\nTotal Errors/Warnings: {len(errors_and_warnings)}")
for ew in errors_and_warnings[:30]:
    print(f"[{ew['ts_str']}] {ew['payload']}")

print(f"\nTotal SQLite/Database Errors: {len(sqlite_errors)}")
for se in sqlite_errors:
    print(f"[{se['ts_str']}] {se['payload']}")

print(f"\nTotal Not Found (404) events: {len(not_founds)}")
for nf in not_founds[:20]:
    print(f"[{nf['ts_str']}] {nf['payload']}")

# Let's count how many instances were started and why
autoscaling_starts = len([e for e in events if "Starting new instance" in e['payload'] and "AUTOSCALING" in e['payload']])
deployment_starts = len([e for e in events if "Starting new instance" in e['payload'] and "DEPLOYMENT_ROLLOUT" in e['payload']])
print(f"\nTotal container boots: {autoscaling_starts + deployment_starts}")
print(f"  - Autoscaling: {autoscaling_starts}")
print(f"  - Deployment: {deployment_starts}")

# Let's see if there are Turnstile verification messages
turnstile_events = [e for e in events if "verify_turnstile" in e['payload']]
print(f"\nTurnstile events: {len(turnstile_events)}")
for te in turnstile_events[:15]:
    print(f"[{te['ts_str']}] {te['payload']}")
