#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt=(apt-get)
if [[ $(id -u) != 0 ]]; then apt=(sudo -E apt-get); fi
"${apt[@]}" update
"${apt[@]}" install -y --no-install-recommends \
  build-essential gcc-11 g++-11 cmake ninja-build python3 git ca-certificates pkg-config ccache gettext \
  flex bison libboost-all-dev libcgal-dev libeigen3-dev libopencsg-dev \
  libglew-dev libgmp-dev libmpfr-dev libfontconfig1-dev libfreetype6-dev \
  libharfbuzz-dev libglib2.0-dev libzip-dev libxml2-dev libdouble-conversion-dev \
  libcairo2-dev lib3mf-dev libtbb-dev libssl-dev libgl1-mesa-dev libglu1-mesa-dev libx11-dev \
  libgl1-mesa-dri libglx-mesa0 xvfb xauth patchelf fonts-liberation
export LIBGL_ALWAYS_SOFTWARE=1
export CC=gcc-11 CXX=g++-11
test "$(getconf GNU_LIBC_VERSION)" = 'glibc 2.35'
uname -m
getconf GNU_LIBC_VERSION
"$CXX" --version
python scripts/openscad-ci/build.py
