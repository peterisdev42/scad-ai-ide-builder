#!/usr/bin/env python3
"""Assemble a release only when every required architecture is present."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
import urllib.request
import zipfile

from build import (ROOT, HERE, SOURCE_COMMIT, SOURCE_TAG, TARGETS, VCPKG_COMMIT,
                   MESA_SHA256, MESA_VERSION, archive_source, output)


def verify_assets(dist):
    for target in TARGETS:
        name = f"openscad-cli-2021.01-{target}"
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
    mesa = sources / f"mesa-{MESA_VERSION}.tar.xz"
    urllib.request.urlretrieve(f"https://archive.mesa3d.org/mesa-{MESA_VERSION}.tar.xz", mesa)
    if hashlib.sha256(mesa.read_bytes()).hexdigest() != MESA_SHA256:
        raise RuntimeError("Mesa source checksum mismatch")
    (sources / "SOURCE-INFO.json").write_text(json.dumps({
        "openscad_tag": SOURCE_TAG, "openscad_commit": SOURCE_COMMIT,
        "mcad_commit": output("git", "-C", repo / "libraries/MCAD", "rev-parse", "HEAD"),
        "vcpkg_commit": VCPKG_COMMIT, "mesa_version": MESA_VERSION,
        "mesa_sha256": MESA_SHA256, "builder_commit": os.environ["GITHUB_SHA"],
    }, indent=2) + "\n")
    shutil.make_archive(str(dist / sources.name), "gztar", sources.parent, sources.name)
    checksums = []
    for archive in sorted(dist.iterdir()):
        if archive.name.endswith((".tar.gz", ".zip")):
            checksums.append(hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name)
    (dist / "SHA256SUMS").write_text("\n".join(checksums) + "\n")
    (ROOT / ".build/release-notes.md").write_text(
        "OpenSCAD **2021.01** headless CLI, with OpenGL and OpenCSG enabled.\n\n"
        "| OS | Architectures |\n|---|---|\n"
        "| Linux (glibc, Ubuntu 22.04 or compatible newer distribution) | x64, ARM64, ARMv7 hard-float |\n"
        "| macOS 11+ | Intel x64, Apple Silicon ARM64 |\n"
        "| Windows | x64, x86, ARM64 |\n\n"
        "Extract the full archive, then run `bin/openscad` (`bin/openscad.exe` on Windows). "
        "Linux PNG rendering requires X11/GLX or `xvfb-run`, Mesa drivers, and `xauth`; "
        "the wrapper starts Xvfb automatically when DISPLAY is unset. "
        "Windows packages include Mesa softpipe for rendering without a GPU.\n\n"
        "Every package passed STL export, OpenCSG preview PNG, and CGAL-rendered PNG checks "
        "after relocation. Fonts and MCAD are included. macOS/Windows builds omit optional 3MF I/O.\n\n"
        f"OpenSCAD source: `{SOURCE_TAG}` (`{SOURCE_COMMIT}`). "
        "The source asset includes OpenSCAD, MCAD, build scripts/compatibility patches, and Mesa source. "
        f"Other macOS/Windows dependencies use vcpkg `{VCPKG_COMMIT}`; "
        "Linux dependency versions are listed in each package's `licenses/system-packages.txt`.\n\n"
        "Use `SHA256SUMS` to verify downloads. See each package's README for runtime requirements.\n")


if __name__ == "__main__":
    main()
