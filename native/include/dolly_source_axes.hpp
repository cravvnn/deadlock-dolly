#pragma once
#include <array>
#include <cmath>

namespace dolly {

// Source pitch/yaw/roll degrees -> forward/right/up world axes. Z is up, yaw
// zero faces +X, positive pitch looks down, and right is -Y at yaw zero.
// Callers that wrap angles first pass the wrapped values.
struct SourceAxes {
    std::array<double, 3> forward{}, right{}, up{};
};

inline SourceAxes source_axes(double pitch, double yaw, double roll) noexcept {
    constexpr double radians = 3.14159265358979323846 / 180.0;
    const double sp = std::sin(pitch * radians), cp = std::cos(pitch * radians);
    const double sy = std::sin(yaw * radians), cy = std::cos(yaw * radians);
    const double sr = std::sin(roll * radians), cr = std::cos(roll * radians);
    return {{cp * cy, cp * sy, -sp},
            {-sr * sp * cy + cr * sy, -sr * sp * sy - cr * cy, -sr * cp},
            {cr * sp * cy + sr * sy, cr * sp * sy - sr * cy, cr * cp}};
}

} // namespace dolly
