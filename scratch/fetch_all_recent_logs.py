import subprocess
import json
import sys
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding='utf-8')

# 16 hours ago from 2026-06-03T15:30:00Z is 2026-06-02T23:30:00Z
start = "2026-06-02T23:30:00Z"
end = "2026-06-03T15:35:00Z"

print(f"Fetching logs from {start} to {end}...")

cmd = [
    'gcloud', 'logging', 'read',
    f'resource.type="cloud_run_revision" AND resource.labels.service_name="hospital-price-search" AND timestamp >= "{start}" AND timestamp <= "{end}"',
    '--format', 'json'
]

res = subprocess.run(cmd, capture_output=True, text=True, shell=True)

if res.returncode != 0:
    print(f"Error running gcloud: {res.stderr}")
    sys.exit(1)

try:
    logs = json.loads(res.stdout)
    print(f"Successfully retrieved {len(logs)} log entries.")
    
    # Sort chronologically
    logs.sort(key=lambda x: x.get('timestamp', ''))
    
    stats = {
        'total': 0,
        'errors': [],
        'warnings': [],
        'spellchecks': [],
        'sqlite_attaches': [],
        'startup_shutdown': [],
        'http_requests': [],
        'other': []
    }
    
    for log in logs:
        ts = log.get('timestamp')
        log_name = log.get('logName', '')
        
        # Check if it is an HTTP request log
        if 'requests' in log_name or 'httpRequest' in log:
            http_request = log.get('httpRequest', {})
            method = http_request.get('requestMethod', 'UNKNOWN')
            url = http_request.get('requestUrl', '')
            status = http_request.get('status', 0)
            latency = http_request.get('latency', '0s')
            stats['http_requests'].append((ts, method, url, status, latency))
            continue
            
        payload = log.get('textPayload', '')
        if not payload:
            payload = json.dumps(log.get('jsonPayload', {}))
        
        # Filter out verbose gcsfuse configs to avoid clutter
        if "Full Config" in payload or "CLI Flags" in payload or "UserAgent" in payload:
            continue
            
        stats['total'] += 1
        payload_lower = payload.lower()
        
        if "error" in payload_lower:
            stats['errors'].append((ts, payload))
        elif "warning" in payload_lower:
            stats['warnings'].append((ts, payload))
        elif "[spellcheck]" in payload:
            stats['spellchecks'].append((ts, payload))
        elif "attach" in payload_lower:
            stats['sqlite_attaches'].append((ts, payload))
        elif "starting new instance" in payload_lower or "booting worker" in payload_lower or "starting gunicorn" in payload_lower or "shutting down" in payload_lower:
            stats['startup_shutdown'].append((ts, payload))
        else:
            stats['other'].append((ts, payload))
            
    print(f"\nSummary of last 16 hours:")
    print(f"- App logs parsed: {stats['total']}")
    print(f"- HTTP request logs: {len(stats['http_requests'])}")
    print(f"- Errors: {len(stats['errors'])}")
    print(f"- Warnings: {len(stats['warnings'])}")
    print(f"- Spellchecks: {len(stats['spellchecks'])}")
    print(f"- Database Attaches: {len(stats['sqlite_attaches'])}")
    print(f"- Startup/Shutdown events: {len(stats['startup_shutdown'])}")
    print(f"- Other App logs: {len(stats['other'])}")
    
    print("\n--- ERRORS ---")
    if stats['errors']:
        for ts, msg in stats['errors']:
            print(f"[{ts}] {msg}")
    else:
        print("No error logs found.")
        
    print("\n--- WARNINGS ---")
    if stats['warnings']:
        for ts, msg in stats['warnings']:
            print(f"[{ts}] {msg}")
    else:
        print("No warning logs found.")
        
    print("\n--- SPELLCHECKS ---")
    if stats['spellchecks']:
        for ts, msg in stats['spellchecks']:
            print(f"[{ts}] {msg}")
    else:
        print("No spellcheck logs found.")

    print("\n--- DATABASE ATTACHES ---")
    if stats['sqlite_attaches']:
        for ts, msg in stats['sqlite_attaches']:
            print(f"[{ts}] {msg}")
    else:
        print("No SQLite Attach logs found.")
        
    print("\n--- STARTUP / SHUTDOWN (sample/first 10) ---")
    for ts, msg in stats['startup_shutdown'][:10]:
        print(f"[{ts}] {msg}")
        
    print("\n--- HTTP REQUEST LOGS ---")
    # Group HTTP requests to see who/what is hitting the service
    # Filter out health checks (like / or TCP probes if any) if we want, but let's show unique paths
    # Let's count them
    paths = {}
    for ts, method, url, status, latency in stats['http_requests']:
        paths[url] = paths.get(url, 0) + 1
    
    print(f"Unique URLs requested ({len(paths)} total unique):")
    for url, count in sorted(paths.items(), key=lambda x: x[1], reverse=True):
        print(f"  {count}x: {url}")
        
    print("\nAll HTTP Requests chronologically:")
    for ts, method, url, status, latency in stats['http_requests']:
        print(f"[{ts}] {method} {url} -> {status} ({latency})")
        
    print("\n--- OTHER APP LOGS ---")
    for ts, msg in stats['other'][:20]:
        print(f"[{ts}] {msg}")
        
except Exception as e:
    print(f"Error parsing logs: {e}")

