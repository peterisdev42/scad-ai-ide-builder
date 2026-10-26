#!/usr/bin/env bash
set -euo pipefail
emcc --version
cmake --version
source_dir=/workspace/.build/wasm/source
build_dir=/workspace/.build/wasm/build
options=(-DCMAKE_BUILD_TYPE=Release -DHEADLESS=ON -DNULLGL=ON -DINFO=OFF
         -DBoost_USE_STATIC_LIBS=ON -DBoost_USE_STATIC_RUNTIME=ON
         -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DCMAKE_CXX_COMPILER_LAUNCHER=ccache
         -DCMAKE_C_COMPILER_LAUNCHER=ccache "-DCMAKE_EXE_LINKER_FLAGS=-fexceptions -sSTACK_SIZE=8MB")
if [[ "$OPENSCAD_TAG" == openscad-2021.01 ]]; then
    # CGAL is header-only. Use the same 5.5.1 API as the native legacy builds,
    # retaining the image's WASM GMP/MPFR/Boost binary dependencies.
    cgal_dir=/workspace/.build/wasm/cgal
    git init "$cgal_dir"
    git -C "$cgal_dir" fetch --depth=1 https://github.com/CGAL/cgal.git a1ae234fa258b054275bde38cc38dde552c50993
    git -C "$cgal_dir" checkout --detach FETCH_HEAD
    git -C "$cgal_dir" apply /workspace/scripts/openscad-ci/patches/cgal-iterator-bool.patch
    options+=(-DCGAL_DIR="$cgal_dir" -DCMAKE_CXX_FLAGS=-fexceptions)
    target=OpenSCAD
else
    options+=(-DWASM_BUILD_TYPE=node-module -DOPENSCAD_VERSION=2026.10-TEST4
              -DOPENSCAD_COMMIT=5a864f17 -DENABLE_TESTS=OFF -DENABLE_GUI_TESTS=OFF
              -DENABLE_CGAL=ON -DENABLE_MANIFOLD=ON -DMANIFOLD_PAR=OFF
              -DENABLE_HIDAPI=OFF -DENABLE_SPNAV=OFF -DENABLE_GAMEPAD=OFF
              -DCMAKE_REQUIRE_FIND_PACKAGE_Lib3MF=OFF -DCMAKE_DISABLE_FIND_PACKAGE_Lib3MF=ON)
    target=OpenSCADExe
fi
emcmake cmake -S "$source_dir" -B "$build_dir" -G Ninja "${options[@]}"
cmake --build "$build_dir" --target "$target" --parallel 2 -- -k 0
