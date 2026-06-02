import subprocess
import json
import sys

# Ensure stdout encodes correctly to avoid UnicodeEncodeError in Windows terminals
sys.stdout.reconfigure(encoding='utf-8')

cmd = [
    'gcloud', 'logging', 'read',
    'resource.type="cloud_run_revision" AND resource.labels.service_name="hospital-price-search"',
    '--limit', '300',
    '--format', 'json'
]

print("Fetching latest 300 logs from gcloud...")
res = subprocess.run(cmd, capture_output=True, text=True, shell=True)

if res.returncode != 0:
    print(f"Error running gcloud: {res.stderr}")
    sys.exit(1)

try:
    logs = json.loads(res.stdout)
    print(f"Successfully retrieved {len(logs)} log entries.")
    
    # Sort chronologically
    logs.sort(key=lambda x: x.get('timestamp', ''))
    
    # Let's print out all lines, filtering out only the extremely verbose GCSFuse Full Config / UserAgent lines
    for log in logs:
        ts = log.get('timestamp')
        payload = log.get('textPayload', '')
        if not payload:
            payload = json.dumps(log.get('jsonPayload', {}))
        
        # Filter out verbose gcsfuse configs to avoid clutter
        if "Full Config" in payload or "CLI Flags" in payload or "UserAgent" in payload:
            continue
            
        print(f"[{ts}] {payload.strip()}")
            
except Exception as e:
    print(f"Error parsing logs: {e}")
