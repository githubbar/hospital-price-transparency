# Activate virtual environment
. .\.venv\Scripts\Activate.ps1

# Load .env into a hash table, substituting the internal ES URL for Cloud Run
# (Cloud Run reaches Elasticsearch via the Serverless VPC connector using the internal IP;
#  load_data.ps1 uses the external IP when run locally)
$envVars = @{}
Get-Content .env | ForEach-Object {
    if ($_ -match '^([^#][^=]+)=(.*)$') {
        $envVars[$matches[1].Trim()] = $matches[2].Trim()
    }
}

if ($envVars.ContainsKey('ELASTICSEARCH_URL_INTERNAL')) {
    $envVars['ELASTICSEARCH_URL'] = $envVars['ELASTICSEARCH_URL_INTERNAL']
    $envVars.Remove('ELASTICSEARCH_URL_INTERNAL')
}

$envVarsString = ($envVars.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ','

# Build the Docker image and deploy to Cloud Run.
# The image includes startup.sh which auto-reloads Elasticsearch data on startup
# if the index is empty (see check_and_reload.py). Run load_data.ps1 separately
# to force a full data reload.
Write-Host "Deploying to Cloud Run..."
gcloud run deploy hospital-price-search `
  --source . `
  --platform managed `
  --region us-central1 `
  --allow-unauthenticated `
  --memory 2Gi `
  --set-env-vars $envVarsString
