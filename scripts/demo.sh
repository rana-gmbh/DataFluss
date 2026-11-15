#!/bin/sh
# Starts the demo on a Mac: local PostgreSQL, invented data, web server on port 8123.
set -e
cd "$(dirname "$0")/.."
PGB=$(brew --prefix postgresql@18)/bin
export LC_ALL=en_US.UTF-8

[ -d .venv ] || { /opt/homebrew/bin/python3.14 -m venv .venv; .venv/bin/pip -q install -r requirements.txt; }
[ -f .env ] || printf 'DEBUG=1\nREQUIRE_2FA=0\nSERVER_ID=eu\n' > .env
if [ ! -d .pgdata ]; then
  "$PGB/initdb" -D .pgdata -U schule --auth=trust --encoding=UTF8 --locale=C -c port=5544 -c listen_addresses=127.0.0.1 -c unix_socket_directories=/tmp >/dev/null
fi
"$PGB/pg_isready" -q -h 127.0.0.1 -p 5544 || "$PGB/pg_ctl" -D .pgdata -l .pgdata/server.log -w start >/dev/null
"$PGB/psql" -h 127.0.0.1 -p 5544 -U schule -d postgres -tc "select 1 from pg_database where datname='schule'" | grep -q 1 || "$PGB/createdb" -h 127.0.0.1 -p 5544 -U schule schule

.venv/bin/python manage.py migrate --noinput >/dev/null
.venv/bin/python manage.py init_vault >/dev/null
.venv/bin/python manage.py seed_demo
echo
echo "Schulverwaltung läuft auf http://127.0.0.1:8123"
echo "Anmeldedaten: .secrets/demo-logins.txt"
exec .venv/bin/python manage.py runserver 127.0.0.1:8123 --noreload
