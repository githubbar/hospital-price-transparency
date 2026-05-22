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
from elasticsearch.helpers import streaming_bulk

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

def gen():
    with gzip.open(CACHE_FILE, 'rt', encoding='utf-8') as f:
        docs = json.load(f)
    for doc in docs:
        yield {'_index': INDEX_NAME, '_id': doc.get('id'), '_source': doc}

print("Loading from cache …")
ok = fail = 0
for success, info in streaming_bulk(es, gen(), chunk_size=50, max_retries=3, raise_on_error=False, request_timeout=60):
    if success:
        ok += 1
    else:
        fail += 1
        print(f"  FAILED: {info}", flush=True)
    if (ok + fail) % 500 == 0:
        print(f"  {ok} indexed, {fail} failed", flush=True)

print(f"Done: {ok} indexed, {fail} failed")
print(f"Final count: {es.count(index=INDEX_NAME)['count']}")
