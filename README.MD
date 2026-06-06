# Hospital Price Transparency Project

This project aims to aggregate, normalize, and analyze machine-readable files (MRF) published by hospitals in compliance with the CMS Hospital Price Transparency final rule. The goal is to make healthcare pricing data more accessible and comparable across different providers.
The policy is outlined at https://www.cms.gov/priorities/key-initiatives/hospital-price-transparency

## Data Sources

### Hospital Pricing & Codes
* **Hospital pricing files**: [hospitalpricingfiles.org](https://hospitalpricingfiles.org/) aggregates the links/files (CSV/JSON) for each hospital in the US.
* **CPT/HCPCS procedure codes**: [CMS List of CPT/HCPCS Codes](https://www.cms.gov/medicare/regulations-guidance/physician-self-referral/list-cpt-hcpcs-codes) lists current clinical procedure codes.

### Insurance / Payer Data (For Canonization)
* **HealthCare.gov Plan Finder Data**: [CMS Plan Finder Data](https://www.cms.gov/marketplace/resources/data/healthcaregov-plan-finder-data) provides comprehensive data on Marketplace health and dental plans, including Public Use Files (PUFs) and QHP Landscape files containing benefits, cost-sharing, premiums, and issuer names/IDs. This represents a separate dimension of price transparency data and can serve as a reference registry for mapping/canonizing the messy, non-standardized insurance payer strings found in hospital pricing files.

## Overview
Starting January 1, 2021, hospitals operating in the United States are required to make their standard charges public in two ways:
1. A comprehensive machine-readable file with all items and services.
2. A display of shoppable services in a consumer-friendly format.

This repository focuses on processing the **comprehensive machine-readable files** to extract standard charges, discounted cash prices, and payer-specific negotiated charges.

Readings on price transparency for independent imaging centers:
https://ajronline.org/doi/10.2214/AJR.22.27326
https://ajronline.org/doi/full/10.2214/AJR.21.25976


## Data format
Described here: https://github.com/CMSgov/hospital-price-transparency?tab=readme-ov-file

## Indexing hospital price data

The hospital price transparency data must be parsed, normalized, and indexed into the local SQLite database (`db.sqlite3`). This database is then baked into the container image for production searches.

### Shoppable services only (recommended & default for Cloud Run deployments)
To keep the database lightweight, fast to query, and quick to upload during deployments, filter rows to the **70 CMS-mandated shoppable services**:
```bash
python load_to_sqlite.py --clean --shoppable-only
```

### Full index (all procedures)
To index every single item and procedure code from all CSV files:
```bash
python load_to_sqlite.py --clean
```

### Load from pre-built cache (fastest)
```bash
# Step 1 – build the cache once (produces data/shoppable_cache.json.gz)
python extract_shoppable.py

# Step 2 – index from cache; skips all CSV parsing
python load_to_sqlite.py --clean --cached-file data/shoppable_cache.json.gz
```

`extract_shoppable.py` reads every hospital CSV in `data/`, applies the shoppable filter, and writes a compressed JSON file (~few MB). Subsequent database loads use the cache instead of re-parsing gigabytes of raw CSVs, which is significantly faster.


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
| `load_to_sqlite.py` | Index hospital pricing CSVs into SQLite (supports `--shoppable-only` and `--cached-file`) |
| `extract_shoppable.py` | Pre-filter all CSVs to shoppable codes and save as `data/shoppable_cache.json.gz` |
| `extract_expensive_codes.py` | Scan all CSVs for the top-N% most expensive CPT/HCPCS codes and append them to `reference/shoppable_codes.csv` |
| `download_indiana.py` | Download Indiana hospital standard charge files from the hospitalpricingfiles.org API into `data/` |
| `analyze_csv.py` | Inspect and summarize a hospital pricing CSV |
| `inspect_db.py` | Inspect the SQLite database |
| `create_db.py` | Create/migrate the SQLite database |

### extract_shoppable.py
```bash
python extract_shoppable.py [--output data/shoppable_cache.json.gz] [--data-dir data/]
```
Produces a compact gzip-compressed JSON file from all hospital CSVs filtered to the shoppable code list. Use `load_to_sqlite.py --cached-file` to index from this cache.

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
load_to_sqlite.py SQLite indexing script
extract_shoppable.py   Build shoppable cache from CSVs
extract_expensive_codes.py  Expand shoppable code list
download_indiana.py    Download Indiana hospital files
startup.sh       Docker container entrypoint
deploy.ps1       Build and deploy Docker image to Cloud Run
load_data.ps1    Load pricing data into SQLite
update-env.ps1   Push .env variables to Cloud Run without rebuilding
```
