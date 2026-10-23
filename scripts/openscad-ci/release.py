#!/usr/bin/env python3
"""Assemble a release only when every required architecture is present."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

from build import (ROOT, HERE, SOURCE_COMMIT, SOURCE_TAG, TARGETS, VCPKG_COMMIT,
                   archive_source, output, package_name, MESA_VERSION, MESA_SHA256)


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
                or not metadata["headless"] or not metadata["opengl"] or metadata["nullgl"]):
            raise RuntimeError(f"Unexpected build metadata: {archive}")


VERSION = SOURCE_TAG.removeprefix("openscad-")
RELEASE_TAG = f"openscad-cli-{VERSION}"
RELEASE_TITLE = f"OpenSCAD CLI {VERSION}"
SOURCE_ASSET = f"openscad-cli-{VERSION}-source.tar.gz"
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
    return names + [SOURCE_ASSET]


def release_notes(repository):
    base = f"https://github.com/{repository}/releases/download/{RELEASE_TAG}/"
    lines = [
        "| OS | Architectures | Download URL | SHA256 |",
        "| :---: | :---: | :---: | :---: |",
    ]
    for (system, architecture, _), archive in zip(DOWNLOADS, release_assets()[:-1:2]):
        lines.append(f"| {system} | {architecture} | [{archive}]({base}{archive}) | "
                     f"[{archive}.sha256]({base}{archive}.sha256) |")
    return "\n".join(lines) + (
        f"\n\nOpenSCAD `{VERSION}` · `HEADLESS=ON` · `NULLGL=OFF`. "
        "OpenGL, OpenCSG and CGAL enabled; Qt GUI and optional 3MF I/O disabled. "
        "macOS uses native offscreen OpenGL; Windows bundles Mesa softpipe.\n\n"
        "All third-party runtime dependencies are bundled. Extract the complete archive "
        "and run `bin/openscad` (macOS) or `bin/openscad.exe` (Windows).\n\n"
        f"[Source and build patches]({base}{SOURCE_ASSET}) include OpenSCAD, MCAD and Mesa.\n"
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
        subprocess.check_call(["gh", "release", "create", RELEASE_TAG, *files,
                               "--target", os.environ["GITHUB_SHA"], "--title", RELEASE_TITLE,
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
    repo = ROOT / "sources/openscad"
    if output("git", "-C", repo, "rev-parse", "HEAD") != SOURCE_COMMIT:
        raise RuntimeError("Wrong OpenSCAD source revision")
    sources = ROOT / ".build/openscad-cli-2021.01-source"
    sources.mkdir(parents=True)
    archive_source(repo, sources / "openscad")
    archive_source(repo / "libraries/MCAD", sources / "openscad/libraries/MCAD")
    shutil.copytree(HERE, sources / "builder/scripts/openscad-ci",
                    ignore=shutil.ignore_patterns("__pycache__"))
    workflow = sources / "builder/.github/workflows"
    workflow.mkdir(parents=True)
    shutil.copy2(ROOT / ".github/workflows/openscad-cli.yml", workflow)
    (sources / "SOURCE-INFO.json").write_text(json.dumps({
        "openscad_tag": SOURCE_TAG, "openscad_commit": SOURCE_COMMIT,
        "mcad_commit": output("git", "-C", repo / "libraries/MCAD", "rev-parse", "HEAD"),
        "vcpkg_commit": VCPKG_COMMIT, "builder_commit": os.environ["GITHUB_SHA"],
    }, indent=2) + "\n")
    mesa = sources / f"mesa-{MESA_VERSION}.tar.xz"
    urllib.request.urlretrieve(f"https://archive.mesa3d.org/{mesa.name}", mesa)
    if hashlib.sha256(mesa.read_bytes()).hexdigest() != MESA_SHA256:
        raise RuntimeError("Mesa source checksum mismatch")
    shutil.make_archive(str(dist / sources.name), "gztar", sources.parent, sources.name)
    (ROOT / ".build/release-notes.md").write_text(
        release_notes(os.environ["GITHUB_REPOSITORY"]))


if __name__ == "__main__":
    if sys.argv[1:] == ["--publish"]:
        publish(ROOT / "dist", ROOT / ".build/release-notes.md", os.environ["GITHUB_REPOSITORY"])
    elif not sys.argv[1:]:
        main()
    else:
        raise SystemExit("Usage: release.py [--publish]")
