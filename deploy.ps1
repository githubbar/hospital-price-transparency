# Load .env values into shell variables
Get-Content .env | ForEach-Object {
    if ($_ -match '^([^#][^=]+)=(.+)$') {
        Set-Variable -Name $matches[1] -Value $matches[2]
    }
}

# Build URL with embedded credentials
$ES_URL_WITH_CREDS = $ELASTICSEARCH_URL -replace '^(https?://)', "`${1}${ELASTICSEARCH_USERNAME}:${ELASTICSEARCH_PASSWORD}@"

gcloud run deploy hospital-price-search `
  --source . `
  --platform managed `
  --region us-central1 `
  --allow-unauthenticated `
  --set-env-vars "ELASTICSEARCH_URL=$ES_URL_WITH_CREDS,TURNSTILE_SITE_KEY=$TURNSTILE_SITE_KEY,TURNSTILE_SECRET_KEY=$TURNSTILE_SECRET_KEY"
