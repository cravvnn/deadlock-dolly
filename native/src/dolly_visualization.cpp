#include "dolly_visualization.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <exception>
#include <utility>

namespace dolly {
namespace {
std::uint32_t u32(const unsigned char* p) noexcept {
    return std::uint32_t(p[0]) | (std::uint32_t(p[1]) << 8) | (std::uint32_t(p[2]) << 16) |
           (std::uint32_t(p[3]) << 24);
}
double number(const unsigned char* p) noexcept {
    std::uint64_t bits = 0;
    for (unsigned i = 0; i < 8; ++i)
        bits |= std::uint64_t(p[i]) << (8 * i);
    double result;
    std::memcpy(&result, &bits, sizeof(result));
    return result;
}
bool valid_pose(const CameraPose& pose) noexcept {
    for (double value : pose)
        if (!std::isfinite(value) || std::abs(value) > 1e8)
            return false;
    return pose[6] >= .25 && pose[6] <= 8;
}
using Vec3 = std::array<double, 3>;
double dot(const Vec3& a, const Vec3& b) noexcept {
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}
bool finite(const Vec3& p) noexcept {
    return std::isfinite(p[0]) && std::isfinite(p[1]) && std::isfinite(p[2]);
}
struct Basis {
    Vec3 forward{}, right{}, up{};
};
Basis angle_vectors(const CameraPose& p) noexcept {
    constexpr double radians = 3.14159265358979323846 / 180;
    const double pitch = std::remainder(p[3], 360.0) * radians;
    const double yaw = std::remainder(p[4], 360.0) * radians;
    const double roll = std::remainder(p[5], 360.0) * radians;
    const double sp = std::sin(pitch), cp = std::cos(pitch), sy = std::sin(yaw), cy = std::cos(yaw);
    const double sr = std::sin(roll), cr = std::cos(roll);
    // Source coordinates: Z up, yaw zero faces +X, positive pitch looks down.
    return {{cp * cy, cp * sy, -sp},
            {-sr * sp * cy + cr * sy, -sr * sp * sy - cr * cy, -sr * cp},
            {cr * sp * cy + sr * sy, cr * sp * sy - sr * cy, cr * cp}};
}
struct Projection {
    Basis basis{};
    Vec3 origin{};
    double tan_x = 0, tan_y = 0, near_plane = 1, width = 0, height = 0;
    bool setup(const VisualizationView& view) noexcept {
        if (!valid_pose(view.pose) || !std::isfinite(view.horizontal_fov) ||
            view.horizontal_fov <= 1 || view.horizontal_fov >= 179 || !std::isfinite(view.width) ||
            !std::isfinite(view.height) || view.width <= 0 || view.height <= 0 ||
            view.width > 65536 || view.height > 65536 || !std::isfinite(view.near_plane) ||
            view.near_plane < .001 || view.near_plane > 10000)
            return false;
        basis = angle_vectors(view.pose);
        origin = {view.pose[0], view.pose[1], view.pose[2]};
        tan_x = std::tan(view.horizontal_fov * 3.14159265358979323846 / 360);
        tan_y = tan_x / view.pose[6];
        width = view.width;
        height = view.height;
        near_plane = view.near_plane;
        return std::isfinite(tan_x) && std::isfinite(tan_y) && tan_x > 0 && tan_y > 0;
    }
    Vec3 camera(const Vec3& world) const noexcept {
        const Vec3 relative{world[0] - origin[0], world[1] - origin[1], world[2] - origin[2]};
        return {dot(relative, basis.right), dot(relative, basis.up), dot(relative, basis.forward)};
    }
    std::array<double, 5> planes(const Vec3& p) const noexcept {
        return {p[2] - near_plane, p[2] * tan_x + p[0], p[2] * tan_x - p[0], p[2] * tan_y + p[1],
                p[2] * tan_y - p[1]};
    }
    VisualizationPoint screen(const Vec3& p) const noexcept {
        return {float(std::clamp((.5 + .5 * p[0] / (p[2] * tan_x)) * width, 0.0, width)),
                float(std::clamp((.5 - .5 * p[1] / (p[2] * tan_y)) * height, 0.0, height))};
    }
    bool point(const Vec3& world, VisualizationPoint& out) const noexcept {
        if (!finite(world))
            return false;
        const Vec3 p = camera(world);
        if (!finite(p))
            return false;
        for (double d : planes(p))
            if (d < 0 || !std::isfinite(d))
                return false;
        out = screen(p);
        return true;
    }
    bool line(const Vec3& world_a, const Vec3& world_b, VisualizationPoint& out_a,
              VisualizationPoint& out_b) const noexcept {
        if (!finite(world_a) || !finite(world_b))
            return false;
        const Vec3 a = camera(world_a), b = camera(world_b);
        if (!finite(a) || !finite(b))
            return false;
        const auto pa = planes(a), pb = planes(b);
        double enter = 0, leave = 1;
        for (std::size_t i = 0; i < pa.size(); ++i) {
            if (!std::isfinite(pa[i]) || !std::isfinite(pb[i]))
                return false;
            if (pa[i] < 0 && pb[i] < 0)
                return false;
            if (pa[i] < 0)
                enter = std::max(enter, pa[i] / (pa[i] - pb[i]));
            else if (pb[i] < 0)
                leave = std::min(leave, pa[i] / (pa[i] - pb[i]));
            if (enter > leave)
                return false;
        }
        Vec3 clipped_a{}, clipped_b{};
        for (std::size_t i = 0; i < 3; ++i) {
            clipped_a[i] = (1 - enter) * a[i] + enter * b[i];
            clipped_b[i] = (1 - leave) * a[i] + leave * b[i];
        }
        // Floating-point rounding at a crossing must never divide by zero.
        if (clipped_a[2] <= 0 || clipped_b[2] <= 0)
            return false;
        out_a = screen(clipped_a);
        out_b = screen(clipped_b);
        return true;
    }
};
Vec3 position(const CameraPose& p) noexcept {
    return {p[0], p[1], p[2]};
}
} // namespace

bool VisualizationPath::load(const void* data, std::size_t bytes, std::string& error) {
    error.clear();
    const auto fail = [&error](const char* message) {
        error = message;
        return false;
    };
    if (!data || bytes < kVisualizationHeaderBytes || bytes > kVisualizationMappingBytes)
        return fail("Viewer packet has an invalid byte length");
    const auto* raw = static_cast<const unsigned char*>(data);
    if (std::memcmp(raw, "DLYVIS01", 8) != 0)
        return fail("Viewer packet magic does not match");
    const auto sequence = u32(raw + 8), abi = u32(raw + 12), enabled = u32(raw + 16),
               selected = u32(raw + 20);
    const auto count = u32(raw + 24), path_bytes = u32(raw + 28), samples = u32(raw + 32),
               markers = u32(raw + 36);
    if (!sequence || (sequence & 1) || abi != kVisualizationAbi || enabled > 1 ||
        count > NativePath::max_camera_keys || samples < 2 || samples > kVisualizationMaxSamples ||
        markers < 1 || markers > kVisualizationMaxMarkers)
        return fail("Viewer packet version, sequence, count, or drawing budget is invalid");
    for (std::size_t i = 40; i < kVisualizationHeaderBytes; ++i)
        if (raw[i])
            return fail("Viewer packet has unknown reserved fields");
    if (!enabled) {
        if (count || path_bytes || selected || bytes != kVisualizationHeaderBytes)
            return fail("A cleared viewer packet must not contain a camera path");
        *this = VisualizationPath{};
        return true;
    }
    if (!count || selected >= count || samples < count || path_bytes > NativePath::max_bytes ||
        bytes != kVisualizationHeaderBytes + std::size_t(count) * 8 + path_bytes)
        return fail("Viewer packet lengths or selected camera do not match");
    const auto* times = raw + kVisualizationHeaderBytes;
    const auto* blob = times + std::size_t(count) * 8;
    NativePath path;
    if (!path.load(blob, path_bytes, error))
        return false;
    if (u32(blob + 12) + 1 != count)
        return fail("Viewer timestamps do not match the camera path");
    for (std::uint32_t i = 0; i < count; ++i) {
        const double time = number(times + i * 8);
        const double expected =
            i + 1 == count
                ? number(blob + 40)
                : number(blob + (count == 1
                                     ? 32
                                     : NativePath::header_bytes + i * NativePath::segment_bytes));
        if (!std::isfinite(time) || time < 0 || time != expected ||
            (i && time <= number(times + (i - 1) * 8)))
            return fail("Viewer timestamps are not the authored camera timestamps");
    }
    VisualizationPath candidate;
    candidate.enabled_ = true;
    candidate.selected_camera_ = selected;
    candidate.camera_count_ = count;
    try {
        const std::size_t segments = count - 1;
        const std::size_t edges = segments ? std::min<std::size_t>(samples - 1, segments * 32) : 0;
        candidate.points_.reserve(edges + 1);
        candidate.breaks_.reserve(edges + 1);
        CameraPose pose{};
        if (!path.evaluate(number(times), pose) || !valid_pose(pose))
            return fail("Viewer path produced an unsupported camera pose");
        candidate.points_.push_back(pose);
        candidate.breaks_.push_back(1);
        for (std::size_t i = 0; i < segments; ++i) {
            const double begin = number(times + i * 8), end = number(times + (i + 1) * 8);
            const std::size_t steps = edges / segments + (i < edges % segments ? 1 : 0);
            bool position_step = false;
            for (std::size_t channel = 0; channel < 3; ++channel) {
                const auto* record = blob + NativePath::header_bytes +
                                     i * NativePath::segment_bytes + 16 + 40 * channel;
                position_step |= u32(record) == 0 && number(record + 8) != number(record + 16);
            }
            for (std::size_t j = 1; j <= steps; ++j) {
                const double time =
                    j == steps ? end : begin + (end - begin) * (double(j) / double(steps));
                if (!path.evaluate(time, pose) || !valid_pose(pose))
                    return fail("Viewer path produced an unsupported camera pose");
                candidate.points_.push_back(pose);
                candidate.breaks_.push_back(std::uint8_t(j == steps && position_step));
            }
        }
        // Evenly spaced glyphs keep very large paths bounded. Reserve a slot
        // for the selected camera so editing never loses its marker.
        const std::size_t visible = std::min<std::size_t>(markers, count);
        std::vector<std::uint32_t> indices;
        indices.reserve(visible);
        if (visible == count)
            for (std::uint32_t i = 0; i < count; ++i)
                indices.push_back(i);
        else {
            indices.push_back(selected);
            for (std::size_t j = 0; j + 1 < visible; ++j) {
                const auto index =
                    std::uint32_t(visible == 2 ? 0 : j * (count - 1) / (visible - 2));
                if (index != selected)
                    indices.push_back(index);
            }
            std::sort(indices.begin(), indices.end());
        }
        candidate.cameras_.reserve(indices.size());
        for (auto index : indices) {
            const double time = number(times + std::size_t(index) * 8);
            if (!path.evaluate(time, pose) || !valid_pose(pose))
                return fail("Viewer camera marker has an unsupported pose");
            candidate.cameras_.push_back({pose, time, index});
        }
    } catch (const std::exception&) {
        return fail("Could not allocate bounded viewer geometry");
    }
    *this = std::move(candidate);
    return true;
}

bool project_visualization_point(const VisualizationView& view, const Vec3& point,
                                 VisualizationPoint& out) noexcept {
    Projection projection;
    return projection.setup(view) && projection.point(point, out);
}
bool project_visualization_line(const VisualizationView& view, const Vec3& a, const Vec3& b,
                                VisualizationPoint& screen_a,
                                VisualizationPoint& screen_b) noexcept {
    Projection projection;
    return projection.setup(view) && projection.line(a, b, screen_a, screen_b);
}

bool project_visualization(const VisualizationPath& path, const VisualizationView& view,
                           VisualizationGeometry& out) noexcept {
    out.line_count = out.label_count = 0;
    if (!path.enabled())
        return false;
    Projection projection;
    if (!projection.setup(view))
        return false;
    const auto append = [&](const Vec3& a, const Vec3& b, VisualizationKind kind) {
        if (out.line_count >= out.lines.size())
            return;
        auto& line = out.lines[out.line_count];
        if (projection.line(a, b, line.a, line.b)) {
            line.kind = kind;
            ++out.line_count;
        }
    };
    const auto& points = path.points();
    for (std::size_t i = 1; i < points.size(); ++i)
        if (!path.breaks()[i])
            append(position(points[i - 1]), position(points[i]), VisualizationKind::Path);
    for (const auto& camera : path.cameras()) {
        const auto& pose = camera.pose;
        const Vec3 center = position(pose);
        const auto basis = angle_vectors(pose);
        const auto kind = camera.index == path.selected_camera() ? VisualizationKind::SelectedCamera
                                                                 : VisualizationKind::Camera;
        // An orientation glyph, not a claim about the camera's final lens.
        // The authored aspect controls its shape; its size stays 24 game units.
        std::array<Vec3, 4> corners{};
        constexpr double xs[4] = {-1, 1, 1, -1}, ys[4] = {-1, -1, 1, 1};
        for (std::size_t i = 0; i < corners.size(); ++i)
            for (std::size_t axis = 0; axis < 3; ++axis)
                corners[i][axis] = center[axis] + 24 * basis.forward[axis] +
                                   12 * xs[i] * basis.right[axis] +
                                   12 / pose[6] * ys[i] * basis.up[axis];
        for (std::size_t i = 0; i < corners.size(); ++i) {
            append(center, corners[i], kind);
            append(corners[i], corners[(i + 1) % corners.size()], kind);
        }
        if (out.label_count < out.labels.size()) {
            auto& label = out.labels[out.label_count];
            if (projection.point(center, label.point)) {
                label.camera_index = camera.index;
                label.selected = kind == VisualizationKind::SelectedCamera;
                ++out.label_count;
            }
        }
    }
    return true;
}

} // namespace dolly
