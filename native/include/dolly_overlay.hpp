#pragma once

namespace dolly {
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
void overlay_raw_mouse_button(int button,bool down) noexcept;
void overlay_raw_mouse_wheel(float vertical,float horizontal=0) noexcept;
void overlay_raw_key(unsigned virtual_key,unsigned scan_code,bool down,bool extended=false) noexcept;
}
