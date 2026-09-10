#pragma once
#include <cstdint>

namespace dolly {
struct OverlayDiagnostics {
    std::uint64_t present_calls = 0, panel_frames = 0, init_attempts = 0, init_successes = 0,
                  release_calls = 0, resize_calls = 0;
    std::uint64_t draw_frames = 0, guide_frames = 0, overlay_last_us = 0, overlay_max_us = 0;
    std::uint64_t present_last_us = 0, present_max_us = 0, lock_skips = 0;
    std::uint64_t overlay_active_since_ms = 0, present_active_since_ms = 0, guide_lines = 0,
                  guide_labels = 0;
};
// Independent monotonic counters; sampled off the render thread.
OverlayDiagnostics overlay_diagnostics() noexcept;
// Called by the native worker only after game fingerprints, development flags,
// the camera guard and MinHook initialization have succeeded. Never DllMain.
// Failure leaves the camera bridge and external editor available.
bool install_overlay_hooks() noexcept;

// Suspends the overlay and releases its device resources. Hook code remains
// resident until process exit, like the native camera callback, to avoid
// unhook/unload races with another renderer or overlay.
void shutdown_overlay() noexcept;
const char* overlay_last_error() noexcept;

// Raw-input games may suppress legacy window mouse/key messages. Input hooks
// enqueue these events; only the render/window callback accesses ImGui.
// Mouse indices: left 0, right 1, middle 2, side 1 3, side 2 4.
void overlay_raw_mouse_button(int button, bool down) noexcept;
void overlay_raw_mouse_wheel(float vertical, float horizontal = 0) noexcept;
void overlay_raw_key(unsigned virtual_key, unsigned scan_code, bool down,
                     bool extended = false) noexcept;
}
