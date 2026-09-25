#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <atomic>
#include <algorithm>
#include <cmath>
#include <cstring>
#include <cstdio>
#include <memory>
#include "MinHook.h"
#include "dolly_editor.hpp"
#include "dolly_bone_picker.hpp"
#include "dolly_flight.hpp"
#include "dolly_overlay.hpp"
#include "dolly_reshade.hpp"
#include "dolly_visualization_runtime.hpp"
namespace dolly {
namespace {
std::atomic<HWND> gWindow{nullptr};
std::atomic<bool> gConnected{false}, gOverlay{false}, gInput{false}, gFocused{false};
std::atomic<EditorOwner> gOwner{EditorOwner::Disabled};
// Unfocused is never an assigned owner and marks an inactive menu handoff.
std::atomic<EditorOwner> gReShadePriorOwner{EditorOwner::Unfocused};
// Deferred F7/F8/F9 transitions run only after the actual ReShade menu closes.
std::atomic<unsigned> gReShadeDeferred{0};
std::shared_ptr<const EditorConfig> gConfig;
std::shared_ptr<const EditorDofConfig> gDofConfig;
std::shared_ptr<const EditorCitadelDofConfig> gCitadelDofConfig;
std::shared_ptr<const EditorAttachConfig> gAttachConfig;
std::shared_ptr<const EditorRoster> gRoster;
std::shared_ptr<const EditorBones> gBones;
std::atomic<double> gSpeed{400};
std::atomic<long> gMouseX{0}, gMouseY{0}, gFramingWheel{0};
// Live wheel-burst tracking; camera -2 means no burst has been applied yet.
struct FramingWheelState {
    int camera = -2;
    double value = 0;
    double stored = 0;
};
FramingWheelState gFramingWheelState;
std::atomic<bool> gKeys[256]{}, gBlocked[256]{}, gFocusBlocked[256]{};
std::atomic<bool> gNoLegacyMouse{false}, gTextInput{false};
std::atomic<std::uint64_t> gRawMousePackets{0}, gRelativeMousePackets{0}, gAcceptedMotionPackets{0};
std::atomic<std::uint64_t> gLegacyMouseMoves{0}, gConsumedMotionFrames{0};
std::atomic<std::uint64_t> gCursorSyncs{0}, gCursorFailures{0}, gCursorRefresh{1};
std::atomic<DWORD> gLastCursorError{0};
std::atomic<bool> gCursorClipped{false};
bool gRawRegistrationKnown = false, gRawMouseRegistered = false;
DWORD gRawRegistrationFlags = 0;
HWND gRawRegistrationTarget = nullptr;
std::atomic_flag gEventLock = ATOMIC_FLAG_INIT;
EditorEvent gEvents[kEditorEventCount]{};
std::uint32_t gLastEvent = 0, gAcknowledged = 0;
std::atomic<std::uint32_t> gDropped{0};
std::atomic<std::uint32_t> gViewSequence{0}, gViewFlags{0};
std::atomic<double> gPose[7]{}, gPhase{0};
std::atomic<double> gHorizontalFov{0};
std::atomic<std::uint32_t> gViewWidth{0}, gViewHeight{0};
std::atomic<std::int32_t> gTick{0};
std::atomic<std::uint64_t> gFrame{0};
using RawDataFn = UINT(WINAPI*)(HRAWINPUT, UINT, LPVOID, PUINT, UINT);
using RawBufferFn = UINT(WINAPI*)(PRAWINPUT, PUINT, UINT);
using SetCursorPosFn = BOOL(WINAPI*)(int, int);
using ClipCursorFn = BOOL(WINAPI*)(const RECT*);
RawDataFn gRawData = nullptr;
RawBufferFn gRawBuffer = nullptr;
SetCursorPosFn gSetCursorPos = nullptr;
ClipCursorFn gClipCursor = nullptr;
std::atomic_flag gCursorLock = ATOMIC_FLAG_INIT;
bool key_down(unsigned vk) noexcept {
    return vk < 256 && gKeys[vk].load(std::memory_order_relaxed) &&
           !gBlocked[vk].load(std::memory_order_relaxed);
}
bool focused() noexcept {
    auto window = gWindow.load();
    return window && GetForegroundWindow() == window;
}
bool configured() noexcept {
    auto c = std::atomic_load(&gConfig);
    return c && c->enabled && gConnected.load() && gOverlay.load() && gInput.load();
}
bool owns_input() noexcept {
    if (!configured() || !focused() || reshade_overlay_open())
        return false;
    auto owner = gOwner.load();
    return (owner == EditorOwner::Flight || owner == EditorOwner::Panel) && (gViewFlags.load() & 1);
}
void reset_keys(bool lost_input = false) noexcept {
    gFramingWheel = 0;
    for (unsigned i = 0; i < 256; ++i) {
        const bool held = (GetAsyncKeyState(int(i)) & 0x8000) != 0;
        if (lost_input) {
            gKeys[i] = false;
            gBlocked[i] = held;
            gFocusBlocked[i] = held;
        } else if (gKeys[i].load() || held) {
            gBlocked[i] = true;
        }
    }
    // Ownership changes must preserve observed down edges. Windows can deliver
    // the same press through raw and legacy input, followed by auto-repeat. A
    // reset between those messages used to turn one F7/F8 press into many.
    gMouseX = 0;
    gMouseY = 0;
}
void unblock_released() noexcept {
    // The worker only clears old-focus blocks; it never creates input from keys
    // pressed while unfocused. Actual movement comes from the game's messages.
    for (unsigned i = 1; i < 256; ++i)
        if (gFocusBlocked[i].load() && !(GetAsyncKeyState(int(i)) & 0x8000)) {
            gBlocked[i] = false;
            gFocusBlocked[i] = false;
        }
}
bool cursor_mode(EditorOwner owner) noexcept {
    if (gCursorLock.test_and_set(std::memory_order_acquire))
        return false;
    HWND window = gWindow.load();
    bool applied = true;
    if (gClipCursor) {
        ++gCursorSyncs;
        if (window && focused() && configured() && owner == EditorOwner::Flight &&
            (gViewFlags.load() & 1)) {
            RECT r{};
            POINT origin{};
            applied = GetClientRect(window, &r) && ClientToScreen(window, &origin);
            if (applied) {
                OffsetRect(&r, origin.x, origin.y);
                applied = gClipCursor(&r) != FALSE;
            }
            if (applied)
                gCursorClipped = true;
        } else {
            applied = gClipCursor(nullptr) != FALSE;
            if (applied)
                gCursorClipped = false;
        }
        if (!applied) {
            gLastCursorError = GetLastError();
            ++gCursorFailures;
        } else
            gLastCursorError = 0;
    }
    gCursorLock.clear(std::memory_order_release);
    return applied;
}
void reconcile_cursor_mode() noexcept {
    // A launch can request Flight before the first replay view is ready. The
    // render callback marks that transition; only this worker performs the OS
    // operation. Reasserting Flight does not clear held movement or mouse deltas.
    static std::uint64_t applied_refresh = 0;
    const auto refresh = gCursorRefresh.load();
    if (refresh != applied_refresh && cursor_mode(gOwner.load()))
        applied_refresh = refresh;
}
unsigned modifiers() noexcept {
    return ((key_down(VK_CONTROL) || key_down(VK_LCONTROL) || key_down(VK_RCONTROL)) ? 1 : 0) |
           ((key_down(VK_MENU) || key_down(VK_LMENU) || key_down(VK_RMENU)) ? 2 : 0) |
           ((key_down(VK_SHIFT) || key_down(VK_LSHIFT) || key_down(VK_RSHIFT)) ? 4 : 0);
}
bool binding_down(const EditorBinding& b, bool movement = false) noexcept {
    if (!b.vk || !key_down(b.vk))
        return false;
    auto actual = modifiers();
    if (movement && b.modifiers == 0)
        return true;
    return actual == b.modifiers;
}
bool function_key(unsigned vk) noexcept {
    return vk >= VK_F1 && vk <= VK_F24;
}
bool reserved_input(unsigned vk) noexcept {
    if (!configured() || !focused())
        return false;
    if (vk == VK_F7)
        return true;
    auto owner = gOwner.load();
    if (owner == EditorOwner::Console && vk == VK_ESCAPE)
        return true;
    auto c = std::atomic_load(&gConfig);
    if (!c)
        return false;
    if (owner != EditorOwner::Console && c->reshade_binding.vk == vk &&
        c->reshade_binding.modifiers == modifiers())
        return true;
    for (unsigned i : {9u, 10u})
        if ((owner != EditorOwner::Console || (i == 9 && function_key(vk))) &&
            c->bindings[i].vk == vk && c->bindings[i].modifiers == modifiers())
            return true;
    return false;
}
bool reshade_owner_tracked() noexcept {
    return gReShadePriorOwner.load() != EditorOwner::Unfocused;
}
void track_reshade_owner() noexcept {
    auto owner = gOwner.load();
    auto expected = EditorOwner::Unfocused;
    gReShadePriorOwner.compare_exchange_strong(
        expected, owner == EditorOwner::ReShade ? EditorOwner::Panel : owner);
    editor_set_owner(EditorOwner::ReShade);
}
bool close_reshade_for(unsigned transition) noexcept {
    if (gOwner.load() != EditorOwner::ReShade && !reshade_overlay_open() &&
        !reshade_overlay_pending())
        return false;
    if (reshade_request_overlay(false))
        gReShadeDeferred = transition;
    return true;
}
void toggle_reshade() noexcept {
    if (gReShadeDeferred.load())
        return;
    const bool opening = gOwner.load() != EditorOwner::ReShade && !reshade_overlay_open();
    if (reshade_request_overlay(opening) && opening)
        track_reshade_owner();
}
void dispatch(EditorAction action) noexcept {
    auto owner = gOwner.load();
    if (action == EditorAction::ReShade) {
        toggle_reshade();
        return;
    }
    if (action == EditorAction::Panel) {
        if (close_reshade_for(2))
            return;
        if (owner == EditorOwner::Console) {
            editor_enqueue(EditorAction::Panel, 1);
            return;
        }
        if (owner == EditorOwner::GameUI) {
            editor_enqueue(EditorAction::Panel, 1);
            return;
        }
        if (owner == EditorOwner::Panel) {
            const auto state = editor_snapshot();
            // A held endpoint or Stop has no manual camera writer. Merely
            // hiding the panel steals game input without moving the camera.
            // Return through the same acknowledged flight handoff as the
            // Fly camera button; keep recovery controls visible until ready.
            // A missing/coherency-retry view cannot confirm a usable camera.
            // Keep the panel available for recovery unless a known shot is
            // running, when F8 should remain only a visibility control.
            if (state.busy || (!state.ready && !state.playing))
                return;
            if (state.ready && state.paused && !state.manual_active && !state.playing) {
                editor_enqueue(EditorAction::Flight);
                return;
            }
            // Active manual flight needs no new command, and closing the
            // panel during playback must not interrupt the running shot.
            editor_set_owner(EditorOwner::Flight);
        } else {
            const auto state = editor_snapshot();
            if (state.ready && !state.manual_active && !state.playing) {
                editor_enqueue(EditorAction::Panel, 1);
                return;
            }
            editor_set_owner(EditorOwner::Panel);
        }
        return;
    }
    if (action == EditorAction::GameUI) {
        if (close_reshade_for(3))
            return;
        bool open = owner != EditorOwner::GameUI;
        if (editor_enqueue(action, open ? 1 : 0) && open)
            editor_set_owner(EditorOwner::GameUI);
        return;
    }
    editor_enqueue(action);
}
void dispatch_key_press(unsigned vk) noexcept {
    // Raw-input and window-message callers check foreground ownership before
    // recording a key. Shortcuts run only on that key's fresh down edge, never
    // when an unrelated modifier changes or a held key sends another message.
    if (!configured())
        return;
    if (vk == VK_F7) {
        if (close_reshade_for(1))
            return;
        auto owner = gOwner.load();
        bool open = owner != EditorOwner::Console;
        if (editor_enqueue(EditorAction::Console, open ? 1 : 0)) {
            // Opening suspends input immediately. Closing retains Console ownership
            // until the controller confirms hideconsole and requests Panel ownership.
            if (open)
                editor_set_owner(EditorOwner::Console);
        }
        return;
    }
    auto owner = gOwner.load();
    auto c = std::atomic_load(&gConfig);
    if (!c)
        return;
    if (owner != EditorOwner::Console && c->reshade_binding.vk == vk &&
        binding_down(c->reshade_binding)) {
        toggle_reshade();
        return;
    }
    for (unsigned i = 0; i < 12; ++i) {
        if ((owner == EditorOwner::ReShade || reshade_overlay_open()) && i != 9 && i != 10)
            continue;
        if (owner == EditorOwner::Console && (i != 9 || !function_key(vk)))
            continue;
        if (owner == EditorOwner::Panel && gTextInput.load() &&
            ((i != 9 && i != 10) || !function_key(vk)))
            continue;
        // Game UI passes ordinary controls through; only its explicit return key
        // and panel toggle are editor shortcuts in that mode.
        if (owner == EditorOwner::GameUI && i != 9 && i != 10)
            continue;
        if (c->bindings[i].vk == vk && binding_down(c->bindings[i])) {
            gMouseX = 0;
            gMouseY = 0;
            dispatch(static_cast<EditorAction>(i));
            return;
        }
    }
}
void key_event(unsigned vk, bool down) noexcept {
    if (vk >= 256)
        return;
    bool was_down = gKeys[vk].exchange(down);
    if (vk == VK_ESCAPE && down && !was_down && !gBlocked[vk].load() &&
        gOwner.load() == EditorOwner::Console) {
        editor_enqueue(EditorAction::Console, 0); // release input only after confirmed hideconsole
    }
    if (!down) {
        gBlocked[vk] = false;
        gFocusBlocked[vk] = false;
    }
    // Win32 raw-input keyboard VKs distinguish left/right modifier keys while
    // normal window messages often use the generic VK. Keep generic matches.
    if (vk == VK_LCONTROL || vk == VK_RCONTROL)
        gKeys[VK_CONTROL] = down || gKeys[vk == VK_LCONTROL ? VK_RCONTROL : VK_LCONTROL].load();
    if (vk == VK_LMENU || vk == VK_RMENU)
        gKeys[VK_MENU] = down || gKeys[vk == VK_LMENU ? VK_RMENU : VK_LMENU].load();
    if (vk == VK_LSHIFT || vk == VK_RSHIFT)
        gKeys[VK_SHIFT] = down || gKeys[vk == VK_LSHIFT ? VK_RSHIFT : VK_LSHIFT].load();
    if (down && !was_down && !gBlocked[vk].load())
        dispatch_key_press(vk);
}
void framing_wheel(long units) noexcept {
    auto c = std::atomic_load(&gConfig);
    if (!owns_input() || gOwner.load() != EditorOwner::Flight || (gViewFlags.load() & 6) != 6 ||
        !c || c->playback_flags)
        return;
    // Bound backlog even when the game stops producing views. Partial wheel
    // detents are retained, including high-resolution mouse/trackpad input.
    auto previous = gFramingWheel.load();
    while (!gFramingWheel.compare_exchange_weak(
        previous, std::clamp<long>(previous + units, -12000, 12000))) {
    }
}
void process_raw(RAWINPUT* raw, bool suppress) noexcept {
    if (!raw)
        return;
    if (raw->header.dwType == RIM_TYPEMOUSE &&
        raw->header.dwSize >= sizeof(RAWINPUTHEADER) + sizeof(RAWMOUSE)) {
        auto& m = raw->data.mouse;
        if (configured() && focused()) {
            ++gRawMousePackets;
            if (!(m.usFlags & MOUSE_MOVE_ABSOLUTE))
                ++gRelativeMousePackets;
            if (gOwner.load() == EditorOwner::Flight && !reshade_overlay_open() &&
                !(m.usFlags & MOUSE_MOVE_ABSOLUTE) && (gViewFlags.load() & 4)) {
                // Never accumulate motion while blocked by console/UI or stalled frames.
                auto clamp = [](LONG value) { return std::clamp<LONG>(value, -2000, 2000); };
                gMouseX.fetch_add(clamp(m.lLastX));
                gMouseY.fetch_add(clamp(m.lLastY));
                if (m.lLastX || m.lLastY)
                    ++gAcceptedMotionPackets;
            }
            const USHORT downs[] = {RI_MOUSE_LEFT_BUTTON_DOWN, RI_MOUSE_RIGHT_BUTTON_DOWN,
                                    RI_MOUSE_MIDDLE_BUTTON_DOWN, RI_MOUSE_BUTTON_4_DOWN,
                                    RI_MOUSE_BUTTON_5_DOWN};
            const USHORT ups[] = {RI_MOUSE_LEFT_BUTTON_UP, RI_MOUSE_RIGHT_BUTTON_UP,
                                  RI_MOUSE_MIDDLE_BUTTON_UP, RI_MOUSE_BUTTON_4_UP,
                                  RI_MOUSE_BUTTON_5_UP};
            const unsigned keys[] = {VK_LBUTTON, VK_RBUTTON, VK_MBUTTON, VK_XBUTTON1, VK_XBUTTON2};
            for (int i = 0; i < 5; ++i) {
                if (m.usButtonFlags & downs[i]) {
                    bool reserved = reserved_input(keys[i]);
                    if (gOwner.load() == EditorOwner::Panel)
                        overlay_raw_mouse_button(i, true);
                    key_event(keys[i], true);
                    if (reserved)
                        m.usButtonFlags &= ~downs[i];
                }
                if (m.usButtonFlags & ups[i]) {
                    bool reserved = reserved_input(keys[i]);
                    if (gOwner.load() == EditorOwner::Panel)
                        overlay_raw_mouse_button(i, false);
                    key_event(keys[i], false);
                    if (reserved)
                        m.usButtonFlags &= ~ups[i];
                }
            }
            if (gNoLegacyMouse.load() && (m.usButtonFlags & RI_MOUSE_WHEEL))
                framing_wheel(static_cast<SHORT>(m.usButtonData));
            if (gOwner.load() == EditorOwner::Panel && gNoLegacyMouse.load()) {
                float wheel = float(static_cast<SHORT>(m.usButtonData)) / WHEEL_DELTA;
                if (m.usButtonFlags & RI_MOUSE_WHEEL)
                    overlay_raw_mouse_wheel(wheel, 0);
                if (m.usButtonFlags & RI_MOUSE_HWHEEL)
                    overlay_raw_mouse_wheel(0, wheel);
            }
        }
        if (suppress) {
            m.lLastX = 0;
            m.lLastY = 0;
            m.usButtonFlags = 0;
            m.usButtonData = 0;
            m.ulRawButtons = 0;
        }
    } else if (raw->header.dwType == RIM_TYPEKEYBOARD &&
               raw->header.dwSize >= sizeof(RAWINPUTHEADER) + sizeof(RAWKEYBOARD)) {
        auto& k = raw->data.keyboard;
        bool reserved = reserved_input(k.VKey);
        if (configured() && focused()) {
            if (gOwner.load() == EditorOwner::Panel)
                overlay_raw_key(k.VKey, k.MakeCode, !(k.Flags & RI_KEY_BREAK),
                                (k.Flags & RI_KEY_E0) != 0);
            key_event(k.VKey, !(k.Flags & RI_KEY_BREAK));
        }
        if (suppress || reserved) {
            k.MakeCode = 0;
            k.VKey = 0;
            k.Flags = RI_KEY_BREAK;
            k.Message = WM_KEYUP;
        }
    }
}
UINT WINAPI raw_data_hook(HRAWINPUT handle, UINT command, LPVOID data, PUINT size, UINT header) {
    UINT result = gRawData(handle, command, data, size, header);
    if (command == RID_INPUT && data && header == sizeof(RAWINPUTHEADER) && result != UINT(-1) &&
        result >= sizeof(RAWINPUTHEADER)) {
        auto raw = static_cast<RAWINPUT*>(data);
        if (raw->header.dwSize <= result)
            process_raw(raw, owns_input());
    }
    return result;
}
UINT WINAPI raw_buffer_hook(PRAWINPUT data, PUINT size, UINT header) {
    UINT capacity = size ? *size : 0;
    UINT result = gRawBuffer(data, size, header);
    if (data && header == sizeof(RAWINPUTHEADER) && result != UINT(-1)) {
        auto begin = reinterpret_cast<unsigned char*>(data);
        auto end = begin + capacity;
        for (UINT i = 0; i < result; ++i) {
            if (begin + sizeof(RAWINPUTHEADER) > end)
                break;
            auto raw = reinterpret_cast<RAWINPUT*>(begin);
            if (raw->header.dwSize < sizeof(RAWINPUTHEADER) ||
                raw->header.dwSize > std::size_t(end - begin))
                break;
            process_raw(raw, owns_input());
            auto next = reinterpret_cast<std::uintptr_t>(begin) + raw->header.dwSize;
            begin = reinterpret_cast<unsigned char*>((next + sizeof(std::uintptr_t) - 1) &
                                                     ~(sizeof(std::uintptr_t) - 1));
        }
    }
    return result;
}
BOOL WINAPI set_cursor_pos_hook(int x, int y) {
    if (owns_input() && gOwner.load() == EditorOwner::Panel)
        return TRUE;
    return gSetCursorPos(x, y);
}
BOOL WINAPI clip_cursor_hook(const RECT* rect) {
    if (owns_input() && gOwner.load() == EditorOwner::Panel)
        return gClipCursor(nullptr);
    return gClipCursor(rect);
}
} // namespace
EditorSnapshot editor_snapshot() noexcept {
    EditorSnapshot result{};
    auto c = std::atomic_load(&gConfig);
    if (c) {
        result.enabled = c->enabled && gConnected.load();
        result.selected_camera = c->selected_camera;
        result.camera_count = c->camera_count;
        result.sensitivity = c->sensitivity;
        result.duration = c->duration;
        result.playhead = c->playhead;
        result.playing = (c->playback_flags & 1) != 0;
        result.busy = (c->playback_flags & 2) != 0;
        std::memcpy(result.shot_name, c->shot_name, sizeof(result.shot_name));
        std::memcpy(result.message, c->message, sizeof(result.message));
    }
    if (c) {
        if (std::isfinite(c->playback_speed) && c->playback_speed >= .05 && c->playback_speed <= 4)
            result.playback_speed = c->playback_speed;
        if (c->playback_rate == 30 || c->playback_rate == 60 || c->playback_rate == 120)
            result.playback_rate = c->playback_rate;
        if (c->video_fps == 30 || c->video_fps == 60 || c->video_fps == 120 ||
            c->video_fps == 300 || c->video_fps == 600)
            result.video_fps = c->video_fps;
        if (c->video_bitrate_mbps == 10 || c->video_bitrate_mbps == 20 ||
            c->video_bitrate_mbps == 40)
            result.video_bitrate_mbps = c->video_bitrate_mbps;
        if (c->video_encoder <= 10)
            result.video_codec = c->video_encoder;
        result.video_fixed_step = (c->video_flags & 1) != 0;
        result.video_depth = (c->video_flags & 2) != 0;
        result.video_pov = (c->video_flags & 64) != 0;
        if (std::isfinite(c->pov_duration) && c->pov_duration >= .1f && c->pov_duration <= 120)
            result.pov_duration = c->pov_duration;
        result.video_depth_exr = (c->video_flags & 4) != 0;
        result.video_layer_world = (c->video_flags & 8) != 0;
        result.video_layer_players = (c->video_flags & 16) != 0;
        result.video_layer_effects = (c->video_flags & 32) != 0;
        const auto confetti = c->reserved;
        const auto confetti_height = confetti & 0xffffu;
        result.confetti_enabled = (confetti & (1u << 16)) != 0;
        result.confetti_despawn_on_ground = (confetti & (1u << 17)) != 0;
        if (confetti_height >= 100 && confetti_height <= 1500)
            result.confetti_spawn_height = double(confetti_height);
        if (std::isfinite(c->video_speed) && c->video_speed >= .05f && c->video_speed <= 4.0f)
            result.video_speed = c->video_speed;
    }
    // Focus and ownership are separate facts. A desktop error dialog must not
    // erase which UI owns input, or recovery can steal the game's mouse/console.
    result.focused = focused();
    result.owner = gOwner.load();
    result.speed = gSpeed.load();
    result.overlay_available = gOverlay.load();
    result.input_available = gInput.load();
    result.dropped_events = gDropped.load();
    auto dof = std::atomic_load(&gDofConfig);
    if (dof && dof->enabled && gConnected.load()) {
        result.dof_available = true;
        std::copy(std::begin(dof->values), std::end(dof->values), result.dof.begin());
    }
    auto citadel = std::atomic_load(&gCitadelDofConfig);
    if (citadel && citadel->available && gConnected.load()) {
        result.citadel_dof_available = true;
        result.citadel_dof_enabled = citadel->enabled != 0;
        result.citadel_dof_sensor = citadel->sensor_size;
        result.citadel_dof_focus = citadel->focus_distance;
    }
    auto attach = std::atomic_load(&gAttachConfig);
    if (attach && (attach->flags & 1) && gConnected.load()) {
        result.attach_available = true;
        result.attach_selected = (attach->flags & 2) != 0;
        result.attach_preview = (attach->flags & 4) != 0;
        result.bone_picker = (attach->flags & 8) != 0;
        result.attach_hide = (attach->hide & 1) != 0;
        result.attach_auto_clearance = (attach->hide & 2) != 0;
        result.attach_point = attach->point;
        std::memcpy(result.attach_bone, attach->bone_name, 64);
        result.attach_target_index = attach->target_index;
        std::copy(std::begin(attach->offset), std::end(attach->offset), result.attach_offsets);
        result.attach_smoothing = attach->smoothing;
        result.source_blend = attach->reserved2 / 1000.0;
        result.attach_keys = attach->attached_keys;
        result.shot_keys = attach->key_count;
    }
    auto roster = std::atomic_load(&gRoster);
    if (roster && gConnected.load())
        result.roster_count = roster->count;
    // Atomic field seqlock gives an internally coherent capture pose and tick.
    // Bounded retries never wait for the render callback.
    for (int attempt = 0; attempt < 4; ++attempt) {
        auto before = gViewSequence.load(std::memory_order_acquire);
        if (before & 1)
            continue;
        auto flags = gViewFlags.load();
        result.ready = (flags & 1) != 0;
        result.paused = (flags & 2) != 0;
        result.manual_active = (flags & 4) != 0;
        for (unsigned i = 0; i < 7; ++i)
            result.pose[i] = gPose[i].load();
        result.phase = gPhase.load();
        result.tick = gTick.load();
        result.horizontal_fov = gHorizontalFov.load();
        result.view_width = gViewWidth.load();
        result.view_height = gViewHeight.load();
        if (before == gViewSequence.load(std::memory_order_acquire))
            return result;
    }
    result.ready = false;
    return result;
}
bool editor_attach_config(EditorAttachConfig& out) noexcept {
    auto attach = std::atomic_load(&gAttachConfig);
    if (!attach || !gConnected.load())
        return false;
    out = *attach;
    return true;
}
bool editor_roster_snapshot(EditorRoster& out) noexcept {
    auto roster = std::atomic_load(&gRoster);
    if (!roster || !gConnected.load())
        return false;
    out = *roster;
    return true;
}
bool editor_bones_snapshot(EditorBones& out) noexcept {
    auto bones = std::atomic_load(&gBones);
    if (!bones || !gConnected.load())
        return false;
    out = *bones;
    return true;
}
bool editor_enqueue(EditorAction action, double value, const CameraPose* pose_override) noexcept {
    // Menu requests may originate from a render-thread button. They only queue
    // adapter work here; the worker changes input/cursor ownership afterwards.
    if (action == EditorAction::ReShade)
        return configured() && !gReShadeDeferred.load() &&
               reshade_request_overlay(!reshade_overlay_open());
    if (!std::isfinite(value) ||
        std::uint32_t(action) > std::uint32_t(EditorAction::FinishBonePicker))
        return false;
    auto state = editor_snapshot();
    if (!state.enabled)
        return false;
    // While inspecting, only recovery/ownership and picker actions may escape
    // to Python. In particular no capture, seek or recording of this view.
    if (state.bone_picker && action != EditorAction::CancelBonePicker &&
        action != EditorAction::FinishBonePicker && action != EditorAction::GameUI &&
        action != EditorAction::Console && action != EditorAction::Panel &&
        action != EditorAction::Stop)
        return false;
    if (action == EditorAction::OpenBonePicker &&
        (!state.attach_selected || !state.ready || !state.manual_active || !state.paused ||
         state.busy || state.playing || state.owner != EditorOwner::Panel || value != 0))
        return false;
    if (action == EditorAction::CancelBonePicker && (!state.bone_picker || value != 0))
        return false;
    if (action == EditorAction::FinishBonePicker &&
        (!state.bone_picker || !pose_override || !std::isfinite((*pose_override)[0]) ||
         (*pose_override)[0] < 0 || (*pose_override)[0] > 4294967295.0 ||
         (*pose_override)[0] != std::floor((*pose_override)[0]) || value < 0 ||
         value >= kPickerMaxBones || value != std::floor(value)))
        return false;
    if (action == EditorAction::ResetCameraPath &&
        (!state.ready || state.playing || state.busy || !state.camera_count ||
         state.owner != EditorOwner::Panel || value != 0))
        return false;
    if (action >= EditorAction::SetDofEnabled && action <= EditorAction::SetDofTilt &&
        (!state.dof_available || !state.ready || !state.paused || state.playing || state.busy ||
         !state.camera_count || state.owner != EditorOwner::Panel ||
         std::abs(value) > 3.4028234663852886e38 ||
         (action <= EditorAction::SetDofOverride && value != 0 && value != 1)))
        return false;
    if (action >= EditorAction::SetCitadelDofEnabled &&
        action <= EditorAction::SetCitadelDofFocusDistance &&
        (!state.citadel_dof_available || !state.ready || !state.paused || state.playing ||
         state.busy || !state.camera_count || state.owner != EditorOwner::Panel ||
         (action == EditorAction::SetCitadelDofEnabled && value != 0 && value != 1) ||
         (action == EditorAction::SetCitadelDofSensorSize && (value < .5 || value > 3)) ||
         (action == EditorAction::SetCitadelDofFocusDistance && (value < 0 || value > 10000))))
        return false;
    if (action >= EditorAction::SetAttachTarget && action <= EditorAction::SetSourceBlend &&
        (!state.attach_available || !state.ready || state.playing || state.busy ||
         (!state.camera_count && action != EditorAction::SetAttachTarget &&
          action != EditorAction::AttachCycleTarget) ||
         state.owner != EditorOwner::Panel))
        return false;
    if (action == EditorAction::SetAttachTarget &&
        (value != std::floor(value) || value < 0 || value >= state.roster_count))
        return false;
    if (action == EditorAction::SetAttachPoint && value != 0 && value != 1 && value != 2)
        return false;
    if (action == EditorAction::SetAttachSmoothing && (value < 0 || value > 5))
        return false;
    if (action == EditorAction::SetAttachHide &&
        (value < 0 || value > 3 || value != std::floor(value)))
        return false;
    if (action == EditorAction::AttachPreview && value != 0 && value != 1)
        return false;
    if (action == EditorAction::AttachPreview && (!state.attach_selected || !state.manual_active))
        return false;
    if (action == EditorAction::AttachSnap && value != 0)
        return false;
    if (action == EditorAction::AttachSnap &&
        (!state.attach_selected || !state.manual_active || !state.paused || state.playing))
        return false;
    if (action == EditorAction::StepReplayTicks &&
        (!state.ready || state.playing || state.busy || state.attach_preview ||
         state.owner != EditorOwner::Panel ||
         (std::abs(value) != 1 && std::abs(value) != 2 && std::abs(value) != 5 &&
          std::abs(value) != 10 && std::abs(value) != 25)))
        return false;
    if (action == EditorAction::SeekShot &&
        (!state.ready || state.playing || state.busy || !state.camera_count ||
         state.owner != EditorOwner::Panel || value < 0 || value > state.duration))
        return false;
    if (action == EditorAction::SetSourceBlend && (value < 0 || value > 10))
        return false;
    if ((action == EditorAction::SetConfettiEnabled ||
         action == EditorAction::SetConfettiDespawnOnGround) &&
        value != 0 && value != 1)
        return false;
    if (action == EditorAction::SetConfettiSpawnHeight && (value < 100 || value > 1500))
        return false;
    if (action == EditorAction::SetAttachBone) {
        EditorBones bones{};
        if (!pose_override || !editor_bones_snapshot(bones) || value != std::floor(value) ||
            value < 0 || value >= bones.count || (*pose_override)[0] != bones.sequence)
            return false;
    } else if (action == EditorAction::SetAttachOffsets) {
        if (!pose_override)
            return false;
        for (int index = 0; index < 6; ++index)
            if (!std::isfinite((*pose_override)[index]) ||
                std::abs((*pose_override)[index]) > 10000)
                return false;
    } else if (action >= EditorAction::SetAttachTarget && action <= EditorAction::SetSourceBlend &&
               pose_override) {
        return false;
    }
    if (action == EditorAction::SetFraming) {
        // The pose override carries the wheel scale factor (Python owns the
        // target key and its base value), not an absolute aspect ratio.
        // Extreme factors are valid; the resulting curve value is clamped.
        if (!pose_override || !state.ready || !state.paused || !state.manual_active ||
            state.playing || state.busy || value != std::floor(value) || value < -1 ||
            (value >= 0 && value >= state.camera_count) || (*pose_override)[6] <= 0)
            return false;
        for (double component : *pose_override)
            if (!std::isfinite(component))
                return false;
    } else if (pose_override && action != EditorAction::SetAttachOffsets &&
               action != EditorAction::SetAttachBone && action != EditorAction::FinishBonePicker)
        return false;
    if (action == EditorAction::SetSpeed) {
        if (value < 1 || value > 10000)
            return false;
    }
    if (action == EditorAction::SetPlaybackSpeed && (value < .05 || value > 4 || state.busy))
        return false;
    if (action == EditorAction::SetPlaybackRate &&
        ((value != 30 && value != 60 && value != 120) || state.playing || state.busy))
        return false;
    if ((action == EditorAction::Capture || action == EditorAction::Replace) && !state.ready)
        return false;
    if (gEventLock.test_and_set(std::memory_order_acquire)) {
        ++gDropped;
        return false;
    }
    if (gLastEvent - gAcknowledged >= kEditorEventCount) {
        gEventLock.clear(std::memory_order_release);
        ++gDropped;
        return false;
    }
    if (action == EditorAction::FinishBonePicker &&
        !picker_finish(std::uint32_t((*pose_override)[0]), int(value))) {
        gEventLock.clear(std::memory_order_release);
        return false;
    }
    if (action == EditorAction::SetSpeed)
        gSpeed = value;
    auto sequence = ++gLastEvent;
    EditorEvent& event = gEvents[(sequence - 1) % kEditorEventCount];
    event = {};
    event.sequence = sequence;
    event.action = std::uint32_t(action);
    event.value = value;
    for (unsigned i = 0; i < 7; ++i)
        event.pose[i] = pose_override ? (*pose_override)[i] : state.pose[i];
    event.tick = state.tick;
    event.paused = state.paused;
    gEventLock.clear(std::memory_order_release);
    return true;
}
bool editor_panel_visible() noexcept {
    return configured() && focused() && gOwner.load() == EditorOwner::Panel;
}
void editor_set_owner(EditorOwner owner) noexcept {
    if (owner > EditorOwner::ReShade || owner == EditorOwner::Unfocused)
        return;
    if (owner != EditorOwner::ReShade && (reshade_overlay_open() || reshade_overlay_pending()))
        reshade_request_overlay(false);
    if (owner == EditorOwner::ReShade && !reshade_overlay_open() && !reshade_overlay_pending())
        return;
    if (gOwner.exchange(owner) == owner)
        return;
    ++gCursorRefresh;
    editor_reset_motion();
    cursor_mode(owner);
}
void editor_overlay_available(bool available) noexcept {
    gOverlay = available;
    ++gCursorRefresh;
    if (!available) {
        reset_keys(true);
        cursor_mode(EditorOwner::Disabled);
    } else
        cursor_mode(gOwner.load());
}
void editor_text_input_active(bool active) noexcept {
    gTextInput = active;
}
void editor_attach_window(HWND window) noexcept {
    gWindow = window;
    gFocused = focused();
    ++gCursorRefresh;
    reset_keys(true);
}
void editor_reset_motion() noexcept {
    reset_keys();
}
bool editor_install_input_hooks() noexcept {
    HMODULE user = GetModuleHandleW(L"user32.dll");
    if (!user)
        return false;
    struct Target {
        const char* name;
        void* hook;
        void** original;
    };
    Target targets[] = {{"GetRawInputData", reinterpret_cast<void*>(raw_data_hook),
                         reinterpret_cast<void**>(&gRawData)},
                        {"GetRawInputBuffer", reinterpret_cast<void*>(raw_buffer_hook),
                         reinterpret_cast<void**>(&gRawBuffer)},
                        {"SetCursorPos", reinterpret_cast<void*>(set_cursor_pos_hook),
                         reinterpret_cast<void**>(&gSetCursorPos)},
                        {"ClipCursor", reinterpret_cast<void*>(clip_cursor_hook),
                         reinterpret_cast<void**>(&gClipCursor)}};
    void* addresses[4]{};
    int created = 0, enabled = 0;
    for (auto& t : targets) {
        auto address = reinterpret_cast<void*>(GetProcAddress(user, t.name));
        if (!address || MH_CreateHook(address, t.hook, t.original) != MH_OK)
            break;
        addresses[created++] = address;
    }
    if (created == 4)
        for (; enabled < created; ++enabled)
            if (MH_EnableHook(addresses[enabled]) != MH_OK)
                break;
    if (enabled != 4) {
        for (int i = 0; i < enabled; ++i)
            MH_DisableHook(addresses[i]);
        for (int i = 0; i < created; ++i)
            MH_RemoveHook(addresses[i]);
        return false;
    }
    gInput = true;
    ++gCursorRefresh;
    return true;
}
void editor_update_view(bool ready, bool paused, bool manual, const CameraPose& pose, double phase,
                        std::int32_t tick, double horizontal_fov, std::uint32_t width,
                        std::uint32_t height) noexcept {
    gViewSequence.fetch_add(1, std::memory_order_acq_rel);
    const auto flags = (ready ? 1u : 0u) | (paused ? 2u : 0u) | (manual ? 4u : 0u);
    const auto previous_flags = gViewFlags.exchange(flags);
    if ((previous_flags ^ flags) & 1u)
        ++gCursorRefresh;
    for (unsigned i = 0; i < 7; ++i)
        gPose[i] = pose[i];
    gPhase = phase;
    gTick = tick;
    ++gFrame;
    gHorizontalFov = horizontal_fov;
    gViewWidth = width;
    gViewHeight = height;
    gViewSequence.fetch_add(1, std::memory_order_release);
}
double framing_wheel_step(double live, double stored, int camera, double factor) noexcept {
    // Continue from the value this wheel already produced while it is still the
    // live pose. Python commits the parked key on its own poll cadence, so
    // re-reading the published curve for every event would recompute the same
    // target and stall the zoom. Re-anchor only when the camera changes, the
    // live pose was moved outside the wheel, or the published curve changed to
    // a value this wheel did not produce (a desktop edit). A timeout would
    // snap the view back to the selected camera's stored framing whenever the
    // wheel rests on a clamp.
    double base = stored;
    if (gFramingWheelState.camera == camera && std::abs(live - gFramingWheelState.value) <= 1e-6) {
        // A parked commit lands as the value this wheel just produced; keep
        // compounding on it instead of rebasing to the outgoing curve value.
        if (std::abs(stored - gFramingWheelState.stored) <= 1e-6 ||
            std::abs(stored - gFramingWheelState.value) <= 1e-4)
            base = gFramingWheelState.value;
    }
    const double next = std::clamp(base * factor, .5, 4.0);
    gFramingWheelState = {camera, next, stored};
    return next;
}
void apply_framing_wheel(CameraPose& pose, const EditorConfig& config, long wheel) noexcept {
    if (!wheel || config.playback_flags || (gViewFlags.load() & 6) != 6)
        return;
    // Anchor to the selected camera's stored framing for immediate on-screen
    // feedback. The published event carries only the scale factor: Python owns
    // the final target and base, so a stale selection or live pose cannot
    // redirect or rebase the edit.
    const int selected = config.camera_count ? int(config.selected_camera) : -1;
    double stored = pose[6];
    if (auto path = visualization_snapshot()) {
        if (selected >= 0 && selected < int(path->cameras().size()) &&
            std::isfinite(path->cameras()[selected].pose[6]))
            stored = path->cameras()[selected].pose[6];
    }
    const double base = pose[6];
    const double factor = std::pow(.9, double(wheel) / WHEEL_DELTA);
    const double next = framing_wheel_step(base, stored, selected, factor);
    CameraPose edit = pose;
    edit[6] = factor;
    if (next != base && factor != 1.0 &&
        editor_enqueue(EditorAction::SetFraming,
                       config.camera_count ? double(config.selected_camera) : -1.0, &edit))
        pose[6] = next;
}
bool flight_movement_active(unsigned view_flags, EditorOwner owner, bool input_owned) noexcept {
    // Manual flight stays armed for both the paused camera and playback
    // free-cam: the view's manual bit, not the replay pause state, decides
    // whether held movement reaches the camera. The ready bit still suspends
    // movement during seeks or a lost view.
    return input_owned && owner == EditorOwner::Flight && (view_flags & 5) == 5;
}
void editor_integrate_flight(CameraPose& pose, double dt) noexcept {
    if (!flight_movement_active(gViewFlags.load(), gOwner.load(), owns_input()) ||
        !std::isfinite(dt) || dt < 0 || dt > .1) {
        gMouseX = 0;
        gMouseY = 0;
        gFramingWheel = 0;
        return;
    }
    auto c = std::atomic_load(&gConfig);
    if (!c)
        return;
    bool command_held = false;
    for (unsigned i = 0; i < 12; ++i)
        command_held |= binding_down(c->bindings[i]);
    FlightInput in{};
    if (!command_held) {
        auto on = [&](unsigned n) { return binding_down(c->bindings[n], true) ? 1.0 : 0.0; };
        in.forward = on(12) - on(13);
        in.right = on(15) - on(14);
        in.up = on(16) - on(17);
        in.yaw = on(20) - on(21);
        in.pitch = on(23) - on(22);
        in.roll = on(25) - on(24);
    }
    in.mouse_x = std::clamp<long>(gMouseX.exchange(0), -2000, 2000);
    in.mouse_y = std::clamp<long>(gMouseY.exchange(0), -2000, 2000);
    if (in.mouse_x || in.mouse_y)
        ++gConsumedMotionFrames;
    double speed = gSpeed.load();
    if (binding_down(c->bindings[18], true))
        speed *= 4;
    if (binding_down(c->bindings[19], true))
        speed *= .2;
    integrate_flight(pose, in, dt, speed, c->sensitivity, (c->flags & 1) != 0);
    const auto wheel = gFramingWheel.exchange(0);
    apply_framing_wheel(pose, *c, wheel);
}
bool editor_window_message(HWND window, UINT message, WPARAM wparam, LPARAM lparam,
                           LRESULT& result) noexcept {
    result = 0;
    if (window != gWindow.load())
        return false;
    if (message == WM_KILLFOCUS || (message == WM_ACTIVATEAPP && !wparam)) {
        gFocused = false;
        ++gCursorRefresh;
        reset_keys(true);
        cursor_mode(EditorOwner::Unfocused);
        return false;
    }
    if (message == WM_SETFOCUS || (message == WM_ACTIVATEAPP && wparam)) {
        gFocused = true;
        ++gCursorRefresh;
        reset_keys(true);
        cursor_mode(gOwner.load());
        return false;
    }
    if (!configured() || !focused())
        return false;
    if (message == WM_MOUSEMOVE)
        ++gLegacyMouseMoves;
    bool before = owns_input();
    if (message == WM_MOUSEWHEEL && !gNoLegacyMouse.load())
        framing_wheel(static_cast<SHORT>(HIWORD(wparam)));
    if (message == WM_INPUT && before) {
        RAWINPUT raw{};
        UINT bytes = sizeof(raw);
        UINT got = gRawData(reinterpret_cast<HRAWINPUT>(lparam), RID_INPUT, &raw, &bytes,
                            sizeof(RAWINPUTHEADER));
        if (got != UINT(-1) && got >= sizeof(RAWINPUTHEADER) && raw.header.dwSize <= got)
            process_raw(&raw, false);
        // DefWindowProc is required to release foreground RAWINPUT storage.
        result = DefWindowProcW(window, message, wparam, lparam);
        return true;
    }
    if ((message == WM_SYSKEYDOWN || message == WM_SYSKEYUP) &&
        (wparam == VK_F4 || wparam == VK_TAB))
        return false;
    if (message == WM_KEYDOWN || message == WM_SYSKEYDOWN || message == WM_KEYUP ||
        message == WM_SYSKEYUP) {
        bool reserved = reserved_input(unsigned(wparam));
        bool down = message == WM_KEYDOWN || message == WM_SYSKEYDOWN;
        key_event(unsigned(wparam), down);
        if (reserved)
            return true;
        if (before)
            return true;
    }
    unsigned mousekey = 0;
    bool down = false;
    switch (message) {
    case WM_LBUTTONDOWN:
        mousekey = VK_LBUTTON;
        down = true;
        break;
    case WM_LBUTTONUP:
        mousekey = VK_LBUTTON;
        break;
    case WM_RBUTTONDOWN:
        mousekey = VK_RBUTTON;
        down = true;
        break;
    case WM_RBUTTONUP:
        mousekey = VK_RBUTTON;
        break;
    case WM_MBUTTONDOWN:
        mousekey = VK_MBUTTON;
        down = true;
        break;
    case WM_MBUTTONUP:
        mousekey = VK_MBUTTON;
        break;
    case WM_XBUTTONDOWN:
        mousekey = HIWORD(wparam) == XBUTTON1 ? VK_XBUTTON1 : VK_XBUTTON2;
        down = true;
        break;
    case WM_XBUTTONUP:
        mousekey = HIWORD(wparam) == XBUTTON1 ? VK_XBUTTON1 : VK_XBUTTON2;
        break;
    }
    if (mousekey) {
        bool reserved = reserved_input(mousekey);
        key_event(mousekey, down);
        if (reserved) {
            result = (message == WM_XBUTTONDOWN || message == WM_XBUTTONUP) ? TRUE : 0;
            return true;
        }
    }
    if (before && (message >= WM_MOUSEFIRST && message <= WM_MOUSELAST)) {
        result = (message == WM_XBUTTONDOWN || message == WM_XBUTTONUP) ? TRUE : 0;
        return true;
    }
    if (before && (message == WM_CHAR || message == WM_SYSCHAR || message == WM_UNICHAR))
        return true;
    // Hide the OS pointer on its owning window thread. The panel draws its own
    // pointer; Present must not change OS cursor/capture ownership.
    if (before && message == WM_SETCURSOR) {
        SetCursor(nullptr);
        result = TRUE;
        return true;
    }
    return false;
}
void editor_worker_tick(unsigned char* memory, bool connected) noexcept {
    if (gConnected.exchange(connected) != connected)
        ++gCursorRefresh;
    static std::uint32_t accepted = ~0u, owner_sequence = ~0u;
    static double configured_speed = -1;
    if (memory && connected) {
        auto source = memory + kEditorDofOffset;
        auto sequence = reinterpret_cast<volatile LONG*>(source + 8);
        const auto first = InterlockedCompareExchange(sequence, 0, 0);
        const auto previous = std::atomic_load(&gDofConfig);
        if (!(first & 1) && (!previous || previous->sequence != std::uint32_t(first))) {
            EditorDofConfig dof{};
            std::memcpy(&dof, source, sizeof(dof));
            MemoryBarrier();
            bool valid = first == InterlockedCompareExchange(sequence, 0, 0) &&
                         std::memcmp(dof.magic, "DLYDOF01", 8) == 0 && dof.abi == 1 &&
                         dof.enabled <= 1 && dof.reserved == 0;
            for (double value : dof.values)
                valid = valid && std::isfinite(value) && std::abs(value) <= 3.4028234663852886e38;
            for (unsigned i = 0; i < 2; ++i)
                valid = valid && (dof.values[i] == 0 || dof.values[i] == 1);
            if (valid) {
                try {
                    std::atomic_store(&gDofConfig, std::make_shared<const EditorDofConfig>(dof));
                } catch (...) {
                }
            }
        }
        auto citadel_source = memory + kEditorCitadelDofOffset;
        auto citadel_sequence = reinterpret_cast<volatile LONG*>(citadel_source + 8);
        const auto citadel_first = InterlockedCompareExchange(citadel_sequence, 0, 0);
        const auto citadel_previous = std::atomic_load(&gCitadelDofConfig);
        if (!(citadel_first & 1) &&
            (!citadel_previous || citadel_previous->sequence != std::uint32_t(citadel_first))) {
            EditorCitadelDofConfig citadel{};
            std::memcpy(&citadel, citadel_source, sizeof(citadel));
            MemoryBarrier();
            const bool valid =
                citadel_first == InterlockedCompareExchange(citadel_sequence, 0, 0) &&
                std::memcmp(citadel.magic, "DLYCDOF1", 8) == 0 && citadel.abi == 1 &&
                citadel.available <= 1 && citadel.enabled <= 1 &&
                std::isfinite(citadel.sensor_size) && citadel.sensor_size >= .5 &&
                citadel.sensor_size <= 3 && std::isfinite(citadel.focus_distance) &&
                citadel.focus_distance >= 0 && citadel.focus_distance <= 10000;
            if (valid) {
                try {
                    std::atomic_store(&gCitadelDofConfig,
                                      std::make_shared<const EditorCitadelDofConfig>(citadel));
                } catch (...) {
                }
            }
        }
        auto attach_source = memory + kEditorAttachOffset;
        auto attach_sequence = reinterpret_cast<volatile LONG*>(attach_source + 8);
        const auto attach_first = InterlockedCompareExchange(attach_sequence, 0, 0);
        const auto attach_previous = std::atomic_load(&gAttachConfig);
        if (!(attach_first & 1) &&
            (!attach_previous || attach_previous->sequence != std::uint32_t(attach_first))) {
            EditorAttachConfig attach{};
            std::memcpy(&attach, attach_source, sizeof(attach));
            MemoryBarrier();
            bool valid =
                attach_first == InterlockedCompareExchange(attach_sequence, 0, 0) &&
                std::memcmp(attach.magic, "DLYATTC1", 8) == 0 &&
                (attach.abi == 2 || attach.abi == 3 || attach.abi == 4 || attach.abi == 5) &&
                attach.flags <= (attach.abi >= 4 ? 15u : 7u) && attach.reserved == 0 &&
                (!(attach.flags & 8) || (attach.flags & 3) == 3) &&
                (attach.reserved2 == 0 || (attach.abi >= 3 && attach.reserved2 <= 10000)) &&
                attach.point <= 2 && attach.hide <= (attach.abi == 5 ? 3u : 1u) &&
                (attach.point == 2 ? attach.bone_hash != 0 : attach.bone_hash == 0);
            for (std::uint32_t offset : attach.offsets)
                valid = valid && offset >= 8 && offset <= 0x8000;
            std::uint64_t bone_token = 14695981039346656037ull;
            bool ended = false;
            for (std::size_t index = 0; index < sizeof(attach.bone_name); ++index) {
                const unsigned char ch = static_cast<unsigned char>(attach.bone_name[index]);
                if (!ch) {
                    ended = true;
                    continue;
                }
                const bool letter = (ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z') ||
                                    ch == '_' || (index == 0 && ch == '$');
                valid = valid && !ended &&
                        (letter || (index > 0 && ((ch >= '0' && ch <= '9') || ch == '.')));
                bone_token = (bone_token ^ ch) * 1099511628211ull;
            }
            valid =
                valid && (attach.point == 2 ? attach.bone_name[0] && bone_token == attach.bone_hash
                                            : attach.bone_name[0] == 0);
            for (double value : attach.offset)
                valid = valid && std::isfinite(value) && std::abs(value) <= 10000;
            valid = valid && std::isfinite(attach.smoothing) && attach.smoothing >= 0 &&
                    attach.smoothing <= 5 && attach.attached_keys <= attach.key_count &&
                    attach.key_count <= 100000;
            if (valid) {
                try {
                    std::atomic_store(&gAttachConfig,
                                      std::make_shared<const EditorAttachConfig>(attach));
                } catch (...) {
                }
            }
        }
        auto bones_source = memory + kEditorBonesOffset;
        auto bones_sequence = reinterpret_cast<volatile LONG*>(bones_source + 8);
        const auto bones_first = InterlockedCompareExchange(bones_sequence, 0, 0);
        const auto bones_previous = std::atomic_load(&gBones);
        if (!(bones_first & 1) &&
            (!bones_previous || bones_previous->sequence != std::uint32_t(bones_first))) {
            EditorBones bones{};
            std::memcpy(&bones, bones_source, sizeof(bones));
            MemoryBarrier();
            if (bones_first == InterlockedCompareExchange(bones_sequence, 0, 0) &&
                std::memcmp(bones.magic, "DLYBONE1", 8) == 0 && bones.abi == 1 &&
                bones.count <= kEditorBoneCount && bones.count <= bones.total &&
                bones.total <= 4096) {
                try {
                    std::atomic_store(&gBones, std::make_shared<const EditorBones>(bones));
                } catch (...) {
                }
            }
        }
        auto roster_source = memory + kEditorRosterOffset;
        auto roster_sequence = reinterpret_cast<volatile LONG*>(roster_source + 8);
        const auto roster_first = InterlockedCompareExchange(roster_sequence, 0, 0);
        const auto roster_previous = std::atomic_load(&gRoster);
        if (!(roster_first & 1) &&
            (!roster_previous || roster_previous->sequence != std::uint32_t(roster_first))) {
            EditorRoster roster{};
            std::memcpy(&roster, roster_source, sizeof(roster));
            MemoryBarrier();
            bool valid = roster_first == InterlockedCompareExchange(roster_sequence, 0, 0) &&
                         std::memcmp(roster.magic, "DLYROS01", 8) == 0 &&
                         roster.abi == kEditorRosterAbi && roster.count <= kEditorRosterPlayers &&
                         (roster.flags & ~1u) == 0;
            for (std::uint32_t index = 0; index < roster.count && valid; ++index)
                valid = std::memchr(roster.players[index].model_path, 0,
                                    sizeof(roster.players[index].model_path)) != nullptr;
            if (valid) {
                try {
                    std::atomic_store(&gRoster, std::make_shared<const EditorRoster>(roster));
                } catch (...) {
                }
            }
        }
        auto src = memory + kEditorConfigOffset;
        auto seq = reinterpret_cast<volatile LONG*>(src + 8);
        LONG before = InterlockedCompareExchange(seq, 0, 0);
        if (!(before & 1) && std::uint32_t(before) != accepted) {
            EditorConfig c{};
            std::memcpy(&c, src, sizeof(c));
            MemoryBarrier();
            LONG after = InterlockedCompareExchange(seq, 0, 0);
            bool okay =
                before == after && !(after & 1) && std::memcmp(c.magic, "DLYEDIT1", 8) == 0 &&
                c.abi == kEditorAbi && c.enabled <= 1 && (c.owner <= 4 || c.owner == 6) &&
                c.flags <= 1 && std::isfinite(c.speed) && c.speed >= 1 && c.speed <= 10000 &&
                std::isfinite(c.sensitivity) && c.sensitivity >= .001 && c.sensitivity <= 10 &&
                std::isfinite(c.duration) && c.duration >= 0 && std::isfinite(c.playhead) &&
                c.playhead >= 0 && c.camera_count <= 4096 && c.playback_flags <= 3 &&
                (c.video_fps == 0 || c.video_fps == 30 || c.video_fps == 60 || c.video_fps == 120 ||
                 c.video_fps == 300 || c.video_fps == 600) &&
                (c.video_bitrate_mbps == 0 || c.video_bitrate_mbps == 10 ||
                 c.video_bitrate_mbps == 20 || c.video_bitrate_mbps == 40) &&
                c.video_encoder <= 10 && c.video_flags <= 127 && c.video_speed >= 0 &&
                c.video_speed <= 4 && std::memchr(c.shot_name, 0, sizeof(c.shot_name)) &&
                std::memchr(c.message, 0, sizeof(c.message));
            for (auto& b : c.bindings)
                okay = okay && b.vk < 256 && b.modifiers < 8 && b.vk != VK_F7;
            const auto& rb = c.reshade_binding;
            okay = okay && rb.vk < 256 && rb.modifiers < 8 && rb.vk != VK_F7 &&
                   rb.vk != VK_CONTROL && rb.vk != VK_MENU && rb.vk != VK_SHIFT;
            for (const auto& b : c.bindings)
                okay = okay && (!rb.vk || b.vk != rb.vk || b.modifiers != rb.modifiers);
            if (okay) {
                std::shared_ptr<const EditorConfig> next;
                try {
                    next = std::make_shared<const EditorConfig>(c);
                } catch (...) {
                    gConnected = false;
                    gOwner = EditorOwner::Disabled;
                    cursor_mode(EditorOwner::Disabled);
                    return;
                }
                std::atomic_store(&gConfig, next);
                accepted = std::uint32_t(after);
                if (c.speed != configured_speed) {
                    gSpeed = c.speed;
                    configured_speed = c.speed;
                }
                if (c.owner_sequence != owner_sequence) {
                    owner_sequence = c.owner_sequence;
                    editor_set_owner(EditorOwner(c.owner));
                }
                if (!c.enabled)
                    editor_set_owner(EditorOwner::Disabled);
            }
        }
    }
    // Configuration publication and action acknowledgements are independent.
    // A busy input producer must not lose an ack merely because the config
    // sequence was already accepted; retry the cached ack every worker pass.
    auto latest = std::atomic_load(&gConfig);
    if (connected && latest && !gEventLock.test_and_set(std::memory_order_acquire)) {
        if (latest->ack_event >= gAcknowledged && latest->ack_event <= gLastEvent)
            gAcknowledged = latest->ack_event;
        gEventLock.clear(std::memory_order_release);
    }
    bool current_focus = focused();
    if (gFocused.exchange(current_focus) != current_focus) {
        ++gCursorRefresh;
        reset_keys(true);
        cursor_mode(current_focus ? gOwner.load() : EditorOwner::Unfocused);
    }
    if (!connected) {
        std::atomic_store(&gDofConfig, std::shared_ptr<const EditorDofConfig>{});
        std::atomic_store(&gCitadelDofConfig, std::shared_ptr<const EditorCitadelDofConfig>{});
        std::atomic_store(&gAttachConfig, std::shared_ptr<const EditorAttachConfig>{});
        std::atomic_store(&gRoster, std::shared_ptr<const EditorRoster>{});
        std::atomic_store(&gBones, std::shared_ptr<const EditorBones>{});
        gOwner = EditorOwner::Disabled;
        reset_keys(true);
        cursor_mode(EditorOwner::Disabled);
    }
    ReShadeInputEvent menu_input{};
    for (unsigned index = 0; index < 128 && reshade_pop_input(menu_input); ++index) {
        if (!configured() || !focused() ||
            (!reshade_owner_tracked() && !reshade_overlay_open() && !reshade_overlay_pending()))
            continue;
        // The modifier snapshot belongs to this edge, not the later worker
        // poll. Preserve existing edge deduplication with raw/legacy delivery.
        key_event(VK_CONTROL, (menu_input.modifiers & 1) != 0);
        key_event(VK_MENU, (menu_input.modifiers & 2) != 0);
        key_event(VK_SHIFT, (menu_input.modifiers & 4) != 0);
        key_event(menu_input.vk, menu_input.down);
    }
    if (connected && configured()) {
        const bool open = reshade_overlay_open(), pending = reshade_overlay_pending();
        if ((open || pending) && !reshade_owner_tracked())
            track_reshade_owner();
        if (!open && !pending && reshade_owner_tracked()) {
            const auto previous = gReShadePriorOwner.exchange(EditorOwner::Unfocused);
            // ReShade can consume key-up messages before Dolly's WndProc sees
            // them. Clear that menu context and block still-held physical keys.
            reset_keys(true);
            // An explicit controller owner change takes precedence over a stale
            // menu-close acknowledgement. Home / the menu close button restore
            // the owner suspended when the real ReShade menu first opened.
            if (gOwner.load() == EditorOwner::ReShade) {
                editor_set_owner(previous == EditorOwner::Unfocused ||
                                         previous == EditorOwner::ReShade
                                     ? EditorOwner::Panel
                                     : previous);
            }
            switch (gReShadeDeferred.exchange(0)) {
            case 1:
                if (editor_enqueue(EditorAction::Console, 1))
                    editor_set_owner(EditorOwner::Console);
                break;
            case 2:
                if (editor_enqueue(EditorAction::Panel, 1))
                    editor_set_owner(EditorOwner::Panel);
                break;
            case 3:
                dispatch(EditorAction::GameUI);
                break;
            }
        }
    } else {
        if (gReShadePriorOwner.exchange(EditorOwner::Unfocused) != EditorOwner::Unfocused ||
            reshade_overlay_open())
            reshade_request_overlay(false);
        gReShadeDeferred = 0;
    }
    reconcile_cursor_mode();
    unblock_released();
    static ULONGLONG last_registration_check = 0;
    auto tick_now = GetTickCount64();
    if (!last_registration_check || tick_now - last_registration_check > 1000) {
        RAWINPUTDEVICE devices[64]{};
        UINT count = 64;
        UINT found = GetRegisteredRawInputDevices(devices, &count, sizeof(RAWINPUTDEVICE));
        bool no_legacy = false;
        gRawRegistrationKnown = found != UINT(-1);
        gRawMouseRegistered = false;
        gRawRegistrationFlags = 0;
        gRawRegistrationTarget = nullptr;
        if (found != UINT(-1))
            for (UINT i = 0; i < found; ++i)
                if (devices[i].usUsagePage == 1 && devices[i].usUsage == 2) {
                    gRawMouseRegistered = true;
                    gRawRegistrationFlags = devices[i].dwFlags;
                    gRawRegistrationTarget = devices[i].hwndTarget;
                    no_legacy = (devices[i].dwFlags & RIDEV_NOLEGACY) == RIDEV_NOLEGACY;
                    break;
                }
        gNoLegacyMouse = no_legacy;
        last_registration_check = tick_now;
    }
    if (!memory)
        return;
    auto state = editor_snapshot();
    EditorStatus status{};
    std::memcpy(status.magic, "DLYEDS01", 8);
    status.abi = kEditorAbi;
    status.owner = std::uint32_t(state.owner);
    EditorInputDiagnostics input{};
    std::memcpy(input.magic, "DLYINP01", 8);
    input.abi = 1;
    input.flags = (gRawRegistrationKnown ? 1u : 0u) | (gRawMouseRegistered ? 2u : 0u) |
                  (gRawMouseRegistered && gRawRegistrationTarget == gWindow.load() ? 4u : 0u) |
                  (state.ready ? 8u : 0u) | (state.manual_active ? 16u : 0u) |
                  (state.focused ? 32u : 0u) | (state.owner == EditorOwner::Flight ? 64u : 0u) |
                  (gCursorClipped.load() ? 128u : 0u) | (gNoLegacyMouse.load() ? 256u : 0u);
    input.registration_flags = gRawRegistrationFlags;
    input.raw_mouse_packets = gRawMousePackets.load();
    input.relative_mouse_packets = gRelativeMousePackets.load();
    input.accepted_motion_packets = gAcceptedMotionPackets.load();
    input.legacy_mouse_moves = gLegacyMouseMoves.load();
    input.consumed_motion_frames = gConsumedMotionFrames.load();
    input.cursor_syncs = gCursorSyncs.load();
    input.cursor_failures = gCursorFailures.load();
    input.registration_target = reinterpret_cast<std::uintptr_t>(gRawRegistrationTarget);
    input.last_cursor_error = gLastCursorError.load();
    auto input_out = memory + kEditorInputDiagnosticsOffset;
    auto input_sequence = reinterpret_cast<volatile LONG*>(input_out + 8);
    const auto input_old = InterlockedCompareExchange(input_sequence, 0, 0);
    const auto input_even = (input_old & 1) ? input_old + 1 : input_old;
    InterlockedExchange(input_sequence, input_even + 1);
    std::memcpy(input_out, &input, 8);
    std::memcpy(input_out + 12, reinterpret_cast<unsigned char*>(&input) + 12, sizeof(input) - 12);
    MemoryBarrier();
    InterlockedExchange(input_sequence, input_even + 2);
    status.flags = (state.enabled ? 1u : 0u) | (state.focused ? 2u : 0u) |
                   (state.paused ? 4u : 0u) | (state.manual_active ? 8u : 0u) |
                   (state.ready ? 16u : 0u) | (state.overlay_available ? 32u : 0u) |
                   (state.input_available ? 64u : 0u);
    status.selected_camera = state.selected_camera;
    status.camera_count = state.camera_count;
    status.speed = state.speed;
    status.phase = state.phase;
    status.tick = state.tick;
    status.frame_count = gFrame.load();
    status.dropped_events = gDropped.load();
    status.reserved = static_cast<std::uint32_t>(visualization_runtime_state());
    std::memcpy(status.message, state.message, sizeof(status.message));
    if (state.enabled && !state.input_available)
        std::snprintf(
            status.message, sizeof(status.message),
            "Native raw-input interception could not be installed; original game controls remain available.");
    else if (state.enabled && !state.overlay_available)
        std::snprintf(status.message, sizeof(status.message), "%s", overlay_last_error());
    else if (state.dropped_events)
        std::snprintf(
            status.message, sizeof(status.message),
            "An editor action could not be queued. Wait for the current operation, then retry (%u dropped).",
            state.dropped_events);
    for (unsigned i = 0; i < 7; ++i)
        status.applied_pose[i] = state.pose[i];
    if (gEventLock.test_and_set(std::memory_order_acquire))
        return;
    status.last_event = gLastEvent;
    std::memcpy(status.events, gEvents, sizeof(gEvents));
    gEventLock.clear(std::memory_order_release);
    auto out = memory + kEditorStatusOffset;
    auto sequence = reinterpret_cast<volatile LONG*>(out + 8);
    LONG old = InterlockedCompareExchange(sequence, 0, 0);
    LONG even = (old & 1) ? old + 1 : old;
    InterlockedExchange(sequence, even + 1);
    std::memcpy(out, &status, 8);
    std::memcpy(out + 12, reinterpret_cast<unsigned char*>(&status) + 12, sizeof(status) - 12);
    MemoryBarrier();
    InterlockedExchange(sequence, even + 2);
}
} // namespace dolly
