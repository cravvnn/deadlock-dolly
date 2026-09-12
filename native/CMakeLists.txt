cmake_minimum_required(VERSION 3.21)
project(DollyNative VERSION 0.5.3 LANGUAGES C CXX)

# Native interpolation tests also build on Linux/macOS. The game bridge is an
# x64 Windows DLL and is deliberately absent from non-Windows targets.
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

add_library(dolly_path STATIC src/dolly_path.cpp src/dolly_effects.cpp src/dolly_visualization.cpp)
target_include_directories(dolly_path PUBLIC include)
add_library(dolly_depth STATIC src/dolly_depth.cpp)
target_include_directories(dolly_depth PUBLIC include)

include(CTest)
if(BUILD_TESTING)
    add_executable(path_tests tests/path_tests.cpp)
    target_link_libraries(path_tests PRIVATE dolly_path)
    add_test(NAME native_path_tests COMMAND path_tests)
    add_executable(effect_tests tests/effect_tests.cpp)
    target_link_libraries(effect_tests PRIVATE dolly_path)
    add_test(NAME native_effect_tests COMMAND effect_tests)
    add_executable(flight_tests tests/flight_tests.cpp)
    target_link_libraries(flight_tests PRIVATE dolly_path)
    add_test(NAME native_flight_tests COMMAND flight_tests)
    add_executable(visualization_tests tests/visualization_tests.cpp)
    target_link_libraries(visualization_tests PRIVATE dolly_path)
    add_test(NAME native_visualization_tests COMMAND visualization_tests)
    add_executable(video_math_tests tests/video_math_tests.cpp)
    target_include_directories(video_math_tests PRIVATE include)
    add_test(NAME native_video_math_tests COMMAND video_math_tests)
    add_executable(depth_tests tests/depth_tests.cpp)
    target_link_libraries(depth_tests PRIVATE dolly_depth)
    add_test(NAME native_depth_tests COMMAND depth_tests)
    add_executable(pattern_tests tests/pattern_tests.cpp)
    target_include_directories(pattern_tests PRIVATE include)
    add_test(NAME native_pattern_tests COMMAND pattern_tests)
    add_executable(compat_locator_tests tests/compat_locator_tests.cpp)
    target_include_directories(compat_locator_tests PRIVATE include src)
    add_test(NAME native_compat_locator_tests COMMAND compat_locator_tests)
endif()

if(WIN32)
    add_library(dolly_depth_readback STATIC src/dolly_depth_readback_win.cpp src/dolly_depth_sequence_win.cpp)
    target_link_libraries(dolly_depth_readback PUBLIC dolly_depth d3d11)
    if(BUILD_TESTING)
        add_executable(depth_readback_smoke tests/depth_readback_smoke.cpp)
        target_link_libraries(depth_readback_smoke PRIVATE dolly_depth_readback)
        add_test(NAME native_depth_readback_smoke COMMAND depth_readback_smoke)
        set_tests_properties(native_depth_readback_smoke PROPERTIES TIMEOUT 20)
        add_executable(depth_sequence_tests tests/depth_sequence_tests.cpp)
        target_link_libraries(depth_sequence_tests PRIVATE dolly_depth_readback)
        add_test(NAME native_depth_sequence_tests COMMAND depth_sequence_tests)
    endif()
    if(NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
        message(FATAL_ERROR "DollyNative requires a Windows x64 toolchain")
    endif()

    add_library(minhook STATIC
        vendor/minhook/src/buffer.c
        vendor/minhook/src/hook.c
        vendor/minhook/src/trampoline.c
        vendor/minhook/src/hde/hde64.c)
    target_include_directories(minhook PUBLIC vendor/minhook/include)

    add_library(dolly_depth_scene STATIC src/dolly_depth_scene_win.cpp src/dolly_depth_hooks_win.cpp)
    target_link_libraries(dolly_depth_scene PUBLIC dolly_depth_readback minhook dxguid)
    if(BUILD_TESTING)
        add_executable(depth_scene_smoke tests/depth_scene_smoke.cpp)
        target_link_libraries(depth_scene_smoke PRIVATE dolly_depth_scene d3dcompiler)
        add_test(NAME native_depth_scene_smoke COMMAND depth_scene_smoke)
        set_tests_properties(native_depth_scene_smoke PROPERTIES TIMEOUT 30)
    endif()

    # Pinned, unmodified Dear ImGui core and official backends. No game assets,
    # online dependency fetches or third-party installer are needed to build.
    add_library(dolly_overlay STATIC
        src/dolly_overlay_win.cpp
        src/dolly_renderer_diagnostics_win.cpp
        src/dolly_video_win.cpp
        src/dolly_reshade_win.cpp
        src/dolly_media_win.cpp
        src/dolly_visualization_runtime_win.cpp
        vendor/imgui/imgui.cpp
        vendor/imgui/imgui_draw.cpp
        vendor/imgui/imgui_tables.cpp
        vendor/imgui/imgui_widgets.cpp
        vendor/imgui/backends/imgui_impl_win32.cpp
        vendor/imgui/backends/imgui_impl_dx11.cpp)
    target_include_directories(dolly_overlay PRIVATE include vendor/imgui vendor/imgui/backends)
    target_compile_definitions(dolly_overlay PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX
        IMGUI_IMPL_WIN32_DISABLE_GAMEPAD)
    target_link_libraries(dolly_overlay PUBLIC dolly_path dolly_depth_scene minhook d3d11 dxgi d3dcompiler dwmapi gdi32 mfuuid ole32)

    add_library(DollyNative SHARED src/bridge_win.cpp src/dolly_editor_win.cpp)
    target_include_directories(DollyNative PRIVATE include)
    target_link_libraries(DollyNative PRIVATE dolly_path dolly_overlay minhook bcrypt shell32)
    target_compile_definitions(DollyNative PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX)
    set_target_properties(DollyNative PROPERTIES
        PREFIX ""
        OUTPUT_NAME "DollyNative"
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/bin/win64"
        RUNTIME_OUTPUT_DIRECTORY_DEBUG "${CMAKE_CURRENT_SOURCE_DIR}/bin/win64"
        RUNTIME_OUTPUT_DIRECTORY_RELEASE "${CMAKE_CURRENT_SOURCE_DIR}/bin/win64"
        RUNTIME_OUTPUT_DIRECTORY_RELWITHDEBINFO "${CMAKE_CURRENT_SOURCE_DIR}/bin/win64"
        RUNTIME_OUTPUT_DIRECTORY_MINSIZEREL "${CMAKE_CURRENT_SOURCE_DIR}/bin/win64")

    if(BUILD_TESTING)
        add_executable(bridge_smoke tests/bridge_smoke.cpp)
        target_include_directories(bridge_smoke PRIVATE include)
        target_link_libraries(bridge_smoke PRIVATE dolly_path dolly_overlay minhook bcrypt shell32)
        add_test(NAME native_bridge_callback_smoke COMMAND bridge_smoke)
        set_tests_properties(native_bridge_callback_smoke PROPERTIES TIMEOUT 30)
        add_executable(overlay_smoke tests/overlay_smoke.cpp)
        target_include_directories(overlay_smoke PRIVATE include vendor/imgui)
        target_link_libraries(overlay_smoke PRIVATE dolly_overlay minhook)
        add_executable(depth_video_smoke tests/depth_video_smoke.cpp)
        target_link_libraries(depth_video_smoke PRIVATE dolly_overlay)
        add_test(NAME native_dx11_overlay_smoke COMMAND overlay_smoke)
        set_tests_properties(native_dx11_overlay_smoke PROPERTIES TIMEOUT 45)
        # CI runners lack the GPU/Media Foundation hardware to encode MP4s.
        # Skip this test in CI, but keep it registered for local development.
        # Quote the expansion: an undefined $ENV{CI} must stay a valid argument.
        if("$ENV{CI}" STREQUAL "true")
            message(STATUS "CI environment detected: Skipping native_video_encoder_smoke test registration.")
        else()
            add_executable(video_smoke tests/video_smoke.cpp)
            target_include_directories(video_smoke PRIVATE include)
            target_link_libraries(video_smoke PRIVATE dolly_overlay)
            add_test(NAME native_video_encoder_smoke COMMAND video_smoke)
            set_tests_properties(native_video_encoder_smoke PROPERTIES TIMEOUT 45)
        endif()
endif()
    if(MSVC)
        target_compile_options(DollyNative PRIVATE /W4 /EHsc /permissive-)
        target_compile_options(dolly_path PRIVATE /W4 /EHsc /permissive-)
        if(BUILD_TESTING)
            target_compile_options(bridge_smoke PRIVATE /W4 /EHsc /permissive-)
        endif()
    elseif(CMAKE_CXX_COMPILER_ID MATCHES "Clang")
        # Retain Windows declaration/calling-convention extensions with clang.
        target_compile_options(DollyNative PRIVATE -fms-extensions)
        if(BUILD_TESTING)
            target_compile_options(bridge_smoke PRIVATE -fms-extensions)
        endif()
    endif()
endif()
