#!/bin/sh
set -e
python manage.py check_installation
python manage.py check_modules
python manage.py fetch_modules || true
python manage.py dump_before_migrate
python manage.py migrate --noinput
python manage.py setup_school
# The container has no cron. Every hour this asks whether tonight's backup is still missing, and
# fetches this version's module bundle if its release came out after the server was updated.
(set +e; while sleep 3600; do python manage.py nightly_backup --if-due; python manage.py fetch_modules; done) &
exec gunicorn schule.wsgi:application --bind 0.0.0.0:8000 --workers 3 --threads 4 --timeout 120 --access-logfile - --access-logformat '%(h)s %(t)s "%(m)s %(U)s" %(s)s %(b)s %(L)s'
