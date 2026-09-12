#pragma once
#include <cstdint>
#include <array>
#include "dolly_path.hpp"
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif
namespace dolly {
constexpr std::size_t kEditorConfigOffset = 576;
constexpr std::size_t kEditorStatusOffset = 2 * 1024 * 1024 + 2048;
constexpr std::size_t kEditorInputDiagnosticsOffset = 2 * 1024 * 1024 + 3584;
constexpr std::uint32_t kEditorAbi = 2;
constexpr std::size_t kEditorBindingCount = 26, kEditorEventCount = 16;
enum class EditorOwner : std::uint32_t {
    Disabled = 0,
    Flight = 1,
    Panel = 2,
    GameUI = 3,
    Console = 4,
    Unfocused = 5,
    ReShade = 6
};
enum class EditorAction : std::uint32_t {
    Capture = 0,
    Replace,
    PlayPause,
    PlayPath,
    Stop,
    PreviousView,
    NextView,
    SeekBack,
    SeekForward,
    Panel,
    GameUI,
    Flight,
    MoveForward,
    MoveBack,
    MoveLeft,
    MoveRight,
    MoveUp,
    MoveDown,
    MoveFast,
    MoveSlow,
    LookLeft,
    LookRight,
    LookUp,
    LookDown,
    RollLeft,
    RollRight,
    Console,
    SetSpeed,
    SelectView,
    SetPlaybackSpeed,
    SetPlaybackRate,
    ReShade,
    StartVideo,
    StopVideo,
    SetVideoFps,
    SetVideoBitrate,
    SetVideoEncoder,
    SetVideoFixedStep,
    SetVideoSpeed,
    DestroyRagdolls,
    SetFraming,
    SetDofEnabled,
    SetDofOverride,
    SetDofRangeNearBlurry,
    SetDofRangeNearCrisp,
    SetDofRangeFarCrisp,
    SetDofRangeFarBlurry,
    SetDofNearBlurry,
    SetDofNearCrisp,
    SetDofFarCrisp,
    SetDofFarBlurry,
    SetDofTilt
};
static_assert(static_cast<std::uint32_t>(EditorAction::ReShade) == 31, "Stable editor action IDs");
static_assert(static_cast<std::uint32_t>(EditorAction::StopVideo) == 33, "Stable media action IDs");
static_assert(static_cast<std::uint32_t>(EditorAction::SetVideoSpeed) == 38,
              "Stable video action IDs");
static_assert(static_cast<std::uint32_t>(EditorAction::DestroyRagdolls) == 39,
              "Stable replay cleanup action ID");
#pragma pack(push, 1)
struct EditorBinding {
    std::uint16_t vk, modifiers;
};
struct EditorConfig {
    char magic[8];
    std::uint32_t sequence, abi, enabled, owner, owner_sequence, selected_camera, camera_count,
        ack_event, flags, reserved;
    double speed, sensitivity;
    EditorBinding bindings[kEditorBindingCount];
    char shot_name[96], message[128];
    double duration, playhead;
    std::int32_t replay_tick;
    std::uint32_t playback_flags;
    double playback_speed;
    std::uint32_t playback_rate;
    EditorBinding reshade_binding;
    // Export (recording) settings, published by the desktop Export tab so the
    // in-game Export page can display and change them. These reuse the previous
    // 16 reserved bytes, so the editor ABI and struct size are unchanged.
    std::uint16_t video_fps, video_bitrate_mbps;
    std::uint8_t video_encoder, video_flags; // flags bit 0: fixed-step export
    float video_speed;
    unsigned char padding[6];
};
struct EditorEvent {
    std::uint32_t sequence, action;
    double value;
    double pose[7];
    std::int32_t tick;
    std::uint32_t paused;
};
struct EditorStatus {
    char magic[8];
    std::uint32_t sequence, abi, owner, flags, selected_camera, camera_count, last_event,
        dropped_events;
    double speed;
    double applied_pose[7];
    char message[128];
    double phase;
    std::int32_t tick;
    std::uint32_t reserved;
    std::uint64_t frame_count;
    EditorEvent events[kEditorEventCount];
};
// Optional observational block. Existing editor/camera ABI and offsets stay
// unchanged; older editors ignore these bytes.
struct EditorInputDiagnostics {
    char magic[8];
    std::uint32_t sequence, abi, flags, registration_flags;
    std::uint64_t raw_mouse_packets, relative_mouse_packets, accepted_motion_packets;
    std::uint64_t legacy_mouse_moves, consumed_motion_frames, cursor_syncs, cursor_failures;
    std::uint64_t registration_target;
    std::uint32_t last_cursor_error;
    unsigned char padding[36];
};
constexpr std::size_t kEditorDofOffset = 2 * 1024 * 1024 + 3712;
struct EditorDofConfig {
    char magic[8];
    std::uint32_t sequence, abi, enabled, reserved;
    double values[11];
};
#pragma pack(pop)
static_assert(sizeof(EditorDofConfig) == 112, "Python optional DOF config layout");
static_assert(kEditorInputDiagnosticsOffset + sizeof(EditorInputDiagnostics) == kEditorDofOffset,
              "DOF config follows diagnostics");
static_assert(kEditorDofOffset + sizeof(EditorDofConfig) <= 2 * 1024 * 1024 + 4096,
              "DOF config fits the mapping");
static_assert(static_cast<std::uint32_t>(EditorAction::SetFraming) == 40 &&
                  static_cast<std::uint32_t>(EditorAction::SetDofTilt) == 51,
              "Stable edit actions");
static_assert(sizeof(EditorConfig) == 448, "Python editor configuration layout");
static_assert(offsetof(EditorConfig, playback_speed) == 416, "Python playback speed offset");
static_assert(offsetof(EditorConfig, playback_rate) == 424, "Python playback rate offset");
static_assert(offsetof(EditorConfig, reshade_binding) == 428, "Python ReShade binding offset");
static_assert(offsetof(EditorStatus, events) == 256, "Python editor events offset");
static_assert(sizeof(EditorStatus) == 1536, "Python editor status layout");
static_assert(sizeof(EditorInputDiagnostics) == 128, "Optional input diagnostic layout");
static_assert(offsetof(EditorInputDiagnostics, raw_mouse_packets) == 24, "Input counter offset");
static_assert(offsetof(EditorInputDiagnostics, last_cursor_error) == 88, "Input error offset");
static_assert(kEditorStatusOffset + sizeof(EditorStatus) == kEditorInputDiagnosticsOffset,
              "Input diagnostics follow editor status");
static_assert(kEditorInputDiagnosticsOffset + sizeof(EditorInputDiagnostics) <=
                  2 * 1024 * 1024 + 4096,
              "Input diagnostics fit the existing mapping");
// Status flags: enabled1, focused2, paused4, manual8, ready16, overlay32,
// raw input interception64. Events are a bounded acknowledged ring. Python
// acknowledges only processed events; full queues reject actions visibly.
struct EditorSnapshot {
    EditorOwner owner = EditorOwner::Disabled;
    bool enabled = false, focused = false, manual_active = false, ready = false, paused = false,
         overlay_available = false, input_available = false;
    std::uint32_t selected_camera = 0, camera_count = 0, dropped_events = 0;
    double speed = 400, sensitivity = .08, phase = 0, duration = 0;
    std::int32_t tick = 0;
    bool playing = false, busy = false;
    double playback_speed = 1;
    std::uint32_t playback_rate = 60;
    std::uint32_t video_fps = 60, video_bitrate_mbps = 20, video_codec = 0;
    bool video_fixed_step = false;
    double video_speed = 1;
    double horizontal_fov = 0;
    std::uint32_t view_width = 0, view_height = 0;
    CameraPose pose{};
    bool dof_available = false;
    std::array<double, 11> dof{};
    char shot_name[96]{}, message[128]{};
};
EditorSnapshot editor_snapshot() noexcept;
bool editor_enqueue(EditorAction action, double value = 0,
                    const CameraPose* pose_override = nullptr) noexcept;
bool editor_panel_visible() noexcept;
void editor_set_owner(EditorOwner owner) noexcept;
void editor_overlay_available(bool available) noexcept;
void editor_text_input_active(bool active) noexcept;
bool editor_install_input_hooks() noexcept;
void editor_worker_tick(unsigned char* mapping, bool connected) noexcept;
void editor_update_view(bool replay_ready, bool paused, bool manual, const CameraPose& pose,
                        double phase = 0, std::int32_t tick = 0, double horizontal_fov = 0,
                        std::uint32_t width = 0, std::uint32_t height = 0) noexcept;
void editor_integrate_flight(CameraPose& pose, double delta_seconds) noexcept;
void editor_reset_motion() noexcept;
#ifdef _WIN32
void editor_attach_window(HWND window) noexcept;
bool editor_window_message(HWND window, UINT message, WPARAM wparam, LPARAM lparam,
                           LRESULT& result) noexcept;
#endif
}
