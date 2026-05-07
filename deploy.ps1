# Activate virtual environment
. .\.venv\Scripts\Activate.ps1

# Load .env values into shell variables
$envVars = @{}
Get-Content .env | ForEach-Object {
    if ($_ -match '^([^#][^=]+)=(.*)$') {
        $envVars[$matches[1].Trim()] = $matches[2].Trim()
        Set-Variable -Name $matches[1].Trim() -Value $matches[2].Trim()
    }
}

# Use internal IP for Cloud Run (reachable via VPC connector); external IP is used locally by load_to_es.py
if ($envVars.ContainsKey('ELASTICSEARCH_URL_INTERNAL')) {
    $envVars['ELASTICSEARCH_URL'] = $envVars['ELASTICSEARCH_URL_INTERNAL']
    $envVars.Remove('ELASTICSEARCH_URL_INTERNAL')
}

# Build KEY=VALUE string for --set-env-vars
$envVarsString = ($envVars.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ','

# Load data into Elasticsearch
# Write-Host "Loading data into Elasticsearch..."
# & ".venv\Scripts\python.exe" load_to_es.py
# if ($LASTEXITCODE -ne 0) { Write-Warning "load_to_es.py exited with code $LASTEXITCODE" }

gcloud run deploy hospital-price-search `
  --source . `
  --platform managed `
  --region us-central1 `
  --allow-unauthenticated `
  --set-env-vars $envVarsString
