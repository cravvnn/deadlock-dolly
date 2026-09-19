#pragma once

#include "dolly_path.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace dolly {

// Attach camera (POV / weapon) payload and resolver.
//
// The Python editor compiles one attach segment per camera time range. An
// enabled segment replaces the free path pose for its range with a live player
// or weapon transform plus authored local offsets; a disabled segment keeps
// the free path. Source changes cut unless the arriving segment opts into
// an eased blend ending at its boundary. The
// per-frame provider fills AttachSample from bounded game reads and calls
// resolve_attach_pose, which performs no allocation, lock, engine call or
// clock access.

enum class AttachPoint : std::uint32_t {
    eyes = 0,
    weapon = 1,
    bone = 2,
};

struct AttachTarget {
    std::uint32_t handle = 0;    // Packed replay pawn handle.
    std::uint32_t entity_id = 0; // Replay entity identifier, 0 when unknown.
    std::uint64_t model = 0;     // Stable model identity, 0 when unknown.
};

struct AttachSegment {
    double begin = 0, end = 0; // Shot seconds; coverage is contiguous from zero.
    std::uint32_t flags = 0;   // 1 enabled, 2 hide the target body.
    AttachPoint point = AttachPoint::eyes;
    AttachTarget target;
    // Local XYZ and pitch/yaw/roll offsets in the resolved frame.
    std::array<double, 6> offset{};
    double smoothing = 0; // Exponential smoothing time constant (seconds).
    // Version 2: FNV-1a 64 of the bone name for AttachPoint::bone.
    std::uint64_t bone_hash = 0;
    double source_blend = 0; // Version 3: eased arrival, ending at begin.
};

// One live sample of the selected target. The provider fills this from bounded
// reads; a partial, stale or identity-mismatched sample stays invalid.
struct AttachSample {
    bool valid = false;
    AttachPoint point = AttachPoint::eyes;
    AttachTarget target;
    std::array<double, 3> eye_local{}; // Decoded m_vecViewOffset.
    std::array<double, 3> origin{};    // Target world origin.
    std::array<double, 3> angles{};    // Body angles (eyes) or node angles (weapon).
    std::array<double, 3> aim{};       // Eye angles, used by the eyes point.
};

// Exponential pose smoothing across rendered views. reset() on mode or target
// changes; apply() overwrites the pose with the smoothed value.
class AttachSmoothing {
public:
    void reset() noexcept;
    bool primed() const noexcept { return primed_; }
    void apply(CameraPose& pose, double dt, double tau) noexcept;

private:
    CameraPose pose_{};
    bool primed_ = false;
};

inline constexpr double kAttachViewOffsetLimit = 200.0;
inline constexpr double kAttachOffsetLimit = 10000.0;
inline constexpr double kAttachSmoothingLimit = 5.0;

// Validate the three floats read at m_vecViewOffset +16/+24/+32.
bool attach_view_offset(const std::array<float, 3>& values, std::array<double, 3>& out) noexcept;

// Verified display name for a model path; nullptr for an unknown stem so the
// caller can show a readable stem instead of guessing a hero.
const char* attach_hero_name(const char* model_path) noexcept;

// Fill out[0..5] from the sample and segment; out[6] is preserved so the
// authored aspect keeps applying. Refuses a missing sample, a point or
// identity mismatch, and nonfinite values.
bool resolve_attach_pose(const AttachSample& sample, const AttachSegment& segment,
                         CameraPose& out) noexcept;

// Authored offsets that place the attached camera exactly at ``camera`` for the
// given live sample (position in the resolved frame, angles relative to the
// resolved orientation). Returns false on nonfinite data or out-of-range
// offsets. resolve_attach_pose(sample, segment with these offsets) reproduces
// the camera pose up to angle wrapping.
bool attach_snap_offsets(const AttachSample& sample, const CameraPose& camera,
                         std::array<double, 6>& out) noexcept;
// Quintic easing gives zero slope and acceleration at both ends. Angles take
// their shortest route; aspect stays with the existing authored lens curve.
double attach_blend_weight(double phase, double begin, double end) noexcept;
CameraPose blend_attach_poses(const CameraPose& from, const CameraPose& to, double weight) noexcept;

class AttachTrack {
public:
    static constexpr std::size_t max_segments = 256;
    static constexpr std::size_t header_bytes = 20;
    static constexpr std::size_t segment_bytes = 96;
    static constexpr std::size_t segment_bytes2 = 104;
    static constexpr std::size_t segment_bytes3 = 112;
    static constexpr std::size_t max_bytes = header_bytes + max_segments * segment_bytes;
    static constexpr std::size_t max_bytes2 = header_bytes + max_segments * segment_bytes2;
    static constexpr std::size_t max_bytes3 = header_bytes + max_segments * segment_bytes3;

    // Load one DLYATT01 block on the non-render thread. Zero bytes is a valid
    // empty track (every camera key is free). Failure leaves a previous valid
    // object untouched.
    bool load(const void* data, std::size_t bytes, double duration, std::string& error);

    // Hard cuts: the segment covering phase, or nullptr before the first
    // segment. The caller checks the enabled flag.
    const AttachSegment* at(double phase) const noexcept;
    const AttachSegment* arriving(double phase, double& weight) const noexcept;
    // First enabled segment, used by the worker to resolve a target once.
    const AttachSegment* first_enabled() const noexcept;
    bool empty() const noexcept { return segments_.empty(); }
    const std::vector<AttachSegment>& segments() const noexcept { return segments_; }
    double duration() const noexcept { return duration_; }

private:
    std::vector<AttachSegment> segments_;
    double duration_ = 0;
};

} // namespace dolly
