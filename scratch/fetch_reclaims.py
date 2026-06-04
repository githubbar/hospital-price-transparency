import subprocess
import json
import sys
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding='utf-8')

# Calculate last 24 hours in UTC
now = datetime.now(timezone.utc)
start_time = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
end_time = now.strftime("%Y-%m-%dT%H:%M:%SZ")

print(f"Querying GCP logs from {start_time} to {end_time}...")

project = "hospital-price-transpare-6a9b0"

# Query 1: Cloud Run container instances scaling down/shutting down
filter_cr = f'resource.type="cloud_run_revision" AND resource.labels.service_name="hospital-price-search" AND timestamp >= "{start_time}" AND timestamp <= "{end_time}"'
cmd_cr = [
    'gcloud', 'logging', 'read',
    filter_cr,
    '--project', project,
    '--format', 'json'
]

print("Fetching Cloud Run logs...")
res_cr = subprocess.run(cmd_cr, capture_output=True, text=True, shell=True)

# Query 2: GCE Preemption logs (in case of Spot VM)
filter_gce = f'resource.type="gce_instance" AND protoPayload.methodName="compute.instances.preempted" AND timestamp >= "{start_time}" AND timestamp <= "{end_time}"'
cmd_gce = [
    'gcloud', 'logging', 'read',
    filter_gce,
    '--project', project,
    '--format', 'json'
]

print("Fetching GCE Spot VM preemption logs...")
res_gce = subprocess.run(cmd_gce, capture_output=True, text=True, shell=True)

# Process Cloud Run logs
cr_shutdowns = 0
cr_startups = 0
cr_events = []

if res_cr.returncode == 0 and res_cr.stdout.strip():
    try:
        cr_logs = json.loads(res_cr.stdout)
        print(f"Retrieved {len(cr_logs)} Cloud Run log entries.")
        for log in cr_logs:
            payload = log.get('textPayload', '')
            if not payload:
                payload = json.dumps(log.get('jsonPayload', {}))
            payload_lower = payload.lower()
            ts = log.get('timestamp')
            
            # Check for instance shutdowns or startup events
            if "shutting down" in payload_lower or "container called exit" in payload_lower:
                cr_shutdowns += 1
                cr_events.append((ts, "SHUTDOWN", payload))
            elif "starting new instance" in payload_lower or "booting worker" in payload_lower or "starting gunicorn" in payload_lower:
                cr_startups += 1
                cr_events.append((ts, "STARTUP", payload))
    except Exception as e:
        print(f"Error parsing Cloud Run logs: {e}")
else:
    print(f"No Cloud Run logs found or error: {res_cr.stderr}")

# Process GCE logs
gce_preemptions = 0
gce_events = []

if res_gce.returncode == 0 and res_gce.stdout.strip():
    try:
        gce_logs = json.loads(res_gce.stdout)
        gce_preemptions = len(gce_logs)
        print(f"Retrieved {gce_preemptions} GCE preemption log entries.")
        for log in gce_logs:
            ts = log.get('timestamp')
            proto_payload = log.get('protoPayload', {})
            resource_name = proto_payload.get('resourceName', 'unknown-instance')
            gce_events.append((ts, resource_name))
    except Exception as e:
        print(f"Error parsing GCE logs: {e}")
else:
    # If the error is permission denied, we report it.
    if "PERMISSION_DENIED" in res_gce.stderr or "Permission denied" in res_gce.stderr:
        print("Note: GCE log check returned permission denied.")
    else:
        print(f"No GCE preemption logs found.")

print("\n=== RESULTS FOR THE LAST 24 HOURS ===")
print(f"GCP Spot VM Preemptions: {gce_preemptions}")
if gce_events:
    for ts, instance in gce_events:
        print(f"  - [{ts}] Instance '{instance}' was preempted.")

print(f"\nCloud Run Instance Terminations (Scale to Zero / Idle Shutdowns): {cr_shutdowns}")
print(f"Cloud Run Instance Startups (Cold Starts): {cr_startups}")

if cr_events:
    cr_events.sort(key=lambda x: x[0])
    print("\nTimeline of Cloud Run Instance Events:")
    for ts, event_type, msg in cr_events:
        # Trim message
        trimmed_msg = msg.strip().replace('\n', ' ')
        if len(trimmed_msg) > 100:
            trimmed_msg = trimmed_msg[:97] + "..."
        print(f"  - [{ts}] {event_type}: {trimmed_msg}")
