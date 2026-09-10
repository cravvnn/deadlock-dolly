#pragma once
#include "dolly_path.hpp"
#include <array>
#include <vector>
#include <string>
#include <limits>

namespace dolly {
struct EffectSpec {
    const char* name;
    unsigned type;
    double minimum, maximum;
    bool discrete;
    unsigned components = 1;
};
inline constexpr double kEffectFloatMax = std::numeric_limits<float>::max();
inline constexpr std::array<EffectSpec, 14> kEffects{
    {{"r_citadel_depthoffield_enable", 0, 0, 1, true},
     {"r_citadel_depthoffield_focus_distance", 7, 0, 10000, false},
     {"r_citadel_depthoffield_aperture_diameter", 7, 0, 3, false},
     {"r_citadel_depthoffield_sensor_size", 7, .5, 3, false},
     {"r_citadel_depthoffield_mode", 3, 0, 2, true},
     {"r_citadel_depthoffield_debug", 0, 0, 1, true},
     {"r_depth_of_field", 0, 0, 1, true},
     {"r_dof_override", 0, 0, 1, true},
     {"r_dof_override_ranges", 13, -kEffectFloatMax, kEffectFloatMax, false, 4},
     {"r_dof_override_near_blurry", 7, -kEffectFloatMax, kEffectFloatMax, false},
     {"r_dof_override_near_crisp", 7, -kEffectFloatMax, kEffectFloatMax, false},
     {"r_dof_override_far_crisp", 7, -kEffectFloatMax, kEffectFloatMax, false},
     {"r_dof_override_far_blurry", 7, -kEffectFloatMax, kEffectFloatMax, false},
     {"r_dof_override_tilt_to_ground", 7, -kEffectFloatMax, kEffectFloatMax, false}}};
struct EffectSegment {
    double begin, end;
    unsigned kind, flags;
    double left, right, left_derivative, right_derivative;
};
struct EffectTrack {
    unsigned id = 0;
    bool restore_override = false;
    unsigned component = 0;
    double first_time = 0, last_time = 0, first = 0, last = 0, restore = 0;
    std::vector<EffectSegment> segments;
    bool evaluate(double phase, double& value) const noexcept;
};
class NativeShot {
public:
    NativePath camera;
    std::vector<EffectTrack> effects;
    bool load(const void* data, std::size_t size, std::string& error);
};
}
