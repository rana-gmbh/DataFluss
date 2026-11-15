# DataFluss, the desktop client

| Folder | Content |
|---|---|
| `sidecar/` | `sidecar.py` runs the Django app for one database and answers framed requests on stdin/stdout. `sidecar.spec` freezes it with PyInstaller |
| `shell/ui/` | The start screen: list of databases, new database, backup import, demo |
| `shell/src-tauri/src/` | The Tauri 2 app. `main.rs`: windows, menus, the commands of the start screen, the `app://<id>/` scheme. `sidecar.rs`: one sidecar per open database, cookies, redirects. `databases.rs`: the database folders and their keys in the Keychain |
| `../core/sealed.py` | Encryption of photos and attachments when `MEDIA_KEY` is set |
| `../static/js/client.js` | The database switcher at the top of the sidebar, only inside the app |
| `../schule/sqlcipher/` | Django's SQLite backend on SQLCipher. The key comes from the Keychain through the environment |

## Build on a Mac

Needs Homebrew with `python@3.14`, `pango`, `rustup` (then `rustup default stable`) and Node.

```bash
client/build-sidecar.sh
```

```bash
cd client/shell && npm install && APPLE_SIGNING_IDENTITY="Developer ID Application: Rana GmbH (D6P24X5377)" npx tauri build --bundles app
```

The app lands in `client/shell/src-tauri/target/release/bundle/macos/DataFluss.app`. For a
signed sidecar, set `CODESIGN_IDENTITY` to the same identity when running `build-sidecar.sh`.
For a release use `release-macos.sh` instead (below).

## Build on Windows

Needs Python 3.14 (with the `py` launcher), MSYS2 in `C:\msys64`, Rust (rustup, MSVC toolchain) with
the Visual Studio C++ Build Tools, and Node. Once, with winget:

```powershell
winget install Python.Python.3.14 OpenJS.NodeJS.LTS Rustlang.Rustup MSYS2.MSYS2
winget install Microsoft.VisualStudio.2022.BuildTools --override "--quiet --wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
```

`build-sidecar.ps1` installs Pango from MSYS2's UCRT64 packages if needed, makes `client\.venv-win`
and freezes the sidecar into `client\dist\schule-sidecar`:

```powershell
powershell -ExecutionPolicy Bypass -File client\build-sidecar.ps1
```

`fetch-winsparkle.ps1` puts the update library into `client\build\winsparkle`; every Windows build of
the shell needs it:

```powershell
powershell -ExecutionPolicy Bypass -File client\fetch-winsparkle.ps1
```

```powershell
cd client\shell; npm install; npx tauri build --bundles nsis
```

The installer lands in `client\shell\src-tauri\target\release\bundle\nsis\`. It installs for the
current user, without administrator rights. The GitHub Actions workflow `Windows-Client` builds the
same installer on a Windows runner as a check. For a release use `release-windows.ps1` (below).

What differs from macOS: database windows show `https://app.<id>/` (WebView2's address for the
`app://` scheme), the keys are in the Windows Credential Manager under `de.rana.datafluss`, the
databases in `%APPDATA%\de.rana.datafluss\databases\`, and the menu bar is per window with
Windows keys (Strg instead of ⌘, Alt+← for back, F5, F11). WebView2 shows `alert`/`confirm`/`prompt`
and PDFs itself, so `js_dialogs.rs` and `pdf_save.rs` are macOS only.

## Run

Start the app. The start screen lists the databases under
`~/Library/Application Support/de.rana.datafluss/databases/`, one folder each. Their keys
are in the Keychain under `de.rana.datafluss`, accounts `<id>:sqlcipher`, `<id>:vault`,
`<id>:django` and `<id>:media`. A demo database shows its logins through its menu (⋯). Databases of the
earlier Schulverwaltung client (`de.rana.datafluss`) move over at the first start
(`src/legacy.rs`); macOS may ask once per key whether DataFluss may read it.

Each database writes its sidecar's output to `sidecar.log` in its folder. Started from a terminal,
the app prints start times and the start screen's script errors; `SIDECAR_LOG=1` also logs every
request. `SCHULE_SIDECAR=/path/to/program` runs another sidecar, for example the unfrozen one:

```bash
printf '#!/bin/sh\ncd %s\nPYTHONPATH=$PWD exec client/.venv/bin/python client/sidecar/sidecar.py\n' "$PWD" > /tmp/dev-sidecar.sh && chmod +x /tmp/dev-sidecar.sh
```

## Tests on SQLCipher

```bash
DATABASE_URL=sqlcipher:// SQLCIPHER_KEY=$(python3 -c "import secrets;print(secrets.token_hex(32))") TESTING=1 DEBUG=1 REQUIRE_2FA=0 client/.venv/bin/python manage.py test tests daz kindergarten teaching --noinput
```

## Smoke test of the sidecar

`client/smoke.py` talks to the sidecar as the shell does: it makes a demo database, signs in and opens
the main pages, restores a backup of it into a new database, and creates an empty one (about 30 s).

```bash
client/.venv/bin/python client/smoke.py
```

```bash
client/.venv/bin/python client/smoke.py --sidecar client/dist/schule-sidecar/schule-sidecar
```

With `--bundles <folder>` the premium module is expected from a bundle in that folder (for a sidecar built
with `DATAFLUSS_MODULES=`, see docs/modules.md). The app keeps bundles in `<app data>/modules`.

## Sync with a server

The Mac side is `sync/client.py` (in the sidecar, thread "sync"), the server side `sync/server.py` and
`sync/views.py`. To try it end to end without the app:

```bash
.venv/bin/python tests/sync_e2e/run.py
```

A teacher's Mac (role Lehrkraft, or any sign-in linked to staff) holds only the lessons and classes of
its user, and pupils without home data (`sync/scope.py`). The server takes from it only what the user
could change in the browser, record by record (`teaching/sync_rules.py`); the entry period is judged by
the day of the change on the Mac, up to two weeks back. The end-to-end test runs such a Mac as `mac4`.

## Releases and updates (macOS)

For the release of both platforms and the handover to Windows, see
[docs/releases.md](../docs/releases.md).

Releases are published in the public repository
[rana-gmbh/DataFluss](https://github.com/rana-gmbh/DataFluss), because Sparkle reads its feed without
signing in. That repository holds only the downloads and `macos/appcast.xml`; this one keeps the code.

The app updates itself with [Sparkle](https://sparkle-project.org) (`src/sparkle.rs`, `Info.plist`,
`tauri.macos.conf.json`): it checks once a day and offers "Nach Updates suchen …" in the DataFluss menu.
Each update must be signed with the Sparkle private key; its public half is `SUPublicEDKey` in
`Info.plist`. The private key is in the login Keychain of the Mac that made it (account `DataFluss`,
export with `client/build/sparkle/bin/generate_keys --account DataFluss -x <file>`). Losing it means
installed apps can no longer be updated.

To release:

1. Raise `version` in `client/shell/src-tauri/tauri.conf.json` (and in `Cargo.toml`), optionally add
   release notes as `client/release-notes/<version>.html`, commit.
2. Push a tag `v<version>`. The workflow `.github/workflows/release-macos.yml` builds, signs,
   notarizes, uploads the DMG and ZIP to the release `v<version>` of rana-gmbh/DataFluss and then
   updates the feed. Started by hand it only builds, unless "publish" is ticked.

The same can be done on a Mac with `client/release-macos.sh` (once:
`xcrun notarytool store-credentials DataFluss --apple-id <Apple ID> --team-id D6P24X5377`). It writes
DMG, ZIP and `appcast.xml` to `client/release/<version>/`; `SKIP_NOTARIZE=1` only signs.

Secrets of this repository the workflow needs:

| Secret | Content |
|---|---|
| `MACOS_CERTIFICATE` | The Developer ID Application certificate with its private key, as `.p12`, base64 |
| `MACOS_CERTIFICATE_PASSWORD` | The password of that `.p12` |
| `APPLE_ID` | The Apple ID used for notarizing |
| `APPLE_APP_PASSWORD` | An app-specific password of that Apple ID (appleid.apple.com) |
| `APPLE_TEAM_ID` | `D6P24X5377` |
| `SPARKLE_PRIVATE_KEY` | The exported Sparkle private key (one line) |
| `DATAFLUSS_RELEASE_TOKEN` | A fine-grained token with "Contents: read and write" on rana-gmbh/DataFluss only |

Builds are for Apple Silicon only, because the frozen sidecar carries Homebrew's Pango for one
architecture.

## Releases and updates (Windows)

The same tag `v<version>` also starts `.github/workflows/release-windows.yml`. It runs
`client/release-windows.ps1`, uploads `DataFluss-<version>-setup.exe` to the same release of
rana-gmbh/DataFluss and then writes the feed `windows/appcast.xml` there. Started by hand it only
builds, unless "publish" is ticked.

The app updates itself with [WinSparkle](https://winsparkle.org) (`src/winsparkle.rs`,
`tauri.windows.conf.json`): it checks once a day and offers "Nach Updates suchen …" in the Hilfe menu.
WinSparkle reads Sparkle's appcast format and checks the same EdDSA signatures, so the Windows updates
are signed with the same `SPARKLE_PRIVATE_KEY` and the release notes are shared. It runs the new
installer passively (`/P /UPDATE /R`) after DataFluss has quit.

Locally, on a PC set up as in "Build on Windows":

```powershell
$env:SPARKLE_PRIVATE_KEY_FILE = "<file with the exported key>"; powershell -ExecutionPolicy Bypass -File client\release-windows.ps1
```

It writes the installer and `appcast.xml` to `client\release\<version>\`. Without the key it builds
the installer only. The release workflow code-signs it with Azure Artifact Signing; locally that
needs the `AZURE_*` variables and `artifact-signing-cli` (docs/releases.md).

To try an update without publishing anything: make a key with
`client\build\winsparkle\bin\winsparkle-tool.exe generate-key -f test.key`, build and install with
`DATAFLUSS_UPDATE_FEED=http://localhost:8765/appcast.xml`, `DATAFLUSS_UPDATE_KEY=<its public key>`,
`DATAFLUSS_DOWNLOADS=http://localhost:8765` and `SPARKLE_PRIVATE_KEY_FILE=test.key` set, then build a
higher version the same way and serve its `client\release\<version>\` folder on port 8765.
