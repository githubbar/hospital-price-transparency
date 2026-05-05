# Deployment Guide

This app runs on two Google Cloud services:

- **Google Compute Engine (GCE)** — hosts Elasticsearch in a Docker container
- **Google Cloud Run** — hosts the Django application (serverless, auto-scaling)

---

## Prerequisites

- A Google Cloud project with **billing enabled**
- [`gcloud` CLI](https://cloud.google.com/sdk/docs/install) installed and authenticated (`gcloud auth login`)
- Python virtual environment set up locally (`.venv`)
- A `.env` file at the project root (see [Environment Variables](#environment-variables))

---

## Step 1 — Create the Elasticsearch VM

> **Spot VM**: Spot VMs are significantly cheaper but can be preempted (stopped) by Google at any time. They are suitable for development and non-critical workloads. When preempted, the VM will stop and restart; follow Step 2 carefully to ensure Docker and Elasticsearch start automatically on resume.

1. In the Google Cloud Console, go to **Compute Engine > VM Instances** and click **Create Instance**.
2. Configure the instance:
   - **Region**: `us-central1`
   - **Machine type**: `e2-medium` (2 vCPU, 4 GB RAM — minimum for Elasticsearch)
   - **Boot disk**: Debian 12 or Ubuntu 22.04 LTS
   - **Firewall**: check **Allow HTTP traffic** and **Allow HTTPS traffic**
3. Under **Advanced options > Availability policies**:
   - Set **VM provisioning model** to **Spot**
   - Set **On VM termination** to **Stop** (so data is preserved on preemption)
4. Under **Advanced options > Networking**, reserve a **Static external IP address** so the address doesn't change on reboot.
5. Click **Create** and wait for the VM to start.

---

## Step 2 — Install Elasticsearch on the VM

> **Important**: SSH into the VM directly. Do not run these commands in Cloud Shell.

1. SSH into the VM from the console or with:
   ```bash
   gcloud compute ssh <VM_NAME> --zone us-central1-c
   ```

2. Install Docker and enable it to start on boot:
   ```bash
   sudo apt-get update && sudo apt-get install -y docker.io
   sudo systemctl enable docker
   sudo systemctl start docker
   ```

3. Start Elasticsearch:
   ```bash
   sudo docker run -d --name es01 \
     -p 0.0.0.0:9200:9200 \
     --restart always \
     -v es_data:/usr/share/elasticsearch/data \
     -e "discovery.type=single-node" \
     -e "xpack.security.enabled=false" \
     -e "ES_JAVA_OPTS=-Xms1500m -Xmx1500m" \
     docker.elastic.co/elasticsearch/elasticsearch:9.0.0
   ```

4. Verify it's running (Elasticsearch takes ~60 seconds to start — wait before running this):
   ```bash
   curl http://localhost:9200
   ```
   You should see a JSON response with `"tagline": "You Know, for Search"`.

   If you get no response, check whether the container is still starting up:
   ```bash
   sudo docker logs es01 --tail 20
   ```
   Wait until you see a line containing `"message": "started"` in the logs, then retry the `curl`.

5. Add a **startup script** so Elasticsearch restarts automatically after any VM preemption or reboot. Run this from **Cloud Shell** or a `gcloud`-authenticated terminal (not inside the VM):

   ```bash
   gcloud compute instances add-metadata <VM-NAME> \
     --zone us-central1-c \
     --metadata startup-script='#!/bin/bash
   systemctl enable docker
   systemctl start docker
   docker start es01 2>/dev/null || docker run -d --name es01 \
     -p 0.0.0.0:9200:9200 \
     --restart always \
     -v es_data:/usr/share/elasticsearch/data \
     -e "discovery.type=single-node" \
     -e "xpack.security.enabled=false" \
     -e "ES_JAVA_OPTS=-Xms1500m -Xmx1500m" \
     docker.elastic.co/elasticsearch/elasticsearch:9.0.0'
   ```

   The script first tries to restart the existing `es01` container (preserving stored data). If that fails (e.g. first boot), it creates a new one.

---

## Step 3 — Open Port 9200 in the Firewall

Run the following from **Cloud Shell** or any terminal with `gcloud` authenticated:

```bash
gcloud compute firewall-rules create allow-es-9200 \
  --direction=INGRESS \
  --action=ALLOW \
  --rules=tcp:9200 \
  --source-ranges=0.0.0.0/0 \
  --network=default \
  --priority=1000
```

> For production, replace `0.0.0.0/0` with the specific IP range of your Cloud Run Serverless VPC connector.

---

## Step 4 — Configure Environment Variables

Create a `.env` file in the project root with the following values:

```env
ELASTICSEARCH_URL=http://<VM_EXTERNAL_IP>:9200
ELASTICSEARCH_USERNAME=
ELASTICSEARCH_PASSWORD=
TURNSTILE_SITE_KEY=<your_cloudflare_turnstile_site_key>
TURNSTILE_SECRET_KEY=<your_cloudflare_turnstile_secret_key>
```

- **`ELASTICSEARCH_URL`**: The external IP of the VM from Step 1 (e.g. `http://34.72.x.x:9200`). Find it under **Compute Engine > VM Instances**.
- **`TURNSTILE_*`**: Get these from the [Cloudflare Turnstile dashboard](https://dash.cloudflare.com/?to=/:account/turnstile). For local testing, leave them as the default test values already in `config/settings.py`.
- Leave `ELASTICSEARCH_USERNAME` and `ELASTICSEARCH_PASSWORD` blank if `xpack.security.enabled=false`.

---

## Step 5 — Load Data into Elasticsearch

Run this from your local machine to index all hospital pricing data from the `data/` directory:

```powershell
# Activate virtual environment first
.\.venv\Scripts\Activate.ps1

python load_to_es.py
```

The script reads `ELASTICSEARCH_URL` from your `.env` file automatically. Wait for it to finish — this may take several minutes depending on file size.

---

## Step 6 — Deploy the Application to Cloud Run

Run the deploy script, which builds the Docker image and deploys it to Cloud Run using the values in your `.env` file:

```powershell
.\deploy.ps1
```

When the deployment finishes, `gcloud` will print a **Service URL** like:
```
https://hospital-price-search-xxxx-uc.a.run.app
```

Open that URL in a browser to verify the app is working.

---

## Updating Environment Variables Only

To push updated `.env` values to Cloud Run **without rebuilding the Docker image**:

```powershell
.\update-env.ps1
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Connection refused` on port 9200 | Verify the firewall rule from Step 3 exists and the VM is running |
| Cloud Run app returns 500 | Check logs: **Cloud Run > hospital-price-search > Logs** |
| Elasticsearch container keeps restarting | The VM may be out of memory; upgrade to `e2-standard-2` (8 GB) |
| `curl` to Elasticsearch returns nothing | SSH into the VM and run `sudo docker ps` to confirm `es01` is running; if not, run `sudo docker start es01` |
| Elasticsearch doesn't start after VM reboot | Run `sudo systemctl is-enabled docker` — if not `enabled`, run `sudo systemctl enable docker`; also verify the startup script is set (see Step 2.5) |
| Search returns no results | Re-run `load_to_es.py` and confirm it completed without errors |
