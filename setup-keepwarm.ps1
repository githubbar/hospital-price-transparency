# One-time setup: ping /health every 5 minutes so the Cloud Run instance
# (min-instances 0) stays warm and visitors don't hit a ~20s cold start.
# Cloud Scheduler is free for up to 3 jobs per billing account.

gcloud services enable cloudscheduler.googleapis.com

gcloud scheduler jobs create http keep-warm `
  --location us-central1 `
  --schedule "*/5 * * * *" `
  --uri "https://hospitalpricesearch.org/health" `
  --http-method GET `
  --attempt-deadline 60s

# Trigger once now to verify; check lastAttemptTime/status afterwards.
gcloud scheduler jobs run keep-warm --location us-central1
gcloud scheduler jobs describe keep-warm --location us-central1
