#!/bin/sh
# Builds DataFluss for macOS, signs it with the Developer ID, has Apple notarize it and writes the
# release files to client/release/<version>/:
#   DataFluss-<version>.dmg   for people downloading the app
#   DataFluss-<version>.zip   the update Sparkle downloads
#   appcast.xml               the update feed, signed with the Sparkle key (goes to rana-gmbh/DataFluss)
#
# Locally: once, `xcrun notarytool store-credentials DataFluss --apple-id <Apple ID> --team-id D6P24X5377`
# (it asks for an app-specific password), then just run this script. The Sparkle key is read from the
# login Keychain (account DataFluss).
# In CI: APPLE_ID, APPLE_PASSWORD (app-specific) and APPLE_TEAM_ID instead of the profile, and the
# Sparkle private key in SPARKLE_PRIVATE_KEY.
#
# SKIP_NOTARIZE=1 signs but does not notarize, for trying the build.
set -e
cd "$(dirname "$0")/.."
ROOT=$PWD
IDENTITY=${CODESIGN_IDENTITY:-"Developer ID Application: Rana GmbH (D6P24X5377)"}
NOTARY_PROFILE=${NOTARY_PROFILE:-DataFluss}
RELEASES=https://github.com/rana-gmbh/DataFluss
VERSION=$(python3 -c "import json;print(json.load(open('client/shell/src-tauri/tauri.conf.json'))['version'])")
OUT=client/release/$VERSION
APP=client/shell/src-tauri/target/release/bundle/macos/DataFluss.app
export PATH="/opt/homebrew/opt/rustup/bin:$HOME/.cargo/bin:$PATH"

step() { printf '\n== %s\n' "$*"; }

notarize() {
  if [ -n "$APPLE_ID" ]; then
    xcrun notarytool submit "$1" --apple-id "$APPLE_ID" --password "$APPLE_PASSWORD" --team-id "$APPLE_TEAM_ID" --wait --timeout 60m --output-format json > "$1.notary.json" || true
  else
    xcrun notarytool submit "$1" --keychain-profile "$NOTARY_PROFILE" --wait --timeout 60m --output-format json > "$1.notary.json" || true
  fi
  status=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('status',''))" "$1.notary.json" 2>/dev/null || true)
  if [ "$status" != "Accepted" ]; then
    echo "Notarization of $1 failed ($status). Apple's log:" >&2
    id=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('id',''))" "$1.notary.json" 2>/dev/null || true)
    if [ -n "$id" ]; then
      if [ -n "$APPLE_ID" ]; then
        xcrun notarytool log "$id" --apple-id "$APPLE_ID" --password "$APPLE_PASSWORD" --team-id "$APPLE_TEAM_ID" >&2 || true
      else
        xcrun notarytool log "$id" --keychain-profile "$NOTARY_PROFILE" >&2 || true
      fi
    fi
    cat "$1.notary.json" >&2
    exit 1
  fi
  rm -f "$1.notary.json"
}

step "DataFluss $VERSION"
rm -rf "$OUT" && mkdir -p "$OUT"

step "Sidecar (signed by PyInstaller with the sidecar's entitlements)"
# A Finder window showing the folder keeps writing .DS_Store into it, which can make PyInstaller's own
# clean-up fail, so it is emptied here first, a few times if needed.
for attempt in 1 2 3 4 5; do
  rm -rf client/dist/schule-sidecar 2>/dev/null && [ ! -e client/dist/schule-sidecar ] && break
  sleep 1
done
CODESIGN_IDENTITY="$IDENTITY" client/build-sidecar.sh

step "Sparkle"
client/fetch-sparkle.sh

step "App"
(cd client/shell && npm ci --silent && npx tauri build --bundles app)

step "Sidecar into the app"
# Tauri's resource copy turns symlinks into files, which breaks Python.framework's bundle and with it
# its signature. rsync copies the sidecar as PyInstaller made it, without Finder's .DS_Store files. PyInstaller signs the framework's
# binary but leaves out the framework's seal, so the framework is signed again as a whole.
SIDECAR="$APP/Contents/Resources/sidecar"
rm -rf "$SIDECAR"
rsync -a --exclude .DS_Store client/dist/schule-sidecar/ "$SIDECAR/"
for framework in $(find "$SIDECAR" -name "*.framework" -type d -prune); do
  codesign --force --timestamp --options runtime --sign "$IDENTITY" "$framework"
  codesign --verify --strict "$framework"
done

step "Signing"
# Sparkle's own guide for apps built outside Xcode: helpers first, the framework last. The XPC services
# are for sandboxed apps only, and DataFluss is not sandboxed.
FW="$APP/Contents/Frameworks/Sparkle.framework"
rm -rf "$FW/Versions/B/XPCServices" "$FW/XPCServices"
codesign --force --timestamp --options runtime --sign "$IDENTITY" "$FW/Versions/B/Autoupdate"
codesign --force --timestamp --options runtime --sign "$IDENTITY" "$FW/Versions/B/Updater.app"
codesign --force --timestamp --options runtime --sign "$IDENTITY" "$FW"
# The sidecar keeps the signatures PyInstaller gave it; the app's seal covers it as a resource.
codesign --force --timestamp --options runtime --sign "$IDENTITY" "$APP"
codesign --verify --strict --deep --verbose=1 "$APP"

if [ -z "$SKIP_NOTARIZE" ]; then
  step "Notarizing the app"
  ditto -c -k --keepParent "$APP" "$OUT/notarize.zip"
  notarize "$OUT/notarize.zip"
  rm -f "$OUT/notarize.zip"
  xcrun stapler staple "$APP"
fi

step "Update archive"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$OUT/DataFluss-$VERSION.zip"

step "Disk image"
STAGE=$(mktemp -d)
ditto "$APP" "$STAGE/DataFluss.app"
ln -s /Applications "$STAGE/Programme"
hdiutil create -quiet -volname "DataFluss $VERSION" -srcfolder "$STAGE" -fs HFS+ -format UDZO -ov "$OUT/DataFluss-$VERSION.dmg"
rm -rf "$STAGE"
codesign --force --timestamp --sign "$IDENTITY" "$OUT/DataFluss-$VERSION.dmg"
if [ -z "$SKIP_NOTARIZE" ]; then
  step "Notarizing the disk image"
  notarize "$OUT/DataFluss-$VERSION.dmg"
  xcrun stapler staple "$OUT/DataFluss-$VERSION.dmg"
  spctl --assess --type open --context context:primary-signature --verbose=1 "$OUT/DataFluss-$VERSION.dmg"
fi

step "Appcast"
FEED=$(mktemp -d)
cp "$OUT/DataFluss-$VERSION.zip" "$FEED/"
[ -f "client/release-notes/$VERSION.html" ] && cp "client/release-notes/$VERSION.html" "$FEED/DataFluss-$VERSION.html"
if [ -n "$SPARKLE_PRIVATE_KEY" ]; then
  printf '%s' "$SPARKLE_PRIVATE_KEY" | client/build/sparkle/bin/generate_appcast --ed-key-file - \
    --download-url-prefix "$RELEASES/releases/download/v$VERSION/" --link "$RELEASES" --embed-release-notes "$FEED"
else
  client/build/sparkle/bin/generate_appcast --account DataFluss \
    --download-url-prefix "$RELEASES/releases/download/v$VERSION/" --link "$RELEASES" --embed-release-notes "$FEED"
fi
cp "$FEED/appcast.xml" "$OUT/appcast.xml"
rm -rf "$FEED"

step "Done"
ls -lh "$OUT"
