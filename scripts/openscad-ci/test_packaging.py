import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

from build import (ROOT, SOURCE_COMMIT, SOURCE_TAG, TARGETS, NATIVE_TARGETS, check_architecture,
                   patch_source, patch_legacy_source, patch_modern_source, MODERN_SOURCE, package_name, verify_runtime_dependencies)
from build import smoke_test, copy_windows_runtime
from release import verify_assets, release_assets, release_notes, publish, RELEASE_TAG
from wasm import WASM_IMAGE, WASM_CAPABILITIES


class PackagingTests(unittest.TestCase):
    def test_smoke_preserves_version_failure_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            error = subprocess.CalledProcessError(1, ['openscad', '--version'],
                                                 output='AddressSanitizer: startup failure\n')
            with mock.patch('build.verify_runtime_dependencies'), mock.patch(
                    'build.run', side_effect=error):
                with self.assertRaises(subprocess.CalledProcessError) as caught:
                    smoke_test(root, 'macos-arm64', root)
            self.assertIs(caught.exception, error)
            self.assertEqual((root / 'smoke/smoke.log').read_text(), error.stdout)

    def test_release_table_links_cover_exactly_six_packages_and_checksums(self):
        import re
        assets = release_assets()
        self.assertEqual(len(assets), 12)
        self.assertEqual(len(set(assets)), 12)
        self.assertNotIn('SHA256SUMS', assets)
        self.assertFalse(any('source' in name for name in assets))
        notes = release_notes('example/builder')
        links = re.findall(r'\[([^]]+)\]\(([^)]+)\)', notes)
        urls = [url for _, url in links]
        self.assertEqual(len(urls), 12)
        base = f'https://github.com/example/builder/releases/download/{RELEASE_TAG}/'
        self.assertEqual({url.removeprefix(base) for url in urls}, set(assets))
        self.assertTrue(all(url.startswith(base) for url in urls))
        self.assertEqual({name for name, _ in links}, set(assets))
        self.assertTrue(all(url == base + name for name, url in links))
        self.assertNotIn('Compatibility', notes)

    def test_new_release_targets_branch_when_build_commit_is_no_longer_head(self):
        with tempfile.TemporaryDirectory() as directory:
            dist = Path(directory)
            for name in release_assets():
                (dist / name).touch()
            current = {'assets': [{'name': name, 'id': i} for i, name in enumerate(release_assets())]}
            with mock.patch.dict(os.environ, {'GITHUB_REF_NAME': 'main', 'GITHUB_SHA': 'old-build-commit'}), mock.patch(
                    'release.verify_assets'), mock.patch('release.subprocess.check_output',
                    side_effect=[json.dumps([[]]), json.dumps(current)]), mock.patch(
                    'release.subprocess.check_call') as command:
                publish(dist, dist / 'notes.md', 'example/builder')
                args = command.call_args_list[0].args[0]
                self.assertEqual(args[:3], ['gh', 'release', 'create'])
                self.assertEqual(args[args.index('--target') + 1], 'main')
                self.assertNotIn('old-build-commit', args)

    def test_release_cleanup_only_after_verified_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            dist = Path(directory)
            for name in release_assets():
                (dist / name).touch()
            existing = {'tag_name': RELEASE_TAG}
            current = {'assets': [{'name': name, 'id': i} for i, name in enumerate(release_assets())]
                       + [{'name': 'openscad-cli-2021.01-source.tar.gz', 'id': 99}]}
            with mock.patch('release.verify_assets'), mock.patch(
                    'release.subprocess.check_output', side_effect=[json.dumps([[existing]]), json.dumps(current)]), mock.patch(
                    'release.subprocess.check_call') as command:
                publish(dist, dist / 'notes.md', 'example/builder')
                calls = [call.args[0] for call in command.call_args_list]
                self.assertEqual(calls[-1], ['gh', 'api', '--method', 'DELETE',
                                            'repos/example/builder/releases/assets/99'])
                self.assertNotIn(str(dist / 'openscad-cli-2021.01-source.tar.gz'), calls[0])
            with mock.patch('release.verify_assets'), mock.patch(
                    'release.subprocess.check_output', return_value=json.dumps([[existing]])), mock.patch(
                    'release.subprocess.check_call', side_effect=subprocess.CalledProcessError(1, 'gh')) as command:
                with self.assertRaises(subprocess.CalledProcessError):
                    publish(dist, dist / 'notes.md', 'example/builder')
                self.assertEqual(command.call_count, 1)

    def test_windows_runtime_collection_follows_transitive_imports_and_toolset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binaries = root / 'bin'
            binaries.mkdir()
            (binaries / 'openscad.exe').touch()
            crt = root / 'VC/Redist/MSVC/14.44.1/arm64/Microsoft.VC143.CRT'
            crt.mkdir(parents=True)
            for name in ('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll'):
                (crt / name).write_bytes(name.encode())
            (root / 'VC/Redist/MSVC/14.51.1/arm64/Microsoft.VC145.CRT').mkdir(parents=True)
            imports = {'openscad.exe': ['MSVCP140.dll'], 'msvcp140.dll': ['VCRUNTIME140.dll'],
                       'vcruntime140.dll': ['KERNEL32.dll']}
            with mock.patch.dict(os.environ, {'VCINSTALLDIR': str(root / 'VC'), 'VCToolsVersion': '14.44.35207'}), mock.patch(
                    'build.windows_imports', side_effect=lambda p: imports[p.name]):
                self.assertEqual(copy_windows_runtime(binaries, 'windows-arm64'), '14.44.1')
            self.assertEqual({p.name for p in binaries.iterdir()},
                             {'openscad.exe', 'msvcp140.dll', 'vcruntime140.dll'})

    def test_windows_smoke_environment_handles_uppercase_copied_keys(self):
        class WindowsEnvironment(dict):
            def __getitem__(self, name):
                return super().__getitem__(name.upper())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = WindowsEnvironment(SYSTEMROOT=str(root), PATH='/third-party/bin')
            def first_command(*args, **kwargs):
                self.assertEqual(kwargs['env']['PATH'], str(root / 'System32') + os.pathsep + str(root))
                raise RuntimeError('Reached CLI with isolated PATH')
            with mock.patch('build.os.environ', environment), mock.patch(
                    'build.verify_runtime_dependencies'), mock.patch('build.run', side_effect=first_command):
                with self.assertRaisesRegex(RuntimeError, 'Reached CLI with isolated PATH'):
                    smoke_test(root, 'windows-x64', root)

    def test_windows_dependency_audit_rejects_missing_transitive_library(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory)
            (package / 'bin').mkdir()
            for name in ('openscad.exe', 'opengl32.dll', 'library.dll'):
                (package / 'bin' / name).touch()
            def imports(*args):
                dependency = 'missing.dll' if args[-1].name == 'library.dll' else 'library.dll'
                return f'Image has the following dependencies:\n    {dependency}\n'
            with mock.patch.dict('os.environ', {'SystemRoot': directory}), mock.patch(
                    'build.check_architecture'), mock.patch('build.output', side_effect=imports):
                with self.assertRaisesRegex(RuntimeError, 'library.dll: missing.dll'):
                    verify_runtime_dependencies(package, 'windows-arm64')
                (package / 'bin/missing.dll').touch()
                verify_runtime_dependencies(package, 'windows-arm64')

    def test_macos_dependency_audit_resolves_rpath_inside_package(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory)
            executable = package / 'OpenSCAD.app/Contents/MacOS/OpenSCAD'
            library = package / 'OpenSCAD.app/Contents/Frameworks/library.dylib'
            data = b'\xcf\xfa\xed\xfe' + struct.pack('<I', 0x100000C)
            for path in (executable, library):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            def linked(*args):
                if args[1] == '-l':
                    return 'cmd LC_RPATH\ncmdsize 48\npath @executable_path/../Frameworks (offset 12)\n'
                dependency = '@rpath/library.dylib' if args[-1] == executable else '/usr/lib/libSystem.B.dylib'
                return f'{args[-1]}:\n\t{dependency} (compatibility version 1.0.0, current version 1.0.0)\n'
            with mock.patch('build.output', side_effect=linked):
                verify_runtime_dependencies(package, 'macos-arm64')
                library.unlink()
                with self.assertRaisesRegex(RuntimeError, 'Unbundled runtime dependency'):
                    verify_runtime_dependencies(package, 'macos-arm64')

    def test_source_pin_matches_peeled_tag_commit_and_workflow(self):
        commit = subprocess.check_output([
            "git", "-C", str(ROOT / "sources/openscad"), "rev-parse",
            f"{SOURCE_TAG}^{{commit}}"], text=True).strip()
        self.assertEqual(SOURCE_COMMIT, commit)
        workflow = (ROOT / ".github/workflows/openscad-cli.yml").read_text()
        self.assertIn(commit, workflow)
        self.assertIn("ref: ${{ env.OPENSCAD_TAG }}", workflow)

    @unittest.skipIf(MODERN_SOURCE, "Legacy patch is exercised when selecting openscad-2021.01")
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

    @unittest.skipUnless(MODERN_SOURCE, "Modern target requires the TEST4 source checkout")
    def test_modern_patch_preserves_backends_and_uses_console_target(self):
        original = subprocess.check_output([
            "git", "-C", str(ROOT / "sources/openscad"), "show", f"{SOURCE_TAG}:CMakeLists.txt"], text=True)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / 'CMakeLists.txt').write_text(original)
            (source / 'cmake/Modules').mkdir(parents=True)
            (source / 'cmake/Modules/FindOpenCSG.cmake').write_text(subprocess.check_output([
                'git', '-C', str(ROOT / 'sources/openscad'), 'show',
                f'{SOURCE_TAG}:cmake/Modules/FindOpenCSG.cmake'], text=True))
            (source / 'src').mkdir()
            (source / 'src/main_wrapper.cc').write_text(subprocess.check_output([
                'git', '-C', str(ROOT / 'sources/openscad'), 'show',
                f'{SOURCE_TAG}:src/main_wrapper.cc'], text=True))
            (source / 'src/glview').mkdir()
            (source / 'src/glview/OffscreenView.cc').write_text(subprocess.check_output([
                'git', '-C', str(ROOT / 'sources/openscad'), 'show',
                f'{SOURCE_TAG}:src/glview/OffscreenView.cc'], text=True))
            (source / 'resources').mkdir()
            for name in ('openscad_win32.rc.in', 'openscad_win32-nightly.rc.in'):
                (source / 'resources' / name).write_text(subprocess.check_output([
                    'git', '-C', str(ROOT / 'sources/openscad'), 'show',
                    f'{SOURCE_TAG}:resources/{name}'], text=True))
            patch_modern_source(source)
            for path in (source / 'resources').glob('*.rc.in'):
                self.assertNotIn('RT_MANIFEST', path.read_text())
            wrapper = (source / 'src/main_wrapper.cc').read_text()
            self.assertIn('std::vector<char *> argv8(argc + 1, nullptr)', wrapper)
            self.assertIn('main(argc, argv8.data())', wrapper)
            patched = (source / 'CMakeLists.txt').read_text()
            self.assertIn('/ENTRY:wmainCRTStartup', patched)
            self.assertIn('target_sources(OpenSCADExe PRIVATE ${RESOURCE_FILES} "${CMAKE_BINARY_DIR}/openscad.manifest")', patched)
            self.assertEqual(patched.count('set(CAIRO_LIBRARIES PkgConfig::CAIRO)'), 2)
            self.assertIn('WIN32_EXECUTABLE OFF', patched)
            self.assertIn('cmake_policy(SET CMP0167 OLD)', patched)
            self.assertIn('add_compile_definitions(BOOST_NO_CXX98_FUNCTION_BASE)', patched)
            self.assertIn('target_link_libraries(OpenSCADLibInternal PUBLIC ${Intl_LIBRARIES})', patched)
            self.assertIn('macro(find_graphics)\n  list(APPEND CMAKE_MODULE_PATH', patched)
            self.assertIn('opencsg.h PATH_SUFFIXES opencsg', (source / 'cmake/Modules/FindOpenCSG.cmake').read_text())
            self.assertIn('/SUBSYSTEM:CONSOLE', patched)
            self.assertIn('option(ENABLE_MANIFOLD "Enable Manifold." ON)', patched)
            self.assertIn('option(ENABLE_CGAL "Enable CGAL." ON)', patched)
            self.assertNotIn('add_custom_command(TARGET OpenSCADExe POST_BUILD', patched)
            self.assertNotIn('ci-dependencies.cmake', patched)
            with self.assertRaises((RuntimeError, ValueError)):
                patch_modern_source(source)

    def test_architecture_detection_rejects_mislabeled_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "binary"
            for target in NATIVE_TARGETS:
                data = bytearray(256)
                if target.startswith("windows"):
                    data[:2] = b"MZ"
                    struct.pack_into("<I", data, 0x3C, 128)
                    data[128:132] = b"PE\0\0"
                    machine = {"x64": 0x8664, "arm64": 0xAA64}[target.split("-")[1]]
                    struct.pack_into("<H", data, 132, machine)
                elif target.startswith("linux"):
                    data[:4] = b"\x7fELF"
                    data[4] = 2
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

    def create_assets(self, dist, asan=False):
        for target in TARGETS:
            name = package_name(target)
            wasm = target == 'wasm'
            metadata = {"target": target, "commit": SOURCE_COMMIT, "tag": SOURCE_TAG, "manifold": MODERN_SOURCE,
                        "headless": True, "opengl": not wasm, "nullgl": wasm, "asan": asan}
            if target.startswith("linux"):
                metadata.update(libc="glibc", minimum_glibc="2.35", required_glibc="2.34", linux_baseline="ubuntu-22.04")
            if wasm:
                metadata.update(WASM_CAPABILITIES, runtime='node', node_min_version=22, wasm_image=WASM_IMAGE)
            metadata = json.dumps(metadata).encode()
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
                    if wasm:
                        for filename, data in {'bin/openscad.mjs': b'// launcher',
                                               'bin/openscad-core.mjs': b'// loader',
                                               'bin/openscad.wasm': b'\0asm\x01\0\0\0'}.items():
                            member = tarfile.TarInfo(name + '/' + filename)
                            member.size = len(data)
                            stream.addfile(member, io.BytesIO(data))
            archive.with_name(archive.name + ".sha256").write_text(
                hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n")

    def test_release_gate_rejects_instrumented_diagnostic_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            dist = Path(directory)
            self.create_assets(dist, asan=True)
            with self.assertRaisesRegex(RuntimeError, 'Unexpected build metadata'):
                verify_assets(dist)

    def test_release_gate_requires_all_architectures_and_valid_checksums(self):
        with tempfile.TemporaryDirectory() as directory:
            dist = Path(directory)
            with self.assertRaises(FileNotFoundError):
                verify_assets(dist)
            self.create_assets(dist)
            self.assertEqual({p.name for p in dist.iterdir() if not p.name.endswith('.sha256')}, {
                'openscad-cli-macos-arm64.tar.gz', 'openscad-cli-macos-x86_64.tar.gz',
                'openscad-cli-linux-arm64.tar.gz', 'openscad-cli-linux-x86_64.tar.gz', 'openscad-cli-windows-x86_64.zip',
                'openscad-cli-wasm.tar.gz',
            })
            verify_assets(dist)
            windows_arm64 = dist / 'openscad-cli-windows-x86_64.zip'
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
