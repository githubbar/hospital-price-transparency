"""
Startup health check: runs as a background process when the container starts.
If Elasticsearch is unreachable (VM stopped), it starts the GCE VM automatically
via the Compute Engine API. Once ES is up, if the index is empty it reloads all
hospital pricing data.

Required environment variables for VM auto-start:
  GCE_PROJECT   — GCP project ID  (e.g. my-project-123)
  GCE_ZONE      — VM zone         (e.g. us-central1-c)
  GCE_INSTANCE  — VM name         (e.g. elasticsearch-vm)
"""
import os
import sys
import time
import subprocess

sys.path.insert(0, '/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

from django.conf import settings
from elasticsearch import Elasticsearch

INDEX_NAME = getattr(settings, 'ELASTICSEARCH_INDEX', 'hospital_prices')
ES_URL = settings.ELASTICSEARCH_URL
ES_USER = getattr(settings, 'ELASTICSEARCH_USERNAME', None) or None
ES_PASS = getattr(settings, 'ELASTICSEARCH_PASSWORD', None) or None
auth = (ES_USER, ES_PASS) if ES_USER and ES_PASS else None

GCE_PROJECT  = os.environ.get('GCE_PROJECT')
GCE_ZONE     = os.environ.get('GCE_ZONE')
GCE_INSTANCE = os.environ.get('GCE_INSTANCE')


def get_es():
    return Elasticsearch(ES_URL, basic_auth=auth, verify_certs=False, request_timeout=5)


def try_start_vm():
    """Start the GCE VM if project/zone/instance are configured."""
    if not all([GCE_PROJECT, GCE_ZONE, GCE_INSTANCE]):
        print("[startup] GCE_PROJECT/GCE_ZONE/GCE_INSTANCE not set — skipping VM auto-start.", flush=True)
        return False
    try:
        from google.cloud import compute_v1
        client = compute_v1.InstancesClient()
        req = compute_v1.StartInstanceRequest(
            project=GCE_PROJECT,
            zone=GCE_ZONE,
            instance=GCE_INSTANCE,
        )
        op = client.start(request=req)
        print(f"[startup] VM start requested (operation: {op.name}). Waiting for boot...", flush=True)
        return True
    except Exception as exc:
        print(f"[startup] Could not start VM: {exc}", flush=True)
        return False


# --- Phase 1: quick check (5 attempts / 10s) to see if ES is already up ---
print("[startup] Checking if Elasticsearch is ready...", flush=True)
es = None
for attempt in range(5):
    try:
        es = get_es()
        es.info()
        print(f"[startup] Elasticsearch ready (attempt {attempt + 1})", flush=True)
        break
    except Exception as exc:
        print(f"[startup] ES not ready (attempt {attempt + 1}/5): {exc}", flush=True)
        time.sleep(2)
        es = None

# --- Phase 2: if still down, try to start the VM then keep waiting ---
if es is None:
    vm_started = try_start_vm()
    # Elasticsearch takes ~90s to start after a VM boot
    wait_attempts = 60 if vm_started else 25  # up to 120s if we started the VM, 50s otherwise
    for attempt in range(wait_attempts):
        try:
            es = get_es()
            es.info()
            print(f"[startup] Elasticsearch ready after VM start (attempt {attempt + 1})", flush=True)
            break
        except Exception as exc:
            print(f"[startup] ES not ready (attempt {attempt + 1}/{wait_attempts}): {exc}", flush=True)
            time.sleep(2)
            es = None

if es is None:
    print("[startup] Elasticsearch unreachable. Skipping data reload.", flush=True)
    sys.exit(0)

# Check how many documents are indexed
try:
    if es.indices.exists(index=INDEX_NAME):
        count = es.count(index=INDEX_NAME)['count']
    else:
        count = 0
except Exception as exc:
    print(f"[startup] Could not check document count: {exc}", flush=True)
    sys.exit(0)

print(f"[startup] Elasticsearch document count: {count}", flush=True)

if count > 0:
    print("[startup] Data already loaded. Nothing to do.", flush=True)
    sys.exit(0)

# Index is empty — reload from the pre-built shoppable cache (fast path, no CSV parsing)
CACHE_FILE = '/app/data/shoppable_cache.json.gz'
if os.path.exists(CACHE_FILE):
    print(f"[startup] Index is empty. Reloading from cache: {CACHE_FILE}", flush=True)
    cmd = [sys.executable, '/app/load_to_es.py', '--cached-file', CACHE_FILE, '--clean']
else:
    print("[startup] Index is empty. Cache file not found, falling back to full CSV reload...", flush=True)
    cmd = [sys.executable, '/app/load_to_es.py', '--clean']
result = subprocess.run(cmd, cwd='/app')
if result.returncode == 0:
    print("[startup] Data reload completed successfully.", flush=True)
else:
    print(f"[startup] Data reload finished with exit code {result.returncode}.", flush=True)
