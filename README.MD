# Hospital Price Transparency Project

This project aims to aggregate, normalize, and analyze machine-readable files (MRF) published by hospitals in compliance with the CMS Hospital Price Transparency final rule. The goal is to make healthcare pricing data more accessible and comparable across different providers.
The policy is outlined at https://www.cms.gov/priorities/key-initiatives/hospital-price-transparency

## Data Source
https://hospitalpricingfiles.org/ aggregates the links/files (CSV/JSON) for each hospital in US

## Overview

Starting January 1, 2021, hospitals operating in the United States are required to make their standard charges public in two ways:
1. A comprehensive machine-readable file with all items and services.
2. A display of shoppable services in a consumer-friendly format.

This repository focuses on processing the **comprehensive machine-readable files** to extract standard charges, discounted cash prices, and payer-specific negotiated charges.


## Data format
Described here: https://github.com/CMSgov/hospital-price-transparency?tab=readme-ov-file

## TODO:

- get funding from advocacy groups for hosting
- use AI to find using "human language" procedure descriptions (R1? or some simple distilled model)
- the data only comes from hospitals, how to get smaller providers?

## Deployment
 gcloud run deploy hospital-price-search \
      --source . \
      --platform managed \
      --region us-central1 \
      --allow-unauthenticated \
      --set-env-vars ELASTICSEARCH_URL="https://elastic:password@34.171.2
03.95:9200/"

## Other products
https://turquoise.health/
https://www.fairhealthconsumer.org/medical
https://app.billy.app/


## File structure
