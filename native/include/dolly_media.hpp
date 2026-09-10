#pragma once
#include <cstddef>
#include <cstdint>

namespace dolly {
constexpr std::uint32_t kMediaAbi = 1;
constexpr std::size_t kMediaMappingBytes = 12288, kMediaStatusOffset = 8192;
#pragma pack(push, 1)
struct MediaCommand {
    char magic[8];
    std::uint32_t sequence, abi, command, fps, bitrate, reserved;
    char16_t path[1024], config_path[1024];
};
struct MediaStatus {
    char magic[8];
    std::uint32_t sequence, abi, ack, command_error, video_state, fps, width, height, reshade_state,
        reshade_open;
    std::uint64_t frames_written, frames_dropped, duration_100ns;
    std::uint32_t video_error, reshade_error;
    char16_t video_message[384], reshade_message[384], command_message[384];
};
#pragma pack(pop)
static_assert(sizeof(MediaCommand) == 4128, "Python media command layout");
static_assert(sizeof(MediaStatus) == 2384, "Python media status layout");
static_assert(offsetof(MediaStatus, frames_written) == 48, "Python media counter offset");
static_assert(kMediaStatusOffset + sizeof(MediaStatus) <= kMediaMappingBytes,
              "Media status fits mapping");
// Optional transport derived from the verified, private native session name.
// Only the existing native worker calls this. No rendering thread waits for it.
// Independent of manual editor ownership; expires with the verified session heartbeat.
bool media_session_active() noexcept;
void media_worker_tick(const wchar_t* session_name, bool connected) noexcept;
}
