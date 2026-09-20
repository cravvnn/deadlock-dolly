#pragma once

#include <cstdint>

namespace dolly::confetti {

struct Camera {
    struct Vec3 { float x{}, y{}, z{}; } origin{};
    struct Angles { float pitch{}, yaw{}, roll{}; } angles{};
    float fov{90.0f};
};

// The particle entry points are enabled only for a fingerprinted client build.
bool initialize(std::uintptr_t client_base) noexcept;
void disable() noexcept;
void on_frame(double replay_time, bool replay_active, bool seeking,
              const Camera* camera, bool enabled, float spawn_height,
              bool despawn_on_ground) noexcept;
const char* status() noexcept;

} // namespace dolly::confetti
