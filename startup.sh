#!/bin/sh
# Container entrypoint.
# Starts gunicorn immediately.

exec gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 0
