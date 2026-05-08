#!/bin/sh
# Container entrypoint.
# Launches a background check that auto-reloads Elasticsearch data if the
# index is empty (e.g. after a Spot VM preemption), then starts gunicorn
# immediately so Cloud Run considers the instance healthy right away.

python /app/check_and_reload.py &

exec gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 0
