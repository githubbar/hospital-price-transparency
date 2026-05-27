# Self-Hosting the SQLite Serverless App

Because this application has been migrated from Elasticsearch to a **self-contained SQLite FTS5 (Full-Text Search) architecture**, self-hosting is now **incredibly simple**. You no longer need to provision, secure, and pay for a separate 4 GB RAM database VM.

The entire application—including all code, static files, and the database itself—is packaged inside a single Docker container. You can host this container on almost any Linux server, Virtual Private Server (VPS), or local computer for a fraction of the cost of traditional setups.

---

## Deployment Architecture

*   **Application & Database**: Both live in the same container.
*   **Persistent vs. Immutable Database**: 
    *   **In Production (Read-Only)**: The container starts up with `db.sqlite3` baked in and queries it in read-only mode (`?mode=ro`). This is perfect for high concurrency and zero-maintenance hosting.
    *   **Updating Data**: When pricing data changes, you re-run the build locally and push a new Docker container. This is called **immutable deployment**.

---

## How to Self-Host (On a Cheap VPS or local server)

Any standard hosting provider that supports Docker (such as DigitalOcean, Linode, Hetzner, AWS LightSail, or a private server) can run this container for as little as **$4–$5/month**.

### Step 1: Initialize the Database (Locally)
1. Generate the SQLite database locally using the shoppable filter:
   ```bash
   python extract_shoppable.py
   python load_to_sqlite.py --clean --cached-file data/shoppable_cache.json.gz
   ```
2. Ensure `db.sqlite3` is present in your project root.

### Step 2: Build the Docker Image
1. Build the Docker container locally:
   ```bash
   docker build -t hospital-price-search:latest .
   ```

### Step 3: Run the Container
You can run the container locally or on your remote VPS:

```bash
docker run -d \
  -p 8080:8080 \
  --name price-search \
  -e DEBUG=False \
  -e TURNSTILE_SITE_KEY=your_key \
  -e TURNSTILE_SECRET_KEY=your_secret \
  hospital-price-search:latest
```

*   **`-p 8080:8080`**: Maps port 8080 on your host machine to port 8080 in the container.
*   **`-e DEBUG=False`**: Disables Django debug mode for security in production.
*   **`--name price-search`**: Names the running container.

Access your website at `http://<your-vps-ip>:8080`.

---

## Updating Data / Rebuilding
To update hospital CSVs or synonym mappings:
1. Re-run `load_to_sqlite.py` locally to rebuild the `db.sqlite3` file.
2. Re-build the Docker image: `docker build -t hospital-price-search:latest .`
3. Restart the container on your host:
   ```bash
   docker stop price-search
   docker rm price-search
   docker run -d -p 8080:8080 --name price-search ... hospital-price-search:latest
   ```

---

## Comparison: Cloud Run vs. Traditional VPS

| Metric | Google Cloud Run (Serverless) | Self-Hosted VPS (e.g. DigitalOcean) |
| :--- | :--- | :--- |
| **Ongoing Cost** | **$0.00** (Within Cloud Run / Build free tier) | **$4.00 – $5.00 / month** |
| **Idle Sleep** | Yes, container scales to zero when traffic stops. | No, container runs 24/7. |
| **Cold Starts** | **~3–8 seconds** on the first request after idle. | **Instant (0ms)** since server is always awake. |
| **Deployment Speed** | Slow (uploads the database to GCP on every deploy). | Instant (if building directly on the VPS). |
| **Scaling** | Automatically scales up to hundreds of instances. | Limited to the CPU/RAM of your single VPS. |
