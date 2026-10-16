#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SOURCE="$ROOT/.build/extensions/unpacked"
TARGET="${1:?Usage: stage-extensions.sh <target-directory>}"

mkdir -p "$TARGET"

for name in \
  openscad-support-vscode \
  vscode-openscad-preview
do
  SRC="$SOURCE/$name"
  DEST="$TARGET/$name"

  if [[ ! -f "$SRC/package.json" ]]; then
    echo "Missing packaged extension: $SRC" >&2
    exit 1
  fi

  echo "==> Staging $name"

  mkdir -p "$DEST"

  rsync -a --delete \
    "$SRC/" \
    "$DEST/"
done

echo "Extensions staged successfully: $TARGET"