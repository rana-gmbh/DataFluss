#!/bin/sh
# Puts Sparkle (the macOS update framework) into client/build/sparkle: Sparkle.framework for the app,
# bin/ with generate_keys and generate_appcast for releases. Version and checksum are pinned.
set -e
cd "$(dirname "$0")/.."
VERSION=2.10.1
SHA256=1a856d2a9edb8e628de88121d7be04cf0e2167f8641650b78fca00b14244cf27
DEST=client/build/sparkle
[ -f "$DEST/.version" ] && [ "$(cat "$DEST/.version")" = "$VERSION" ] && exit 0
ARCHIVE=client/build/sparkle-download/Sparkle-$VERSION.tar.xz
mkdir -p client/build/sparkle-download
[ -f "$ARCHIVE" ] || curl -sSL -o "$ARCHIVE" "https://github.com/sparkle-project/Sparkle/releases/download/$VERSION/Sparkle-$VERSION.tar.xz"
echo "$SHA256  $ARCHIVE" | shasum -a 256 -c - >/dev/null || { echo "Sparkle archive does not match its checksum" >&2; exit 1; }
rm -rf "$DEST" && mkdir -p "$DEST"
tar -xJf "$ARCHIVE" -C "$DEST"
echo "$VERSION" > "$DEST/.version"
