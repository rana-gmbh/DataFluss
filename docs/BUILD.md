# Building DataFluss from source

DataFluss is one Django application in three forms: the server (a Docker image), and the desktop apps
for macOS and Windows, which run the same application as a frozen "sidecar" inside a Tauri shell.

## The server, on a Mac

Needs Homebrew with `postgresql@18` and `pango`, and Python 3.14.

```bash
scripts/demo.sh
```

It creates a virtual environment, a local PostgreSQL in `.pgdata/`, invented demo data, and serves the
application on <http://127.0.0.1:8123>. The demo accounts and their passwords are written to
`.secrets/demo-logins.txt`. Every person in the demo is invented.

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
```

```bash
TESTING=1 DEBUG=1 REQUIRE_2FA=0 .venv/bin/python manage.py test tests
```

The same suite runs on SQLCipher, as in the desktop app (see `client/README.md`), and on every push in
GitHub Actions (`.github/workflows/ci.yml`). Sync between a server and several clients, end to end:

```bash
.venv/bin/python tests/sync_e2e/run.py
```

## Styles, icons and translations

After changing styles or icons: `cd frontend && npm install && node build.mjs`.

Texts are German first and marked for translation. After changing texts, `scripts/translations.sh`
collects them into `locale/*/LC_MESSAGES/*.po`; `scripts/translations.sh compile` builds the `.mo` files.
Both are committed. Needs GNU gettext (`brew install gettext`).

## Dependencies

`requirements.txt` names them; the image installs exactly the versions in `requirements.lock`:

```bash
uv pip compile requirements.txt --python-version 3.13 --python-platform x86_64-unknown-linux-gnu --generate-hashes -o requirements.lock
```

## The desktop apps

How to build the sidecar and the app on macOS and Windows, and how to test the sidecar the way the
shell talks to it: [client/README.md](../client/README.md).

## Versions

One version for server, apps and modules, in `VERSION`, `tauri.conf.json`, `Cargo.toml` and `Cargo.lock`:

```bash
scripts/set-version.sh 1.2.0
```

How a release is made: [releases.md](releases.md).
