# Hospital Price Transparency Project

This project aims to aggregate, normalize, and analyze machine-readable files (MRF) published by hospitals in compliance with the CMS Hospital Price Transparency final rule. The goal is to make healthcare pricing data more accessible and comparable across different providers.
The policy is outlined at https://www.cms.gov/priorities/key-initiatives/hospital-price-transparency

## Data Source
https://hospitalpricingfiles.org/ aggregates the links/files (CSV/JSON) for each hospital in US

List of current CPT/HCPCS procedure codes:
https://www.cms.gov/medicare/regulations-guidance/physician-self-referral/list-cpt-hcpcs-codes

## Overview
Starting January 1, 2021, hospitals operating in the United States are required to make their standard charges public in two ways:
1. A comprehensive machine-readable file with all items and services.
2. A display of shoppable services in a consumer-friendly format.

This repository focuses on processing the **comprehensive machine-readable files** to extract standard charges, discounted cash prices, and payer-specific negotiated charges.


## Data format
Described here: https://github.com/CMSgov/hospital-price-transparency?tab=readme-ov-file

## Indexing hospital price data

### Full index (all procedures)
```bash
python load_to_es.py
```

### Shoppable services only (recommended for large datasets)
```bash
python load_to_es.py --shoppable-only
```

The `--shoppable-only` flag filters rows to the **70 CMS-mandated shoppable services** defined in the CY 2020 Hospital Price Transparency final rule (84 FR 65524, Table 3). These are the most consumer-relevant procedures: common lab tests, imaging, outpatient surgery, maternity care, and ER visits.

The code list is stored in `reference/shoppable_codes.csv` (70 CPT/HCPCS + 5 MS-DRG codes).

**Size impact:** A large hospital file (~5M rows, ~945 MB uncompressed) is reduced to ~2.5% of its original row count — roughly 97% reduction — while preserving all payer × plan price combinations for each retained procedure.

**Source:** [CMS 2020 Final Rule, Section II.F.3, Table 3](https://www.federalregister.gov/documents/2019/11/27/2019-24931/price-transparency-requirements-for-hospitals-to-make-standard-charges-public)

### Load from pre-built cache (fastest)
```bash
# Step 1 – build the cache once (produces data/shoppable_cache.json.gz)
python extract_shoppable.py

# Step 2 – index from cache; skips all CSV parsing
python load_to_es.py --cached-file data/shoppable_cache.json.gz
```

`extract_shoppable.py` reads every hospital CSV in `data/`, applies the shoppable filter, and writes a compressed JSON file (~few MB). Subsequent index loads use the cache instead of re-parsing gigabytes of raw CSVs, which is significantly faster.

## Deployment
    see DEPLOYMENT.md

### Cloud Run Cost Note
Avoid setting `--min-instances 1` combined with `--no-cpu-throttling` in `deploy.ps1`. That combination keeps an instance running 24/7 with CPU always allocated, which raises costs from near-zero to ~$1/day even with no traffic. The default (`--min-instances 0`) scales to zero when idle and is the recommended setting for low-traffic deployments.

## Print env vars in GCP
gcloud run services describe hospital-price-search --region us-central1 --format="value(spec.template.spec.containers[0].env)"

### Adding/Updating Environment Variables
To add or update keys (like Cloudflare Turnstile) without rebuilding the Docker image, update your `.env` file and run:

```powershell
.\update-env.ps1
```

Alternatively, you can use the **Web Console**:
1. Go to [Google Cloud Run Console](https://console.cloud.google.com/run), click **hospital-price-search**, then **Edit & Deploy New Revision**.
2. Add keys under the **Variables & Secrets** tab and click **Deploy**.

## Other products
https://turquoise.health/
https://www.fairhealthconsumer.org/medical
https://app.billy.app/
https://www.healthprices.org/
https://visiblecharges.com

## Utility Scripts

| Script | Purpose |
|---|---|
| `load_to_es.py` | Index hospital pricing CSVs into Elasticsearch (supports `--shoppable-only` and `--cached-file`) |
| `extract_shoppable.py` | Pre-filter all CSVs to shoppable codes and save as `data/shoppable_cache.json.gz` |
| `extract_expensive_codes.py` | Scan all CSVs for the top-N% most expensive CPT/HCPCS codes and append them to `reference/shoppable_codes.csv` |
| `download_indiana.py` | Download Indiana hospital standard charge files from the hospitalpricingfiles.org API into `data/` |
| `check_and_reload.py` | Background health check (runs inside the Docker container): auto-starts the Elasticsearch VM and re-indexes data after a Spot VM preemption |
| `analyze_csv.py` | Inspect and summarize a hospital pricing CSV |
| `inspect_db.py` | Inspect the SQLite database |
| `create_db.py` | Create/migrate the SQLite database |

### extract_shoppable.py
```bash
python extract_shoppable.py [--output data/shoppable_cache.json.gz] [--data-dir data/]
```
Produces a compact gzip-compressed JSON file from all hospital CSVs filtered to the shoppable code list. Use `load_to_es.py --cached-file` to index from this cache.

### extract_expensive_codes.py
```bash
python extract_expensive_codes.py [--percentile 95] [--data-dir data/] [--out reference/shoppable_codes.csv]
```
Scans all Indiana hospital CSVs, calculates mean negotiated prices per CPT/HCPCS code, and appends the top-N% most expensive codes to `reference/shoppable_codes.csv` (deduplicating against codes already present). Useful for expanding the shoppable code list with high-cost procedures.

### download_indiana.py
```bash
python download_indiana.py [--refresh-manifest] [--limit N] [--dry-run]
```
Downloads Indiana hospital standard charge files from the visiblecharges API into `data/`. Resume-safe (skips already-downloaded files). Prefers ZIP over CSV when both are available.

---

## File structure

```
config/          Django project settings, URLs, WSGI/ASGI
data/            Hospital pricing CSVs (downloaded locally, not committed)
prices/          Django app: views, models, templates, admin
reference/       Static reference data (shoppable_codes.csv, etc.)
load_to_es.py    Elasticsearch indexing script
extract_shoppable.py   Build shoppable cache from CSVs
extract_expensive_codes.py  Expand shoppable code list
download_indiana.py    Download Indiana hospital files
check_and_reload.py    Container auto-recovery script
startup.sh       Docker container entrypoint
deploy.ps1       Build and deploy Docker image to Cloud Run
load_data.ps1    Load pricing data into Elasticsearch
update-env.ps1   Push .env variables to Cloud Run without rebuilding
```
