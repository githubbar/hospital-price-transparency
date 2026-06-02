import subprocess
import json
import sys

# Ensure stdout encodes correctly to avoid UnicodeEncodeError in Windows terminals
sys.stdout.reconfigure(encoding='utf-8')

# Query run.googleapis.com/requests logs to see exact HTTP request times and durations for searches
cmd = [
    'gcloud', 'logging', 'read',
    'resource.type="cloud_run_revision" AND logName:"logs/run.googleapis.com%2Frequests" AND httpRequest.requestUrl:"q="',
    '--limit', '100',
    '--format', 'json'
]

print("Fetching latest search request logs...")
res = subprocess.run(cmd, capture_output=True, text=True, shell=True)

if res.returncode != 0:
    print(f"Error running gcloud: {res.stderr}")
    sys.exit(1)

try:
    logs = json.loads(res.stdout)
    # Sort chronologically (newest last) or reverse chronologically (newest first)?
    # Let's sort chronologically by default
    logs.sort(key=lambda x: x.get('timestamp', ''))
    
    search_logs = []
    for log in logs:
        ts = log.get('timestamp')
        httpRequest = log.get('httpRequest', {})
        method = httpRequest.get('requestMethod')
        status = httpRequest.get('status')
        latency = httpRequest.get('latency')
        requestUrl = httpRequest.get('requestUrl', '')
        
        # Make sure it's actually a search query (contains ?q= or &q=)
        if 'q=' in requestUrl:
            search_logs.append({
                'timestamp': ts,
                'method': method,
                'url': requestUrl,
                'status': status,
                'latency': latency
            })
            
    # Get last 5 searches
    last_5 = search_logs[-5:]
    print(f"\nFound {len(search_logs)} total search logs. Last 5 searches (chronological order):")
    for idx, item in enumerate(last_5, 1):
        print(f"{idx}. [{item['timestamp']}] {item['method']} {item['url']} -> Status: {item['status']}, Latency: {item['latency']}")
        
except Exception as e:
    print(f"Error parsing logs: {e}")
