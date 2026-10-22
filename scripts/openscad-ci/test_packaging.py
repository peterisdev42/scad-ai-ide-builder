import hashlib
import io
import json
from pathlib import Path
import struct
import subprocess
import tarfile
import tempfile
import unittest
import zipfile

from build import ROOT, SOURCE_COMMIT, SOURCE_TAG, TARGETS, check_architecture, patch_source, package_name
from release import verify_assets


class PackagingTests(unittest.TestCase):
    def test_source_pin_matches_peeled_tag_commit_and_workflow(self):
        commit = subprocess.check_output([
            "git", "-C", str(ROOT / "sources/openscad"), "rev-parse",
            f"{SOURCE_TAG}^{{commit}}"], text=True).strip()
        self.assertEqual(SOURCE_COMMIT, commit)
        workflow = (ROOT / ".github/workflows/openscad-cli.yml").read_text()
        self.assertIn(f"  OPENSCAD_COMMIT: {commit}\n", workflow)

    def test_patch_applies_to_actual_tag_and_rejects_reapplication(self):
        original = subprocess.check_output([
            "git", "-C", str(ROOT / "sources/openscad"), "show",
            "openscad-2021.01:CMakeLists.txt"], text=True)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "CMakeLists.txt").write_text(original)
            (source / "src").mkdir()
            (source / "src/openscad.cc").write_text(subprocess.check_output([
                "git", "-C", str(ROOT / "sources/openscad"), "show",
                "openscad-2021.01:src/openscad.cc"], text=True))
            (source / "src/cgalutils-polyhedron.cc").write_text(subprocess.check_output([
                "git", "-C", str(ROOT / "sources/openscad"), "show",
                "openscad-2021.01:src/cgalutils-polyhedron.cc"], text=True))
            (source / "src/cgalutils-tess.cc").write_text(subprocess.check_output([
                "git", "-C", str(ROOT / "sources/openscad"), "show",
                "openscad-2021.01:src/cgalutils-tess.cc"], text=True))
            patch_source(source)
            patched = (source / "CMakeLists.txt").read_text()
            self.assertIn('set(VERSION "2021.01")', patched)
            self.assertIn('option(NULLGL "Build without OpenGL', patched)
            self.assertIn("src/OffscreenContextGLX.cc", patched)
            self.assertIn("src/OffscreenContextWGL.cc", patched)
            self.assertIn("src/OffscreenContextCGL.mm", patched)
            self.assertIn("/SUBSYSTEM:CONSOLE", patched)
            self.assertNotIn("add_subdirectory(tests)", patched)
            self.assertTrue((source / "ci-dependencies.cmake").is_file())
            self.assertNotIn("boost::join(", (source / "src/openscad.cc").read_text())
            self.assertIn("CGAL::Projection_traits_3<K>", (source / "src/cgalutils-tess.cc").read_text())
            with self.assertRaises((RuntimeError, ValueError)):
                patch_source(source)

    def test_architecture_detection_rejects_mislabeled_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "binary"
            for target in TARGETS:
                data = bytearray(256)
                if target.startswith("windows"):
                    data[:2] = b"MZ"
                    struct.pack_into("<I", data, 0x3C, 128)
                    data[128:132] = b"PE\0\0"
                    machine = {"x86": 0x14C, "x64": 0x8664, "arm64": 0xAA64}[target.split("-")[1]]
                    struct.pack_into("<H", data, 132, machine)
                elif target.startswith("linux"):
                    data[:4] = b"\x7fELF"
                    data[5] = 1
                    struct.pack_into("<H", data, 18, {"x64": 62, "arm64": 183, "armv7": 40}[target.split("-")[1]])
                    struct.pack_into("<I", data, 36, 0x400)
                else:
                    data[:4] = b"\xcf\xfa\xed\xfe"
                    struct.pack_into("<I", data, 4, 0x100000C if target.endswith("arm64") else 0x1000007)
                executable.write_bytes(data)
                check_architecture(executable, target)
                wrong = target.split("-")[0] + ("-x64" if not target.endswith("x64") else "-arm64")
                with self.assertRaises(RuntimeError):
                    check_architecture(executable, wrong)

    def create_assets(self, dist):
        for target in TARGETS:
            name = package_name(target)
            metadata = json.dumps({"target": target, "commit": SOURCE_COMMIT,
                                   "headless": True, "opengl": True, "nullgl": False}).encode()
            if target.startswith("windows"):
                archive = dist / (name + ".zip")
                with zipfile.ZipFile(archive, "w") as stream:
                    stream.writestr(name + "/build-info.json", metadata)
            else:
                archive = dist / (name + ".tar.gz")
                with tarfile.open(archive, "w:gz") as stream:
                    member = tarfile.TarInfo(name + "/build-info.json")
                    member.size = len(metadata)
                    stream.addfile(member, io.BytesIO(metadata))
            archive.with_name(archive.name + ".sha256").write_text(
                hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n")

    def test_release_gate_requires_all_architectures_and_valid_checksums(self):
        with tempfile.TemporaryDirectory() as directory:
            dist = Path(directory)
            with self.assertRaises(FileNotFoundError):
                verify_assets(dist)
            self.create_assets(dist)
            self.assertEqual({p.name for p in dist.iterdir() if not p.name.endswith('.sha256')}, {
                'openscad-cli-macos-arm64.tar.gz', 'openscad-cli-macos-x86_64.tar.gz',
                'openscad-cli-windows-arm64.zip', 'openscad-cli-windows-x86_64.zip',
                'openscad-cli-windows-x86.zip',
            })
            verify_assets(dist)
            windows_arm64 = dist / 'openscad-cli-windows-arm64.zip'
            content = windows_arm64.read_bytes()
            windows_arm64.unlink()
            with self.assertRaises(FileNotFoundError):
                verify_assets(dist)
            windows_arm64.write_bytes(content)
            archive = dist / "openscad-cli-macos-arm64.tar.gz"
            archive.write_bytes(archive.read_bytes() + b"tampered")
            with self.assertRaisesRegex(RuntimeError, "Invalid checksum"):
                verify_assets(dist)


if __name__ == "__main__":
    unittest.main()
