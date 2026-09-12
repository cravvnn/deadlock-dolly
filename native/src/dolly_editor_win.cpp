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
std::atomic<double> gSpeed{400};
std::atomic<long> gMouseX{0}, gMouseY{0};
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
        } else
            editor_set_owner(EditorOwner::Panel);
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
bool editor_enqueue(EditorAction action, double value) noexcept {
    // Menu requests may originate from a render-thread button. They only queue
    // adapter work here; the worker changes input/cursor ownership afterwards.
    if (action == EditorAction::ReShade)
        return configured() && !gReShadeDeferred.load() &&
               reshade_request_overlay(!reshade_overlay_open());
    if (!std::isfinite(value) || std::uint32_t(action) > std::uint32_t(EditorAction::StopVideo))
        return false;
    auto state = editor_snapshot();
    if (!state.enabled)
        return false;
    if (action == EditorAction::SetSpeed) {
        if (value < 1 || value > 10000)
            return false;
    }
    if (action == EditorAction::SetPlaybackSpeed &&
        (value < .05 || value > 4 || state.playing || state.busy))
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
    if (action == EditorAction::SetSpeed)
        gSpeed = value;
    auto sequence = ++gLastEvent;
    EditorEvent& event = gEvents[(sequence - 1) % kEditorEventCount];
    event = {};
    event.sequence = sequence;
    event.action = std::uint32_t(action);
    event.value = value;
    for (unsigned i = 0; i < 7; ++i)
        event.pose[i] = state.pose[i];
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
void editor_integrate_flight(CameraPose& pose, double dt) noexcept {
    if (!owns_input() || gOwner.load() != EditorOwner::Flight || !(gViewFlags.load() & 2)) {
        gMouseX = 0;
        gMouseY = 0;
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
                c.video_encoder <= 10 && c.video_flags <= 1 && c.video_speed >= 0 &&
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
