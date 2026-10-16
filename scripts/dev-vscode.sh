#!/usr/bin/env bash
set -euo pipefail

# ============================================
# Paths
# ============================================

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

VSCODE="$ROOT/sources/vscode"
DEV="$ROOT/.dev"
STAGE="$DEV/stage"

BUILTIN_EXTENSIONS="$STAGE/extensions"
USER_DATA="$DEV/user-data"
USER_EXTENSIONS="$DEV/extensions"

echo "==> Starting VSCode development instance"

# ============================================
# 1. Validate stage
# ============================================

if [[ ! -d "$BUILTIN_EXTENSIONS" ]]; then
  echo "ERROR: Development stage not found."
  echo "Run: npm run prepare:vscode"
  exit 1
fi

if [[ ! -f "$VSCODE/scripts/code.sh" ]]; then
  echo "ERROR: VSCode development launcher not found."
  exit 1
fi

# ============================================
# 2. Prepare user directories
# ============================================

mkdir -p "$USER_DATA"
mkdir -p "$USER_EXTENSIONS"

# ============================================
# 3. Launch VSCode
# ============================================

cd "$VSCODE"

exec ./scripts/code.sh \
  --new-window \
  --builtin-extensions-dir="$BUILTIN_EXTENSIONS" \
  --user-data-dir="$USER_DATA" \
  --extensions-dir="$USER_EXTENSIONS" \
  --disable-telemetry \
  --disable-experiments