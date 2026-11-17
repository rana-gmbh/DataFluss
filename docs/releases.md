# Releases

One version for the server image, the macOS and Windows apps and the modules.

## Making a release

1. `scripts/set-version.sh X.Y.Z`, write the release notes in English: `client/release-notes/X.Y.Z.html`
   (short, shown in the apps' update dialog) and, for the release page, a Markdown file. Commit, push a
   tag `vX.Y.Z` and create the release with `gh release create vX.Y.Z --verify-tag --title "DataFluss X.Y.Z"
   --notes-file <notes.md>`; the workflows then only add their files to it.
2. The tag starts three workflows, each waiting for an approval of the environment `release`:
   - `release-macos.yml`: builds, signs and notarises the app; attaches `DataFluss-X.Y.Z.dmg`, the update
     archive `DataFluss-X.Y.Z.zip` and `appcast-macos.xml` to the release `vX.Y.Z`.
   - `release-windows.yml`: builds the installer `DataFluss-X.Y.Z-setup.exe` (signed with Azure Artifact
     Signing once its secrets exist) and attaches it with `appcast-windows.xml`.
   - `image.yml`: builds the server image and pushes `ghcr.io/rana-gmbh/datafluss:X.Y.Z` and `:latest`.
3. **Update your servers first.** A new version may bring migrations, and computers only sync with a
   server on the same migrations.
4. Run the workflow **Promote** with the version. It copies the release's update feeds to
   `macos/appcast.xml` and `windows/appcast.xml`, which the installed apps read; only then do they update.

Version numbers only go up: the updaters compare them.

## Secrets

Kept in the environment `release` (Settings → Environments, with required reviewers), never in the
repository:

| Secret | Use |
|---|---|
| `MACOS_CERTIFICATE`, `MACOS_CERTIFICATE_PASSWORD` | Developer ID Application certificate (.p12, base64) |
| `APPLE_ID`, `APPLE_APP_PASSWORD`, `APPLE_TEAM_ID` | Notarisation |
| `SPARKLE_PRIVATE_KEY` | Signs the updates of both apps (Sparkle and WinSparkle use the same EdDSA format) |
| `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` | Code signing of the Windows installer |

The public half of the update key is in `client/shell/src-tauri/Info.plist` and `src/winsparkle.rs`.
