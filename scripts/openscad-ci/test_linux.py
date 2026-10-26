import io
import json
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest
from unittest import mock

import build
import release
import test_packaging


class LinuxPackagingTests(unittest.TestCase):
    def test_glibc_requirement_checks_all_bundled_elf_files(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory)
            (package / 'lib').mkdir()
            (package / 'lib/libexample.so').touch()
            with mock.patch('build.output', side_effect=['Name: GLIBC_2.17', 'Name: GLIBC_2.35']):
                self.assertEqual(build.linux_glibc_requirement(package), '2.35')
            with mock.patch('build.output', side_effect=['Name: GLIBC_2.17', 'Name: GLIBC_2.36']):
                with self.assertRaisesRegex(RuntimeError, 'above baseline'):
                    build.linux_glibc_requirement(package)

    def test_linux_audit_rejects_external_non_system_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / 'relocated package'
            binary = package / 'bin/openscad-bin'
            binary.parent.mkdir(parents=True)
            (package / 'lib').mkdir()
            data = bytearray(64)
            data[:6] = b'\x7fELF\x02\x01'
            struct.pack_into('<H', data, 18, 62)
            binary.write_bytes(data)
            with mock.patch('build.output', return_value='libexample.so => /usr/lib/libexample.so (0x1234)'):
                with self.assertRaisesRegex(RuntimeError, 'Unbundled runtime dependency'):
                    build.verify_runtime_dependencies(package, 'linux-x64')
            with mock.patch('build.output', return_value='libc.so.6 => /lib/libc.so.6 (0x1234)'), mock.patch(
                    'build.linux_glibc_requirement', return_value='2.34'):
                build.verify_runtime_dependencies(package, 'linux-x64')
            library = package / 'lib/libexample.so'
            library.write_bytes(data)
            def linked(*args):
                return (f'libexample.so => {library} (0x1234)' if args[-1] == binary
                        else 'libc.so.6 => /lib/libc.so.6 (0x1234)')
            with mock.patch('build.output', side_effect=linked), mock.patch(
                    'build.linux_glibc_requirement', return_value='2.34'):
                build.verify_runtime_dependencies(package, 'linux-x64')

    def test_release_rejects_linux_packages_above_glibc_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            dist = Path(directory)
            test_packaging.PackagingTests().create_assets(dist)
            archive = dist / 'openscad-cli-linux-arm64.tar.gz'
            name = 'openscad-cli-linux-arm64/build-info.json'
            with tarfile.open(archive) as stream:
                metadata = json.load(stream.extractfile(name))
            metadata['required_glibc'] = '2.36'
            data = json.dumps(metadata).encode()
            with tarfile.open(archive, 'w:gz') as stream:
                member = tarfile.TarInfo(name)
                member.size = len(data)
                stream.addfile(member, io.BytesIO(data))
            import hashlib
            archive.with_name(archive.name + '.sha256').write_text(
                hashlib.sha256(archive.read_bytes()).hexdigest() + '  ' + archive.name + '\n')
            with self.assertRaisesRegex(RuntimeError, 'Unexpected Linux ABI metadata'):
                release.verify_assets(dist)
