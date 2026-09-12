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
// Media Foundation is the no-dependency fallback. FFmpeg is an external
// encoder (spawned child process) used when the caller supplies its path.
enum class Encoder : std::uint32_t { media_foundation = 0, ffmpeg = 1 };
// Codec identifiers are part of the Python<->native wire protocol; keep the
// numeric values stable. auto_select resolves to hardware H.264 when possible.
enum class Codec : std::uint32_t {
    auto_select = 0,
    h264_nvenc = 1,
    hevc_nvenc = 2,
    h264_mf = 3,
    libx264 = 4,
    libx265 = 5,
    h264_qsv = 6,
    hevc_qsv = 7,
    h264_amf = 8,
    hevc_amf = 9,
    lossless = 10
};
struct Options {
    const wchar_t* path = nullptr;   // absolute .mp4/.mkv destination
    const wchar_t* ffmpeg = nullptr; // absolute ffmpeg.exe when encoder==ffmpeg
    std::uint32_t fps = 60;
    std::uint32_t bitrate = 20000000;
    // Rate-control quality (CRF/CQ/QP). 0 selects the codec's default.
    std::uint32_t quality = 0;
    // Encoder preset index (see preset table); 0 selects the default.
    std::uint32_t preset = 0;
    Encoder encoder = Encoder::media_foundation;
    Codec codec = Codec::auto_select;
    // Fixed-step export: capture exactly one frame per rendered Present and
    // timestamp it at the requested rate, instead of sampling wall-clock slots.
    // The host is expected to run the engine at a fixed frame rate (set by the
    // caller) so each captured frame is a distinct simulation step.
    bool fixed_step = false;
};
struct Status {
    State state = State::idle;
    std::uint32_t width = 0, height = 0, fps = 0, error_code = 0;
    std::uint64_t frames_written = 0, frames_dropped = 0, duration_100ns = 0;
    wchar_t error[256]{};
};
// Starts a video-only, real-time recording. The encoder initializes
// asynchronously after the next Present supplies dimensions. 30/60/120/300/600 FPS,
// current even-sized SDR game resolution, up to 3840 x 2160. Existing
// destinations are refused. Calls return promptly; disk/codec failures are
// reported through status().
bool start(const Options& options) noexcept;
// Media Foundation convenience overload retained for the native smoke test.
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
