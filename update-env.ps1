# Load .env into a hash table, substituting the internal ES URL for Cloud Run
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

# Push updated env vars to Cloud Run (no rebuild).
# To also reload Elasticsearch data, run .\load_data.ps1 beforehand.
Write-Host "Updating Cloud Run environment variables..."
gcloud run services update hospital-price-search `
  --region us-central1 `
  --update-env-vars $envVarsString
