# Read-only health check: live site response times plus keep-warm job status.
# Anything over 2s suggests a cold start.
Write-Host ("Checked at {0} UTC" -f (Get-Date -AsUTC -Format "HH:mm:ss"))
foreach ($u in 'https://hospitalpricesearch.org/', 'https://hospitalpricesearch.org/health') {
    curl.exe -s -o NUL -w "$u  %{http_code}  total=%{time_total}s  ttfb=%{time_starttransfer}s`n" $u
}
gcloud scheduler jobs describe keep-warm --location us-central1 --format="value(state,lastAttemptTime,status)"