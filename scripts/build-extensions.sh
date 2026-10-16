
#!/usr/bin/env bash
set -euo pipefail

# SCAD AI IDE Builder - Build and stage built-in VS Code extensions.
#
# Supported refs:
#   submodule              Commit currently checked out in sources/<repo>
#   working-tree           Local source files, including uncommitted changes
#   branch:<name>          Latest commit on origin/<name>
#   tag:<name>             Commit referenced by a tag
#   commit:<sha>           An explicit, locally available commit
#
# Examples:
#   npm run build:extensions
#   npm run build:extensions -- --language=tag:v2.0.1 --preview=branch:main

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCES="$ROOT/sources"
BUILD="$ROOT/.build/extensions"
WORKTREES="$BUILD/worktrees"
VSIX_DIR="$BUILD/vsix"
UNPACKED_DIR="$BUILD/unpacked"
EXTRACT_DIR="$BUILD/extract"
STAGE_DIR="$ROOT/.dev/stage/extensions"
CONFIG="$ROOT/config/extensions.conf"
VSCE="$ROOT/node_modules/.bin/vsce"

LANGUAGE_REF="submodule"
PREVIEW_REF="submodule"

# This file is owned by Builder; it is not loaded from a third-party repository.
if [[ -f "$CONFIG" ]]; then
  # shellcheck source=/dev/null
  source "$CONFIG"
fi

usage() {
  cat <<'EOF'
Usage: bash scripts/build-extensions.sh [options]

Options:
  --language=<ref>  Version for openscad-support-vscode
  --preview=<ref>   Version for vscode-openscad-preview
  -h, --help        Display this help

Ref formats: submodule | working-tree | branch:<name> | tag:<name> | commit:<sha>
EOF
}

for arg in "$@"; do
  case "$arg" in
    --language=*) LANGUAGE_REF="${arg#*=}" ;;
    --preview=*)  PREVIEW_REF="${arg#*=}" ;;
    -h|--help)    usage; exit 0 ;;
    *) echo "ERROR: Unknown argument: $arg" >&2; usage >&2; exit 1 ;;
  esac
done

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

for cmd in git node npm unzip rsync curl; do
  command -v "$cmd" >/dev/null 2>&1 || fail "Missing command: $cmd"
done
[[ -x "$VSCE" ]] || fail "Missing @vscode/vsce. Run 'npm install' at the Builder root."
[[ -f "$ROOT/scripts/stage-extensions.sh" ]] || fail "Missing scripts/stage-extensions.sh"
[[ -d "$STAGE_DIR" ]] || fail "Missing Dev stage. Run 'npm run prepare:vscode' first."

mkdir -p "$WORKTREES" "$VSIX_DIR" "$UNPACKED_DIR" "$EXTRACT_DIR"

# Automatically remove an active worktree on both success and failure.
ACTIVE_REPO=""
ACTIVE_WORKTREE=""
cleanup_active_worktree() {
  if [[ -n "$ACTIVE_WORKTREE" && -n "$ACTIVE_REPO" ]]; then
    if ! git -C "$ACTIVE_REPO" worktree remove --force "$ACTIVE_WORKTREE"; then
      echo "WARNING: Worktree cleanup failed: $ACTIVE_WORKTREE" >&2
    fi
    ACTIVE_REPO=""
    ACTIVE_WORKTREE=""
  fi
}
trap cleanup_active_worktree EXIT

resolve_ref() {
  local repo="$1"
  local spec="$2"
  local name

  case "$spec" in
    submodule|working-tree)
      git -C "$repo" rev-parse --verify 'HEAD^{commit}'
      ;;
    branch:*)
      name="${spec#branch:}"
      [[ -n "$name" ]] || fail "Empty branch name"
      git -C "$repo" fetch origin \
        "+refs/heads/$name:refs/remotes/origin/$name" >&2 || return $?
      git -C "$repo" rev-parse --verify "refs/remotes/origin/$name^{commit}"
      ;;
    tag:*)
      name="${spec#tag:}"
      [[ -n "$name" ]] || fail "Empty tag name"
      # Do not force-update an existing local tag: a moved tag is suspicious.
      git -C "$repo" fetch origin \
        "refs/tags/$name:refs/tags/$name" >&2 || return $?
      git -C "$repo" rev-parse --verify "refs/tags/$name^{commit}"
      ;;
    commit:*)
      name="${spec#commit:}"
      [[ "$name" =~ ^[0-9a-fA-F]{7,64}$ ]] || fail "Invalid commit SHA: $name"
      git -C "$repo" rev-parse --verify "$name^{commit}"
      ;;
    *)
      fail "Invalid ref '$spec'. Use submodule, working-tree, branch:..., tag:..., or commit:..."
      ;;
  esac
}

# v2.0.1 tracks four runtime assets as links to the author's workstation.
# Restore their real contents from the matching published VSIX, in the
# disposable worktree only. Pin the archive digest; never use a latest release.
fix_language_assets() {
  local worktree="$1"
  local asset target needs_repair=false
  local version
  local cache="$BUILD/cache"
  local archive="$cache/Leathong.openscad-language-support-2.0.1.vsix"
  local url="https://open-vsx.org/api/Leathong/openscad-language-support/2.0.1/file/Leathong.openscad-language-support-2.0.1.vsix"
  local digest="68cf74d13ad5c5fdb794c2ea932ee443f95e9b513b830b8691a46f031668a337"
  local assets=(media/builtins.scad server/openscad-lsp server/openscad-lsp-linux server/openscad-lsp.exe)

  for asset in "${assets[@]}"; do
    if [[ -L "$worktree/$asset" ]]; then
      target="$(readlink "$worktree/$asset")"
      if [[ "$target" == /* || ! -e "$worktree/$asset" ]]; then
        needs_repair=true
      fi
    fi
  done
  [[ "$needs_repair" == true ]] || return 0
  version="$(node -p 'require(process.argv[1]).version' "$worktree/package.json")"
  [[ "$version" == "2.0.1" ]] || fail "Non-portable language assets in version $version; no matching runtime repair is configured."

  mkdir -p "$cache"
  if [[ ! -f "$archive" ]]; then
    echo "==> Downloading published v2.0.1 language runtime assets"
    curl --fail --location --retry 3 --connect-timeout 20 \
      "$url" --output "$archive.download" || fail "Could not download language runtime assets: $url"
    mv -- "$archive.download" "$archive"
  fi
  node - "$archive" "$digest" <<'NODE'
const fs = require('node:fs');
const crypto = require('node:crypto');
const actual = crypto.createHash('sha256').update(fs.readFileSync(process.argv[2])).digest('hex');
if (actual !== process.argv[3]) {
  throw new Error(`Runtime VSIX checksum mismatch; remove ${process.argv[2]} and retry.`);
}
NODE

  for asset in "${assets[@]}"; do
    if [[ -L "$worktree/$asset" ]]; then
      target="$(readlink "$worktree/$asset")"
      if [[ "$target" == /* || ! -e "$worktree/$asset" ]]; then
        echo "==> Restoring $asset from verified v2.0.1 release"
        # Remove the link before writing; never follow it outside the worktree.
        rm -- "$worktree/$asset"
        unzip -p "$archive" "extension/$asset" > "$worktree/$asset"
        [[ -s "$worktree/$asset" ]] || fail "Empty runtime asset: $asset"
      fi
    fi
  done
  chmod +x "$worktree/server/openscad-lsp" "$worktree/server/openscad-lsp-linux"
}

# Validate the extracted, installable payload rather than just VSIX existence.
verify_extension() {
  local directory="$1"
  node - "$directory" <<'NODE'
const fs = require('node:fs');
const path = require('node:path');
const directory = process.argv[2];
const manifestPath = path.join(directory, 'package.json');
if (!fs.existsSync(manifestPath)) {
  throw new Error(`Missing extension package.json: ${manifestPath}`);
}
const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
if (!manifest.name || !manifest.publisher || !manifest.version) {
  throw new Error(`Invalid VS Code extension manifest: ${manifestPath}`);
}
if (manifest.main) {
  const main = path.resolve(directory, manifest.main);
  if (!main.startsWith(directory + path.sep) || !fs.statSync(main, { throwIfNoEntry: true })?.isFile()) {
    throw new Error(`Extension entry point is missing or invalid: ${manifest.main}`);
  }
}
if (manifest.publisher === 'Leathong' && manifest.name === 'openscad-language-support') {
  for (const asset of ['media/builtins.scad', 'server/openscad-lsp', 'server/openscad-lsp-linux', 'server/openscad-lsp.exe']) {
    const file = path.join(directory, asset);
    const stat = fs.lstatSync(file);
    if (!stat.isFile() || !stat.size) throw new Error(`Missing language runtime asset: ${asset}`);
    if (asset.startsWith('server/') && !asset.endsWith('.exe')) fs.accessSync(file, fs.constants.X_OK);
  }
}
console.log(`==> Verified ${manifest.publisher}.${manifest.name}@${manifest.version}`);
NODE
}

build_extension() {
  local repo_name="$1"
  local ref="$2"
  local repo="$SOURCES/$repo_name"
  local worktree="$WORKTREES/$repo_name"
  local vsix="$VSIX_DIR/$repo_name.vsix"
  local extracted="$EXTRACT_DIR/$repo_name"
  local unpacked="$UNPACKED_DIR/$repo_name"
  local commit

  echo ""
  echo "==> Building $repo_name"
  [[ -f "$repo/package.json" ]] || fail "Extension submodule not initialized: $repo"
  commit="$(resolve_ref "$repo" "$ref")" || fail "Could not resolve $repo_name ref: $ref"
  echo "    Ref:    $ref"
  echo "    Commit: $commit"

  # Recover from an interrupted prior run without touching sources/<repo>.
  if [[ -e "$worktree" || -L "$worktree" ]]; then
    if ! git -C "$repo" worktree remove --force "$worktree" 2>/dev/null; then
      git -C "$repo" worktree prune
      rm -rf -- "$worktree"  # Fixed path under Builder/.build only.
    fi
  fi
  git -C "$repo" worktree prune
  git -C "$repo" worktree add --detach "$worktree" "$commit"
  ACTIVE_REPO="$repo"
  ACTIVE_WORKTREE="$worktree"

  git -C "$worktree" submodule update --init --recursive

  if [[ "$ref" == "working-tree" ]]; then
    echo "==> Copying local source changes for $repo_name"
    rsync -a --delete --exclude='.git' --exclude='/node_modules' --exclude='/dist' \
      "$repo/" "$worktree/"
  fi

  if [[ "$repo_name" == "openscad-support-vscode" ]]; then
    fix_language_assets "$worktree"
  fi

  echo "==> Installing dependencies for $repo_name"
  (
    cd "$worktree"
    if [[ -f package-lock.json ]]; then
      npm ci
    else
      npm install
    fi
  )

  echo "==> Packaging $repo_name as VSIX"
  rm -f -- "$vsix"
  (
    cd "$worktree"
    # Runs the extension's vscode:prepublish script when present.
    # Keep dependency inclusion enabled: the language extension is not bundled.
    "$VSCE" package --out "$vsix"
  )
  [[ -s "$vsix" ]] || fail "VSIX was not created: $vsix"

  echo "==> Extracting $repo_name"
  rm -rf -- "$extracted"
  mkdir -p "$extracted"
  unzip -q "$vsix" -d "$extracted"
  [[ -f "$extracted/extension/package.json" ]] || fail "Invalid VSIX payload: $vsix"

  mkdir -p "$unpacked"
  rsync -a --delete "$extracted/extension/" "$unpacked/"
  verify_extension "$unpacked"
  rm -rf -- "$extracted"

  # Only the packaged payload is needed beyond this point.
  cleanup_active_worktree
  echo "==> Completed $repo_name"
}

build_extension "openscad-support-vscode" "$LANGUAGE_REF"
build_extension "vscode-openscad-preview" "$PREVIEW_REF"

echo ""
echo "==> Staging extensions into $STAGE_DIR"
bash "$ROOT/scripts/stage-extensions.sh" "$STAGE_DIR"

echo ""
echo "Successfully built and staged both extensions."
echo "VSIX artifacts:    $VSIX_DIR"
echo "Unpacked payloads: $UNPACKED_DIR"
echo "Dev extensions:    $STAGE_DIR"
