# Loads all hospital pricing CSVs into Elasticsearch.
# Uses ELASTICSEARCH_URL from .env (the external IP, reachable from your local machine).
# Run this after setting up the VM, or to force a full data reload at any time.
#
# Usage:
#   .\load_data.ps1

# Activate virtual environment
. .\.venv\Scripts\Activate.ps1

# Load ELASTICSEARCH_URL from .env into the process environment so load_to_es.py picks it up
Get-Content .env | ForEach-Object {
    if ($_ -match '^([^#][^=]+)=(.*)$') {
        $key   = $matches[1].Trim()
        $value = $matches[2].Trim()
        [System.Environment]::SetEnvironmentVariable($key, $value, 'Process')
    }
}

Write-Host "Loading data into SQLite..."
& ".venv\Scripts\python.exe" load_to_sqlite.py --clean --cached-file data/shoppable_cache.json.gz
if ($LASTEXITCODE -ne 0) {
    Write-Warning "load_to_sqlite.py exited with code $LASTEXITCODE"
    exit $LASTEXITCODE
}
Write-Host "Done."
