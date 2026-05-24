"""One-shot script: delete + reload the hospital_prices index from the local cache file."""
import gzip
import json
import os
import sys

sys.path.insert(0, '.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

from django.conf import settings
from elasticsearch import Elasticsearch
from elasticsearch.helpers import parallel_bulk

INDEX_NAME = getattr(settings, 'ELASTICSEARCH_INDEX', 'hospital_prices')
ES_URL = settings.ELASTICSEARCH_URL
ES_USER = getattr(settings, 'ELASTICSEARCH_USERNAME', None) or None
ES_PASS = getattr(settings, 'ELASTICSEARCH_PASSWORD', None) or None
auth = (ES_USER, ES_PASS) if ES_USER and ES_PASS else None

CACHE_FILE = os.path.join(os.path.dirname(__file__), 'data', 'shoppable_cache.json.gz')

es = Elasticsearch(ES_URL, basic_auth=auth, request_timeout=60) if auth else Elasticsearch(ES_URL, request_timeout=60)
print(f"ES: {ES_URL}  index: {INDEX_NAME}")
print(f"Cache: {CACHE_FILE}")

# Delete existing index so we start clean
if es.indices.exists(index=INDEX_NAME):
    es.indices.delete(index=INDEX_NAME)
    print("Deleted existing index.")

# Create index with explicit mappings
print("Creating index with explicit keyword mappings...")
es.indices.create(
    index=INDEX_NAME,
    settings={
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "index.mapping.nested_objects.limit": 10000,
    },
    mappings={
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
)

def gen():
    with gzip.open(CACHE_FILE, 'rt', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                doc = json.loads(line)
                yield {'_index': INDEX_NAME, '_id': doc.get('id'), '_source': doc}

print("Loading from cache …")
ok = fail = 0
for success, info in parallel_bulk(
    es, gen(),
    thread_count=4,
    chunk_size=50,
    max_chunk_bytes=2 * 1024 * 1024,
    raise_on_exception=False,
    request_timeout=60
):
    if success:
        ok += 1
    else:
        fail += 1
        print(f"  FAILED: {info}", flush=True)
    if (ok + fail) % 500 == 0:
        print(f"  {ok} indexed, {fail} failed", flush=True)

print(f"Done: {ok} indexed, {fail} failed")
es.indices.refresh(index=INDEX_NAME)
print(f"Final count: {es.count(index=INDEX_NAME)['count']}")
