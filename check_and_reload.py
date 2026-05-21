"""
Startup health check + watchdog: runs as a background process when the container starts.
If Elasticsearch is unreachable (VM stopped), it starts the GCE VM automatically
via the Compute Engine API. Once ES is up, if the index is empty it reloads all
hospital pricing data.

After the initial check, a watchdog loop runs every WATCHDOG_INTERVAL_SECONDS and
re-triggers the full recover+reload cycle if the index drops to 0 (e.g. after a
mid-session Spot VM preemption while the Cloud Run container is still alive).

Required environment variables for VM auto-start:
  GCE_PROJECT   — GCP project ID  (e.g. my-project-123)
  GCE_ZONE      — VM zone         (e.g. us-central1-c)
  GCE_INSTANCE  — VM name         (e.g. elasticsearch-vm)
"""
import gzip
import os
import sys
import time

sys.path.insert(0, '/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

from django.conf import settings
from elasticsearch import Elasticsearch
from elasticsearch.helpers import streaming_bulk

INDEX_NAME = getattr(settings, 'ELASTICSEARCH_INDEX', 'hospital_prices')
ES_URL = settings.ELASTICSEARCH_URL
ES_USER = getattr(settings, 'ELASTICSEARCH_USERNAME', None) or None
ES_PASS = getattr(settings, 'ELASTICSEARCH_PASSWORD', None) or None
auth = (ES_USER, ES_PASS) if ES_USER and ES_PASS else None

GCE_PROJECT  = os.environ.get('GCE_PROJECT')
GCE_ZONE     = os.environ.get('GCE_ZONE')
GCE_INSTANCE = os.environ.get('GCE_INSTANCE')

# How often the watchdog checks the document count after initial startup (seconds)
WATCHDOG_INTERVAL_SECONDS = 5 * 60  # 5 minutes

CACHE_FILE = '/app/data/shoppable_cache.json.gz'

def _count_cache_docs():
    """Count documents in the cache file.
    Uses a pre-computed .count file (written by Dockerfile) for instant lookup.
    Falls back to streaming through the gzip file if the count file is absent.
    """
    count_file = CACHE_FILE + '.count'
    if os.path.exists(count_file):
        try:
            with open(count_file) as f:
                return int(f.read().strip())
        except Exception:
            pass
    if not os.path.exists(CACHE_FILE):
        return 0
    try:
        import ijson
        count = 0
        with gzip.open(CACHE_FILE, 'rb') as f:
            for _ in ijson.items(f, 'item'):
                count += 1
        return count
    except Exception as exc:
        print(f"[startup] Could not count cache docs: {exc}", flush=True)
        return 0

CACHE_DOC_COUNT = _count_cache_docs()
print(f"[startup] Cache contains {CACHE_DOC_COUNT} documents.", flush=True)


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


def ensure_data_loaded(label="startup"):
    """
    Ensure Elasticsearch is reachable and the index is populated.
    Starts the VM if ES is unreachable, waits for it to boot, then reloads
    data if the index is empty. Returns True if data is confirmed loaded.
    """
    # --- Phase 1: quick check (5 attempts / 10s) to see if ES is already up ---
    print(f"[{label}] Checking if Elasticsearch is ready...", flush=True)
    es = None
    for attempt in range(5):
        try:
            es = get_es()
            es.info()
            print(f"[{label}] Elasticsearch ready (attempt {attempt + 1})", flush=True)
            break
        except Exception as exc:
            print(f"[{label}] ES not ready (attempt {attempt + 1}/5): {exc}", flush=True)
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
                print(f"[{label}] Elasticsearch ready after VM start (attempt {attempt + 1})", flush=True)
                break
            except Exception as exc:
                print(f"[{label}] ES not ready (attempt {attempt + 1}/{wait_attempts}): {exc}", flush=True)
                time.sleep(2)
                es = None

    if es is None:
        print(f"[{label}] Elasticsearch unreachable. Skipping data reload.", flush=True)
        return False

    # Check doc count AND mapping correctness together.
    # A full doc count alone is not enough: if ES auto-created the index with
    # dynamic mapping (payer_name=text instead of keyword), aggregations break
    # even though all 8050 docs are present. We must verify both before we
    # declare the index healthy and return early.
    index_exists = False
    count = 0
    mapping_ok = False
    try:
        index_exists = bool(es.indices.exists(index=INDEX_NAME))
        if index_exists:
            count = es.count(index=INDEX_NAME)['count']
    except Exception as exc:
        print(f"[{label}] Could not check document count: {exc}", flush=True)
        return False

    if index_exists:
        try:
            m = es.indices.get_mapping(index=INDEX_NAME)
            payer_type = (m[INDEX_NAME]['mappings']['properties']
                          .get('prices', {}).get('properties', {})
                          .get('payer_name', {}).get('type', ''))
            mapping_ok = (payer_type == 'keyword')
        except Exception as exc:
            print(f"[{label}] Could not verify mapping: {exc}", flush=True)

    print(f"[{label}] Elasticsearch: {count} docs, mapping_ok={mapping_ok}", flush=True)

    # Treat >=98% as fully loaded — a small number of docs may consistently
    # fail (e.g. oversized nested objects) and retrying endlessly is wasteful.
    LOAD_THRESHOLD = max(CACHE_DOC_COUNT * 0.98, CACHE_DOC_COUNT - 50) if CACHE_DOC_COUNT > 0 else 0
    if CACHE_DOC_COUNT > 0 and count >= LOAD_THRESHOLD and mapping_ok:
        print(f"[{label}] Data loaded and mapping correct ({count}/{CACHE_DOC_COUNT} docs). Nothing to do.", flush=True)
        return True
    if not mapping_ok and index_exists:
        print(f"[{label}] Wrong mapping detected — forcing full re-index.", flush=True)
    elif count > 0:
        print(f"[{label}] Only {count}/{CACHE_DOC_COUNT} docs — re-indexing all from scratch (idempotent).", flush=True)
    else:
        print(f"[{label}] Index is empty. Loading cache: {CACHE_FILE}", flush=True)

    # Index is incomplete or empty — stream all docs from the beginning.
    # Bulk indexing by _id is idempotent: re-indexing already-present docs is safe
    # and much faster than skipping through the gzip stream to find a resume offset.
    if not os.path.exists(CACHE_FILE):
        print(f"[{label}] Cache file not found at {CACHE_FILE}. Cannot reload.", flush=True)
        return False

    try:
        # Ensure index exists with the correct keyword mappings.
        # If the index was auto-created by ES dynamic mapping (e.g. when the
        # container loaded docs before this script could create it explicitly),
        # payer_name ends up as 'text' and aggregations fail. Detect and fix.
        def _correct_mappings():
            return {
                "properties": {
                    "id":              {"type": "keyword"},
                    "group_key":       {"type": "keyword"},
                    "description":     {"type": "text", "analyzer": "english"},
                    "code":            {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                    "code_type":       {"type": "keyword"},
                    "ms_drg":          {"type": "keyword"},
                    "apr_drg":         {"type": "keyword"},
                    "rc":              {"type": "keyword"},
                    "apc":             {"type": "keyword"},
                    "ndc":             {"type": "keyword"},
                    "cdm":             {"type": "keyword"},
                    "codes": {
                        "type": "nested",
                        "properties": {
                            "value": {"type": "keyword"},
                            "type":  {"type": "keyword"},
                        },
                    },
                    "is_standard_group": {"type": "boolean"},
                    "stats": {
                        "properties": {
                            "min":   {"type": "float"},
                            "max":   {"type": "float"},
                            "avg":   {"type": "float"},
                            "count": {"type": "integer"},
                        },
                    },
                    "prices": {
                        "type": "nested",
                        "properties": {
                            "hospital_id": {"type": "keyword"},
                            "payer_name":  {"type": "keyword"},
                            "plan_name":   {"type": "keyword"},
                            "setting":     {"type": "keyword"},
                            "price":       {"type": "float"},
                        },
                    },
                }
            }

        # mapping_ok and index_exists were computed above; reuse them here.
        needs_create = False
        if index_exists and not mapping_ok:
            print(f"[{label}] Deleting index with wrong mapping.", flush=True)
            es.indices.delete(index=INDEX_NAME)
            needs_create = True
        elif not index_exists:
            needs_create = True

        if needs_create:
            print(f"[{label}] Creating index with explicit keyword mappings.", flush=True)
            es.indices.create(
                index=INDEX_NAME,
                settings={
                    "number_of_shards": 1,
                    "number_of_replicas": 0,
                    "index.mapping.nested_objects.limit": 10000,
                },
                mappings=_correct_mappings(),
            )

        # Stream the full gzip JSON array from the beginning.
        # Indexing by _id is idempotent, so re-indexing existing docs is safe.
        import ijson

        def _actions():
            with gzip.open(CACHE_FILE, 'rb') as f:
                for doc in ijson.items(f, 'item'):
                    yield {"_index": INDEX_NAME, "_id": doc.get("id"), "_source": doc}

        MAX_LOAD_ATTEMPTS = 10
        RETRY_DELAY_SECONDS = 30

        for attempt in range(1, MAX_LOAD_ATTEMPTS + 1):
            try:
                success = 0
                # Use small chunks (10 docs / 2 MB max) so each bulk request
                # completes in seconds and doesn't hit Cloud NAT idle-connection
                # timeouts. Indexing by _id is idempotent so retrying from the
                # start is safe — already-loaded docs are simply overwritten.
                for ok, _ in streaming_bulk(
                    es, _actions(),
                    raise_on_error=False,
                    request_timeout=300,
                ):
                    if ok:
                        success += 1
                    if success % 500 == 0 and success > 0:
                        print(f"[{label}] ...{success}/{CACHE_DOC_COUNT} documents indexed", flush=True)

                es.indices.refresh(index=INDEX_NAME)
                print(f"[{label}] Indexed {success} docs. Total in index: {success}/{CACHE_DOC_COUNT}.", flush=True)
                return success >= CACHE_DOC_COUNT

            except Exception as exc:
                print(f"[{label}] Load attempt {attempt}/{MAX_LOAD_ATTEMPTS} failed: {exc}", flush=True)
                if attempt < MAX_LOAD_ATTEMPTS:
                    print(f"[{label}] Retrying in {RETRY_DELAY_SECONDS}s...", flush=True)
                    time.sleep(RETRY_DELAY_SECONDS)
                    es = get_es()  # fresh connection for next attempt

        print(f"[{label}] All {MAX_LOAD_ATTEMPTS} load attempts failed.", flush=True)
        return False
    except Exception as exc:
        print(f"[{label}] Inline reload failed: {exc}", flush=True)
        return False


# --- Initial startup check ---
ensure_data_loaded(label="startup")

# --- Watchdog loop: re-check every WATCHDOG_INTERVAL_SECONDS ---
# This handles mid-session Spot VM preemptions where the Cloud Run container
# stays alive but ES restarts empty after the VM reboots.
print(f"[watchdog] Starting watchdog loop (interval: {WATCHDOG_INTERVAL_SECONDS}s).", flush=True)
while True:
    time.sleep(WATCHDOG_INTERVAL_SECONDS)
    # Always delegate to ensure_data_loaded — it checks both doc count AND
    # mapping correctness, so a wrong-mapped-but-full index is caught too.
    try:
        ensure_data_loaded(label="watchdog")
    except Exception as exc:
        print(f"[watchdog] Unexpected error: {exc}", flush=True)
