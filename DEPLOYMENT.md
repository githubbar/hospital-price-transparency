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
    *   SSH into the VM.
    *   Install Docker:
        ```bash
        sudo apt-get update && sudo apt-get install -y docker.io
        ```
    *   Run Elasticsearch:
        ```bash
        sudo docker run -d --name es01 -p 9200:9200 --restart always \
          -e "discovery.type=single-node" \
          -e "xpack.security.enabled=false" \
          -e "ES_JAVA_OPTS=-Xms2g -Xmx2g" \
          docker.elastic.co/elasticsearch/elasticsearch:8.11.1
        ```

3.  **Allow Traffic**:
    *   Go to **VPC Network > Firewall**.
    *   Create rule `allow-es-9200`.
    *   **Targets**: All instances in network.
    *   **Source IP**: `0.0.0.0/0` (For testing) OR the specific subnet of your Cloud Run setup (Serverless VPC Access) for security.
    *   **Ports**: `tcp:9200`.

---

## Phase 2: Data Population (Run Locally)

Now that the VM is up, fill it with data from your local machine.

1.  **Get the IP**: Copy the External IP of your VM (e.g., `35.x.x.x`).
2.  **Run the Loader**:
    ```powershell
    # In VS Code Terminal
    $env:ELASTICSEARCH_URL = "http://35.x.x.x:9200"
    & "X:/Hospital Price Transparency/.venv/Scripts/python.exe" load_to_es.py
    ```

---

## Phase 3: The Application (Cloud Run)

Deploy the Django code to Google Cloud Run.

1.  **Install gcloud CLI** (if not installed) or use Cloud Shell.
2.  **Deploy**:
    Run this command in your project root:
    ```bash
    gcloud run deploy hospital-price-search \
      --source . \
      --platform managed \
      --region us-central1 \
      --allow-unauthenticated \
      --set-env-vars ELASTICSEARCH_URL="https://elastic:password@34.171.2
03.95:9200/"
    ```

3.  **Visit the URL**:
    Cloud Run will output a Service URL (e.g., `https://hospital-price-search-xyz-uc.a.run.app`).

## Troubleshooting

-   **Connection Refused?**: Check the GCP Firewall Rule for port 9200.
-   **App Error?**: Check Cloud Run logs in the console.
-   **Elastic Crashing?**: Check VM memory usage. Java needs RAM!
