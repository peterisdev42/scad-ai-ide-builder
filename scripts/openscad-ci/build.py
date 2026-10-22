#!/usr/bin/env python3
"""Build the fixed OpenSCAD tag and test the actual relocatable archive."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
# The annotated tag object has a different SHA; pin its peeled commit.
SOURCE_COMMIT = "41f58fe57c03457a3a8b4dc541ef5654ec3e8c78"
SOURCE_TAG = "openscad-2021.01"
VCPKG_COMMIT = "6f7ffeb18f99796233b958aaaf14ec7bd4fb64b2"
MESA_VERSION = "24.3.4"
MESA_SHA256 = "e641ae27191d387599219694560d221b7feaa91c900bcec46bf444218ed66025"
TARGETS = ("macos-x64", "macos-arm64", "windows-x64", "windows-x86", "windows-arm64")


def run(*args, **kwargs):
    print("+", *map(str, args), flush=True)
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def output(*args, **kwargs):
    return subprocess.check_output(list(map(str, args)), text=True, **kwargs).strip()


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise RuntimeError(f"Unexpected upstream source; expected exactly one: {old!r}")
    return text.replace(old, new, 1)


def patch_source(source):
    """Build-only compatibility patch; leave the checked-out submodule untouched."""
    path = source / "CMakeLists.txt"
    text = path.read_text()
    start = text.index("set(BOOST_DIRECTLY_REQUIRED_LIBRARIES")
    end = text.index("# Output compilation database", start)
    text = text[:start] + 'include("${CMAKE_SOURCE_DIR}/ci-dependencies.cmake")\n\n' + text[end:]
    text = replace_once(text, 'string(TIMESTAMP VERSION "%Y.%m.%d")', 'set(VERSION "2021.01")')
    text = replace_once(text, "set(CMAKE_OSX_DEPLOYMENT_TARGET 10.9)",
                        'set(CMAKE_OSX_DEPLOYMENT_TARGET "11.0" CACHE STRING "Minimum macOS")')
    text = replace_once(text, 'LINK_FLAGS "-subsystem:windows -ENTRY:mainCRTStartup -stack:8388608"',
                        'LINK_FLAGS "/SUBSYSTEM:CONSOLE /STACK:8388608"')
    # tests/ adds GUI test programs and a second project. We run CLI checks below.
    text = replace_once(text, "add_subdirectory(tests)", "# CLI package tests run after relocation.")
    path.write_text(text)
    shutil.copy2(HERE / "dependencies.cmake", source / "ci-dependencies.cmake")
    path = source / "src/openscad.cc"
    text = path.read_text()
    if text.count("boost::join(") != 2:
        raise RuntimeError("Unexpected OpenSCAD boost::join call sites")
    path.write_text(text.replace("boost::join(", "boost::algorithm::join("))
    path = source / "src/cgalutils-polyhedron.cc"
    text = replace_once(path.read_text(), '#include "cgalutils.h"',
                        '#include "cgalutils.h"\n#include <map>')
    text = replace_once(text, "    generic_print_polyhedron(sstream, p, writer);", """    writer.write_header(sstream, p.size_of_vertices(), p.size_of_halfedges(), p.size_of_facets());
    std::map<typename Polyhedron::Vertex_const_handle, std::size_t> indices;
    std::size_t index = 0;
    for (auto vertex = p.vertices_begin(); vertex != p.vertices_end(); ++vertex) {
      indices[vertex] = index++;
      const auto &point = vertex->point();
      writer.write_vertex(CGAL::to_double(point.x()), CGAL::to_double(point.y()), CGAL::to_double(point.z()));
    }
    writer.write_facet_header();
    for (auto facet = p.facets_begin(); facet != p.facets_end(); ++facet) {
      auto edge = facet->facet_begin();
      const auto first = edge;
      writer.write_facet_begin(0);
      do {
        writer.write_facet_vertex_index(indices.at(edge->vertex()));
        ++edge;
      } while (edge != first);
      writer.write_facet_end();
    }
    writer.write_footer();""")
    path.write_text(text)
    path = source / "src/cgalutils-tess.cc"
    text = replace_once(path.read_text(), """#if CGAL_VERSION_NR >= CGAL_VERSION_NUMBER(4,11,0)
  #include <CGAL/Triangulation_2_projection_traits_3.h>
#else
  #include <CGAL/Triangulation_2_filtered_projection_traits_3.h>
#endif""", "#include <CGAL/Projection_traits_3.h>")
    text = replace_once(text, "CGAL::Triangulation_2_filtered_projection_traits_3<K>",
                        "CGAL::Projection_traits_3<K>")
    path.write_text(text)


def archive_source(repo, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile() as stream:
        run("git", "-C", repo, "archive", "HEAD", stdout=stream)
        stream.seek(0)
        with tarfile.open(fileobj=stream) as archive:
            archive.extractall(destination)


def install_dependencies(target, triplet):
    vcpkg = ROOT / ".build/vcpkg"
    if output("git", "-C", vcpkg, "rev-parse", "HEAD") != VCPKG_COMMIT:
        raise RuntimeError("Unexpected vcpkg revision")
    # Modern Apple SDKs expose TARGET_OS_MAC to old dependencies. Exclude
    # macOS from the Classic Mac OS branches (fdopen macro / removed fp.h).
    for port, patch_name in (("zlib", "zlib-modern-macos.patch"),
                             ("libpng", "libpng-modern-macos.patch"),
                             ("pixman", "pixman-modern-clang.patch"),
                             ("harfbuzz", "harfbuzz-modern-clang.patch")):
        portfile = vcpkg / f"ports/{port}/portfile.cmake"
        patch = (HERE / "patches" / patch_name).as_posix()
        portfile.write_text(replace_once(portfile.read_text(), "    PATCHES\n",
                                        f'    PATCHES\n        "{patch}"\n'))
    portfile = vcpkg / "ports/cgal/portfile.cmake"
    patch = (HERE / "patches/cgal-iterator-bool.patch").as_posix()
    portfile.write_text(replace_once(portfile.read_text(), "    HEAD_REF master\n",
                                    f'    HEAD_REF master\n    PATCHES "{patch}"\n'))
    # The historical gmplib download host times out on GitHub runners.
    # GNU distributes the exact same archive; keep the port's SHA512 check.
    portfile = vcpkg / "ports/gmp/portfile.cmake"
    portfile.write_text(replace_once(portfile.read_text(),
        "    URLS https://gmplib.org/download/gmp/gmp-6.2.1.tar.xz",
        "    URLS https://ftp.gnu.org/gnu/gmp/gmp-6.2.1.tar.xz\n"
        "         https://gmplib.org/download/gmp/gmp-6.2.1.tar.xz"))
    triplet_dir = HERE / "triplets"
    if not (triplet_dir / f"{triplet}.cmake").is_file():
        raise RuntimeError(f"Missing pinned triplet: {triplet}")
    if target.startswith("windows"):
        # The pinned vcpkg predates native Windows ARM64 host tooling. Its x64
        # host tools run under Windows emulation while libraries target ARM64.
        tools = vcpkg / "scripts/vcpkgTools.xml"
        tools.write_text(replace_once(tools.read_text(),
            "https://www.7-zip.org/a/7z2107-extra.7z",
            "https://github.com/ip7z/7zip/releases/download/21.07/7z2107-extra.7z"))
        os.environ["VCPKG_DEFAULT_HOST_TRIPLET"] = "x64-windows"
        run("cmd", "/c", vcpkg / "bootstrap-vcpkg.bat", "-disableMetrics")
        executable = vcpkg / "vcpkg.exe"
    else:
        os.environ["VCPKG_FORCE_SYSTEM_BINARIES"] = "1"
        run("bash", vcpkg / "bootstrap-vcpkg.sh", "-disableMetrics")
        executable = vcpkg / "vcpkg"
    installed = ROOT / ".build/installed"
    prefix = installed / triplet
    os.environ["PKG_CONFIG_PATH"] = ""
    os.environ["PKG_CONFIG_LIBDIR"] = os.pathsep.join(
        str(prefix / item) for item in ("lib/pkgconfig", "share/pkgconfig"))
    try:
        run(executable, "install", f"--triplet={triplet}",
            f"--overlay-triplets={triplet_dir}", f"--x-manifest-root={HERE}",
            f"--x-install-root={installed}", "--clean-after-build")
    except subprocess.CalledProcessError:
        # vcpkg normally only prints the log paths. Include their contents in
        # the job log as well, so failures are diagnosable through the API.
        logs = sorted((vcpkg / "buildtrees").glob("**/*.log"), key=lambda p: p.stat().st_mtime)
        for log in logs[-6:]:
            print(f"\n--- {log.relative_to(vcpkg)} ---\n", flush=True)
            lines = log.read_text(errors="replace").splitlines()
            selected = set(range(max(0, len(lines) - 40), len(lines)))
            for index, line in enumerate(lines):
                if re.search(r"error:|FAILED:|Undefined symbols", line):
                    selected.update(range(max(0, index - 2), min(len(lines), index + 10)))
            print("\n".join(lines[index] for index in sorted(selected)), flush=True)
        raise
    prefix = installed / triplet
    os.environ["PKG_CONFIG_PATH"] = os.pathsep.join(
        str(prefix / item) for item in ("lib/pkgconfig", "share/pkgconfig"))
    os.environ["PKG_CONFIG_LIBDIR"] = os.environ["PKG_CONFIG_PATH"]
    os.environ["PATH"] = str(prefix / "bin") + os.pathsep + os.environ["PATH"]
    return vcpkg, prefix


def build_mesa(work, prefix):
    archive = work / f"mesa-{MESA_VERSION}.tar.xz"
    urllib.request.urlretrieve(f"https://archive.mesa3d.org/mesa-{MESA_VERSION}.tar.xz", archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != MESA_SHA256:
        raise RuntimeError("Mesa source checksum mismatch")
    with tarfile.open(archive) as stream:
        stream.extractall(work)
    mesa_build = work / "mesa-build"
    run("meson", "setup", mesa_build, work / f"mesa-{MESA_VERSION}",
        "--buildtype=release", "--backend=ninja", "-Db_vscrt=md",
        "-Dgallium-drivers=softpipe", "-Dvulkan-drivers=[]", "-Dllvm=disabled",
        "-Dplatforms=windows", "-Dglx=disabled", "-Degl=disabled",
        "-Dgles1=disabled", "-Dgles2=disabled", "-Dshared-glapi=enabled",
        "-Dgallium-va=disabled", "-Dgallium-vdpau=disabled", "-Dshader-cache=disabled",
        "-Dbuild-tests=false", f"--cmake-prefix-path={prefix}")
    run("meson", "compile", "-C", mesa_build, "-j", "2")
    if not list(mesa_build.rglob("opengl32.dll")):
        raise RuntimeError("Mesa did not produce its WGL OpenGL library")
    return mesa_build


def check_architecture(executable, target):
    data = executable.read_bytes()
    arch = target.split("-", 1)[1]
    if target.startswith("windows"):
        offset = struct.unpack_from("<I", data, 0x3C)[0]
        if data[offset:offset + 4] != b"PE\0\0":
            raise RuntimeError("Not a PE executable")
        machine = struct.unpack_from("<H", data, offset + 4)[0]
        expected = {"x86": 0x14C, "x64": 0x8664, "arm64": 0xAA64}[arch]
    elif target.startswith("linux"):
        if data[:4] != b"\x7fELF" or data[5] != 1:
            raise RuntimeError("Not a little endian ELF executable")
        machine = struct.unpack_from("<H", data, 18)[0]
        expected = {"x64": 62, "arm64": 183, "armv7": 40}[arch]
        if arch == "armv7" and not struct.unpack_from("<I", data, 36)[0] & 0x400:
            raise RuntimeError("ARMv7 executable must use the hard-float ABI")
    else:
        if data[:4] != b"\xcf\xfa\xed\xfe":
            raise RuntimeError("Expected a thin 64-bit Mach-O executable")
        machine = struct.unpack_from("<I", data, 4)[0]
        expected = {"x64": 0x1000007, "arm64": 0x100000C}[arch]
    if machine != expected:
        raise RuntimeError(f"Wrong architecture: {machine:#x}, expected {expected:#x}")


def bundle_linux(binary, package):
    lib = package / "lib"
    lib.mkdir()
    # Keep the GL/GLX driver stack and glibc on the host. Bundle other linked
    # libraries including libstdc++ so Ubuntu 22.04 is a clear ABI floor.
    system = re.compile(r"^(ld-linux|lib(c|m|pthread|dl|rt|resolv|util)\.so|lib(GL|GLX|GLdispatch|EGL|glapi)\.)")
    pending, seen = [binary], set()
    while pending:
        current = pending.pop()
        linked = output("ldd", current)
        if "not found" in linked:
            raise RuntimeError(f"Unresolved dependency: {linked}")
        for name, location in re.findall(r"^\s*(\S+) => (/\S+)", linked, re.MULTILINE):
            if name in seen or system.match(name):
                continue
            seen.add(name)
            destination = lib / name
            shutil.copy2(location, destination)
            pending.append(destination)
    for dependency in lib.iterdir():
        run("patchelf", "--set-rpath", "$ORIGIN", dependency)
    run("patchelf", "--set-rpath", "$ORIGIN/../lib", binary)


def copy_licenses(package, prefix):
    licenses = package / "licenses"
    licenses.mkdir()
    if prefix:
        for copyright_file in (prefix / "share").glob("*/copyright"):
            shutil.copy2(copyright_file, licenses / f"{copyright_file.parent.name}.txt")
    else:
        for copyright_file in Path("/usr/share/doc").glob("*/copyright"):
            if copyright_file.is_file():
                shutil.copy2(copyright_file, licenses / f"{copyright_file.parent.name}.txt")
        (licenses / "system-packages.txt").write_text(output(
            "dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n"))


def stage(source, build, work, target, prefix, mesa):
    package = work / f"openscad-cli-2021.01-{target}"
    package.mkdir()
    bin_dir = package / "bin"
    bin_dir.mkdir()
    if target.startswith("macos"):
        app = package / "OpenSCAD.app"
        shutil.copytree(build / "OpenSCAD.app", app)
        binary = app / "Contents/MacOS/OpenSCAD"
        resources = app / "Contents/Resources"
        resources.mkdir(exist_ok=True)
        fixup = work / "fixup.cmake"
        fixup.write_text('include(BundleUtilities)\nfixup_bundle("'
                         + app.as_posix() + '" "" "' + (prefix / "lib").as_posix() + '")\n')
        run("cmake", "-P", fixup)
        wrapper = bin_dir / "openscad"
        wrapper.write_text('#!/bin/sh\nset -eu\nbase=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\n'
                           'exec "$base/OpenSCAD.app/Contents/MacOS/OpenSCAD" "$@"\n')
        wrapper.chmod(0o755)
    elif target.startswith("windows"):
        binary = bin_dir / "openscad.exe"
        shutil.copy2(build / "OpenSCAD.exe", binary)
        for dll in (prefix / "bin").glob("*.dll"):
            shutil.copy2(dll, bin_dir / dll.name)
        for dll in mesa.rglob("*.dll"):
            shutil.copy2(dll, bin_dir / dll.name)
        redist = Path(os.environ["VCToolsRedistDir"]) / target.split("-")[1]
        crt_dirs = list(redist.glob("Microsoft.VC*.CRT"))
        if not crt_dirs:
            raise RuntimeError(f"MSVC CRT redistributables missing: {redist}")
        for dll in crt_dirs[-1].glob("*.dll"):
            shutil.copy2(dll, bin_dir / dll.name)
        resources = bin_dir
        shutil.copy2(work / f"mesa-{MESA_VERSION}/docs/license.rst", package / "MESA-LICENSE.rst")
    else:
        binary = bin_dir / "openscad-bin"
        shutil.copy2(build / "openscad", binary)
        bundle_linux(binary, package)
        wrapper = bin_dir / "openscad"
        wrapper.write_text('#!/bin/sh\nset -eu\nbase=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
                           'if [ -z "${DISPLAY:-}" ]; then\n'
                           '  exec xvfb-run -a -s "-screen 0 1280x1024x24 +extension GLX" "$base/openscad-bin" "$@"\n'
                           'fi\nexec "$base/openscad-bin" "$@"\n')
        wrapper.chmod(0o755)
        resources = package / "share/openscad"
        resources.mkdir(parents=True)
    check_architecture(binary, target)
    for directory in ("color-schemes", "libraries", "fonts", "examples"):
        shutil.copytree(source / directory, resources / directory, dirs_exist_ok=True)
    # localization_init() expects the resource directory, and gettext needs
    # compiled catalogs rather than the source .po files.
    for line in (source / "locale/LINGUAS").read_text().splitlines():
        for language in line.split("#", 1)[0].split():
            messages = resources / "locale" / language / "LC_MESSAGES"
            messages.mkdir(parents=True, exist_ok=True)
            if target.startswith("windows"):
                from babel.messages.pofile import read_po
                from babel.messages.mofile import write_mo
                with (source / "locale" / f"{language}.po").open("rb") as stream:
                    catalog = read_po(stream, locale=language)
                errors = list(catalog.check())
                if errors:
                    raise RuntimeError(f"Invalid translation catalog: {language}: {errors}")
                with (messages / "openscad.mo").open("wb") as stream:
                    write_mo(stream, catalog)
            else:
                run("msgfmt", "--check-format", "-o", messages / "openscad.mo",
                    source / "locale" / f"{language}.po")
    (resources / "fonts/fonts.conf").write_text(
        '<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">\n'
        '<fontconfig><dir prefix="relative">.</dir>'
        '<cachedir prefix="xdg">fontconfig</cachedir></fontconfig>\n')
    shutil.copy2(source / "COPYING", package / "COPYING")
    shutil.copy2(HERE / "README.md", package / "README.md")
    copy_licenses(package, prefix)
    (package / "build-info.json").write_text(json.dumps({
        "tag": SOURCE_TAG, "commit": SOURCE_COMMIT, "target": target,
        "headless": True, "opengl": True, "nullgl": False,
        "vcpkg_commit": VCPKG_COMMIT if prefix else None,
        "mesa": MESA_VERSION if mesa else None,
        "lib3mf": target.startswith("linux"),
        "builder_commit": os.environ.get("GITHUB_SHA"),
    }, indent=2) + "\n")
    if target.startswith("macos"):
        # Re-sign after install_name_tool changes (required on Apple Silicon).
        run("codesign", "--force", "--deep", "--sign", "-", package / "OpenSCAD.app")
    return package


def smoke_test(package, target, work):
    binary = package / "bin" / ("openscad.exe" if target.startswith("windows") else "openscad")
    env = os.environ.copy()
    for name in ("OPENSCADPATH", "OPENSCAD_FONT_PATH", "FONTCONFIG_PATH", "FONTCONFIG_FILE",
                 "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH"):
        env.pop(name, None)
    env["PATH"] = os.pathsep.join(p for p in env["PATH"].split(os.pathsep)
                                   if ".build" not in p and "sources/openscad" not in p)
    env["LIBGL_ALWAYS_SOFTWARE"] = "1"
    if target.startswith("windows"):
        env["GALLIUM_DRIVER"] = "softpipe"
    test = work / "smoke"
    test.mkdir()
    model = test / "smoke.scad"
    model.write_text('use <MCAD/boxes.scad>\n'
                     'difference() { roundedBox([20,20,10],2,true); cylinder(h=30,r=4,center=true,$fn=32); }\n'
                     'translate([0,0,5]) linear_extrude(1) text("CLI",size=4,font="Liberation Sans");\n')
    log = test / "smoke.log"
    with log.open("w") as stream:
        result = run(binary, "--version", env=env, cwd=test, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        stream.write(result.stdout)
        stream.flush()
        if "2021.01" not in result.stdout:
            raise RuntimeError("Packaged executable reports the wrong version")
        for name, flags in (("model.stl", []), ("preview.png", ["--preview", "--imgsize=256,256"]),
                            ("render.png", ["--render=cgal", "--imgsize=256,256"])):
            run(binary, *flags, "-o", test / name, model, env=env, cwd=test, stdout=stream, stderr=subprocess.STDOUT)
            artifact = test / name
            if not artifact.is_file() or artifact.stat().st_size < 1024:
                raise RuntimeError(f"Invalid or empty smoke-test output: {artifact}")
            if name.endswith(".png"):
                data = artifact.read_bytes()
                if data[:8] != b"\x89PNG\r\n\x1a\n" or struct.unpack_from(">II", data, 16) != (256, 256):
                    raise RuntimeError("Invalid PNG output")
        run(binary, "--info", env=env, cwd=test, stdout=stream, stderr=subprocess.STDOUT)
    if re.search(r"ERROR:|WARNING: Can't open|WARNING: Ignoring unknown module|Could not initialize localization", log.read_text()):
        raise RuntimeError("Smoke test reported a missing resource or rendering error")


def main():
    target = os.environ["TARGET"]
    if target not in TARGETS:
        raise RuntimeError(f"Unsupported target: {target}")
    repo = ROOT / "sources/openscad"
    if output("git", "-C", repo, "rev-parse", "HEAD") != SOURCE_COMMIT:
        raise RuntimeError("Only openscad-2021.01 at its pinned commit is supported")
    work = ROOT / ".build" / target
    work.mkdir(parents=True)
    source = work / "source"
    archive_source(repo, source)
    archive_source(repo / "libraries/MCAD", source / "libraries/MCAD")
    patch_source(source)
    prefix = mesa = None
    options = []
    if not target.startswith("linux"):
        triplet = os.environ["TRIPLET"]
        vcpkg, prefix = install_dependencies(target, triplet)
        options += [f"-DCMAKE_TOOLCHAIN_FILE={vcpkg}/scripts/buildsystems/vcpkg.cmake",
                    f"-DVCPKG_INSTALLED_DIR={prefix.parent}", f"-DVCPKG_TARGET_TRIPLET={triplet}",
                    f"-DVCPKG_OVERLAY_TRIPLETS={HERE}/triplets", "-DVCPKG_MANIFEST_MODE=OFF"]
    if target.startswith("macos"):
        options += [f"-DCMAKE_OSX_ARCHITECTURES={'arm64' if target.endswith('arm64') else 'x86_64'}"]
        # Boost 1.80 MPL uses enum constants accepted by its original compiler.
        options += ["-DCMAKE_CXX_FLAGS=-Wno-enum-constexpr-conversion"]
    if target.startswith("windows"):
        options += [f"-DFLEX_EXECUTABLE={shutil.which('win_flex')}",
                    f"-DBISON_EXECUTABLE={shutil.which('win_bison')}"]
        mesa = build_mesa(work, prefix)
    build = work / "build"
    run("cmake", "-S", source, "-B", build, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
        "-DHEADLESS=ON", "-DNULLGL=OFF", "-DINFO=OFF", *options)
    # Report all translation-unit failures together instead of stopping at the first.
    run("cmake", "--build", build, "--target", "OpenSCAD", "--parallel", "2", "--", "-k", "0")
    package = stage(source, build, work, target, prefix, mesa)
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    archive = Path(shutil.make_archive(str(dist / package.name),
                   "zip" if target.startswith("windows") else "gztar", package.parent, package.name))
    # Test a fresh extraction from a path containing spaces, with the original
    # install tree hidden so absolute dependency paths cannot accidentally pass.
    relocated = work / "relocated package"
    relocated.mkdir()
    shutil.unpack_archive(archive, relocated)
    hidden = []
    for original in (build, ROOT / ".build/installed"):
        if original.exists():
            renamed = original.with_name(original.name + "-hidden")
            original.rename(renamed)
            hidden.append((original, renamed))
    try:
        smoke_test(relocated / package.name, target, work)
    finally:
        for original, renamed in reversed(hidden):
            renamed.rename(original)
        log = work / "smoke/smoke.log"
        if log.exists():
            print(log.read_text(errors="replace"), flush=True)
    (dist / (archive.name + ".sha256")).write_text(
        hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n")


if __name__ == "__main__":
    main()
