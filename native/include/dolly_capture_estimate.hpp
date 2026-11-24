#pragma once
// Take-size estimate and recording progress text for the in-game Export drawer.
// Mirrors dolly/capture_estimate.py: the same inputs give the same words on the
// desktop and in game (shared cases in native/tests/capture_estimate_tests.cpp
// and tests/test_capture_estimate.py). Text uses only Latin-1 so the overlay's
// default glyph ranges draw it; " \xC2\xB7 " is the middle-dot separator.
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <string>

namespace dolly::estimate {

inline constexpr double kMinRateSeconds = 5.0;
inline constexpr std::uint64_t kMinRateFrames = 30;
// Below this the remaining time reads "almost done" instead of "about 5 s left".
inline constexpr double kAlmostDoneSeconds = 5.0;
inline constexpr const char* kDot = " \xC2\xB7 ";

// Frames one take renders; 0 when the shot length is unknown. Export speed
// applies only to fixed-step takes.
inline std::uint64_t take_frames(double duration, double fps, double speed,
                                 bool fixed_step) noexcept {
    if (!fixed_step)
        speed = 1.0;
    if (!std::isfinite(duration) || !std::isfinite(fps) || !std::isfinite(speed) ||
        duration <= 0 || fps <= 0 || speed <= 0)
        return 0;
    const double frames = std::ceil(duration * fps / speed - 1e-6);
    return frames < 1 ? 1 : static_cast<std::uint64_t>(frames);
}
inline double video_seconds(double duration, double speed, bool fixed_step) noexcept {
    return duration / (fixed_step ? speed : 1.0);
}
inline std::string grouped(std::uint64_t value) {
    std::string digits = std::to_string(value), out;
    for (std::size_t i = 0; i < digits.size(); ++i) {
        if (i && (digits.size() - i) % 3 == 0)
            out += ',';
        out += digits[i];
    }
    return out;
}
// "40 s", "3 min", "1 h 5 min"; half-up rounding like the Python copy.
inline std::string format_duration(double seconds) {
    if (!(seconds > 0))
        seconds = 0;
    char text[48];
    if (seconds < 55) {
        long long rounded = static_cast<long long>(std::floor(seconds / 5.0 + .5)) * 5;
        std::snprintf(text, sizeof(text), "%lld s", rounded < 5 ? 5 : rounded);
        return text;
    }
    long long minutes = static_cast<long long>(std::floor(seconds / 60.0 + .5));
    if (minutes < 1)
        minutes = 1;
    if (minutes < 60) {
        std::snprintf(text, sizeof(text), "%lld min", minutes);
        return text;
    }
    if (minutes % 60)
        std::snprintf(text, sizeof(text), "%lld h %lld min", minutes / 60, minutes % 60);
    else
        std::snprintf(text, sizeof(text), "%lld h", minutes / 60);
    return text;
}
// Before recording: "15,720 frames · 26.2 s of video · about 24 min to record".
// `rate` <= 0 omits the time. A layered export records several passes of
// different cost, so `per_pass` gives the per-pass size without a time.
inline std::string estimate_line(std::uint64_t frames, double video, double rate,
                                 bool per_pass = false) {
    char length[32];
    std::snprintf(length, sizeof(length), "%.1f s of video", video);
    if (per_pass)
        return grouped(frames) + " frames per pass" + kDot + length;
    std::string text = grouped(frames) + " frames" + kDot + length;
    if (rate > 0 && std::isfinite(rate))
        text += std::string(kDot) + "about " + format_duration(double(frames) / rate) + " to record";
    return text;
}
// While recording: "1,810 / 15,720 frames · about 21 min left". `total` 0 is
// unknown; `seconds_left` < 0 is not yet measured.
inline std::string progress_line(std::uint64_t written, std::uint64_t total,
                                 double seconds_left) {
    if (!total)
        return grouped(written) + " frames";
    std::string text =
        grouped(written) + " / " + grouped(total > written ? total : written) + " frames";
    if (!(seconds_left >= 0))
        return text + kDot + "estimating time left";
    if (seconds_left < kAlmostDoneSeconds)
        return text + kDot + "almost done";
    return text + kDot + "about " + format_duration(seconds_left) + " left";
}
// Frames per wall-clock second once enough of the take has run, else 0.
inline double capture_rate(std::uint64_t frames, double elapsed) noexcept {
    if (frames < kMinRateFrames || !(elapsed >= kMinRateSeconds))
        return 0;
    return double(frames) / elapsed;
}
inline double seconds_left(std::uint64_t written, std::uint64_t total, double rate) noexcept {
    if (!(rate > 0))
        return -1;
    return double(total > written ? total - written : 0) / rate;
}
// Seconds between two QueryPerformanceCounter readings; 0 when unknown.
inline double elapsed(std::uint64_t first_qpc, std::uint64_t now_qpc,
                      std::uint64_t frequency) noexcept {
    if (!first_qpc || !frequency || now_qpc <= first_qpc)
        return 0;
    return double(now_qpc - first_qpc) / double(frequency);
}
// Kinds of take whose capture rate is remembered separately: no depth, depth,
// depth with EXR (EXR without depth is no depth).
inline constexpr int kRateKinds = 3;
inline int rate_kind(bool depth, bool depth_exr) noexcept {
    return depth ? (depth_exr ? 2 : 1) : 0;
}

} // namespace dolly::estimate
