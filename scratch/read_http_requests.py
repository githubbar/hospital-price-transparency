import subprocess
import json
import sys

# Ensure stdout encodes correctly to avoid UnicodeEncodeError in Windows terminals
sys.stdout.reconfigure(encoding='utf-8')

# Query run.googleapis.com/requests logs to see exact HTTP request times and durations
cmd = [
    'gcloud', 'logging', 'read',
    'resource.type="cloud_run_revision" AND logName:"logs/run.googleapis.com%2Frequests"',
    '--limit', '50',
    '--format', 'json'
]

print("Fetching latest HTTP request logs...")
res = subprocess.run(cmd, capture_output=True, text=True, shell=True)

if res.returncode != 0:
    print(f"Error running gcloud: {res.stderr}")
    sys.exit(1)

try:
    logs = json.loads(res.stdout)
    print(f"Successfully retrieved {len(logs)} request log entries.")
    
    # Sort chronologically
    logs.sort(key=lambda x: x.get('timestamp', ''))
    
    for log in logs:
        ts = log.get('timestamp')
        httpRequest = log.get('httpRequest', {})
        status = httpRequest.get('status')
        latency = httpRequest.get('latency')
        requestUrl = httpRequest.get('requestUrl', '')
        
        if '94621' in requestUrl or 'q=mri' in requestUrl:
            print(f"\n[{ts}] DETAILED LOG:")
            print(json.dumps(log, indent=2))
        else:
            print(f"[{ts}] {httpRequest.get('requestMethod')} {requestUrl} -> Status: {status}, Latency: {latency}")
            
except Exception as e:
    print(f"Error parsing logs: {e}")
