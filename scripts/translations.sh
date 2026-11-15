#!/bin/sh
# Collects the texts marked for translation into locale/*/LC_MESSAGES/*.po (German is the source),
# or, with "compile", turns the .po files into the .mo files the app reads. Both are committed.
#   scripts/translations.sh           after changing texts: new texts appear with an empty msgstr
#   scripts/translations.sh compile   after translating
# Needs GNU gettext (brew install gettext).
set -e
cd "$(dirname "$0")/.."
PY=.venv/bin/python
export DATABASE_URL=${DATABASE_URL:-sqlite://}
if [ "$1" = "compile" ]; then
  $PY manage.py compilemessages -i .venv -i client -i frontend
  exit
fi
for domain in django djangojs; do
  $PY manage.py makemessages -d "$domain" -l en -l zh_Hans -l zh_Hant --no-location \
    -i client -i frontend -i .venv -i staticfiles -i vendor -i tests -i node_modules -i backups -i media -i data -i docs
done
grep -c '^msgstr ""$' locale/*/LC_MESSAGES/*.po || true
