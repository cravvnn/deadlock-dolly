#pragma once
#include <cstdint>
#include "dolly_protocol.hpp"
struct ID3D11DeviceContext;
namespace dolly::environment {
constexpr std::size_t ProbeRequestOffset = kEnvironmentPageOffset;
constexpr std::size_t ProbeStatusOffset = kEnvironmentPageOffset + 64;
#pragma pack(push, 1)
struct ProbeRequest {
    char magic[8];
    std::uint32_t sequence, abi, command, enabled, editor_pid, game_pid;
    std::uint32_t frames, draws, milliseconds, reserved;
};
struct ProbeStatus {
    char magic[8];
    std::uint32_t sequence, abi, command, state;
    char json[1024];
};
#pragma pack(pop)
static_assert(sizeof(ProbeRequest) == 48 && sizeof(ProbeStatus) == 1048);
static_assert(ProbeRequestOffset >= kEnvironmentPageOffset, "After the entire legacy mapping");
static_assert(ProbeRequestOffset + sizeof(ProbeRequest) <= ProbeStatusOffset);
static_assert(ProbeStatusOffset + sizeof(ProbeStatus) <= kEnvironmentPageOffset + 1152,
              "Probe status ends before the HDR request block");
// Only the owned editor's control worker may arm/publish. State: 0 idle,
// 1 observing, 2 bounded completion, 3 cancelled, 4 invalid, 5 hooks unavailable.
void probe_worker(unsigned char* mapping, bool owned, unsigned editor_pid, unsigned game_pid) noexcept;
bool probe_active() noexcept;
void probe_hooks(bool success) noexcept;
void probe_present() noexcept;
void probe_draw(ID3D11DeviceContext* context) noexcept;
}
