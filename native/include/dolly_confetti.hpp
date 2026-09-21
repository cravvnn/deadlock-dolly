#pragma once

#include <cstdint>

namespace dolly::confetti {

struct Camera {
    struct Vec3 {
        float x{}, y{}, z{};
    } origin{};
    struct Angles {
        float pitch{}, yaw{}, roll{};
    } angles{};
    float fov{90.0f};
};

// Bounded Dolly-side counters, published for diagnostics only. They prove
// whether Dolly created, reset or released an effect at a given moment; they
// carry no engine liveness or particle-state information.
struct Diagnostics {
    std::uint32_t state{};
    std::uint32_t handles{};
    std::uint32_t starts{}, start_failures{};
    std::uint32_t frames{}, running_frames{}, resets{};
    std::uint32_t stop_disabled{}, stop_reconfigured{}, stop_seek{}, stop_backward{};
    std::uint32_t stop_invalid{}, stop_shutdown{}, stop_create_failed{};
    std::uint32_t state_changes{};
    // Worst backward step seen from the published replay clock, in seconds.
    double max_backward_delta{};
};

// The particle entry points are enabled only for a fingerprinted client build.
bool initialize(std::uintptr_t client_base) noexcept;
void disable() noexcept;
void on_frame(double replay_time, bool replay_active, bool seeking, const Camera* camera,
              bool enabled, float spawn_height, bool despawn_on_ground) noexcept;
const char* status() noexcept;
Diagnostics diagnostics() noexcept;

} // namespace dolly::confetti
