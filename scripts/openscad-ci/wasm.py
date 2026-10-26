#!/usr/bin/env python3
"""Build a portable Node.js geometry CLI from the same pinned OpenSCAD sources."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.request

from build import (ROOT, HERE, SOURCE_TAG, SOURCE_COMMIT, SOURCE_VERSION, MODERN_SOURCE,
                   archive_source, output, patch_legacy_source, replace_once, run)

# Official OpenSCAD dependency image, Emscripten 4.0.10. Never follow :latest.
WASM_IMAGE = "openscad/wasm-base@sha256:f73d33d5f2fd4c7ae4d3aaacb1e2e2deb193b878b38bb80c8235c933ac340c66"
CGAL_LEGACY_COMMIT = "a1ae234fa258b054275bde38cc38dde552c50993"  # v5.5.1
WASM_CAPABILITIES = {"stl": True, "cgal": True, "opengl": False,
                     "png": False, "pdf": False, "lib3mf": False}


def patch_wasm_source(source):
    path = source / "CMakeLists.txt"
    if MODERN_SOURCE:
        text = path.read_text()
        # NodeFS supplies portable virtual paths on Windows as well as POSIX.
        if text.count("      -sNODERAWFS\n") != 2:
            raise RuntimeError("Unexpected upstream Node filesystem options")
        text = text.replace("      -sNODERAWFS\n", "      -lnodefs.js\n")
        text = text.replace("$<$<CONFIG:Release>:-sSINGLE_FILE>", "")
        text = replace_once(text, "['ENV','ERRNO_CODES','FS','PATH','callMain']",
                            "['ENV','ERRNO_CODES','FS','NODEFS','PATH','callMain']")
        # Emscripten 4's abort instrumentation marks normal exit(0) as ABORT
        # before unwinding into callMain(), then rejects its cleanup exports.
        # Keep C++ exception handling and EXIT_RUNTIME; omit that optional
        # instrumentation so successful and failed CLI exits retain their codes.
        text = replace_once(text, "    -sABORT_ON_WASM_EXCEPTIONS\n", "")
        path.write_text(text)
        # Upstream's web build searches for resources in the current directory.
        # A CLI must preserve cwd for user imports and find its own resources
        # relative to argv[0], just like the native executable.
        path = source / "src/openscad.cc"
        path.write_text(replace_once(path.read_text(),
            "const auto applicationPath = boost::dll::fs::current_path();",
            "const auto applicationPath = fs::path(argv[0]).parent_path().generic_string();"))
    else:
        patch_legacy_source(source)
        shutil.copy2(HERE / "wasm-dependencies.cmake", source / "ci-dependencies.cmake")
        text = path.read_text()
        # CGAL 5.5 requires C++14; Boost 1.87's filesystem supports the new API.
        text += """
target_compile_definitions(OpenSCAD PRIVATE CGAL_DISABLE_ROUNDING_MATH_CHECK)
target_compile_options(OpenSCAD PRIVATE -fexceptions)
target_link_options(OpenSCAD PRIVATE
  -fexceptions -sALLOW_MEMORY_GROWTH=1 -sMAXIMUM_MEMORY=4GB
  -sSTACK_SIZE=8MB -sDISABLE_EXCEPTION_CATCHING=0 -sEXIT_RUNTIME=1
  -sENVIRONMENT=node -sEXPORT_ES6=1 -sEXPORT_NAME=OpenSCAD -sMODULARIZE=1
  -sEXPORTED_RUNTIME_METHODS=['ENV','FS','NODEFS','callMain'] -lnodefs.js)
"""
        path.write_text(text)
        path = source / "src/FileModule.cc"
        path.write_text(replace_once(path.read_text(), "fs::is_regular(path)", "fs::is_regular_file(path)"))
        path = source / "src/Geometry.h"
        path.write_text(replace_once(path.read_text(), "#pragma once", "#pragma once\n#include <cassert>"))
        path = source / "src/cgal.h"
        # CGAL 5.5 uses boost::prior, which newer Boost no longer includes
        # transitively. Include its owning header before the CGAL headers.
        path.write_text(replace_once(path.read_text(), "#pragma once", "#pragma once\n#include <boost/next_prior.hpp>"))
        # The 2021 NULLGL implementation predates shader-aware renderer
        # signatures. Keep its existing no-GL implementation consistent with
        # the declarations; this does not enable or replace PNG rendering.
        path = source / "src/GLView.h"
        text = replace_once(path.read_text(), "#ifdef ENABLE_OPENCSG\n\t/// Shader attribute identifiers",
                            "\t/// Shader attribute identifiers")
        path.write_text(replace_once(text, "\tshaderinfo_t shaderinfo;",
                                    "#ifdef ENABLE_OPENCSG\n\tshaderinfo_t shaderinfo;"))
        path = source / "src/NULLGL.cc"
        text = replace_once(path.read_text(), "void ThrownTogetherRenderer::draw(bool showfaces, bool showedges) const {};",
                            "void ThrownTogetherRenderer::draw(bool showfaces, bool showedges) const {};\n"
                            "void ThrownTogetherRenderer::draw_with_shader(const GLView::shaderinfo_t *, bool) const {}")
        text = replace_once(text, "renderCSGProducts(const CSGProducts &products, bool",
                            "renderCSGProducts(const CSGProducts &products, const GLView::shaderinfo_t *, bool")
        path.write_text(replace_once(text, "renderChainObject(const class CSGChainObject &csgobj, bool",
                                    "renderChainObject(const class CSGChainObject &csgobj, const GLView::shaderinfo_t *, bool"))


def stage_wasm(source, build, work):
    package = work / "openscad-cli-wasm"
    binary = package / "bin"
    binary.mkdir(parents=True)
    shutil.copy2(build / "openscad.js", binary / "openscad-core.mjs")
    shutil.copy2(build / "openscad.wasm", binary / "openscad.wasm")
    if (binary / "openscad.wasm").read_bytes()[:8] != b"\0asm\x01\0\0\0":
        raise RuntimeError("Expected a wasm32 module")
    shutil.copy2(HERE / "wasm-cli.mjs", binary / "openscad.mjs")
    resources = package / "share/openscad"
    resources.mkdir(parents=True)
    for directory in ("libraries", "fonts", "color-schemes", "examples"):
        shutil.copytree(source / directory, resources / directory)
    # Use CPython's pinned catalog compiler so packaging has no host gettext
    # dependency (the native Windows path uses the same verified compiler).
    compiler = work / "msgfmt.py"
    urllib.request.urlretrieve(
        "https://raw.githubusercontent.com/python/cpython/v3.11.14/Tools/i18n/msgfmt.py", compiler)
    if hashlib.sha256(compiler.read_bytes()).hexdigest() != "79c83d2646e7cf742226b52c989431cea1b08749adbb599fba001bb0203efe1f":
        raise RuntimeError("CPython msgfmt checksum mismatch")
    for line in (source / "locale/LINGUAS").read_text().splitlines():
        for language in line.split("#", 1)[0].split():
            messages = resources / "locale" / language / "LC_MESSAGES"
            messages.mkdir(parents=True)
            run(sys.executable, compiler, "-o", messages / "openscad.mo", source / "locale" / (language + ".po"))
    (resources / "fonts/fonts.conf").write_text(
        '<?xml version="1.0"?><fontconfig><dir prefix="relative">.</dir></fontconfig>\n')
    shutil.copy2(source / "COPYING", package / "COPYING")
    shutil.copy2(HERE / "README.md", package / "README.md")
    licenses = package / "licenses"
    licenses.mkdir()
    if MODERN_SOURCE:
        for module in (source / "submodules").iterdir():
            for license_file in module.glob("*"):
                if license_file.is_file() and license_file.name.lower().startswith(("license", "copying")):
                    shutil.copy2(license_file, licenses / (module.name + "-" + license_file.name))
    # Record the exact dependency image and its upstream build recipe as well
    # as source and builder revisions, so the binary's origin is auditable.
    (licenses / "wasm-dependencies.txt").write_text(
        f"Dependency image: {WASM_IMAGE}\n"
        "Build recipe: https://github.com/openscad/openscad-wasm/tree/eed4ab616d094cc25ab8b8064204e08c072eba0b\n"
        f"Legacy CGAL: https://github.com/CGAL/cgal/tree/{CGAL_LEGACY_COMMIT}\n")
    (package / "build-info.json").write_text(json.dumps({
        "tag": SOURCE_TAG, "commit": SOURCE_COMMIT, "target": "wasm",
        "headless": True, "nullgl": True, **WASM_CAPABILITIES,
        "manifold": MODERN_SOURCE, "asan": False, "runtime": "node",
        "node_min_version": 22, "wasm_image": WASM_IMAGE,
        "builder_commit": os.environ.get("GITHUB_SHA"),
    }, indent=2) + "\n")
    return package


def smoke_wasm(package, work):
    """Run the unpacked archive with no build tree or installed OpenSCAD."""
    node = shutil.which("node")
    if not node:
        raise RuntimeError("WASM acceptance requires Node.js 22+")
    info = json.loads((package / "build-info.json").read_text())
    if info["commit"] != SOURCE_COMMIT or info["tag"] != SOURCE_TAG:
        raise RuntimeError("Wrong WASM source revision")
    test = work / "smoke"
    test.mkdir(parents=True)
    (test / "shape.scad").write_text('module shape() { cube([12,10,8]); }\n')
    model = test / "unicode-模型.scad"
    model.write_text('use <MCAD/boxes.scad>\nuse <shape.scad>\n'
                     'difference() { roundedBox([12,10,8],1,true); cylinder(h=20,r=2,$fn=24); }\n'
                     'translate([15,0,0]) shape();\n'
                     'translate([0,0,8]) linear_extrude(1) text("CLI",font="Liberation Sans",size=3);\n')
    env = os.environ.copy()
    for key in ("OPENSCADPATH", "OPENSCAD_FONT_PATH", "FONTCONFIG_FILE", "FONTCONFIG_PATH",
                "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "NODE_PATH", "NODE_OPTIONS"):
        env.pop(key, None)
    command = [node, str(package / "bin/openscad.mjs")]
    with (test / "smoke.log").open("w") as log:
        def execute(*args):
            try:
                result = run(*command, *args, cwd=test, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, timeout=120)
            except subprocess.CalledProcessError as error:
                log.write(error.stdout or "")
                log.flush()
                print((error.stdout or "")[-12000:], flush=True)
                raise
            log.write(result.stdout)
            log.flush()
            return result.stdout
        if SOURCE_VERSION not in execute("--version"):
            raise RuntimeError("WASM executable reports the wrong version")
        backends = ("CGAL", "Manifold") if MODERN_SOURCE else ("CGAL",)
        for backend in backends:
            destination = test / (backend + ".stl")
            flags = [f"--backend={backend}"] if MODERN_SOURCE else []
            execute(*flags, "--export-format", "asciistl", "-o", destination, model)
            data = destination.read_bytes()
            # Validate real STL triangles, rather than accepting any nonempty file.
            if not data.startswith(b"solid") or not re.search(rb"facet normal\s+[-0-9.e+]+", data):
                raise RuntimeError(f"Invalid ASCII STL from {backend}")
        # The combined model would still contain triangles if text() silently
        # lost its fonts. Require a font-only extrusion to produce geometry.
        text_model = test / "text.scad"
        text_model.write_text('linear_extrude(1) text("CLI",font="Liberation Sans",size=3);\n')
        text_stl = test / "text.stl"
        execute("--export-format", "asciistl", "-o", text_stl, text_model)
        if not re.search(rb"facet normal\s+[-0-9.e+]+", text_stl.read_bytes()):
            raise RuntimeError("WASM text() did not produce geometry with bundled fonts")
        # A failed model must propagate a nonzero exit to the caller.
        invalid = test / "invalid.scad"
        invalid.write_text("cube( ;\n")
        failure = subprocess.run([*command, "-o", str(test / "invalid.stl"), str(invalid)],
                                 env=env, cwd=test, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if failure.returncode == 0:
            raise RuntimeError("WASM CLI masked a model error")
        for format in ("png", "pdf", "3mf"):
            destination = test / ("unsupported." + format)
            failure = subprocess.run([*command, "-o", str(destination), str(model)],
                                     env=env, cwd=test, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            if failure.returncode != 2 or "does not support" not in failure.stdout or destination.exists():
                raise RuntimeError(f"WASM silently accepted unavailable {format} output")
    content = (test / "smoke.log").read_text()
    if re.search(r"ERROR:|WARNING: Can't open|WARNING: Ignoring unknown module|Fontconfig error|Can't initialize fontconfig|Could not initialize localization", content):
        raise RuntimeError("WASM smoke test reported a resource or geometry error")
    print(content, flush=True)


def main():
    work = ROOT / ".build/wasm"
    work.mkdir(parents=True)
    source = work / "source"
    repo = ROOT / "sources/openscad"
    if output("git", "-C", repo, "rev-parse", "HEAD") != SOURCE_COMMIT:
        raise RuntimeError("Unexpected WASM source revision")
    archive_source(repo, source)
    for line in output("git", "-C", repo, "submodule", "status", "--recursive").splitlines():
        if line[0] in "-+U":
            raise RuntimeError(f"Unpinned submodule: {line}")
        path = line.split()[1]
        archive_source(repo / path, source / path)
    patch_wasm_source(source)
    cache = ROOT / ".build/wasm-ccache"
    cache.mkdir(exist_ok=True)
    # Source and build directories are mounted separately from the packaged
    # runtime. No native vcpkg libraries can leak into this cross build.
    run("docker", "run", "--rm", "--platform=linux/amd64",
        "-v", f"{ROOT}:/workspace", "-v", f"{cache}:/ccache",
        "-e", "CCACHE_DIR=/ccache", "-e", "CCACHE_BASEDIR=/workspace",
        "-e", f"OPENSCAD_TAG={SOURCE_TAG}", "-w", "/workspace", WASM_IMAGE,
        "bash", "scripts/openscad-ci/wasm-build.sh")
    package = stage_wasm(source, work / "build", work)
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    archive = Path(shutil.make_archive(str(dist / package.name), "gztar", package.parent, package.name))
    relocated = work / "relocated package"
    shutil.unpack_archive(archive, relocated)
    # Hide all source/compiler output before exercising the actual archive.
    source.rename(work / "source-hidden")
    (work / "build").rename(work / "build-hidden")
    try:
        smoke_wasm(relocated / package.name, work)
    finally:
        (work / "source-hidden").rename(source)
        (work / "build-hidden").rename(work / "build")
    (dist / (archive.name + ".sha256")).write_text(
        hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n")


if __name__ == "__main__":
    if sys.argv[1:] == ["--smoke"]:
        smoke_wasm(Path(os.environ["WASM_PACKAGE"]).resolve(), Path(os.environ["WASM_WORK"]).resolve())
    else:
        main()
