#pragma once
#include <cmath>

namespace dolly {
// Replay sub-tick corrections can produce short/long steps. Admission follows
// actual advancing rendered views, never floor(phase * FPS).
inline bool capture_phase_advances(double previous, double phase) noexcept {
    return std::isfinite(phase) && phase >= 0 && phase > previous;
}
enum class CaptureImageAdmission { skip, capture, missing, inconsistent };
inline CaptureImageAdmission capture_image_admission(double previous, double phase,
                                                     bool image) noexcept {
    if (!capture_phase_advances(previous, phase))
        return image ? CaptureImageAdmission::inconsistent : CaptureImageAdmission::skip;
    return image ? CaptureImageAdmission::capture : CaptureImageAdmission::missing;
}
} // namespace dolly
