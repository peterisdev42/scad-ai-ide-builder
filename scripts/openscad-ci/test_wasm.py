import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

from build import ROOT, SOURCE_TAG, MODERN_SOURCE
from wasm import patch_wasm_source
import test_packaging
from release import verify_assets


class WasmTests(unittest.TestCase):
    def test_wasm_patch_applies_to_selected_real_source_without_native_patches(self):
        archive = subprocess.check_output(['git', '-C', str(ROOT / 'sources/openscad'), 'archive', SOURCE_TAG])
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
                stream.extractall(source)
            patch_wasm_source(source)
            cmake = (source / 'CMakeLists.txt').read_text()
            self.assertIn('-lnodefs.js', cmake)
            self.assertNotIn('-sNODERAWFS', cmake)
            self.assertNotIn('-sSINGLE_FILE', cmake)
            self.assertNotIn('-sABORT_ON_WASM_EXCEPTIONS', cmake)
            if MODERN_SOURCE:
                self.assertIn('option(ENABLE_CGAL "Enable CGAL." ON)', cmake)
                self.assertIn('option(ENABLE_MANIFOLD "Enable Manifold." ON)', cmake)
                self.assertNotIn('/ENTRY:wmainCRTStartup', cmake)
                self.assertIn('fs::path(argv[0]).parent_path().generic_string()',
                              (source / 'src/openscad.cc').read_text())
            else:
                self.assertIn('CGAL::CGAL', (source / 'ci-dependencies.cmake').read_text())
                self.assertNotIn('OpenGL REQUIRED', (source / 'ci-dependencies.cmake').read_text())

    def test_release_rejects_wasm_disguised_as_native_or_with_missing_module(self):
        with tempfile.TemporaryDirectory() as directory:
            dist = Path(directory)
            test_packaging.PackagingTests().create_assets(dist)
            archive = dist / 'openscad-cli-wasm.tar.gz'
            original = archive.read_bytes()
            for invalid in ('native-flags', 'missing-module', 'wrong-module'):
                archive.write_bytes(original)
                with tarfile.open(archive) as stream:
                    files = {member.name: stream.extractfile(member).read() for member in stream.getmembers()}
                if invalid == 'native-flags':
                    name = 'openscad-cli-wasm/build-info.json'
                    metadata = json.loads(files[name])
                    metadata.update(opengl=True, nullgl=False)
                    files[name] = json.dumps(metadata).encode()
                elif invalid == 'missing-module':
                    del files['openscad-cli-wasm/bin/openscad.wasm']
                else:
                    files['openscad-cli-wasm/bin/openscad.wasm'] = b'not wasm'
                with tarfile.open(archive, 'w:gz') as stream:
                    for name, data in files.items():
                        member = tarfile.TarInfo(name)
                        member.size = len(data)
                        stream.addfile(member, io.BytesIO(data))
                archive.with_name(archive.name + '.sha256').write_text(
                    hashlib.sha256(archive.read_bytes()).hexdigest() + '  ' + archive.name + '\n')
                with self.assertRaises((RuntimeError, KeyError)):
                    verify_assets(dist)


if __name__ == '__main__':
    unittest.main()
