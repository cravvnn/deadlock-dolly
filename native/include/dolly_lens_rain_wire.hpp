#pragma once
// The editor's lens rain block: the shot's lens rain switch and look controls.
//
// dolly/weather.py mirrors this layout (LENS_WIRE_*) and the ranges (LENS_RANGES);
// tests assert the two stay in step. Do not wrap this header in a namespace named
// `dolly` (see AGENTS.md rule 1).

#include "dolly_lens_rain.hpp"

#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <optional>

namespace lens_rain {

constexpr char kWireMagic[8] = {'D', 'L', 'Y', 'L', 'E', 'N', 'S', '1'};
constexpr std::uint32_t kWireAbi = 1;
constexpr std::uint32_t kWireFlagEnabled = 1;
// Published seqlocked in the shared mapping (magic at 0, sequence at 8, ABI at 12),
// in the gap between the replay events block and the cinematic status.
constexpr std::size_t kWireOffset = 2 * 1024 * 1024 + 59392;

struct Range {
    float low, high;
};
// Controls in wire and drawer order: amount, size, slide, trails, refraction, softness.
constexpr Range kRanges[6] = {{0.0f, 1.0f}, {0.3f, 3.0f}, {0.0f, 2.0f},
                              {0.0f, 1.0f}, {0.0f, 2.0f}, {0.0f, 1.0f}};

#pragma pack(push, 1)
struct Wire {
    char magic[8];
    std::uint32_t sequence;
    std::uint32_t abi;
    std::uint32_t flags;
    // Shot start tick: seeds the shot's drops.
    std::int32_t start_tick;
    float amount;
    float size;
    float slide;
    float trails;
    float refraction;
    float softness;
};
#pragma pack(pop)
static_assert(sizeof(Wire) == 48, "Python lens rain wire layout must match");
static_assert(offsetof(Wire, sequence) == 8, "Seqlocked block layout");
static_assert(offsetof(Wire, amount) == 24, "Python lens rain wire layout must match");

struct Controls {
    bool enabled = false;
    Settings look;
};

inline bool in_range(float value, Range range) noexcept {
    return std::isfinite(value) && value >= range.low && value <= range.high;
}

// Rejects the wrong magic or ABI, unknown flag bits, a negative start tick and any
// value outside its range. A zeroed block (nothing published yet) is rejected too.
inline std::optional<Controls> decode(const Wire& wire) noexcept {
    if (std::memcmp(wire.magic, kWireMagic, sizeof(wire.magic)) != 0 || wire.abi != kWireAbi ||
        (wire.flags & ~kWireFlagEnabled) != 0 || wire.start_tick < 0)
        return std::nullopt;
    const float values[6] = {wire.amount, wire.size,       wire.slide,
                             wire.trails, wire.refraction, wire.softness};
    for (int i = 0; i < 6; ++i)
        if (!in_range(values[i], kRanges[i]))
            return std::nullopt;
    Controls c;
    c.enabled = (wire.flags & kWireFlagEnabled) != 0;
    c.look.amount = wire.amount;
    c.look.size = wire.size;
    c.look.slide = wire.slide;
    c.look.trails = wire.trails;
    c.look.refraction = wire.refraction;
    c.look.softness = wire.softness;
    // A fixed scramble of the shot start keeps neighbouring shots visibly different.
    c.look.seed = static_cast<std::uint32_t>(wire.start_tick) * 2654435761u ^ 0x1e35a7bdu;
    return c;
}

// In-game Scene drawer controls in EditorAction order from SetLensRain: the switch,
// then the six look controls. Whether `value` is valid for the control at `index`.
inline bool valid_panel_value(unsigned index, double value) noexcept {
    if (!std::isfinite(value))
        return false;
    if (index == 0)
        return value == 0.0 || value == 1.0;
    if (index > 6)
        return false;
    const Range r = kRanges[index - 1];
    return value >= r.low && value <= r.high;
}

} // namespace lens_rain
