# 2021.01 does not yet have upstream Emscripten CMake support. Keep its
# geometry/font/import dependencies, but use the upstream WASM NULLGL policy.
find_package(CGAL REQUIRED)
find_package(Boost REQUIRED COMPONENTS filesystem system regex program_options)
find_package(Eigen3 CONFIG REQUIRED)
find_package(Freetype REQUIRED)
find_package(LibXml2 REQUIRED)
find_package(PkgConfig REQUIRED)
pkg_check_modules(GLIB2 REQUIRED IMPORTED_TARGET glib-2.0)
pkg_check_modules(FONTCONFIG REQUIRED IMPORTED_TARGET fontconfig)
pkg_check_modules(HARFBUZZ REQUIRED IMPORTED_TARGET harfbuzz)
pkg_check_modules(LIBZIP REQUIRED IMPORTED_TARGET libzip)
find_library(DOUBLE_CONVERSION_LIBRARY NAMES double-conversion REQUIRED)
set(COMMON_LIBRARIES
  CGAL::CGAL Boost::filesystem Boost::system Boost::regex Boost::program_options
  Eigen3::Eigen Freetype::Freetype LibXml2::LibXml2
  PkgConfig::GLIB2 PkgConfig::FONTCONFIG PkgConfig::HARFBUZZ PkgConfig::LIBZIP
  ${DOUBLE_CONVERSION_LIBRARY} /emsdk/upstream/emscripten/cache/sysroot/lib/libz.a)
add_definitions(-DENABLE_CGAL -DENABLE_LIBZIP -DEIGEN_DONT_ALIGN -DNULLGL)
find_package(FLEX REQUIRED)
find_package(BISON REQUIRED)
