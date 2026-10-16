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

FRESH=false
for arg in "$@"; do
  case "$arg" in
    --fresh) FRESH=true ;;
    -h|--help)
      echo "Usage: bash scripts/dev-vscode.sh [--fresh]"
      echo "  --fresh  Start with temporary settings, caches and user extensions."
      exit 0
      ;;
    *) echo "ERROR: Unknown option: $arg" >&2; exit 1 ;;
  esac
done

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

if [[ "$FRESH" == true ]]; then
  SESSION_DIR="$(mktemp -d "${TMPDIR:-/tmp}/scad-ai-ide-dev.XXXXXX")"
  USER_DATA="$SESSION_DIR/user-data"
  USER_EXTENSIONS="$SESSION_DIR/extensions"
  cleanup() { rm -rf -- "$SESSION_DIR"; }
  trap cleanup EXIT
  echo "==> Fresh session (removed on exit): $SESSION_DIR"
fi

mkdir -p "$USER_DATA"
mkdir -p "$USER_EXTENSIONS"

# ============================================
# 3. Launch VSCode
# ============================================

cd "$VSCODE"

launch_args=(
  --new-window
  "--builtin-extensions-dir=$BUILTIN_EXTENSIONS"
  "--user-data-dir=$USER_DATA"
  "--extensions-dir=$USER_EXTENSIONS"
  --disable-telemetry
  --disable-experiments
)

if [[ "$FRESH" == true ]]; then
  # Keep this shell alive until the isolated Electron instance exits so its
  # profile is never removed while the application is still using it.
  ./scripts/code.sh "${launch_args[@]}" &
  CHILD_PID=$!
  stop_session() {
    trap '' INT TERM
    kill "$CHILD_PID" 2>/dev/null || true
    wait "$CHILD_PID" 2>/dev/null || true
    exit "$1"
  }
  trap 'stop_session 130' INT
  trap 'stop_session 143' TERM
  wait "$CHILD_PID"
else
  exec ./scripts/code.sh "${launch_args[@]}"
fi
