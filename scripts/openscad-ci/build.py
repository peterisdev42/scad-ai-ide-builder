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
import sys
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
# The annotated tag object has a different SHA; pin its peeled commit.
SOURCE_REVISIONS = {
    "openscad-2021.01": "41f58fe57c03457a3a8b4dc541ef5654ec3e8c78",
    "openscad-2026.10-TEST4": "5a864f17b6b022904004704f64e6168d2cfb8435",
}
SOURCE_TAG = os.environ.get("OPENSCAD_TAG", "openscad-2026.10-TEST4")
SOURCE_COMMIT = SOURCE_REVISIONS[SOURCE_TAG]
SOURCE_VERSION = SOURCE_TAG.removeprefix("openscad-")
MODERN_SOURCE = SOURCE_TAG != "openscad-2021.01"
VCPKG_COMMIT = "6f7ffeb18f99796233b958aaaf14ec7bd4fb64b2"
MESA_VERSION = "24.3.4"
MESA_SHA256 = "e641ae27191d387599219694560d221b7feaa91c900bcec46bf444218ed66025"
NATIVE_TARGETS = ("macos-x64", "macos-arm64", "windows-x64", "linux-x64", "linux-arm64")
TARGETS = (*NATIVE_TARGETS, "wasm")
LINUX_GLIBC_BASELINE = "2.35"
LINUX_SYSTEM_LIBRARY = re.compile(
    r"^(ld-linux|lib(c|m|pthread|dl|rt|resolv|util)\.so|lib(GL|GLX|GLdispatch|EGL|glapi)\.)")
LINUX_LDD_ENTRY = re.compile(r"^\s*(\S+) => (/.+?)\s+\(0x[0-9a-fA-F]+\)\s*$", re.MULTILINE)


def package_name(target):
    return "openscad-cli-" + target.replace("-x64", "-x86_64")


def run(*args, **kwargs):
    print("+", *map(str, args), flush=True)
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def output(*args, **kwargs):
    return subprocess.check_output(list(map(str, args)), text=True, **kwargs).strip()


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise RuntimeError(f"Unexpected upstream source; expected exactly one: {old!r}")
    return text.replace(old, new, 1)


def patch_legacy_source(source):
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


def patch_modern_source(source):
    """Adapt the upstream headless target without the 2021.01 API patches."""
    path = source / "CMakeLists.txt"
    text = path.read_text()
    text = replace_once(text, 'set(Boost_USE_STATIC_LIBS TRUE)', 'set(Boost_USE_STATIC_LIBS FALSE)')
    text = replace_once(text, 'GLEW::glew)', 'GLEW::GLEW)')
    if text.count('pkg_check_modules(CAIRO REQUIRED cairo)') != 2:
        raise RuntimeError("Unexpected upstream Cairo discovery branches")
    text = text.replace('pkg_check_modules(CAIRO REQUIRED cairo)',
                        'pkg_check_modules(CAIRO REQUIRED IMPORTED_TARGET cairo)\n'
                        '    set(CAIRO_LIBRARIES PkgConfig::CAIRO)')
    # Boost 1.80 uses CMake's FindBoost module, not the newer BoostConfig package.
    text = replace_once(text, 'cmake_policy(SET CMP0167 NEW)', 'cmake_policy(SET CMP0167 OLD)')
    # Select Boost 1.80's own typedef-preserving compatibility implementation.
    # libc++ no longer provides std::unary_function in C++17 mode.
    text = replace_once(text, '\nfind_package(PkgConfig)\n',
                        '\nif(APPLE)\n  add_compile_definitions(BOOST_NO_CXX98_FUNCTION_BASE)\n'
                        '  find_package(Intl REQUIRED)\n'
                        '  target_include_directories(OpenSCADLibInternal SYSTEM PUBLIC ${Intl_INCLUDE_DIRS})\n'
                        '  target_link_libraries(OpenSCADLibInternal PUBLIC ${Intl_LIBRARIES})\n'
                        'endif()\n\nfind_package(PkgConfig)\n')
    # MSVC invokes find_graphics before upstream adds its custom FindOpenCSG module.
    text = replace_once(text, '\nmacro(find_graphics)\n',
                        '\nmacro(find_graphics)\n  list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/cmake/Modules")\n')
    # Do not export the RC source from the static library to the executable.
    # CMake merges the application manifest with its generated CRT manifest once.
    text = replace_once(text, 'target_sources(OpenSCADLibInternal PUBLIC ${RESOURCE_FILES})',
                        'if(WIN32)\n'
                        '  configure_file("${CMAKE_SOURCE_DIR}/scripts/winmanifest.xml" "${CMAKE_BINARY_DIR}/openscad.manifest" COPYONLY)\n'
                        '  target_sources(OpenSCADExe PRIVATE ${RESOURCE_FILES} "${CMAKE_BINARY_DIR}/openscad.manifest")\n'
                        'else()\n'
                        '  target_sources(OpenSCADLibInternal PUBLIC ${RESOURCE_FILES})\n'
                        'endif()')
    text = replace_once(text, 'set_property(TARGET OpenSCADExe PROPERTY WIN32_EXECUTABLE ON)',
                        'set_property(TARGET OpenSCADExe PROPERTY WIN32_EXECUTABLE OFF)')
    text = replace_once(text, 'LINK_FLAGS "-subsystem:windows -ENTRY:mainCRTStartup -stack:${STACKSIZE}"',
                        'LINK_FLAGS "/SUBSYSTEM:CONSOLE /ENTRY:wmainCRTStartup /STACK:${STACKSIZE}"')
    # stage() compiles locale catalogs with verified tooling on every platform.
    start = text.index('add_custom_command(TARGET OpenSCADExe POST_BUILD\n')
    end = text.index('if(APPLE AND NOT APPLE_UNIX)', start)
    text = text[:start] + '# Locale catalogs are compiled during CLI packaging.\n\n' + text[end:]
    text = replace_once(text, 'find_path(OPENCSG_INCLUDE_DIRS opencsg.h)',
                        'find_path(OPENCSG_INCLUDE_DIRS opencsg.h PATH_SUFFIXES opencsg)')
    path.write_text(text)
    path = source / "cmake/Modules/FindOpenCSG.cmake"
    path.write_text(replace_once(path.read_text(), '            opencsg.h\n',
                                 '            opencsg.h PATH_SUFFIXES opencsg\n'))
    path = source / "src/main_wrapper.cc"
    text = replace_once(path.read_text(), '#include <string>', '#include <string>\n#include <vector>')
    text = replace_once(text, '  char *argv8[argc + 1];\n  std::string argvString[argc];',
                        '  std::vector<char *> argv8(argc + 1, nullptr);\n  std::vector<std::string> argvString(argc);')
    text = replace_once(text, 'return (main(argc, argv8));', 'return (main(argc, argv8.data()));')
    path.write_text(text)
    for name in ("openscad_win32.rc.in", "openscad_win32-nightly.rc.in"):
        path = source / "resources" / name
        path.write_text(replace_once(path.read_text(), '1 RT_MANIFEST "scripts/winmanifest.xml"\n', ''))
    # Derived members (including ctx) die before the GLView base destructor.
    # Release the base's GPU resources while this context is still current.
    path = source / "src/glview/OffscreenView.cc"
    path.write_text(replace_once(path.read_text(), """OffscreenView::~OffscreenView()
{
  fbo.reset();
}""", """OffscreenView::~OffscreenView()
{
  ctx->makeCurrent();
  renderer.reset();
  teardownShader();
  edge_shader.reset();
  fbo.reset();
}"""))


def patch_source(source):
    if MODERN_SOURCE:
        patch_modern_source(source)
    else:
        patch_legacy_source(source)


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
                             ("harfbuzz", "harfbuzz-modern-clang.patch"),
                             ("glew", "glew-default-image-base.patch")):
        portfile = vcpkg / f"ports/{port}/portfile.cmake"
        patch = (HERE / "patches" / patch_name).as_posix()
        portfile.write_text(replace_once(portfile.read_text(), "    PATCHES\n",
                                        f'    PATCHES\n        "{patch}"\n'))
    header_patches = [("cgal", "cgal-iterator-bool.patch")]
    if target.startswith("macos"):
        header_patches.append(("boost-mpl", "boost-mpl-lazy-enum-constants.patch"))
        portfile = vcpkg / "ports/gperf/portfile.cmake"
        portfile.write_text(replace_once(portfile.read_text(),
            "    URLS http://ftp.gnu.org/pub/gnu/gperf/gperf-3.1.tar.gz",
            "    URLS https://ftp.gnu.org/gnu/gperf/gperf-3.1.tar.gz\n"
            "         https://ftpmirror.gnu.org/gperf/gperf-3.1.tar.gz"))
    for port, patch_name in header_patches:
        portfile = vcpkg / f"ports/{port}/portfile.cmake"
        patch = (HERE / "patches" / patch_name).as_posix()
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
        helper = vcpkg / "ports/boost-modular-build-helper/boost-modular-build.cmake"
        helper.write_text(replace_once(helper.read_text(),
            'string(REPLACE "-vc143-" "-vc140-" NEW_FILENAME ${NEW_FILENAME})',
            'string(REGEX REPLACE "-vc14[0-9]-" "-vc140-" NEW_FILENAME "${NEW_FILENAME}")'))
        os.environ["VCPKG_FORCE_SYSTEM_BINARIES"] = "1"
        os.environ["VCPKG_DEFAULT_HOST_TRIPLET"] = "x64-windows"
        executable = vcpkg / "vcpkg.exe"
        # Upgrade only the host tool, preserving the pinned ports and libraries.
        urllib.request.urlretrieve(
            "https://github.com/microsoft/vcpkg-tool/releases/download/2026-09-26/vcpkg.exe",
            executable)
        if hashlib.sha256(executable.read_bytes()).hexdigest() != "2a4ae1467b9e797964c60c919d057b9e5dc1ec03f4b9632c1150026db25201f2":
            raise RuntimeError("vcpkg host tool checksum mismatch")
        urllib.request.urlretrieve(
            "https://raw.githubusercontent.com/microsoft/vcpkg/52d80838fb40c755b1615fbc9c7b994a33742a22/scripts/vcpkg-tools.json",
            vcpkg / "scripts/vcpkg-tools.json")
        msys = vcpkg / "scripts/cmake/vcpkg_acquire_msys.cmake"
        urllib.request.urlretrieve(
            "https://raw.githubusercontent.com/microsoft/vcpkg/52d80838fb40c755b1615fbc9c7b994a33742a22/scripts/cmake/vcpkg_acquire_msys.cmake",
            msys)
        urllib.request.urlretrieve(
            "https://raw.githubusercontent.com/microsoft/vcpkg/52d80838fb40c755b1615fbc9c7b994a33742a22/scripts/cmake/compile_wrapper_consider_clang-cl.patch",
            vcpkg / "scripts/cmake/compile_wrapper_consider_clang-cl.patch")
        # The public download helper has the same URL/hash interface in 2022.
        msys.write_text(replace_once(msys.read_text(),
            "    z_vcpkg_download_distfile(msys_archive", "    vcpkg_download_distfile(msys_archive"))
        # Match upstream's GMP/MSVC workaround: autoconf 2.72 fails to
        # recognize dumpbin symbols, leaving an empty libtool symbol filter.
        msys.write_text(replace_once(msys.read_text(), "endmacro()", """    z_vcpkg_acquire_msys_declare_package(
        URL "https://mirror.msys2.org/msys/x86_64/autoconf2.71-2.71-4-any.pkg.tar.zst"
        SHA512 c93b791eb55893cbe7c425e764074837355fd165deb7b1775f652c8e25d9d1f0cdd4120ab710d56fb859b7df55c4f971eccda7c112448f60615bff8a2dc81166
        DEPS awk bash diffutils m4 perl sed
    )
endmacro()"""))
        gmp = vcpkg / "ports/gmp/portfile.cmake"
        gmp.write_text(replace_once(gmp.read_text(), "vcpkg_configure_make(\n",
            'set(ENV{WANT_AUTOCONF} 2.71)\nvcpkg_configure_make(\n'
            '    ADDITIONAL_MSYS_PACKAGES autoconf2.71\n'))
        os.environ["VCPKG_DISABLE_METRICS"] = "1"
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
        expected = {"x64": 0x8664, "arm64": 0xAA64}[arch]
    elif target.startswith("linux"):
        if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
            raise RuntimeError("Expected a little endian 64-bit ELF executable")
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
        raise RuntimeError(f"Wrong architecture: {executable}: {machine:#x}, expected {expected:#x}")


def bundle_linux(binary, package):
    lib = package / "lib"
    lib.mkdir()
    # Keep the GL/GLX driver stack and glibc on the host. Bundle other linked
    # libraries including libstdc++ so Ubuntu 22.04 is a clear ABI floor.
    pending, seen = [binary], set()
    while pending:
        current = pending.pop()
        linked = output("ldd", current)
        if "not found" in linked:
            raise RuntimeError(f"Unresolved dependency: {linked}")
        for name, location in LINUX_LDD_ENTRY.findall(linked):
            if name in seen or LINUX_SYSTEM_LIBRARY.match(name):
                continue
            seen.add(name)
            destination = lib / name
            shutil.copy2(location, destination)
            pending.append(destination)
    # DT_RPATH also covers indirect dependencies of the host GL/GLX libraries.
    # DT_RUNPATH only covers direct dependencies and can select host X11 libs
    # despite bundled copies being present.
    for dependency in lib.iterdir():
        run("patchelf", "--force-rpath", "--set-rpath", "$ORIGIN", dependency)
    run("patchelf", "--force-rpath", "--set-rpath", "$ORIGIN/../lib", binary)


def linux_glibc_requirement(package):
    versions = set()
    for binary in [package / "bin/openscad-bin", *(package / "lib").iterdir()]:
        versions.update(re.findall(r"\bName: GLIBC_(\d+(?:\.\d+)+)\b",
                                   output("readelf", "--version-info", binary)))
    if not versions:
        raise RuntimeError("Linux package has no verifiable glibc requirements")
    required = max(versions, key=lambda v: tuple(map(int, v.split("."))))
    if tuple(map(int, required.split("."))) > tuple(map(int, LINUX_GLIBC_BASELINE.split("."))):
        raise RuntimeError(f"Linux package requires glibc {required}, above baseline {LINUX_GLIBC_BASELINE}")
    return required


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


def windows_imports(binary):
    imports = output("dumpbin", "/nologo", "/dependents", binary)
    return re.findall(r"^\s+([^\s]+\.dll)\s*$", imports, re.MULTILINE | re.IGNORECASE)


def copy_windows_runtime(bin_dir, target):
    # VsDevCmd may select a newer redistributable than the requested toolset.
    toolset = ".".join(os.environ["VCToolsVersion"].split(".")[:2])
    redist = Path(os.environ["VCINSTALLDIR"]) / "Redist/MSVC"
    versions = sorted(redist.glob(toolset + ".*"), key=lambda p: tuple(map(int, p.name.split("."))))
    if not versions:
        raise RuntimeError(f"MSVC {toolset} redistributables missing: {redist}")
    crt_dirs = list((versions[-1] / target.split("-")[1]).glob("Microsoft.VC*.CRT"))
    if len(crt_dirs) != 1:
        raise RuntimeError(f"Expected one MSVC CRT directory: {versions[-1]}")
    runtime = {p.name.lower(): p for p in crt_dirs[0].glob("*.dll")}
    pending = list(bin_dir.glob("*.dll")) + [bin_dir / "openscad.exe"]
    bundled = {p.name.lower() for p in pending}
    while pending:
        binary = pending.pop()
        for dependency in windows_imports(binary):
            name = dependency.lower()
            if name in runtime and name not in bundled:
                source = runtime[name]
                destination = bin_dir / source.name
                shutil.copy2(source, destination)
                bundled.add(name)
                pending.append(destination)
                print(f"Bundled required MSVC runtime: {source}", flush=True)
    return versions[-1].name


def stage(source, build, work, target, prefix, mesa):
    package = work / package_name(target)
    package.mkdir()
    bin_dir = package / "bin"
    bin_dir.mkdir()
    runtime_version = None
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
        runtime_version = copy_windows_runtime(bin_dir, target)
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
    for directory in ("color-schemes", "libraries", "fonts", "examples", *(("shaders", "templates") if MODERN_SOURCE else ())):
        shutil.copytree(source / directory, resources / directory, dirs_exist_ok=True)
    # localization_init() expects the resource directory, and gettext needs
    # compiled catalogs rather than the source .po files.
    for line in (source / "locale/LINGUAS").read_text().splitlines():
        for language in line.split("#", 1)[0].split():
            messages = resources / "locale" / language / "LC_MESSAGES"
            messages.mkdir(parents=True, exist_ok=True)
            if target.startswith("windows"):
                compiler = work / "msgfmt.py"
                if not compiler.exists():
                    urllib.request.urlretrieve(
                        "https://raw.githubusercontent.com/python/cpython/v3.11.14/Tools/i18n/msgfmt.py",
                        compiler)
                if hashlib.sha256(compiler.read_bytes()).hexdigest() != "79c83d2646e7cf742226b52c989431cea1b08749adbb599fba001bb0203efe1f":
                    raise RuntimeError("CPython msgfmt checksum mismatch")
                run(sys.executable, compiler, "-o", messages / "openscad.mo",
                    source / "locale" / f"{language}.po")
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
    if MODERN_SOURCE:
        for module in (source / "submodules").iterdir():
            if module.is_dir():
                for license_file in module.glob("*"):
                    if license_file.is_file() and license_file.name.lower().startswith(("license", "copying")):
                        shutil.copy2(license_file, package / "licenses" / (module.name + "-" + license_file.name))
    (package / "build-info.json").write_text(json.dumps({
        "tag": SOURCE_TAG, "commit": SOURCE_COMMIT, "target": target,
        "headless": True, "opengl": True, "nullgl": False,
        "vcpkg_commit": VCPKG_COMMIT if prefix else None,
        "mesa": MESA_VERSION if mesa else None,
        "msvc_runtime": runtime_version,
        "lib3mf": target.startswith("linux"),
        "manifold": MODERN_SOURCE,
        "asan": False,
        "builder_commit": os.environ.get("GITHUB_SHA"),
        **({"libc": "glibc", "minimum_glibc": LINUX_GLIBC_BASELINE,
            "required_glibc": linux_glibc_requirement(package), "linux_baseline": "ubuntu-22.04"}
           if target.startswith("linux") else {}),
    }, indent=2) + "\n")
    if target.startswith("macos"):
        # Re-sign after install_name_tool changes (required on Apple Silicon).
        run("codesign", "--force", "--deep", "--sign", "-", package / "OpenSCAD.app")
    return package


def verify_runtime_dependencies(package, target):
    """Reject third-party libraries that would be resolved outside the archive."""
    if target.startswith("windows"):
        binaries = list((package / "bin").glob("*.dll")) + [package / "bin/openscad.exe"]
        bundled = {p.name.lower() for p in binaries}
        if "opengl32.dll" not in bundled:
            raise RuntimeError("The Windows package must include Mesa OpenGL")
        system = Path(os.environ["SystemRoot"]) / "System32"
        for binary in binaries:
            check_architecture(binary, target)
            for dependency in windows_imports(binary):
                name = dependency.lower()
                if name in bundled or name.startswith(("api-ms-win-", "ext-ms-win-")):
                    continue
                if (not name.startswith(("msvcp", "vcruntime", "concrt", "msvcr"))
                        and (system / dependency).is_file()):
                    continue
                raise RuntimeError(f"Unbundled runtime dependency: {binary.name}: {dependency}")
    elif target.startswith("macos"):
        executable = package / "OpenSCAD.app/Contents/MacOS/OpenSCAD"
        binaries = []
        for path in (package / "OpenSCAD.app").rglob("*"):
            if path.is_file():
                with path.open("rb") as stream:
                    if stream.read(4) == b"\xcf\xfa\xed\xfe":
                        binaries.append(path)

        def expand(path, binary):
            return Path(path.replace("@loader_path", str(binary.parent))
                        .replace("@executable_path", str(executable.parent)))

        def rpaths(binary):
            commands = output("otool", "-l", binary)
            return [expand(p, binary) for p in re.findall(
                r"cmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset \d+\)", commands)]

        executable_paths = rpaths(executable)
        for binary in binaries:
            check_architecture(binary, target)
            for line in output("otool", "-L", binary).splitlines()[1:]:
                dependency = line.strip().split(" (compatibility version", 1)[0]
                if dependency.startswith(("/System/Library/", "/usr/lib/")):
                    continue
                if dependency.startswith("@rpath/"):
                    candidates = [p / dependency[len("@rpath/"):] for p in rpaths(binary) + executable_paths]
                elif dependency.startswith(("@loader_path/", "@executable_path/")):
                    candidates = [expand(dependency, binary)]
                else:
                    raise RuntimeError(f"Non-relocatable runtime dependency: {binary}: {dependency}")
                if not any(p.is_file() and p.resolve().is_relative_to(package.resolve()) for p in candidates):
                    raise RuntimeError(f"Unbundled runtime dependency: {binary}: {dependency}")
    elif target.startswith("linux"):
        for binary in [package / "bin/openscad-bin", *(package / "lib").iterdir()]:
            check_architecture(binary, target)
            linked = output("ldd", binary)
            if "not found" in linked:
                raise RuntimeError(f"Unresolved runtime dependency: {binary}: {linked}")
            for name, location in LINUX_LDD_ENTRY.findall(linked):
                if not LINUX_SYSTEM_LIBRARY.match(name) and not Path(location).resolve().is_relative_to(package.resolve()):
                    raise RuntimeError(f"Unbundled runtime dependency: {binary.name}: {name}: {location}")
        linux_glibc_requirement(package)
    print(f"Verified bundled runtime dependencies: {target}", flush=True)


def smoke_test(package, target, work):
    verify_runtime_dependencies(package, target)
    binary = package / "bin" / ("openscad.exe" if target.startswith("windows") else "openscad")
    env = os.environ.copy()
    for name in ("OPENSCADPATH", "OPENSCAD_FONT_PATH", "FONTCONFIG_PATH", "FONTCONFIG_FILE",
                 "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH"):
        env.pop(name, None)
    if target.startswith("windows"):
        # os.environ is case-insensitive on Windows, but its copied dict is not.
        system = Path(os.environ["SystemRoot"])
        env["PATH"] = os.pathsep.join(map(str, (system / "System32", system)))
    else:
        env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    env["LIBGL_ALWAYS_SOFTWARE"] = "1"
    if target.startswith("windows"):
        env["GALLIUM_DRIVER"] = "softpipe"
    test = work / "smoke"
    test.mkdir()
    model = test / ("unicode-模型.scad" if MODERN_SOURCE else "smoke.scad")
    model.write_text('use <MCAD/boxes.scad>\n'
                     'difference() { roundedBox([20,20,10],2,true); cylinder(h=30,r=4,center=true,$fn=32); }\n'
                     'translate([0,0,5]) linear_extrude(1) text("CLI",size=4,font="Liberation Sans");\n')
    log = test / "smoke.log"
    with log.open("w") as stream:
        try:
            result = run(binary, "--version", env=env, cwd=test, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except subprocess.CalledProcessError as error:
            stream.write(error.stdout or "")
            stream.flush()
            raise
        stream.write(result.stdout)
        stream.flush()
        if SOURCE_VERSION not in result.stdout:
            raise RuntimeError("Packaged executable reports the wrong version")
        checks = [("model.stl", []), ("preview.png", ["--preview", "--imgsize=256,256"]),
                  ("render.png", (["--backend=CGAL", "--render"] if MODERN_SOURCE else ["--render=cgal"])
                   + ["--imgsize=256,256"])]
        if MODERN_SOURCE:
            checks.append(("manifold.png", ["--backend=Manifold", "--render", "--imgsize=256,256"]))
        if target.startswith("linux"):
            checks.append(("model.3mf", []))
        for name, flags in checks:
            run(binary, *flags, "-o", test / name, model, env=env, cwd=test, stdout=stream, stderr=subprocess.STDOUT)
            artifact = test / name
            if not artifact.is_file() or artifact.stat().st_size < 1024:
                raise RuntimeError(f"Invalid or empty smoke-test output: {artifact}")
            if name.endswith(".png"):
                data = artifact.read_bytes()
                if data[:8] != b"\x89PNG\r\n\x1a\n" or struct.unpack_from(">II", data, 16) != (256, 256):
                    raise RuntimeError("Invalid PNG output")
            elif name.endswith(".3mf") and artifact.read_bytes()[:4] != b"PK\x03\x04":
                raise RuntimeError("Invalid 3MF output")
        run(binary, "--info", env=env, cwd=test, stdout=stream, stderr=subprocess.STDOUT)
    if re.search(r"ERROR:|WARNING: Can't open|WARNING: Ignoring unknown module|Could not initialize localization", log.read_text()):
        raise RuntimeError("Smoke test reported a missing resource or rendering error")


def main():
    target = os.environ["TARGET"]
    if target not in NATIVE_TARGETS:
        raise RuntimeError(f"Unsupported target: {target}")
    repo = ROOT / "sources/openscad"
    if output("git", "-C", repo, "rev-parse", "HEAD") != SOURCE_COMMIT:
        raise RuntimeError(f"Expected {SOURCE_TAG} at {SOURCE_COMMIT}")
    work = ROOT / ".build" / target
    work.mkdir(parents=True)
    source = work / "source"
    archive_source(repo, source)
    for line in output("git", "-C", repo, "submodule", "status", "--recursive").splitlines():
        if line[0] in "-+U":
            raise RuntimeError(f"Submodule is not at its pinned commit: {line}")
        path = line.split()[1]
        archive_source(repo / path, source / path)
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
    if target.startswith("windows"):
        options += [f"-DFLEX_EXECUTABLE={shutil.which('win_flex')}",
                    f"-DBISON_EXECUTABLE={shutil.which('win_bison')}"]
        mesa = build_mesa(work, prefix)
    if MODERN_SOURCE:
        options += [f"-DOPENSCAD_VERSION={SOURCE_VERSION}", f"-DOPENSCAD_COMMIT={SOURCE_COMMIT[:8]}",
                    "-DENABLE_TESTS=OFF", "-DENABLE_GUI_TESTS=OFF", "-DUSE_QT6=OFF",
                    "-DUSE_MIMALLOC=OFF", "-DENABLE_MANIFOLD=ON", "-DENABLE_CGAL=ON",
                    "-DENABLE_HIDAPI=OFF", "-DENABLE_SPNAV=OFF", "-DENABLE_GAMEPAD=OFF",
                    "-DCMAKE_OSX_DEPLOYMENT_TARGET=11.0"]
        options += (["-DCMAKE_REQUIRE_FIND_PACKAGE_Lib3MF=ON"] if target.startswith("linux") else
                    ["-DCMAKE_REQUIRE_FIND_PACKAGE_Lib3MF=OFF", "-DCMAKE_DISABLE_FIND_PACKAGE_Lib3MF=ON"])
    build = work / "build"
    run("cmake", "-S", source, "-B", build, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
        "-DHEADLESS=ON", "-DNULLGL=OFF", "-DINFO=OFF", *options)
    # Report all translation-unit failures together instead of stopping at the first.
    run("cmake", "--build", build, "--target", "OpenSCADExe" if MODERN_SOURCE else "OpenSCAD", "--parallel", "2", "--", "-k", "0")
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
