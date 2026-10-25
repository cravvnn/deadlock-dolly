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

add_library(dolly_path STATIC src/dolly_path.cpp src/dolly_effects.cpp src/dolly_attach.cpp
    src/dolly_visualization.cpp src/dolly_bone_picker.cpp src/dolly_hero_portrait.cpp
    src/dolly_object_picker.cpp src/dolly_object_runtime.cpp)
target_include_directories(dolly_path PUBLIC include)
if(WIN32)
    target_link_libraries(dolly_path PUBLIC windowscodecs ole32)
endif()
add_library(dolly_depth STATIC src/dolly_depth.cpp)
target_include_directories(dolly_depth PUBLIC include)

include(CTest)
if(BUILD_TESTING)
    add_executable(replay_events_tests tests/replay_events_tests.cpp)
    target_include_directories(replay_events_tests PRIVATE include)
    add_test(NAME native_replay_events_tests COMMAND replay_events_tests)
    add_executable(reviewed_contract_tests tests/reviewed_contract_tests.cpp)
    target_include_directories(reviewed_contract_tests PRIVATE include src)
    add_test(NAME native_reviewed_contract_tests COMMAND reviewed_contract_tests)
    add_executable(capability_tests tests/capability_tests.cpp)
    target_include_directories(capability_tests PRIVATE include)
    add_test(NAME native_capability_tests COMMAND capability_tests)
    add_executable(follow_anchor_tests tests/follow_anchor_tests.cpp)
    target_include_directories(follow_anchor_tests PRIVATE include)
    add_test(NAME native_follow_anchor_tests COMMAND follow_anchor_tests)
    find_package(Threads REQUIRED)
    add_executable(player_producer_tests tests/player_producer_tests.cpp)
    target_include_directories(player_producer_tests PRIVATE include)
    target_link_libraries(player_producer_tests PRIVATE Threads::Threads)
    add_test(NAME native_player_producer_tests COMMAND player_producer_tests)
    add_executable(player_capture_lifecycle_tests tests/player_capture_lifecycle_tests.cpp)
    target_include_directories(player_capture_lifecycle_tests PRIVATE include)
    add_test(NAME native_player_capture_lifecycle_tests COMMAND player_capture_lifecycle_tests)
    add_executable(path_tests tests/path_tests.cpp)
    target_link_libraries(path_tests PRIVATE dolly_path)
    add_test(NAME native_path_tests COMMAND path_tests)
    add_executable(effect_tests tests/effect_tests.cpp)
    target_link_libraries(effect_tests PRIVATE dolly_path)
    add_test(NAME native_effect_tests COMMAND effect_tests)
    add_executable(flight_tests tests/flight_tests.cpp)
    target_link_libraries(flight_tests PRIVATE dolly_path)
    add_test(NAME native_flight_tests COMMAND flight_tests)
    add_executable(attach_tests tests/attach_tests.cpp)
    target_link_libraries(attach_tests PRIVATE dolly_path)
    target_compile_definitions(attach_tests PRIVATE
        DOLLY_ATTACH_FIXTURE="${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/attach-shot.bin")
    add_test(NAME native_attach_tests COMMAND attach_tests)
    add_executable(visualization_tests tests/visualization_tests.cpp)
    target_link_libraries(visualization_tests PRIVATE dolly_path)
    add_test(NAME native_visualization_tests COMMAND visualization_tests)
    add_executable(bone_picker_tests tests/bone_picker_tests.cpp)
    target_link_libraries(bone_picker_tests PRIVATE dolly_path)
    add_test(NAME native_bone_picker_tests COMMAND bone_picker_tests)
    add_executable(object_picker_tests tests/object_picker_tests.cpp)
    target_link_libraries(object_picker_tests PRIVATE dolly_path)
    add_test(NAME native_object_picker_tests COMMAND object_picker_tests)
    add_executable(object_runtime_tests tests/object_runtime_tests.cpp)
    target_link_libraries(object_runtime_tests PRIVATE dolly_path)
    add_test(NAME native_object_runtime_tests COMMAND object_runtime_tests)
    add_executable(hero_portrait_tests tests/hero_portrait_tests.cpp)
    target_link_libraries(hero_portrait_tests PRIVATE dolly_path)
    add_test(NAME native_hero_portrait_tests COMMAND hero_portrait_tests)
    add_executable(replay_clock_tests tests/replay_clock_tests.cpp)
    target_include_directories(replay_clock_tests PRIVATE include)
    add_test(NAME native_replay_clock_tests COMMAND replay_clock_tests)
    add_executable(video_math_tests tests/video_math_tests.cpp)
    target_include_directories(video_math_tests PRIVATE include)
    add_test(NAME native_video_math_tests COMMAND video_math_tests)
    add_executable(depth_live_tests tests/depth_live_tests.cpp)
    target_include_directories(depth_live_tests PRIVATE include)
    add_test(NAME native_depth_live_tests COMMAND depth_live_tests)
    add_executable(depth_tests tests/depth_tests.cpp)
    target_link_libraries(depth_tests PRIVATE dolly_depth)
    add_test(NAME native_depth_tests COMMAND depth_tests)
    add_executable(pattern_tests tests/pattern_tests.cpp)
    target_include_directories(pattern_tests PRIVATE include)
    add_test(NAME native_pattern_tests COMMAND pattern_tests)
    add_executable(compat_locator_tests tests/compat_locator_tests.cpp)
    target_include_directories(compat_locator_tests PRIVATE include src)
    add_test(NAME native_compat_locator_tests COMMAND compat_locator_tests)
    add_executable(sound_compat_tests tests/sound_compat_tests.cpp)
    target_include_directories(sound_compat_tests PRIVATE include src)
    add_test(NAME native_sound_compat_tests COMMAND sound_compat_tests)
endif()

if(WIN32)
    if(BUILD_TESTING)
        add_executable(player_scratch_tests tests/player_scratch_tests.cpp)
        target_include_directories(player_scratch_tests PRIVATE include)
        target_compile_definitions(player_scratch_tests PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
        add_test(NAME native_player_scratch_tests COMMAND player_scratch_tests)
        set_tests_properties(native_player_scratch_tests PROPERTIES TIMEOUT 15)
    endif()
    add_executable(DollyGameAudio src/dolly_game_audio.cpp)
    target_link_libraries(DollyGameAudio PRIVATE ole32 mmdevapi)
    set_target_properties(DollyGameAudio PROPERTIES
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/bin/win64"
        RUNTIME_OUTPUT_DIRECTORY_DEBUG "${CMAKE_CURRENT_SOURCE_DIR}/bin/win64"
        RUNTIME_OUTPUT_DIRECTORY_RELEASE "${CMAKE_CURRENT_SOURCE_DIR}/bin/win64")
    if(BUILD_TESTING)
        add_executable(player_matte_pass_tests tests/player_matte_pass_tests.cpp)
        target_include_directories(player_matte_pass_tests PRIVATE include)
        target_link_libraries(player_matte_pass_tests PRIVATE d3d11 d3dcompiler)
        add_test(NAME native_player_matte_pass_tests COMMAND player_matte_pass_tests)
        set_tests_properties(native_player_matte_pass_tests PROPERTIES TIMEOUT 30)
        add_executable(player_vertex_guard_tests tests/player_vertex_guard_tests.cpp)
        target_include_directories(player_vertex_guard_tests PRIVATE include)
        target_link_libraries(player_vertex_guard_tests PRIVATE d3d11 d3dcompiler)
        add_test(NAME native_player_vertex_guard_tests COMMAND player_vertex_guard_tests)
        set_tests_properties(native_player_vertex_guard_tests PROPERTIES TIMEOUT 30)
    endif()
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

    add_library(dolly_environment_source STATIC src/dolly_environment_source_win.cpp
        src/dolly_environment_probe_win.cpp src/dolly_environment_readback_win.cpp
        src/dolly_environment_capture_win.cpp)
    target_include_directories(dolly_environment_source PUBLIC include)
    target_link_libraries(dolly_environment_source PUBLIC d3d11 bcrypt)
    if(BUILD_TESTING)
        add_executable(environment_source_tests tests/environment_source_tests.cpp)
        target_link_libraries(environment_source_tests PRIVATE dolly_environment_source d3dcompiler)
        add_test(NAME native_environment_source_tests COMMAND environment_source_tests)
        set_tests_properties(native_environment_source_tests PROPERTIES TIMEOUT 30)
    endif()
    add_library(dolly_depth_scene STATIC src/dolly_depth_scene_win.cpp src/dolly_depth_hooks_win.cpp
        src/dolly_render_class_win.cpp src/dolly_player_capture.cpp)
    target_link_libraries(dolly_depth_scene PUBLIC dolly_depth_readback dolly_environment_source
        minhook dxguid)
    if(BUILD_TESTING)
        add_executable(environment_metadata_tests tests/environment_metadata_tests.cpp)
        target_link_libraries(environment_metadata_tests PRIVATE dolly_depth_scene d3dcompiler)
        add_test(NAME native_environment_metadata_tests COMMAND environment_metadata_tests)
        set_tests_properties(native_environment_metadata_tests PROPERTIES TIMEOUT 30)
        add_executable(depth_scene_smoke tests/depth_scene_smoke.cpp)
        target_link_libraries(depth_scene_smoke PRIVATE dolly_depth_scene d3dcompiler)
        add_test(NAME native_depth_scene_smoke COMMAND depth_scene_smoke)
        set_tests_properties(native_depth_scene_smoke PROPERTIES TIMEOUT 30)
    endif()

    # Pinned, unmodified Dear ImGui core and official backends. No game assets,
    # online dependency fetches or third-party installer are needed to build.
    add_library(dolly_overlay STATIC
        src/dolly_overlay_win.cpp
        src/dolly_overlay_panel.cpp src/dolly_lite_panel.cpp src/dolly_curve_panel.cpp src/dolly_ui_layout.cpp src/dolly_ui_storage.cpp
        src/dolly_bone_picker_ui.cpp
        src/dolly_object_picker_ui.cpp
        src/dolly_renderer_diagnostics_win.cpp
        src/dolly_video_win.cpp
        src/dolly_reshade_win.cpp
        src/dolly_media_win.cpp
        src/dolly_sound_capture_win.cpp
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
    target_link_libraries(dolly_overlay PUBLIC dolly_path dolly_depth_scene minhook d3d11 dxgi d3dcompiler dwmapi gdi32 mfuuid ole32 shell32)

    add_library(DollyNative SHARED src/bridge_win.cpp src/dolly_editor_win.cpp
        src/dolly_confetti.cpp src/dolly_prop_runtime.cpp)
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
        add_library(module_residency_fixture SHARED tests/module_residency_fixture.cpp)
        target_include_directories(module_residency_fixture PRIVATE include)
        add_executable(module_residency_tests tests/module_residency_tests.cpp)
        add_test(NAME native_module_residency_tests COMMAND module_residency_tests "$<TARGET_FILE:module_residency_fixture>")
        set_tests_properties(native_module_residency_tests PROPERTIES TIMEOUT 10)
        add_executable(bridge_smoke tests/bridge_smoke.cpp src/dolly_confetti.cpp
            src/dolly_prop_runtime.cpp)
        target_compile_definitions(bridge_smoke PRIVATE NOMINMAX)
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
        add_test(NAME native_dx11_lite_campaths COMMAND overlay_smoke --screenshot-lite "${CMAKE_CURRENT_BINARY_DIR}/lite-campaths-test.ppm")
        set_tests_properties(native_dx11_lite_campaths PROPERTIES TIMEOUT 45)
        set_tests_properties(native_dx11_overlay_smoke PROPERTIES TIMEOUT 45)
        # Production-renderer layout checks use a non-activating off-screen
        # window, and keep generated pixels inside the build directory.
        foreach(layout IN ITEMS compact empty paged)
            add_test(NAME native_dx11_layout_${layout}
                COMMAND overlay_smoke --screenshot "${CMAKE_CURRENT_BINARY_DIR}/layout-${layout}.ppm")
            set_tests_properties(native_dx11_layout_${layout} PROPERTIES TIMEOUT 45
                ENVIRONMENT "DOLLY_UI_TEST_HIDDEN=1;DOLLY_UI_TEST_LAYOUT=1;DOLLY_UI_TEST_WIDTH=640;DOLLY_UI_TEST_HEIGHT=480;DOLLY_UI_TEST_CAMERAS=64")
        endforeach()
        set_tests_properties(native_dx11_layout_empty PROPERTIES
            ENVIRONMENT "DOLLY_UI_TEST_HIDDEN=1;DOLLY_UI_TEST_LAYOUT=1;DOLLY_UI_TEST_WIDTH=640;DOLLY_UI_TEST_HEIGHT=480;DOLLY_UI_TEST_CAMERAS=0")
        set_tests_properties(native_dx11_layout_paged PROPERTIES
            ENVIRONMENT "DOLLY_UI_TEST_HIDDEN=1;DOLLY_UI_TEST_LAYOUT=1;DOLLY_UI_TEST_WIDTH=1280;DOLLY_UI_TEST_HEIGHT=720;DOLLY_UI_TEST_CAMERAS=64")
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
            add_test(NAME native_video_cap_smoke COMMAND video_smoke --cap-only)
            set_tests_properties(native_video_cap_smoke PROPERTIES TIMEOUT 40)
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
