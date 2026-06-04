# Deploying to Google Cloud Run (SQLite Serverless Architecture)

This application is built as a **fully serverless, zero-cost search engine**. Instead of running expensive, persistent search clusters (like Elasticsearch) or cloud database instances (like Cloud SQL), it leverages **Django, SQLite FTS5 (Full-Text Search), and Google Cloud Run**.

---

## Architecture Overview

1. **Local Pricing Aggregation**: Pricing data is parsed, normalized, and indexed locally into a SQLite database (`db.sqlite3`) using SQLite's native `FTS5` extension.
2. **Containerized Database**: The `db.sqlite3` file is copied directly into the Docker container image.
3. **Serverless Hosting (Cloud Run)**: The Django application runs inside Cloud Run, accessing the SQLite database in **read-only mode** (`?mode=ro`).
4. **Zero Infrastructure Cost**: Because Cloud Run scales to zero instances when there is no traffic and there are no persistent VMs or database instances running, the ongoing infrastructure cost is **strictly $0.00** (under free tier limits).

---

## Deployment Prerequisites

1. A Google Cloud project with **billing enabled**
2. [`gcloud` CLI](https://cloud.google.com/sdk/docs/install) installed and authenticated:
   ```bash
   gcloud auth login
   gcloud config set project <YOUR_PROJECT_ID>
   ```
3. A `.env` file at the project root containing Cloudflare Turnstile keys (if applicable).

---

## How to Deploy (Step-by-Step)

### Step 1 — Prepare the Database (Local)
Before deploying, make sure you have generated the SQLite database file (`db.sqlite3`) locally. 

> [!TIP]
> **Database Size Optimization:**
> If you load the full dataset, `db.sqlite3` will grow to roughly **5.2 GB**. While Cloud Run handles this gracefully, uploading a 5.2 GB file over residential/commercial broadband to Cloud Build can take several minutes.
> 
> To keep the image extremely lightweight and make deployments instant, we highly recommend indexing **shoppable services only**:
> ```powershell
> # 1. Extract shoppable codes from CSVs to cache
> python extract_shoppable.py
> 
> # 2. Load from cache into SQLite (creates a compact ~few MB db.sqlite3)
> python load_to_sqlite.py --clean --cached-file data/shoppable_cache.json.gz
> ```

### Step 2 — Deploy to Cloud Run
Run the deployment script:
```powershell
.\deploy.ps1
```

This PowerShell script will read your local `.env` variables and run:
```powershell
gcloud run deploy hospital-price-search `
  --source . `
  --platform managed `
  --region us-central1 `
  --allow-unauthenticated `
  --min-instances 0 `
  --memory 4Gi `
  --cpu 2 `
  --cpu-boost `
  --clear-vpc-connector `
  --set-env-vars $envVarsString
```

Once deployment completes, the `gcloud` CLI will output your public **Service URL** (e.g., `https://hospital-price-search-xxxx-uc.a.run.app`).

---

## Understanding Cold Starts & Performance

Because you have configured `--min-instances 0`, Cloud Run shuts down all container instances when there is no incoming traffic to eliminate costs. When a new user visits the site, Cloud Run must spin up a new container instance. This is a **Cold Start**.

### How Cold Starts Work with a Large Database File

Contrary to typical virtual machine boot times, Cloud Run cold starts are highly optimized:

* **Container Image Streaming**: Cloud Run uses Google's container image streaming technology. It does **not** download the full 5.2 GB container image before starting! Instead, it boots the container in **1–3 seconds** by loading only the system packages and Python/Django files on demand.
* **On-Demand SQLite Queries**: When a search query is executed, SQLite reads only the specific database pages required from the container's virtual filesystem. The first search query after a cold start might experience slightly higher latency (a few hundred milliseconds) as those SQLite database pages are retrieved, but subsequent searches are served instantly from the container instance's memory cache.
* **The Real Deployment Bottleneck**: The primary trade-off of this architecture is **not cold-starts**, but **deploy times**. If you include the full 5.2 GB `db.sqlite3` in the build, the `gcloud` CLI must upload this massive file to Google Cloud Build, which will take several minutes.

### Maximizing Cold Start Performance
We have pre-configured two optimizations to keep container startup fast:
1. **Startup CPU Boost**: When a container instance spins up, Cloud Run temporarily boosts the CPU allocation during the startup phase. With our configuration of `--cpu 2`, Startup CPU Boost will allocate **4 CPUs** during boot to initialize Django and WhiteNoise static asset caching quickly, reverting to 2 CPUs once the container is ready. This reduces cold start time significantly without increasing base idle or running costs.
2. **Read-Only SQLite Connect (`settings.py`)**: When running in Cloud Run (`K_SERVICE` env var detected), the app connects in read-only mode:
   ```python
   'NAME': f"file:{BASE_DIR / 'db.sqlite3'}?mode=ro"
   ```
   This prevents SQLite locking issues, eliminates RAM write penalties, and ensures fast concurrent query execution.

---

## Scaling to 50 States (GCS Bucket Volume Mount)

To scale this application to 50 states without packaging a massive database inside your container image, use **Google Cloud Storage (GCS) volume mounts** (via Cloud Storage FUSE) and separate state-specific SQLite files.

### 1. Build State-Specific Databases Locally
Instead of compiling all data into a single `db.sqlite3` file, you can compile separate files for each state using the `--output-db` flag in `load_to_sqlite.py`:

```powershell
# Build database for California (outputs to ca.sqlite3)
python load_to_sqlite.py --clean --cached-file data/ca_cache.json.gz --output-db ca.sqlite3

# Build database for Indiana (outputs to in.sqlite3)
python load_to_sqlite.py --clean --cached-file data/in_cache.json.gz --output-db in.sqlite3
```

### 2. Upload Databases to a GCS Bucket
1. Create a Google Cloud Storage bucket in your GCP console (e.g., `my-hospital-prices-bucket`) in the **same region** as your Cloud Run service (e.g., `us-central1` to ensure free internal bandwidth and ultra-low latency).
2. Upload all state `.sqlite3` files (e.g. `in.sqlite3`, `ca.sqlite3`, `ky.sqlite3`) directly into the root of the bucket.

### 3. Deploy Cloud Run with GCS Volume Mounts
Deploy your container to Cloud Run while instructing it to mount your GCS bucket dynamically:

```powershell
gcloud run deploy hospital-price-search `
  --source . `
  --platform managed `
  --region us-central1 `
  --allow-unauthenticated `
  --min-instances 0 `
  --memory 4Gi `
  --cpu 2 `
  --cpu-boost `
  --clear-vpc-connector `
  --add-volume "name=gcs-db-volume,type=cloud-storage,bucket=YOUR_GCS_BUCKET_NAME,readonly=true" `
  --add-volume-mount "volume=gcs-db-volume,mount-path=/mnt/gcs" `
  --set-env-vars "SQLITE_DB_DIR=/mnt/gcs"
```

*   **`--add-volume`**: Defines a volume connected to your GCS bucket. Setting `readonly=true` is recommended for high performance and read concurrency.
*   **`--add-volume-mount`**: Mounts the bucket at `/mnt/gcs` inside your container filesystem.
*   **`SQLITE_DB_DIR=/mnt/gcs`**: Instructs the Django search view to load and attach SQLite database files from the `/mnt/gcs` directory instead of the local project root.

> [!TIP]
> **Supercharged Deployments:**
> When using GCS volume mounts, you can add `*.sqlite3` to your `.gcloudignore` and `.dockerignore` files. This keeps your container image size under **50 MB**, dropping your `gcloud run deploy` command duration from several minutes to **less than 15 seconds**!

---

## Updating Environment Variables
To update environment variables (such as Cloudflare Turnstile keys) on Cloud Run without rebuilding or re-uploading the database:

```powershell
.\update-env.ps1
```

---

## Troubleshooting

| Symptom | Probable Cause | Solution |
| :--- | :--- | :--- |
| **Deploy fails / uploads forever** | Zipping and uploading the 5.2 GB SQLite database is timing out. | Re-generate the database using the `--shoppable-only` flag in `load_to_sqlite.py` to shrink the database size to a few megabytes before deploying. |
| **Site returns `Total Procedure Codes: 0`** | `db.sqlite3` was missing locally when the Docker build ran. | Run `.\load_data.ps1` locally first to verify the SQLite file is present and populated, then redeploy. |
| **Container OOMs / Crashes** | Cloud Run is running out of memory during a heavy search. | We have allocated `4Gi` memory and `2` CPUs in `deploy.ps1`. Do not reduce these specs in `deploy.ps1` as large SQLite scans require adequate memory headroom. |
