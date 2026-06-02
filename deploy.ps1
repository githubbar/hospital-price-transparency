# Activate virtual environment
. .\.venv\Scripts\Activate.ps1

# Load .env into a hash table
$envVars = @{}
Get-Content .env | ForEach-Object {
    if ($_ -match '^([^#][^=]+)=(.*)$') {
        $envVars[$matches[1].Trim()] = $matches[2].Trim()
    }
}

# Set production SQLITE_DB_DIR environment variable
$envVars["SQLITE_DB_DIR"] = "/mnt/gcs"
$envVarsString = ($envVars.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ','

# Build the Docker image and deploy to Cloud Run.
Write-Host "Deploying to Cloud Run..."
gcloud run deploy hospital-price-search `
  --source . `
  --platform managed `
  --region us-central1 `
  --allow-unauthenticated `
  --min-instances 0 `
  --memory 2Gi `
  --cpu 1 `
  --cpu-boost `
  --clear-vpc-connector `
  --add-volume "name=gcs-db-volume,type=cloud-storage,bucket=hospital-price-db-6a9b0,readonly=true" `
  --add-volume-mount "volume=gcs-db-volume,mount-path=/mnt/gcs" `
  --set-env-vars $envVarsString
