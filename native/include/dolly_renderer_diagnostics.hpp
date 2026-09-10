#pragma once
#include <cstddef>
#include <cstdint>
#include "dolly_protocol.hpp"
namespace dolly {
// Optional observational block; camera ABI 3 and all existing offsets stay fixed.
constexpr std::size_t kRendererDiagnosticsOffset = kControlBytes + 1024;
constexpr std::uint32_t kRendererDiagnosticsAbi = 2;
constexpr char kRendererDiagnosticsHash[] =
    "386bdc4adfc8b8a0db67520b98b391f872a214e07077cc17a02f10bf94e3b2d8";
constexpr std::uint32_t kRendererDiagnosticsImageSize = 0x4d6000;
enum class RendererProbeState : std::uint32_t {
    Waiting = 0,
    Supported = 1,
    Unsupported = 2,
    Unreadable = 3,
    Racing = 4
};
// Flags: header read1, header/node samples stable2, retirement frame4,
// execution frame8, head stamp16, tail stamp32, main-view sample stable64.
#pragma pack(push, 1)
struct RendererDiagnostics {
    char magic[8];
    std::uint32_t sequence, abi, state, flags;
    std::uint64_t sample, uptime_ms, main_view_frames;
    std::uint64_t present_calls, panel_frames, init_attempts, init_successes, release_calls,
        resize_calls;
    std::uint32_t pending_count, capacity, allocated_slots, head_index, tail_index;
    std::uint32_t retirement_frame, execution_frame, head_buffer_frame, tail_buffer_frame,
        image_size;
    char renderer_hash[65];
    unsigned char hash_padding[7];
    char message[192];
    // ABI 2 occupies previously reserved bytes; all older counter offsets stay fixed.
    std::uint64_t overlay_draw_frames, guide_frames, overlay_last_us, overlay_max_us;
    std::uint64_t present_last_us, present_max_us, overlay_lock_skips;
    std::uint64_t overlay_active_since_ms, present_active_since_ms, guide_lines, guide_labels;
    unsigned char padding[24];
};
#pragma pack(pop)
static_assert(sizeof(RendererDiagnostics) == 512, "Optional graphics diagnostic layout");
static_assert(offsetof(RendererDiagnostics, pending_count) == 96,
              "Optional graphics counter offset");
static_assert(offsetof(RendererDiagnostics, renderer_hash) == 136, "Optional graphics hash offset");
static_assert(offsetof(RendererDiagnostics, message) == 208, "Optional graphics message offset");
static_assert(offsetof(RendererDiagnostics, overlay_draw_frames) == 400,
              "Optional overlay timing offset");
static_assert(kRendererDiagnosticsOffset >= kControlBytes + sizeof(Status),
              "No overlap with camera status");
static_assert(kRendererDiagnosticsOffset + sizeof(RendererDiagnostics) <= kControlBytes + 2048,
              "No overlap with editor status");
// Both functions run only on the native control worker. Probe once after the
// module loads; the hash is calculated off the render/input threads.
void renderer_diagnostics_probe(std::uintptr_t module, const char* file_sha256) noexcept;
void renderer_diagnostics_tick(unsigned char* mapping) noexcept;
}
