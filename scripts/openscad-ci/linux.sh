#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  build-essential cmake ninja-build python3 git ca-certificates pkg-config \
  flex bison libboost-all-dev libcgal-dev libeigen3-dev libopencsg-dev \
  libglew-dev libgmp-dev libmpfr-dev libfontconfig1-dev libfreetype6-dev \
  libharfbuzz-dev libglib2.0-dev libzip-dev libxml2-dev libdouble-conversion-dev \
  libcairo2-dev lib3mf-dev libgl1-mesa-dev libglu1-mesa-dev libx11-dev \
  libgl1-mesa-dri libglx-mesa0 xvfb xauth patchelf fonts-liberation
export LIBGL_ALWAYS_SOFTWARE=1
python3 scripts/openscad-ci/build.py
