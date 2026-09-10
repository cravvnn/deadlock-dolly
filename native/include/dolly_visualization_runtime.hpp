#pragma once

#include "dolly_visualization.hpp"
#include <cstdint>
#include <memory>

namespace dolly {

// Call only from the existing control worker. The optional read-only mapping
// is separate from ABI-3 camera control and is sampled no faster than 10 Hz.
// A disconnected editor drops guides immediately and closes the mapping.
void visualization_worker_tick(const wchar_t* main_mapping_name, bool connected) noexcept;

// Immutable pre-sampled geometry. No IPC, allocation, engine calls, or GPU
// work. Retired snapshots remain worker-owned until readers release them.
std::shared_ptr<const VisualizationPath> visualization_snapshot() noexcept;

enum class VisualizationRuntimeState : std::uint32_t {
    Disconnected = 0, Waiting = 1, Ready = 2, Disabled = 3,
    Invalid = 4, Updating = 5, Unavailable = 6
};
VisualizationRuntimeState visualization_runtime_state() noexcept;

} // namespace dolly
