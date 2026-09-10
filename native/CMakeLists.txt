cmake_minimum_required(VERSION 3.21)
project(DollyNative VERSION 0.4.0 LANGUAGES C CXX)

# Native interpolation tests also build on Linux/macOS. The game bridge is an
# x64 Windows DLL and is deliberately absent from non-Windows targets.
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

add_library(dolly_path STATIC src/dolly_path.cpp src/dolly_effects.cpp)
target_include_directories(dolly_path PUBLIC include)

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
endif()

if(WIN32)
    if(NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
        message(FATAL_ERROR "DollyNative requires a Windows x64 toolchain")
    endif()

    add_library(minhook STATIC
        vendor/minhook/src/buffer.c
        vendor/minhook/src/hook.c
        vendor/minhook/src/trampoline.c
        vendor/minhook/src/hde/hde64.c)
    target_include_directories(minhook PUBLIC vendor/minhook/include)

    # Pinned, unmodified Dear ImGui core and official backends. No game assets,
    # online dependency fetches or third-party installer are needed to build.
    add_library(dolly_overlay STATIC
        src/dolly_overlay_win.cpp
        vendor/imgui/imgui.cpp
        vendor/imgui/imgui_draw.cpp
        vendor/imgui/imgui_tables.cpp
        vendor/imgui/imgui_widgets.cpp
        vendor/imgui/backends/imgui_impl_win32.cpp
        vendor/imgui/backends/imgui_impl_dx11.cpp)
    target_include_directories(dolly_overlay PRIVATE include vendor/imgui vendor/imgui/backends)
    target_compile_definitions(dolly_overlay PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX
        IMGUI_IMPL_WIN32_DISABLE_GAMEPAD)
    target_link_libraries(dolly_overlay PUBLIC minhook d3d11 dxgi d3dcompiler dwmapi gdi32)

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
        target_include_directories(overlay_smoke PRIVATE include)
        target_link_libraries(overlay_smoke PRIVATE dolly_overlay minhook)
        add_test(NAME native_dx11_overlay_smoke COMMAND overlay_smoke)
        set_tests_properties(native_dx11_overlay_smoke PROPERTIES TIMEOUT 45)
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
