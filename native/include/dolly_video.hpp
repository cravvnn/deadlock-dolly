#pragma once
#include <cstdint>

struct IDXGISwapChain;
struct ID3D11Device;
struct ID3D11DeviceContext;

namespace dolly::video {
enum class State : std::uint32_t {
    idle = 0,
    starting = 1,
    recording = 2,
    finalizing = 3,
    completed = 4,
    cancelled = 5,
    failed = 6
};
struct Status {
    State state = State::idle;
    std::uint32_t width = 0, height = 0, fps = 0, error_code = 0;
    std::uint64_t frames_written = 0, frames_dropped = 0, duration_100ns = 0;
    wchar_t error[256]{};
};
// Starts a video-only, real-time MP4. The encoder initializes asynchronously
// after the next Present supplies dimensions. 30/60 FPS, current even-sized
// SDR game resolution, up to 3840 x 2160. Existing destinations are refused.
// Calls return promptly; disk/codec failures are reported through status().
bool start(const wchar_t* path, std::uint32_t fps, std::uint32_t bitrate) noexcept;
void stop(bool cancel = false) noexcept;
Status status() noexcept;

// Call once for the game swapchain before Dolly's UI/guide rendering, under
// the same serialization as ResizeBuffers/reset_resources. Never waits for
// GPU readiness, the encoder, a file, or another thread's mutex.
void capture(IDXGISwapChain* swapchain, ID3D11Device* device,
             ID3D11DeviceContext* context) noexcept;
// Call at a serialized render/resize boundary before releasing the device.
// Stops an active recording and releases only recorder-owned GPU resources.
void reset_resources() noexcept;
// Off-render-thread only; finishes/cancels the worker before DLL shutdown.
// GPU resources are separately released by reset_resources().
void shutdown() noexcept;
}
