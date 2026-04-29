# Deployment Guide: Firebase + Self-Hosted Elasticsearch

This architecture uses **Google Compute Engine (GCE)** to host the database (Elasticsearch) and **Google Cloud Run** to host the Django application.

## Prerequisites
1.  **Google Cloud Project**: Created and Billing enabled.
    *   *Note: If you created a Firebase project, you already have a Google Cloud Project! They are the same thing. Just verify that billing is enabled for it in the Google Cloud Console.*
2.  **Google Cloud SDK**: Installed locally (optional, but helpful).

---

## Phase 1: The Database (Google Compute Engine)

Since we cannot run Elasticsearch on serverless Firebase, we use a VM.

1.  **Create VM**:
    *   Go to **Compute Engine > VM Instances**.
    *   Create New Instance:
        *   **Region**: `us-central1` (Keep consistent).
        *   **Type**: `e2-medium` (4GB RAM).
        *   **OS**: Debian 11 or Ubuntu.
        *   **Firewall**: Allow HTTP/HTTPS.
    *   **Advanced Networking**:
        *   Reserve a **Static External IP** for this VM so your app configuration doesn't break on reboot.

2.  **Install Elasticsearch**:
    *   **!!!!!!!!!!!!!!!!!!!!!!! SSH into the VM: DO NOT RUN THIS IN CLOUD SHELL**
    *   Install Docker:
        ```bash
        sudo apt-get update && sudo apt-get install -y docker.io
        ```
    *   Run Elasticsearch:
        ```bash
        sudo docker stop es01 && sudo docker rm es01
        sudo docker run -d --name es01 -p 0.0.0.0:9200:9200 --restart always \
        -v es_data:/usr/share/elasticsearch/data \
        -e "discovery.type=single-node" \
        -e "xpack.security.enabled=false" \
        -e "ES_JAVA_OPTS=-Xms1500m -Xmx1500m" \
        docker.elastic.co/elasticsearch/elasticsearch:9.0.0
        ```

3.  **Allow Traffic**:
    *   Go to **VPC Network > Firewall**.
    *   Create rule `allow-es-9200`.
    *   **Targets**: All instances in network.
    *   **Source IP**: `0.0.0.0/0` (For testing) OR the specific subnet of your Cloud Run setup (Serverless VPC Access) for security.
    *   **Ports**: `tcp:9200`.
    or from cloud shell:
        gcloud compute firewall-rules delete allow-es-9200 --quiet
        gcloud compute firewall-rules create allow-es-9200 \
        --direction=INGRESS \
        --action=ALLOW \
        --rules=tcp:9200 \
        --source-ranges=0.0.0.0/0 \
        --network=default \
        --priority=1000

---

## Phase 2: Data Population (Run Locally)
TODO: Get External VM IP

    Go to Compute Engine > VM Instances in the Google Cloud Console.
    Find the VM where Elasticsearch is installed.
    Copy the External IP.

Now that the VM is up, fill it with data from your local machine.

1.  **Get the IP**: Copy the External IP of your VM (e.g., `35.x.x.x`).
2.  **Run the Loader**:
    ```powershell
    # In VS Code Terminal
    $env:ELASTICSEARCH_URL = "http://34.72.209.69:9200"
    & "X:/Hospital Price Transparency/.venv/Scripts/python.exe" load_to_es.py
    ```
---

## Phase 3: The Application (Cloud Run)

Deploy the Django code to Google Cloud Run.

1.  **Install gcloud CLI** (if not installed) or use Cloud Shell.
2.  **Deploy** (full build + deploy, reads credentials from `.env`):
    ```powershell
    .\deploy.ps1
    ```
    To update only environment variables without rebuilding:
    ```powershell
    .\update-env.ps1
    ```

3.  **Visit the URL**:
    Cloud Run (previous step) will output a Service URL (e.g., `https://hospital-price-search-xyz-uc.a.run.app`).

## Troubleshooting

-   **Connection Refused?**: Check the GCP Firewall Rule for port 9200.
-   **App Error?**: Check Cloud Run logs in the console.
-   **Elastic Crashing?**: Check VM memory usage. Java needs RAM!
