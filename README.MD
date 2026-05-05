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
    see DEPLOYMENT.md

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


## File structure
