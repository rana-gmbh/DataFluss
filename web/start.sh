#!/bin/sh
# Start of the template site's container (web/Dockerfile): bring the database up to date, then serve.
set -e
cd /app/web
python manage.py migrate --noinput
exec gunicorn datafluss_web.wsgi:application --bind 0.0.0.0:8000 --workers "${WEB_WORKERS:-2}" --access-logfile - --forwarded-allow-ips="*"
