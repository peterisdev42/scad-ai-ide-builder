#!/usr/bin/env python3
"""Assemble a release only when every required architecture is present."""
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import zipfile

from build import ROOT, SOURCE_COMMIT, SOURCE_TAG, TARGETS, MODERN_SOURCE, package_name


def verify_assets(dist):
    for target in TARGETS:
        name = package_name(target)
        extension = ".zip" if target.startswith("windows") else ".tar.gz"
        archive = dist / (name + extension)
        checksum = archive.with_name(archive.name + ".sha256")
        expected = hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n"
        if checksum.read_text() != expected:
            raise RuntimeError(f"Invalid checksum: {archive}")
        if extension == ".zip":
            with zipfile.ZipFile(archive) as stream:
                metadata = json.loads(stream.read(name + "/build-info.json"))
        else:
            with tarfile.open(archive) as stream:
                metadata = json.load(stream.extractfile(name + "/build-info.json"))
        if (metadata["commit"] != SOURCE_COMMIT or metadata["target"] != target
                or metadata.get("tag") != SOURCE_TAG
                or (MODERN_SOURCE and not metadata.get("manifold"))
                or metadata.get("asan", False)
                or not metadata["headless"] or not metadata["opengl"] or metadata["nullgl"]):
            raise RuntimeError(f"Unexpected build metadata: {archive}")


VERSION = SOURCE_TAG.removeprefix("openscad-")
RELEASE_TAG = f"openscad-cli-{VERSION}"
RELEASE_TITLE = f"OpenSCAD CLI {VERSION}"
DOWNLOADS = (
    ("macOS", "Apple Silicon", "macos-arm64"),
    ("macOS", "Intel x64", "macos-x64"),
    ("Windows", "ARM64", "windows-arm64"),
    ("Windows", "x64", "windows-x64"),
    ("Windows", "x86", "windows-x86"),
)


def release_assets():
    names = []
    for _, _, target in DOWNLOADS:
        archive = package_name(target) + (".zip" if target.startswith("windows") else ".tar.gz")
        names.extend((archive, archive + ".sha256"))
    return names


def release_notes(repository):
    base = f"https://github.com/{repository}/releases/download/{RELEASE_TAG}/"
    lines = [
        "| OS | Architectures | Download URL | SHA256 |",
        "| :---: | :---: | :---: | :---: |",
    ]
    for (system, architecture, _), archive in zip(DOWNLOADS, release_assets()[::2]):
        lines.append(f"| {system} | {architecture} | [{archive}]({base}{archive}) | "
                     f"[{archive}.sha256]({base}{archive}.sha256) |")
    return "\n".join(lines) + (
        f"\n\nOpenSCAD `{VERSION}` · `HEADLESS=ON` · `NULLGL=OFF`. "
        f"OpenGL, OpenCSG, CGAL{', Manifold' if MODERN_SOURCE else ''} enabled; "
        "Qt GUI and optional 3MF I/O disabled. "
        "macOS uses native offscreen OpenGL; Windows bundles Mesa softpipe.\n\n"
        "All third-party runtime dependencies are bundled. Extract the complete archive "
        "and run `bin/openscad` (macOS) or `bin/openscad.exe` (Windows).\n"
    )


def publish(dist, notes, repository):
    # Never upload an unchecked dist glob or remove old assets before upload succeeds.
    verify_assets(dist)
    assets = release_assets()
    for name in assets:
        if not (dist / name).is_file():
            raise FileNotFoundError(dist / name)
    releases = json.loads(subprocess.check_output(
        ["gh", "api", "--paginate", "--slurp", f"repos/{repository}/releases?per_page=100"], text=True))
    existing = next((r for page in releases for r in page if r["tag_name"] == RELEASE_TAG), None)
    files = [str(dist / name) for name in assets]
    common = ["--repo", repository]
    if existing:
        subprocess.check_call(["gh", "release", "upload", RELEASE_TAG, *files, "--clobber", *common])
        subprocess.check_call(["gh", "release", "edit", RELEASE_TAG, "--title", RELEASE_TITLE,
                               "--notes-file", str(notes), *common])
    else:
        # A moving branch ref remains publishable if main advances during the build.
        # The package build-info.json records the actual immutable builder commit.
        subprocess.check_call(["gh", "release", "create", RELEASE_TAG, *files,
                               "--target", os.environ["GITHUB_REF_NAME"], "--title", RELEASE_TITLE,
                               "--notes-file", str(notes), "--latest=false", *common])
    current = json.loads(subprocess.check_output(
        ["gh", "api", f"repos/{repository}/releases/tags/{RELEASE_TAG}"], text=True))
    uploaded = {a["name"] for a in current["assets"]}
    if not set(assets).issubset(uploaded):
        raise RuntimeError("Release upload incomplete; retaining existing assets")
    for asset in current["assets"]:
        if asset["name"] not in assets:
            subprocess.check_call(["gh", "api", "--method", "DELETE",
                                   f"repos/{repository}/releases/assets/{asset['id']}"])


def main():
    dist = ROOT / "dist"
    verify_assets(dist)
    (ROOT / ".build").mkdir(exist_ok=True)
    (ROOT / ".build/release-notes.md").write_text(
        release_notes(os.environ["GITHUB_REPOSITORY"]))


if __name__ == "__main__":
    if sys.argv[1:] == ["--publish"]:
        publish(ROOT / "dist", ROOT / ".build/release-notes.md", os.environ["GITHUB_REPOSITORY"])
    elif not sys.argv[1:]:
        main()
    else:
        raise SystemExit("Usage: release.py [--publish]")
