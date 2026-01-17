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

## TODO
TODO:get funding from advocacy groups for hosting
TODO: use AI to find using "human language"
TODO: procedure descriptions (R1? or some simple distilled model)
TODO: the data only comes from hospitals, how to get smaller providers?
TODO: why certain imaging centers show and others don't on https://hospitalpricingfiles.org/

## Deployment
**PowerShell (Windows)**
```powershell
gcloud run deploy hospital-price-search `
      --source . `
      --platform managed `
      --region us-central1 `
      --allow-unauthenticated `
      --update-env-vars ELASTICSEARCH_URL="
      https://elastic:your_password@34.171.203.95:9200/,
      TURNSTILE_SECRET_KEY=MY_SECRET_KEY,
      TURNSTILE_SITE_KEY=MY_SITE_KEY"
```

## Print env vars in GCP
gcloud run services describe hospital-price-search --region us-central1 --format="value(spec.template.spec.containers[0].env)"

### Adding/Updating Environment Variables
To add keys like Cloudflare Turnstile without redeploying the whole app:

**Command Line:**
```powershell
gcloud run services update hospital-price-search `
    --region us-central1 `
    --update-env-vars "TURNSTILE_SECRET_KEY=your_secret_key,TURNSTILE_SITE_KEY=your_site_key"
```

**Web Console:**
1. Go to [Google Cloud Run Console](https://console.cloud.google.com/run).
2. Click on **hospital-price-search**.
3. Click **Edit & Deploy New Revision** (top bar).
4. Go to **Variables & Secrets** tab.
5. Add your keys there and click **Deploy**.

## Other products
https://turquoise.health/
https://www.fairhealthconsumer.org/medical
https://app.billy.app/


## File structure
