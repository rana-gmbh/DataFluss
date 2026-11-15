#!/bin/sh
# Sets the DataFluss version everywhere it is written: VERSION (server, sidecar, modules),
# tauri.conf.json and Cargo.toml (the desktop app). The release workflows refuse a tag that differs.
#   scripts/set-version.sh 0.4.0
set -e
cd "$(dirname "$0")/.."
version="$1"
echo "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || { echo "Usage: scripts/set-version.sh X.Y.Z" >&2; exit 1; }
printf '%s\n' "$version" > VERSION
python3 - "$version" <<'PY'
import json, re, sys
from pathlib import Path
version = sys.argv[1]
conf = Path("client/shell/src-tauri/tauri.conf.json")
data = conf.read_text()
conf.write_text(re.sub(r'("version":\s*")[^"]+(")', rf"\g<1>{version}\g<2>", data, count=1))
assert json.loads(conf.read_text())["version"] == version
for name in ("client/shell/src-tauri/Cargo.toml", "client/shell/src-tauri/Cargo.lock"):
    path = Path(name)
    text = path.read_text()
    if name.endswith(".toml"):
        text = re.sub(r'(?m)^(version = ")[^"]+(")', rf"\g<1>{version}\g<2>", text, count=1)
    else:
        text = re.sub(r'(name = "schulverwaltung"\nversion = ")[^"]+(")', rf"\g<1>{version}\g<2>", text, count=1)
    path.write_text(text)
PY
echo "Version $version in VERSION, tauri.conf.json, Cargo.toml and Cargo.lock"
