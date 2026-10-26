#!/usr/bin/env python3
"""Assemble a release only when every required architecture is present."""
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import zipfile

from build import ROOT, SOURCE_COMMIT, SOURCE_TAG, TARGETS, MODERN_SOURCE, package_name, LINUX_GLIBC_BASELINE
from wasm import WASM_IMAGE, WASM_CAPABILITIES


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
                if target == "wasm":
                    for file in ("bin/openscad.mjs", "bin/openscad-core.mjs", "bin/openscad.wasm"):
                        member = stream.getmember(name + "/" + file)
                        if not member.isfile() or member.size == 0:
                            raise RuntimeError(f"Invalid WASM runtime file: {file}")
                    if stream.extractfile(name + "/bin/openscad.wasm").read(8) != b"\0asm\x01\0\0\0":
                        raise RuntimeError("Invalid WebAssembly binary")
        wasm = target == "wasm"
        if target.startswith("linux"):
            required = metadata.get("required_glibc", "")
            if (metadata.get("libc") != "glibc" or metadata.get("minimum_glibc") != LINUX_GLIBC_BASELINE
                    or metadata.get("linux_baseline") != "ubuntu-22.04"
                    or not required or not all(part.isdigit() for part in required.split("."))
                    or tuple(map(int, required.split("."))) > tuple(map(int, LINUX_GLIBC_BASELINE.split(".")))):
                raise RuntimeError(f"Unexpected Linux ABI metadata: {archive}")
        if (metadata["commit"] != SOURCE_COMMIT or metadata["target"] != target
                or metadata.get("tag") != SOURCE_TAG
                or (MODERN_SOURCE and not metadata.get("manifold"))
                or metadata.get("asan", False)
                or not metadata["headless"]
                or metadata["opengl"] != (not wasm) or metadata["nullgl"] != wasm
                or (wasm and (metadata.get("runtime") != "node"
                              or metadata.get("node_min_version") != 22
                              or metadata.get("wasm_image") != WASM_IMAGE
                              or any(metadata.get(key) != value for key, value in WASM_CAPABILITIES.items())))):
            raise RuntimeError(f"Unexpected build metadata: {archive}")


VERSION = SOURCE_TAG.removeprefix("openscad-")
RELEASE_TAG = f"openscad-cli-{VERSION}"
RELEASE_TITLE = f"OpenSCAD CLI {VERSION}"
DOWNLOADS = (
    ("macOS", "Apple Silicon", "macos-arm64"),
    ("macOS", "Intel x64", "macos-x64"),
    ("Windows", "x64", "windows-x64"),
    ("Linux", "x64", "linux-x64"),
    ("Linux", "ARM64", "linux-arm64"),
    ("Node.js / WASM", "Portable wasm32", "wasm"),
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
        f"\n\nNative OpenSCAD `{VERSION}` · `HEADLESS=ON` · `NULLGL=OFF`. "
        f"OpenGL, OpenCSG, CGAL{', Manifold' if MODERN_SOURCE else ''} enabled; "
        "Qt GUI disabled. 3MF I/O is enabled on Linux and disabled on macOS/Windows. "
        "macOS uses native offscreen OpenGL; Windows bundles Mesa softpipe. "
        "Linux targets glibc 2.35+ and uses the host GL/GLX stack; "
        "headless PNG rendering also requires Xvfb and xauth.\n\n"
        "WASM is a portable Node.js 22+ geometry fallback (`HEADLESS=ON`, `NULLGL=ON`), "
        f"with CGAL{' and Manifold' if MODERN_SOURCE else ''}. "
        "OpenGL/OpenCSG PNG preview, Cairo/PDF and 3MF I/O are unavailable in WASM. "
        "Run `node bin/openscad.mjs` after extracting the complete WASM archive. "
        "\n\n"
        "Extract the complete archive and run `bin/openscad` (macOS/Linux) "
        "or `bin/openscad.exe` (Windows). Linux bundles non-system dependencies "
        "including libstdc++; glibc, the loader and graphics drivers come from the host.\n"
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
