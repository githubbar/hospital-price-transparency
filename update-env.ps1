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

# Override ELASTICSEARCH_URL with embedded credentials
$envVars['ELASTICSEARCH_URL'] = $ELASTICSEARCH_URL -replace '^(https?://)', "`${1}${ELASTICSEARCH_USERNAME}:${ELASTICSEARCH_PASSWORD}@"

# Build KEY=VALUE string for --update-env-vars
$envVarsString = ($envVars.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ','

# Load data into Elasticsearch
Write-Host "Loading data into Elasticsearch..."
& ".venv\Scripts\python.exe" load_to_es.py
if ($LASTEXITCODE -ne 0) { Write-Warning "load_to_es.py exited with code $LASTEXITCODE" }

gcloud run services update hospital-price-search `
  --region us-central1 `
  --update-env-vars $envVarsString
