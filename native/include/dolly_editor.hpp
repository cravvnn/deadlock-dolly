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
    SetDofTilt,
    SetVideoDepth,
    SetVideoDepthExr,
    SetVideoLayerWorld,
    SetVideoLayerPlayers,
    SetVideoLayerEffects,
    ToggleCitadelGlow,
    ToggleHealthbars,
    NearPlayerOpacityFix,
    SetCitadelDofEnabled,
    SetCitadelDofSensorSize,
    SetCitadelDofFocusDistance,
    SetAttachTarget,
    SetAttachPoint,
    SetAttachOffsets,
    SetAttachSmoothing,
    SetAttachHide,
    AttachCycleTarget,
    AttachCyclePoint,
    AttachReset,
    AttachPreview,
    AttachSnap,
    SetAttachBone,
    SetSourceBlend,
    SeekShot,
    StepReplayTicks,
    ResetCameraPath,
    SetVideoSource,
    SetPovDuration,
    SetConfettiEnabled,
    SetConfettiSpawnHeight,
    SetConfettiDespawnOnGround
};
static_assert(static_cast<std::uint32_t>(EditorAction::ResetCameraPath) == 77,
              "Stable camera reset action ID");
static_assert(static_cast<std::uint32_t>(EditorAction::ReShade) == 31, "Stable editor action IDs");
static_assert(static_cast<std::uint32_t>(EditorAction::StopVideo) == 33, "Stable media action IDs");
static_assert(static_cast<std::uint32_t>(EditorAction::SetVideoSpeed) == 38,
              "Stable video action IDs");
static_assert(static_cast<std::uint32_t>(EditorAction::DestroyRagdolls) == 39,
              "Stable replay cleanup action ID");
static_assert(static_cast<std::uint32_t>(EditorAction::SetVideoDepth) == 52,
              "Stable depth master action ID");
static_assert(static_cast<std::uint32_t>(EditorAction::SetVideoDepthExr) == 53,
              "Stable depth EXR action ID");
static_assert(static_cast<std::uint32_t>(EditorAction::ToggleCitadelGlow) == 57 &&
                  static_cast<std::uint32_t>(EditorAction::SetCitadelDofFocusDistance) == 62,
              "Stable Citadel action IDs");
static_assert(static_cast<std::uint32_t>(EditorAction::SetAttachTarget) == 63 &&
                  static_cast<std::uint32_t>(EditorAction::AttachSnap) == 72,
              "Stable attach action IDs");
static_assert(static_cast<std::uint32_t>(EditorAction::SeekShot) == 75,
              "Stable shot seek action ID");
static_assert(static_cast<std::uint32_t>(EditorAction::StepReplayTicks) == 76,
              "Stable tick-step action ID");
static_assert(static_cast<std::uint32_t>(EditorAction::SetConfettiDespawnOnGround) == 82,
              "Stable confetti action IDs");
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
    float pov_duration;
    unsigned char padding[2];
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
// Citadel DOF follows the native DOF block. `available` means a camera exists
// to author against; `enabled` is the authored game switch itself.
constexpr std::size_t kEditorCitadelDofOffset = kEditorDofOffset + sizeof(EditorDofConfig);
struct EditorCitadelDofConfig {
    char magic[8];
    std::uint32_t sequence, abi, available, enabled;
    double sensor_size, focus_distance;
};
// Attach-camera runtime offsets follow the Citadel DOF block. The desktop
// editor queries the live schema once per session and republishes it; the
// native attach provider refuses attach keys while this block is absent.
constexpr std::size_t kEditorAttachOffset =
    kEditorCitadelDofOffset + sizeof(EditorCitadelDofConfig);
struct EditorAttachConfig {
    char magic[8];
    std::uint32_t sequence, abi, flags, reserved;
    std::uint64_t model;
    // scene_node, owner, origin, angles, view_offset, eye_angles, child, sibling
    std::uint32_t offsets[8];
    // ABI 3 uses reserved2 for source blend milliseconds (0..10000).
    std::uint32_t handle, entity_id, target_index, point, hide, reserved2;
    double offset[6];
    double smoothing;
    std::uint32_t attached_keys, key_count;
    // Monotonic snap requests from the editor; the preview callback consumes
    // changes while the free camera is active.
    std::uint32_t snap_request;
    std::uint64_t bone_hash;
    char bone_name[64]; // ASCII, full-width names need not have a terminator.
};
// Native -> editor live player roster for the attach target picker. The bridge
// worker writes it with the same seqlock discipline as the status block; the
// mapping is 4 KiB larger than the editor blocks it follows.
constexpr std::size_t kEditorRosterOffset = 2 * 1024 * 1024 + 4096;
constexpr std::uint32_t kEditorRosterAbi = 1;
constexpr std::size_t kEditorRosterPlayers = 16;
struct EditorRosterEntry {
    std::uint32_t handle, entity_index;
    std::uint64_t model;
    char model_path[96];
};
struct EditorRoster {
    char magic[8];
    std::uint32_t sequence, abi, count, flags; // flags bit 0: offsets available
    EditorRosterEntry players[kEditorRosterPlayers];
};
// Native -> editor attach result: authored offsets computed by the snap action
// or edited by attached fly. Written by the bridge with the status seqlock.
constexpr std::size_t kEditorAttachResultOffset = kEditorRosterOffset + sizeof(EditorRoster);
constexpr std::uint32_t kEditorAttachResultAbi = 1;
struct EditorAttachResult {
    char magic[8];
    std::uint32_t sequence, abi, flags, reserved; // flags bit 0: valid offsets
    std::uint32_t handle, entity_index, point, reserved2;
    std::uint64_t model;
    double offset[6];
    double smoothing;
};
constexpr std::size_t kEditorBonesOffset = kEditorAttachResultOffset + sizeof(EditorAttachResult);
constexpr std::size_t kEditorBoneCount = 256;
struct EditorBones {
    char magic[8];
    std::uint32_t sequence, abi, handle, entity_index;
    std::uint64_t model;
    std::uint32_t count, total;
    char names[kEditorBoneCount][64];
};
#pragma pack(pop)
static_assert(sizeof(EditorDofConfig) == 112, "Python optional DOF config layout");
static_assert(sizeof(EditorCitadelDofConfig) == 40, "Python optional Citadel DOF config layout");
static_assert(sizeof(EditorAttachConfig) == 228, "Python optional attach config layout");
static_assert(kEditorAttachOffset + sizeof(EditorAttachConfig) <= 2 * 1024 * 1024 + 4096,
              "Attach config fits the mapping");
static_assert(sizeof(EditorRosterEntry) == 112, "Python attach roster entry layout");
static_assert(sizeof(EditorRoster) == 1816, "Python attach roster layout");
static_assert(sizeof(EditorAttachResult) == 104, "Python attach result layout");
static_assert(sizeof(EditorBones) == 16424, "Python bone picker layout");
static_assert(kEditorBonesOffset + sizeof(EditorBones) <= 2 * 1024 * 1024 + 24576,
              "Bone picker fits the mapping");
static_assert(kEditorRosterOffset + sizeof(EditorRoster) <= 2 * 1024 * 1024 + 8192,
              "Attach roster fits the enlarged mapping");
static_assert(kEditorAttachResultOffset + sizeof(EditorAttachResult) <= 2 * 1024 * 1024 + 8192,
              "Attach result fits the enlarged mapping");
static_assert(kEditorCitadelDofOffset + sizeof(EditorCitadelDofConfig) <= 2 * 1024 * 1024 + 4096,
              "Citadel DOF config fits the mapping");
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
    double speed = 400, sensitivity = .08, phase = 0, duration = 0, playhead = 0;
    std::int32_t tick = 0;
    bool playing = false, busy = false;
    double playback_speed = 1;
    std::uint32_t playback_rate = 60;
    std::uint32_t video_fps = 60, video_bitrate_mbps = 20, video_codec = 0;
    bool video_fixed_step = false;
    bool video_depth = false;
    bool video_pov = false;
    double pov_duration = 10;
    bool video_depth_exr = false;
    bool video_layer_world = false;
    bool video_layer_players = false;
    bool video_layer_effects = false;
    double video_speed = 1;
    double horizontal_fov = 0;
    std::uint32_t view_width = 0, view_height = 0;
    CameraPose pose{};
    bool dof_available = false;
    std::array<double, 11> dof{};
    bool citadel_dof_available = false, citadel_dof_enabled = false;
    double citadel_dof_sensor = 1.0, citadel_dof_focus = 200.0;
    bool confetti_enabled = false, confetti_despawn_on_ground = false;
    double confetti_spawn_height = 250.0;
    bool attach_available = false, attach_selected = false, attach_hide = true;
    bool attach_preview = false;
    std::uint32_t attach_point = 0, attach_target_index = 0, roster_count = 0;
    char attach_bone[65]{};
    double attach_offsets[6] = {}, attach_smoothing = 0, source_blend = 0;
    std::uint32_t attach_keys = 0, shot_keys = 0;
    char shot_name[96]{}, message[128]{};
};
EditorSnapshot editor_snapshot() noexcept;
// Published attach-camera schema offsets; false while no valid block arrived.
bool editor_attach_config(EditorAttachConfig& out) noexcept;
bool editor_bones_snapshot(EditorBones& out) noexcept;
// Latest native player roster; false while no valid block arrived.
bool editor_roster_snapshot(EditorRoster& out) noexcept;
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
bool flight_movement_active(unsigned view_flags, EditorOwner owner, bool input_owned) noexcept;
void editor_integrate_flight(CameraPose& pose, double delta_seconds) noexcept;
void editor_reset_motion() noexcept;
#ifdef _WIN32
void editor_attach_window(HWND window) noexcept;
bool editor_window_message(HWND window, UINT message, WPARAM wparam, LPARAM lparam,
                           LRESULT& result) noexcept;
#endif
}
