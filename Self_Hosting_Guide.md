# Self-Hosting Elasticsearch on Google Cloud (Firebase Project)

"Firebase" itself is a suite of serverless tools (Database, Auth, Hosting) and **cannot run persistent servers** like Elasticsearch directly.

However, every Firebase project is backed by a **Google Cloud Platform (GCP)** project. You can create a Virtual Machine (VM) in that same project to host Elasticsearch yourself.

### Architecture
- **Frontend/App**: Hosted on Firebase Hosting / Cloud Run.
- **Database**: Firebase Firestore (for user data) + Elasticsearch (for search).
- **Elasticsearch Host**: A Google Compute Engine (GCE) Virtual Machine running Docker.

### How to set it up (The "Self-Hosted" Path)

1.  **Go to Google Cloud Console**
    - Visit [console.cloud.google.com](https://console.cloud.google.com).
    - Select your Firebase project from the top dropdown.

2.  **Create a VM Instance**
    - Navigate to **Compute Engine** > **VM instances**.
    - Click **Create Instance**.
    - **Machine Type**: Choose at least `e2-medium` (2 vCPU, 4GB RAM). Elasticsearch is memory hungry; smaller instances will crash.
    - **OS**: Ubuntu or Debian.
    - **Firewall**: Check "Allow HTTP/HTTPS traffic".

3.  **Install Elasticsearch on the VM**
    - SSH into the VM (click the "SSH" button in the console).
    - Run these commands to install Docker and Elasticsearch:
      ```bash
      # Install Docker
      sudo apt-get update
      sudo apt-get install -y docker.io

    # 3.1. Stop and remove the old container
    sudo docker stop es01
    sudo docker rm es01

    # 3.2. Run with security and a password (replace 'your_strong_password')
    # wait a couple minuts for the instance to start
    sudo docker run -d --name es01 -p 9200:9200 \
    -e "discovery.type=single-node" \
    -e "xpack.security.enabled=true" \
    -e "ELASTIC_PASSWORD=password" \
    -e "ES_JAVA_OPTS=-Xms1g -Xmx1g" \
    docker.elastic.co/elasticsearch/elasticsearch:9.2.4

4.  **Network Configuration (Crucial)**
    - By default, port 9200 is blocked from the outside world.
    - To connect from your **Local PC**, you need to create a VPC Firewall Rule:
      - Go to **VPC Network** > **Firewall**.
      - Create Rule: `allow-elastic`.
      - Targets: `All instances in the network`.
      - Source IP ranges: `0.0.0.0/0` (Or preferably, just your home IP).
      - Protocols and ports: `tcp:9200`.

5.  **Connect**
    - Get the **External IP** of your VM.
    - Update your `config/settings.py`:
      ```python
      ELASTICSEARCH_URL = 'http://<YOUR_VM_EXTERNAL_IP>:9200'
      ```

### Warning on Costs
- A VM with 4GB RAM (required for stable ES) costs roughly **$25-30/month** on Google Cloud.
- **Elastic Cloud** often has managed plans starting around similar prices but handles backups/security for you.

### Recommendation
For a "Hospital Price Transparency" project:
1.  **Fastest Dev**: Use **Elastic Cloud** (Managed). You get a URL, it just works.
2.  **Cheapest/Hobby**: Self-host on a cheap VPS (e.g., DigitalOcean, Hetzner) for ~$5-10/mo, not Google Cloud.
3.  **Strictly Google**: The VM method above.
