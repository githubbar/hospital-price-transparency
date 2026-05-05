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

# Build KEY=VALUE string for --set-env-vars
$envVarsString = ($envVars.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ','

gcloud run deploy hospital-price-search `
  --source . `
  --platform managed `
  --region us-central1 `
  --allow-unauthenticated `
  --set-env-vars $envVarsString
