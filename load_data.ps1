# Loads all hospital pricing CSVs into the local SQLite database.
# Run this to parse raw CSV files and load them into SQLite locally.
#
# Usage:
#   .\load_data.ps1

# Activate virtual environment
. .\.venv\Scripts\Activate.ps1


Write-Host "Loading data into SQLite..."
& ".venv\Scripts\python.exe" load_to_sqlite.py --clean --cached-file data/shoppable_cache.json.gz
if ($LASTEXITCODE -ne 0) {
    Write-Warning "load_to_sqlite.py exited with code $LASTEXITCODE"
    exit $LASTEXITCODE
}
Write-Host "Done."
