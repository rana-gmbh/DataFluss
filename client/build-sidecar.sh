#!/bin/sh
# Freezes the Django app into client/dist/schule-sidecar. Run from anywhere.
set -e
cd "$(dirname "$0")/.."
[ -d client/.venv ] || { /opt/homebrew/bin/python3.14 -m venv client/.venv; client/.venv/bin/pip -q install -r requirements.txt -r client/requirements.txt; }
# Production mode, so the hashed file names match what the frozen app asks for (a local .env may say DEBUG=1).
rm -rf staticfiles
DEBUG=0 SECRET_KEY=build client/.venv/bin/python manage.py collectstatic --noinput -v0
# The Pango libraries are listed in the spec, because WeasyPrint loads them by name at run time.
client/.venv/bin/pyinstaller --noconfirm --log-level WARN \
  --workpath client/build --distpath client/dist client/sidecar/sidecar.spec
# Two different builds under one install name would be mixed at run time (Pillow's HarfBuzz under
# Homebrew's harfbuzz-subset crashed WeasyPrint). Copies of one build share their LC_UUID.
client/.venv/bin/python - client/dist/schule-sidecar <<'PY'
import re, subprocess, sys
from pathlib import Path
seen, clash = {}, []
for path in Path(sys.argv[1]).rglob("*.dylib"):
    if path.is_symlink():
        continue
    name = subprocess.run(["otool", "-D", str(path)], capture_output=True, text=True).stdout.strip().splitlines()[-1]
    uuid = re.search(r"uuid ([0-9A-F-]+)", subprocess.run(["otool", "-l", str(path)], capture_output=True, text=True).stdout)
    build = uuid.group(1) if uuid else path.name
    if name in seen and seen[name][1] != build:
        clash.append(f"{name}: {seen[name][0]} and {path}")
    seen.setdefault(name, (path, build))
if clash:
    sys.exit("Different builds share an install name:\n" + "\n".join(clash))
print(f"{len(seen)} install names, each with one build")
PY
du -sh client/dist/schule-sidecar
