import subprocess
import json
import sys

# Ensure stdout encodes correctly to avoid UnicodeEncodeError in Windows terminals
sys.stdout.reconfigure(encoding='utf-8')

def fetch_logs(start, end):
    print(f"\n--- Fetching logs between {start} and {end} ---")
    cmd = [
        'gcloud', 'logging', 'read',
        f'resource.type="cloud_run_revision" AND resource.labels.service_name="hospital-price-search" AND timestamp >= "{start}" AND timestamp <= "{end}"',
        '--format', 'json'
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, shell=True)
    if res.returncode != 0:
        print(f"Error running gcloud: {res.stderr}")
        return
        
    try:
        logs = json.loads(res.stdout)
        print(f"Retrieved {len(logs)} log entries.")
        logs.sort(key=lambda x: x.get('timestamp', ''))
        for log in logs:
            ts = log.get('timestamp')
            payload = log.get('textPayload', '')
            if not payload:
                payload = json.dumps(log.get('jsonPayload', {}))
            
            # Avoid showing the massive gcsfuse config lines
            if "Full Config" in payload or "CLI Flags" in payload or "UserAgent" in payload:
                continue
                
            print(f"[{ts}] {payload.strip()}")
    except Exception as e:
        print(f"Error parsing logs: {e}")

# Current window: around 17:20 local (21:20 UTC)
fetch_logs("2026-06-02T21:10:00Z", "2026-06-02T21:30:00Z")
