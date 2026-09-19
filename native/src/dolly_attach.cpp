#include "dolly_attach.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <exception>
#include <utility>

namespace dolly {
namespace {

constexpr double kDegreesToRadians = 3.14159265358979323846 / 180.0;

class Reader {
public:
    explicit Reader(const void* data) : cursor_(static_cast<const std::uint8_t*>(data)) {}

    std::uint32_t u32() noexcept {
        std::uint32_t value = 0;
        for (unsigned i = 0; i != 4; ++i)
            value |= std::uint32_t(*cursor_++) << (i * 8);
        return value;
    }
    std::uint64_t u64() noexcept {
        std::uint64_t value = 0;
        for (unsigned i = 0; i != 8; ++i)
            value |= std::uint64_t(*cursor_++) << (i * 8);
        return value;
    }
    double number() noexcept {
        std::uint64_t bits = 0;
        for (unsigned i = 0; i != 8; ++i)
            bits |= std::uint64_t(*cursor_++) << (i * 8);
        double value;
        std::memcpy(&value, &bits, sizeof(value));
        return value;
    }
    void skip(std::size_t bytes) noexcept { cursor_ += bytes; }

private:
    const std::uint8_t* cursor_;
};

bool finite3(const std::array<double, 3>& value, double limit) noexcept {
    for (double component : value)
        if (!std::isfinite(component) || std::abs(component) > limit)
            return false;
    return true;
}

// Source pitch/yaw/roll degrees, the same convention as camera poses: yaw zero
// faces +X, pitch is positive down, and right is -Y at yaw zero.
void source_basis(const std::array<double, 3>& angles, double basis[3][3]) noexcept {
    const double sp = std::sin(angles[0] * kDegreesToRadians),
                 cp = std::cos(angles[0] * kDegreesToRadians);
    const double sy = std::sin(angles[1] * kDegreesToRadians),
                 cy = std::cos(angles[1] * kDegreesToRadians);
    const double sr = std::sin(angles[2] * kDegreesToRadians),
                 cr = std::cos(angles[2] * kDegreesToRadians);
    const double forward[3] = {cp * cy, cp * sy, -sp};
    const double right[3] = {-sr * sp * cy + cr * sy, -sr * sp * sy - cr * cy, -sr * cp};
    const double up[3] = {cr * sp * cy + sr * sy, cr * sp * sy - sr * cy, cr * cp};
    for (int i = 0; i < 3; ++i) {
        basis[i][0] = forward[i];
        basis[i][1] = right[i];
        basis[i][2] = up[i];
    }
}

void rotate(const double basis[3][3], const std::array<double, 3>& local,
            std::array<double, 3>& out) noexcept {
    for (int i = 0; i < 3; ++i)
        out[i] = basis[i][0] * local[0] + basis[i][1] * local[1] + basis[i][2] * local[2];
}

} // namespace

void AttachSmoothing::reset() noexcept {
    primed_ = false;
    pose_.fill(0);
    pose_[6] = 16.0 / 9;
}

void AttachSmoothing::apply(CameraPose& pose, double dt, double tau) noexcept {
    if (!primed_ || !(tau > 0) || !(dt > 0)) {
        pose_ = pose;
        primed_ = true;
        return;
    }
    dt = std::min(dt, 0.25); // A stall must not snap the camera.
    const double alpha = 1 - std::exp(-dt / tau);
    for (int i = 0; i < 3; ++i)
        pose_[i] += (pose[i] - pose_[i]) * alpha;
    for (int i = 3; i < 6; ++i)
        pose_[i] += std::remainder(pose[i] - pose_[i], 360.0) * alpha;
    pose_[6] = pose[6];
    pose = pose_;
}

bool attach_view_offset(const std::array<float, 3>& values, std::array<double, 3>& out) noexcept {
    for (int i = 0; i < 3; ++i) {
        const double value = values[i];
        if (!std::isfinite(value) || std::abs(value) > kAttachViewOffsetLimit)
            return false;
        out[i] = value;
    }
    return true;
}

const char* attach_hero_name(const char* model_path) noexcept {
    if (!model_path)
        return nullptr;
    const char* stem = model_path;
    for (const char* p = model_path; *p; ++p)
        if (*p == '/' || *p == '\\')
            stem = p + 1;
    std::size_t length = 0;
    while (stem[length] && stem[length] != '.')
        ++length;
    struct Entry {
        const char* stem;
        const char* name;
    };
    static constexpr Entry kNames[] = {
        {"astro", "Holliday"}, {"abrams", "Abrams"},       {"familiar_wip", "Rem"},
        {"yamato", "Yamato"},  {"digger", "Mo and Krill"}, {"drifter", "Drifter"},
        {"lash", "Lash"},      {"necro", "Graves"},        {"wraith", "Wraith"},
        {"nano", "Calico"},    {"hornet", "Vindicta"},     {"warden", "Warden"}};
    for (const auto& entry : kNames) {
        if (std::strlen(entry.stem) == length && std::strncmp(stem, entry.stem, length) == 0)
            return entry.name;
    }
    return nullptr;
}

bool attach_snap_offsets(const AttachSample& sample, const CameraPose& camera,
                         std::array<double, 6>& out) noexcept {
    if (!sample.valid || !finite3(sample.origin, 1e8) || !finite3(sample.angles, 1e6) ||
        !finite3(sample.aim, 1e6) || !finite3(sample.eye_local, kAttachViewOffsetLimit))
        return false;
    for (int i = 0; i < 6; ++i)
        if (!std::isfinite(camera[i]))
            return false;
    const std::array<double, 3> base_angles =
        sample.point == AttachPoint::eyes ? sample.aim : sample.angles;
    std::array<double, 3> base_position = sample.origin;
    if (sample.point == AttachPoint::eyes) {
        double body[3][3];
        source_basis(sample.angles, body);
        std::array<double, 3> local{};
        rotate(body, sample.eye_local, local);
        for (int i = 0; i < 3; ++i)
            base_position[i] += local[i];
    }
    double basis[3][3];
    source_basis(base_angles, basis);
    const std::array<double, 3> delta = {camera[0] - base_position[0], camera[1] - base_position[1],
                                         camera[2] - base_position[2]};
    for (int axis = 0; axis < 3; ++axis) {
        double value = 0.0;
        for (int i = 0; i < 3; ++i)
            value += delta[i] * basis[i][axis];
        if (!std::isfinite(value) || std::abs(value) > kAttachOffsetLimit)
            return false;
        out[axis] = value;
    }
    for (int axis = 0; axis < 3; ++axis) {
        const double value = std::remainder(camera[3 + axis] - base_angles[axis], 360.0);
        if (!std::isfinite(value) || std::abs(value) > kAttachOffsetLimit)
            return false;
        out[3 + axis] = value;
    }
    return true;
}

bool resolve_attach_pose(const AttachSample& sample, const AttachSegment& segment,
                         CameraPose& out) noexcept {
    if (!sample.valid || sample.point != segment.point)
        return false;
    if (segment.target.handle && sample.target.handle != segment.target.handle)
        return false;
    if (segment.target.entity_id && sample.target.entity_id != segment.target.entity_id)
        return false;
    if (segment.target.model && sample.target.model != segment.target.model)
        return false;
    if (!finite3(sample.origin, 1e8) || !finite3(sample.angles, 1e6) || !finite3(sample.aim, 1e6) ||
        !finite3(sample.eye_local, kAttachViewOffsetLimit))
        return false;
    const std::array<double, 3> base_angles =
        segment.point == AttachPoint::eyes ? sample.aim : sample.angles;
    std::array<double, 3> base_position = sample.origin;
    if (segment.point == AttachPoint::eyes) {
        double body[3][3];
        source_basis(sample.angles, body);
        std::array<double, 3> local{};
        rotate(body, sample.eye_local, local);
        for (int i = 0; i < 3; ++i)
            base_position[i] += local[i];
    }
    double basis[3][3];
    source_basis(base_angles, basis);
    std::array<double, 3> position_offset{};
    rotate(basis, {segment.offset[0], segment.offset[1], segment.offset[2]}, position_offset);
    for (int i = 0; i < 3; ++i)
        out[i] = base_position[i] + position_offset[i];
    for (int i = 0; i < 3; ++i)
        out[i + 3] = base_angles[i] + segment.offset[i + 3];
    for (int i = 0; i < 6; ++i)
        if (!std::isfinite(out[i]) || std::abs(out[i]) > 1e8)
            return false;
    return true;
}

bool AttachTrack::load(const void* data, std::size_t bytes, double duration, std::string& error) {
    auto fail = [&error](const char* reason) {
        error = reason;
        return false;
    };
    error.clear();
    if (!std::isfinite(duration) || duration < 0)
        return fail("Attach track duration is invalid");
    if (bytes == 0) {
        AttachTrack empty;
        empty.duration_ = duration;
        *this = std::move(empty);
        return true;
    }
    if (!data || bytes < header_bytes || bytes > max_bytes3)
        return fail("Attach block has an invalid byte length");
    if (std::memcmp(data, "DLYATT01", 8) != 0)
        return fail("Attach block magic does not match");
    Reader reader(data);
    reader.skip(8);
    const auto version = reader.u32();
    const auto reserved = reader.u32();
    const auto count = reader.u32();
    if ((version != 1 && version != 2 && version != 3) || reserved != 0 || count > max_segments)
        return fail("Attach block header is invalid");
    const std::size_t stride = version == 3   ? segment_bytes3
                               : version == 2 ? segment_bytes2
                                              : segment_bytes;
    if (bytes != header_bytes + std::size_t(count) * stride)
        return fail("Attach block header is invalid");
    AttachTrack candidate;
    try {
        candidate.segments_.reserve(count);
        double previous_end = 0;
        for (std::uint32_t index = 0; index < count; ++index) {
            AttachSegment segment;
            segment.begin = reader.number();
            segment.end = reader.number();
            segment.flags = reader.u32();
            const auto point = reader.u32();
            segment.target.handle = reader.u32();
            segment.target.entity_id = reader.u32();
            segment.target.model = reader.u64();
            for (double& value : segment.offset)
                value = reader.number();
            segment.smoothing = reader.number();
            if (version >= 2)
                segment.bone_hash = reader.u64();
            if (version == 3)
                segment.source_blend = reader.number();
            if (!std::isfinite(segment.begin) || !std::isfinite(segment.end) ||
                segment.begin != previous_end ||
                (segment.end < segment.begin ||
                 (segment.end == segment.begin &&
                  !(version == 3 && index + 1 == count && segment.begin == duration))) ||
                segment.end > duration + 1e-6 || segment.flags > 3 ||
                point > static_cast<std::uint32_t>(version >= 2 ? AttachPoint::bone
                                                                : AttachPoint::weapon))
                return fail("Attach segments are not ordered and contiguous");
            if (!std::isfinite(segment.source_blend) || segment.source_blend < 0 ||
                segment.source_blend > 10)
                return fail("Source blend duration is invalid");
            segment.point = static_cast<AttachPoint>(point);
            if (segment.point == AttachPoint::bone && segment.bone_hash == 0)
                return fail("A bone attach segment has no bone identity");
            for (double value : segment.offset)
                if (!std::isfinite(value) || std::abs(value) > kAttachOffsetLimit)
                    return fail("Attach segment offset is invalid");
            if (!std::isfinite(segment.smoothing) || segment.smoothing < 0 ||
                segment.smoothing > kAttachSmoothingLimit)
                return fail("Attach segment smoothing is invalid");
            if ((segment.flags & 1) && !segment.target.handle && !segment.target.model)
                return fail("An enabled attach segment has no target identity");
            candidate.segments_.push_back(segment);
            previous_end = segment.end;
        }
        if (!candidate.segments_.empty() && candidate.segments_.front().begin != 0)
            return fail("Attach segment coverage must start at zero");
    } catch (const std::exception&) {
        return fail("Could not allocate attach track storage");
    }
    candidate.duration_ = duration;
    *this = std::move(candidate);
    return true;
}

const AttachSegment* AttachTrack::at(double phase) const noexcept {
    if (!std::isfinite(phase) || segments_.empty() || phase < segments_.front().begin)
        return nullptr;
    std::size_t low = 0, high = segments_.size();
    while (low < high) {
        const auto mid = low + (high - low) / 2;
        if (segments_[mid].begin <= phase)
            low = mid + 1;
        else
            high = mid;
    }
    return &segments_[low - 1];
}

double attach_blend_weight(double phase, double begin, double end) noexcept {
    if (!(end > begin))
        return 1;
    const double t = std::clamp((phase - begin) / (end - begin), 0.0, 1.0);
    return t * t * t * (t * (t * 6 - 15) + 10);
}
CameraPose blend_attach_poses(const CameraPose& from, const CameraPose& to,
                              double weight) noexcept {
    CameraPose result = to;
    const double w = std::clamp(weight, 0.0, 1.0);
    for (int axis = 0; axis < 6; ++axis) {
        const double delta =
            axis < 3 ? to[axis] - from[axis] : std::remainder(to[axis] - from[axis], 360.0);
        result[axis] = from[axis] + delta * w;
    }
    return result;
}
const AttachSegment* AttachTrack::arriving(double phase, double& weight) const noexcept {
    weight = 0;
    const auto current = at(phase);
    if (!current)
        return nullptr;
    const auto index = std::size_t(current - segments_.data());
    if (index + 1 >= segments_.size())
        return nullptr;
    const auto& next = segments_[index + 1];
    if (!(next.source_blend > 0))
        return nullptr;
    const double start = std::max(current->begin, next.begin - next.source_blend);
    if (phase < start || !(next.begin > start))
        return nullptr;
    weight = attach_blend_weight(phase, start, next.begin);
    return &next;
}

const AttachSegment* AttachTrack::first_enabled() const noexcept {
    for (const auto& segment : segments_)
        if (segment.flags & 1)
            return &segment;
    return nullptr;
}

} // namespace dolly
