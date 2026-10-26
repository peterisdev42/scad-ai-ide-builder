# 2021.01 does not yet have upstream Emscripten CMake support. Keep its
# geometry/font/import dependencies, but use the upstream WASM NULLGL policy.
find_package(CGAL REQUIRED)
# CGAL 5.5's config resets Boost_USE_STATIC_LIBS to OFF; set the image's
# static-only selection after CGAL, rather than relying on a cache variable.
set(Boost_USE_STATIC_LIBS ON)
set(Boost_USE_STATIC_RUNTIME ON)
find_package(Boost REQUIRED COMPONENTS filesystem system regex program_options)
find_package(Eigen3 CONFIG REQUIRED)
find_package(Freetype REQUIRED)
find_package(LibXml2 REQUIRED)
find_package(PkgConfig REQUIRED)
pkg_check_modules(GLIB2 REQUIRED IMPORTED_TARGET glib-2.0)
pkg_check_modules(FONTCONFIG REQUIRED IMPORTED_TARGET fontconfig)
pkg_check_modules(HARFBUZZ REQUIRED IMPORTED_TARGET harfbuzz)
pkg_check_modules(LIBZIP REQUIRED IMPORTED_TARGET libzip)
# GLib's .pc file carries native -pthread flags even though this image's
# libraries and the portable geometry runtime are built without WASM threads.
foreach(property INTERFACE_COMPILE_OPTIONS INTERFACE_LINK_OPTIONS)
  get_target_property(glib_options PkgConfig::GLIB2 ${property})
  if(glib_options)
    list(REMOVE_ITEM glib_options -pthread)
    set_target_properties(PkgConfig::GLIB2 PROPERTIES ${property} "${glib_options}")
  endif()
endforeach()
find_library(DOUBLE_CONVERSION_LIBRARY NAMES double-conversion REQUIRED)
set(COMMON_LIBRARIES
  CGAL::CGAL Boost::filesystem Boost::system Boost::regex Boost::program_options
  Eigen3::Eigen Freetype::Freetype LibXml2::LibXml2
  PkgConfig::GLIB2 PkgConfig::FONTCONFIG PkgConfig::HARFBUZZ PkgConfig::LIBZIP
  ${DOUBLE_CONVERSION_LIBRARY} /emsdk/upstream/emscripten/cache/sysroot/lib/libz.a)
add_definitions(-DENABLE_CGAL -DENABLE_LIBZIP -DEIGEN_DONT_ALIGN -DNULLGL)
find_package(FLEX REQUIRED)
find_package(BISON REQUIRED)
