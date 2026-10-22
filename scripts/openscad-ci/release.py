#!/usr/bin/env python3
"""Assemble a release only when every required architecture is present."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
import zipfile

from build import (ROOT, HERE, SOURCE_COMMIT, SOURCE_TAG, TARGETS, VCPKG_COMMIT,
                   archive_source, output)


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
    (sources / "SOURCE-INFO.json").write_text(json.dumps({
        "openscad_tag": SOURCE_TAG, "openscad_commit": SOURCE_COMMIT,
        "mcad_commit": output("git", "-C", repo / "libraries/MCAD", "rev-parse", "HEAD"),
        "vcpkg_commit": VCPKG_COMMIT, "builder_commit": os.environ["GITHUB_SHA"],
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
        "| macOS 11+ | Intel x64, Apple Silicon ARM64 |\n\n"
        "This first-stage release contains macOS builds only. "
        "Extract the full archive, then run `bin/openscad`. "
        "Both architectures use native macOS offscreen OpenGL.\n\n"
        "Every package passed STL export, OpenCSG preview PNG, and CGAL-rendered PNG checks "
        "after relocation. Fonts and MCAD are included. These builds omit optional 3MF I/O.\n\n"
        f"OpenSCAD source: `{SOURCE_TAG}` (`{SOURCE_COMMIT}`). "
        "The source asset includes OpenSCAD, MCAD, build scripts/compatibility patches. "
        f"Dependencies use vcpkg `{VCPKG_COMMIT}`; "
        "Dependency licenses are included in each package.\n\n"
        "Use `SHA256SUMS` to verify downloads. See each package's README for runtime requirements.\n")


if __name__ == "__main__":
    main()
