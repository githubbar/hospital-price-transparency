import re
from datetime import datetime, timezone

# We want logs from 2026-05-28T08:00:00Z onwards (since current time is 2026-05-29T08:00:00-04:00 which is 12:00:00Z)
# Let's target the last 24-30 hours starting from 2026-05-28T00:00:00Z to cover the full "past day"
start_time = datetime(2026, 5, 28, 0, 0, 0, tzinfo=timezone.utc)

log_file_path = r"x:\Hospital Price Transparency\scratch\server_logs_full.txt"

print("Analyzing logs...")

stats = {
    'total_lines': 0,
    'in_time_range': 0,
    'errors': [],
    'warnings': [],
    'spellchecks': [],
    'not_founds': [],
    'sqlite_attaches': [],
    'startup_shutdown': [],
    'turnstile': [],
    'other_interesting': []
}

with open(log_file_path, 'r', encoding='utf-8') as f:
    for line in f:
        stats['total_lines'] += 1
        line = line.strip()
        if not line:
            continue
        
        # Parse timestamp
        # E.g., 2026-05-28T11:50:26.291104Z
        m = re.match(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)', line)
        if not m:
            continue
            
        ts_str = m.group(1)
        try:
            # Parse datetime
            ts = datetime.strptime(ts_str[:26] + 'Z', "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        except Exception:
            continue
            
        if ts < start_time:
            continue
            
        stats['in_time_range'] += 1
        
        # Check severity or content
        content = line[m.end():].strip()
        
        # Filter out GCSFuse verbose configs to avoid noise
        if "GCSFuse Config" in content or "gcsfuse/" in content or "Full Config" in content or "CLI Flags" in content or "Mounting file system" in content or "File system has been successfully mounted" in content or "UniverseDomain" in content or "GetStorageLayout" in content:
            continue
        if "Garbage collection succeeded" in content or "Starting a garbage collection run" in content:
            continue
            
        # Classify interesting logs
        if "error" in content.lower():
            stats['errors'].append((ts_str, content))
        elif "warning" in content.lower():
            stats['warnings'].append((ts_str, content))
        elif "[spellcheck]" in content:
            stats['spellchecks'].append((ts_str, content))
        elif "Not Found:" in content or "404" in content:
            stats['not_founds'].append((ts_str, content))
        elif "[SQLite Attach]" in content:
            stats['sqlite_attaches'].append((ts_str, content))
        elif "Starting new instance" in content or "Booting worker" in content or "Starting gunicorn" in content or "Shutting down" in content or "exiting" in content:
            stats['startup_shutdown'].append((ts_str, content))
        elif "verify_turnstile" in content:
            stats['turnstile'].append((ts_str, content))
        else:
            # Let's keep it if it looks like an application request or interesting log
            if not content.startswith("INFO") and content != "":
                stats['other_interesting'].append((ts_str, content))

print(f"Total lines parsed: {stats['total_lines']}")
print(f"Lines in time range: {stats['in_time_range']}")
print(f"Errors found: {len(stats['errors'])}")
print(f"Warnings found: {len(stats['warnings'])}")
print(f"Spellcheck events: {len(stats['spellchecks'])}")
print(f"Not Found (404) events: {len(stats['not_founds'])}")
print(f"SQLite Attaches: {len(stats['sqlite_attaches'])}")
print(f"Startup/Shutdown events: {len(stats['startup_shutdown'])}")
print(f"Turnstile events: {len(stats['turnstile'])}")
print(f"Other interesting: {len(stats['other_interesting'])}")

print("\n--- ERRORS ---")
for ts, c in stats['errors'][:20]:
    print(f"[{ts}] {c}")

print("\n--- WARNINGS ---")
for ts, c in stats['warnings'][:20]:
    print(f"[{ts}] {c}")

print("\n--- SPELLCHECKS ---")
for ts, c in stats['spellchecks'][:20]:
    print(f"[{ts}] {c}")

print("\n--- NOT FOUNDS / 404s ---")
for ts, c in stats['not_founds'][:20]:
    print(f"[{ts}] {c}")

print("\n--- STARTUP / SHUTDOWN (first 10) ---")
for ts, c in stats['startup_shutdown'][:10]:
    print(f"[{ts}] {c}")

print("\n--- OTHER INTERESTING ---")
for ts, c in stats['other_interesting'][:20]:
    print(f"[{ts}] {c}")
