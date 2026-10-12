cmake_minimum_required(VERSION 3.16)
# Load wherever Blender 5.2 runs (its macOS minimum is 11.2), not only on the build machine's
# macOS. Set before project() so the first configure uses it.
if(APPLE AND NOT CMAKE_OSX_DEPLOYMENT_TARGET)
  set(CMAKE_OSX_DEPLOYMENT_TARGET "11.2" CACHE STRING "Minimum macOS version")
endif()
project(libopenpainter VERSION 0.15.0 LANGUAGES C)
set(CMAKE_C_STANDARD 17)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)
option(OP_SANITIZERS "Enable address and undefined behavior sanitizers" OFF)
set(OP_SANITIZER_SET "address,undefined" CACHE STRING "Clang/GCC sanitizer list")

add_library(op_build_options INTERFACE)
# Kernels reproduce reference float arithmetic exactly: never contract a multiply and an add.
if(MSVC)
  target_compile_options(op_build_options INTERFACE /W4 /fp:precise)
  # Tests keep their assertions in every configuration. MSVC warns (D9025) when /UNDEBUG
  # overrides the configuration's /DNDEBUG, so NDEBUG goes on the library targets instead.
  foreach(config RELEASE RELWITHDEBINFO MINSIZEREL)
    string(REPLACE "/DNDEBUG" "" CMAKE_C_FLAGS_${config} "${CMAKE_C_FLAGS_${config}}")
  endforeach()
  add_library(op_release_ndebug INTERFACE)
  target_compile_definitions(op_release_ndebug INTERFACE $<$<NOT:$<CONFIG:Debug>>:NDEBUG>)
else()
  target_compile_options(op_build_options INTERFACE -Wall -Wextra -Wpedantic -ffp-contract=off)
endif()
# x86-64 kernels use SSE4.2 (src/core/simd.h); MSVC allows the intrinsics without a flag.
if(NOT MSVC)
  if(APPLE AND CMAKE_OSX_ARCHITECTURES)
    if(CMAKE_OSX_ARCHITECTURES STREQUAL "x86_64")
      target_compile_options(op_build_options INTERFACE -msse4.2)
    elseif(CMAKE_OSX_ARCHITECTURES MATCHES "x86_64")
      target_compile_options(op_build_options INTERFACE "SHELL:-Xarch_x86_64 -msse4.2")
    endif()
  elseif(CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64|amd64)$")
    target_compile_options(op_build_options INTERFACE -msse4.2)
  endif()
endif()
if(OP_SANITIZERS)
  if(MSVC)
    message(FATAL_ERROR "Use a Clang/GCC sanitizer build for the native safety gate")
  endif()
  target_compile_options(op_build_options INTERFACE
    -fsanitize=${OP_SANITIZER_SET} -fno-omit-frame-pointer)
  target_link_options(op_build_options INTERFACE -fsanitize=${OP_SANITIZER_SET})
endif()
set(OP_SOURCES src/graph/mask.c src/graph/channels.c src/core/context.c src/core/document.c src/core/evaluate.c
               src/core/snapshot.c src/core/composite.c src/gpu/probe.c src/graph/graph.c src/graph/stack.c src/graph/filter.c src/graph/program.c src/gpu/pool.c src/gpu/revision.c src/gpu/cache.c
               src/shaders/fixed.c src/shaders/compiler.c src/shaders/affine.c src/geometry/geometry.c src/geometry/mesh.c src/geometry/padding.c src/geometry/seams.c src/geometry/topology.c src/paint/brush.c src/paint/reach.c src/core/pixels.c src/core/sha256.c)
add_library(openpainter STATIC ${OP_SOURCES})
add_library(openpainter_shared SHARED ${OP_SOURCES})
foreach(target openpainter openpainter_shared)
  target_include_directories(${target} PUBLIC include)
  target_link_libraries(${target} PRIVATE op_build_options)
  if(MSVC)
    target_link_libraries(${target} PRIVATE op_release_ndebug)
  endif()
  if(UNIX)
    target_link_libraries(${target} PRIVATE m)
  endif()
endforeach()
# Export the C ABI on Windows; use a distinct import library name there.
set_target_properties(openpainter_shared PROPERTIES OUTPUT_NAME openpainter
  WINDOWS_EXPORT_ALL_SYMBOLS ON)
if(WIN32)
  set_target_properties(openpainter_shared PROPERTIES ARCHIVE_OUTPUT_NAME openpainter_import)
endif()
install(TARGETS openpainter_shared LIBRARY DESTINATION lib RUNTIME DESTINATION bin)
install(DIRECTORY include/openpainter DESTINATION include FILES_MATCHING PATTERN "*.h")
enable_testing()
# Seconds per test; an emulated run (Wine, Rosetta) raises it.
set(OP_TEST_TIMEOUT 20 CACHE STRING "Seconds each C test may take")
function(op_test name)
  add_executable(${name} tests/${name}.c)
  target_link_libraries(${name} PRIVATE openpainter op_build_options)
  # Assertions execute test operations; Release must not compile them away (MSVC: see above).
  if(NOT MSVC)
    target_compile_options(${name} PRIVATE -UNDEBUG)
  endif()
  add_test(NAME ${name} COMMAND ${name})
  set_tests_properties(${name} PROPERTIES TIMEOUT ${OP_TEST_TIMEOUT})
endfunction()
op_test(test_abi)
op_test(test_document)
op_test(test_snapshot)
op_test(test_composite)
op_test(test_lifetime)

op_test(test_snapshot_validation)

op_test(test_graph)
op_test(test_pool)
op_test(test_ir)

op_test(test_revision)

# Optional production binding; the pure C build has no Python dependency.
set(OP_PYTHON_INCLUDE "" CACHE PATH "Blender bundled Python include directory")
if(OP_PYTHON_INCLUDE)
  set_target_properties(openpainter PROPERTIES POSITION_INDEPENDENT_CODE ON)
  add_library(openpainter_python MODULE bindings/python/module.c)
  target_include_directories(openpainter_python PRIVATE "${OP_PYTHON_INCLUDE}")
  target_link_libraries(openpainter_python PRIVATE openpainter op_build_options)
  set_target_properties(openpainter_python PROPERTIES PREFIX "" OUTPUT_NAME "_native" SUFFIX ".abi3.so")
  if(APPLE)
    target_link_options(openpainter_python PRIVATE -undefined dynamic_lookup)
  elseif(WIN32)
    # The limited API imports python3.dll, which Blender ships beside python313.dll (in the
    # Blender folder and in <version>/python/bin). Its import library comes from a CPython
    # 3.11+ install, next to the headers by default. pyconfig.h also names python3.lib
    # (#pragma comment(lib)), so its folder goes on the link path.
    set(OP_PYTHON_LIBRARY "" CACHE FILEPATH "python3.lib of a CPython 3.11+ install (Windows binding)")
    if(NOT OP_PYTHON_LIBRARY)
      get_filename_component(op_python_root "${OP_PYTHON_INCLUDE}" DIRECTORY)
      set(OP_PYTHON_LIBRARY "${op_python_root}/libs/python3.lib")
    endif()
    if(NOT EXISTS "${OP_PYTHON_LIBRARY}")
      message(FATAL_ERROR "No ${OP_PYTHON_LIBRARY}; set OP_PYTHON_LIBRARY to a CPython 3.11+ python3.lib")
    endif()
    get_filename_component(op_python_libs "${OP_PYTHON_LIBRARY}" DIRECTORY)
    target_link_directories(openpainter_python PRIVATE "${op_python_libs}")
    target_link_libraries(openpainter_python PRIVATE "${OP_PYTHON_LIBRARY}")
    set_target_properties(openpainter_python PROPERTIES SUFFIX ".pyd")
  endif()
  if(MSVC)
    target_link_libraries(openpainter_python PRIVATE op_release_ndebug)
  endif()
endif()

op_test(test_cache)

op_test(test_stack)
op_test(test_filter)
op_test(test_program)
op_test(test_paint)
op_test(test_brush_coverage)
op_test(test_padding)
op_test(test_rasterize)
op_test(test_pixels)
op_test(test_pixels_paths)
op_test(test_sha256)
op_test(test_mesh)
op_test(test_reach)
op_test(test_publication)
op_test(test_seams)

op_test(test_blend)

op_test(test_graph_execution)

op_test(test_topology)

op_test(test_channels)

op_test(test_effects)

op_test(test_masks)

op_test(test_affine)
op_test(test_suffix)
