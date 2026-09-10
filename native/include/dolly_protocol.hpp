#pragma once
#include <cstddef>
#include <cstdint>

namespace dolly {
constexpr std::uint32_t kBridgeAbi = 3;
constexpr std::size_t kControlBytes = 2 * 1024 * 1024;
constexpr std::size_t kMappingBytes = kControlBytes + 4096;
constexpr std::size_t kPayloadOffset = 1024;
constexpr std::size_t kMaxPayloadBytes = kControlBytes - kPayloadOffset;
constexpr char kControlMagic[8] = {'D','L','Y','C','A','M','0','1'};
constexpr char kStatusMagic[8] = {'D','L','Y','S','T','A','T','1'};
enum class Mode : std::uint32_t { Release=0, Hold=1, Play=2, HoldCurrent=3, Manual=4 };
enum class State : std::uint32_t { Starting=0, Probe=1, Armed=2, Playing=3, Completed=4, Stopped=5, Fault=6, Unsupported=7 };
constexpr std::uint32_t kFrozen = 1;
constexpr std::uint32_t kAspect = 2;
#pragma pack(push, 1)
struct ControlHeader {
 char magic[8];
 std::uint32_t abi;
 std::uint32_t sequence;
 std::uint32_t command;
 std::uint32_t mode;
 std::uint32_t editor_pid;
 std::uint32_t game_pid;
 std::uint64_t heartbeat;
 std::uint32_t flags;
 std::uint32_t payload_bytes;
 double start_phase;
 double speed;
 char demo_name[512];
};
struct Status {
 char magic[8];
 std::uint32_t sequence;
 std::uint32_t abi;
 std::uint32_t state;
 std::uint32_t game_pid;
 std::uint32_t ack_command;
 std::uint32_t error;
 std::uint64_t frame_count;
 double real_time;
 double engine_time;
 double phase;
 std::int32_t tick;
 std::uint32_t paused;
 double original_pose[7];
 double applied_pose[7];
 double original_fov;
 double applied_fov;
 std::uint64_t hook_calls;
 char message[256];
 char demo_name[512];
 double max_frame_interval_ms;
 double frame_interval_ms;
 std::uint32_t effect_count;
 std::uint32_t effect_error;
 std::uint64_t effect_frames;
 double effect_phase;
};
#pragma pack(pop)
static_assert(sizeof(ControlHeader)==576, "Python control layout must match");
static_assert(offsetof(ControlHeader, heartbeat)==32, "Heartbeat requires aligned atomic access");
static_assert(offsetof(Status, original_pose)==72, "Python status layout must match");
static_assert(offsetof(Status, message)==208, "Python message layout must match");
static_assert(sizeof(Status)==1016, "Python status size must match");
}
