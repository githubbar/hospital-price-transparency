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
ELASTICSEARCH_URL_INTERNAL=http://<VM_INTERNAL_IP>:9200
ELASTICSEARCH_USERNAME=
ELASTICSEARCH_PASSWORD=
TURNSTILE_SITE_KEY=<your_cloudflare_turnstile_site_key>
TURNSTILE_SECRET_KEY=<your_cloudflare_turnstile_secret_key>
GCE_PROJECT=<your_gcp_project_id>
GCE_ZONE=us-central1-c
GCE_INSTANCE=<your_vm_name>
```

- **`ELASTICSEARCH_URL`**: The **external** IP of the VM (e.g. `http://34.x.x.x:9200`). Used by `load_to_es.py` when run locally. Find it under **Compute Engine > VM Instances**.
- **`ELASTICSEARCH_URL_INTERNAL`**: The **internal** IP of the VM (e.g. `http://10.128.0.x:9200`). Used by Cloud Run to reach Elasticsearch via the Serverless VPC connector. Find the internal IP under **Compute Engine > VM Instances** (the `10.x.x.x` address).
- **`TURNSTILE_*`**: Get these from the [Cloudflare Turnstile dashboard](https://dash.cloudflare.com/?to=/:account/turnstile). For local testing, leave them as the default test values already in `config/settings.py`.
- **`GCE_PROJECT`**: Your GCP project ID (e.g. `my-project-123`). Found in the Cloud Console header or via `gcloud config get-value project`.
- **`GCE_ZONE`**: The zone where the Elasticsearch VM lives (e.g. `us-central1-c`).
- **`GCE_INSTANCE`**: The VM instance name shown under **Compute Engine > VM Instances**.
- Leave `ELASTICSEARCH_USERNAME` and `ELASTICSEARCH_PASSWORD` blank if `xpack.security.enabled=false`.

---

## Step 5 — Grant Cloud Run Permission to Start the VM

So that Cloud Run can automatically start the Elasticsearch VM after a preemption, grant the Cloud Run service account the `compute.instanceAdmin.v1` role:

```bash
gcloud projects add-iam-policy-binding <GCE_PROJECT> \
  --member="serviceAccount:<PROJECT_NUMBER>-compute@developer.gserviceaccount.com" \
  --role="roles/compute.instanceAdmin.v1"
```

Find your project number at **Cloud Console > Home > Project info**, or via:
```bash
gcloud projects describe <GCE_PROJECT> --format="value(projectNumber)"
```

---

## Step 6 — Deploy the Application to Cloud Run

Deploy scripts are split into two separate scripts:

**Build and deploy the Docker image** (run after code changes):
```powershell
.\deploy.ps1
```

**Load hospital pricing data into Elasticsearch** (run once after first deploy, or after data files change):
```powershell
.\load_data.ps1
```

The data load reads `ELASTICSEARCH_URL` (external IP) from your `.env` file — this may take several minutes depending on file size.

> **Tip — faster re-loads with a pre-built cache:** If you add or update hospital CSV files frequently, pre-build a shoppable cache first to speed up subsequent loads:
> ```powershell
> # Build the cache once (produces data/shoppable_cache.json.gz, ~few MB)
> python extract_shoppable.py
>
> # Index from cache instead of re-parsing all CSVs
> python load_to_es.py --cached-file data/shoppable_cache.json.gz
> ```

When the deployment finishes, `gcloud` will print a **Service URL** like:
```
https://hospital-price-search-xxxx-uc.a.run.app
```

Open that URL in a browser to verify the app is working.

---

## Updating Environment Variables

To push updated `.env` values to Cloud Run **without rebuilding the Docker image**:

```powershell
.\update-env.ps1
```

---

## Spot VM Data Loss

Spot VMs are preempted by Google Cloud roughly once per day. When this happens the VM stops, Elasticsearch goes offline, and the site shows **Total Procedure Codes: 0**.

**Fully automatic recovery:** The container image includes `check_and_reload.py`, which runs in the background on every Cloud Run container startup. The recovery sequence is:

1. Quickly checks if Elasticsearch is reachable (5 attempts over ~10 seconds).
2. If unreachable, calls the **Compute Engine API** to start the stopped VM automatically.
3. Waits up to **120 seconds** for the VM to boot and Elasticsearch to initialize.
4. If the index is empty after ES comes up, re-indexes all hospital pricing CSVs.

No manual action is required. The first visitor after a preemption triggers the recovery. The site responds immediately (gunicorn starts before the health check), but search results may be empty for ~10–15 minutes while the VM boots and data reloads.

**Manual reload:** If you need to force a data reload immediately:
```powershell
.\load_data.ps1
```

**Permanent fix:** To eliminate Spot VM preemptions entirely, change the VM provisioning model from **Spot** to **Standard** in **Compute Engine > VM Instances > Edit**. An `e2-medium` standard VM costs ~$35/month.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Connection refused` on port 9200 | Verify the firewall rule from Step 3 exists and the VM is running |
| Cloud Run app returns 500 | Check logs: **Cloud Run > hospital-price-search > Logs** |
| Site shows `Total Procedure Codes: 0` | Spot VM was preempted. The first visitor will trigger auto-recovery (VM start + data reload). Wait ~15 minutes, then refresh. If it persists, check Cloud Run logs for `[startup]` lines. |
| Elasticsearch container keeps restarting | The VM may be out of memory; upgrade to `e2-standard-2` (8 GB) |
| `curl` to Elasticsearch returns nothing | SSH into the VM and run `sudo docker ps` to confirm `es01` is running; if not, run `sudo docker start es01` |
| Elasticsearch doesn't start after VM reboot | Run `sudo systemctl is-enabled docker` — if not `enabled`, run `sudo systemctl enable docker`; also verify the startup script is set (see Step 2.5) |
| Search returns no results after reload | Re-run `.\load_data.ps1` and confirm it completed without errors |
