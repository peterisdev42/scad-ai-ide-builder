#!/usr/bin/env bash
set -euo pipefail

# ============================================
# Paths
# ============================================

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

VSCODE="$ROOT/sources/vscode"
DEV="$ROOT/.dev"
STAGE="$DEV/stage"

echo "==> Preparing VSCode development environment"

# ============================================
# 1. Validate environment
# ============================================

if [[ ! -f "$VSCODE/package.json" ]]; then
  echo "ERROR: VSCode submodule not found."
  exit 1
fi

for cmd in node npm rsync; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: Missing command: $cmd"
    exit 1
  fi
done

# ============================================
# 2. Install dependencies
# ============================================

echo "==> Installing VSCode dependencies"

cd "$VSCODE"

npm install

# ============================================
# 3. Compile VSCode
# ============================================

echo "==> Compiling VSCode"

npm run compile

echo "==> Compiling built-in extensions"

npm run gulp -- \
  copy-codicons \
  compile-extensions \
  compile-extension-media

# ============================================
# 4. Prepare Electron
# ============================================

echo "==> Preparing Electron runtime"

node build/lib/preLaunch.ts

# ============================================
# 5. Create development directories
# ============================================

echo "==> Creating development directories"

mkdir -p "$STAGE/extensions"
mkdir -p "$STAGE/resources"
mkdir -p "$DEV/user-data"
mkdir -p "$DEV/extensions"

# ============================================
# 6. Stage built-in extensions
# ============================================

echo "==> Staging built-in extensions"

rsync -a --delete \
  --exclude=".git/" \
  "$VSCODE/extensions/" \
  "$STAGE/extensions/"

# ============================================
# 7. Stage source resources
# ============================================

echo "==> Staging VSCode resources"

rsync -a --delete \
  "$VSCODE/resources/" \
  "$STAGE/resources/"

# ============================================
# Done
# ============================================

echo ""
echo "VSCode preparation completed."
echo "Stage: $STAGE"
echo ""
echo "Next: npm run dev:vscode"