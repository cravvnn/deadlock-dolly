#pragma once

#include "dolly_path.hpp"
#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace dolly {

constexpr std::size_t kVisualizationMappingBytes = 2 * 1024 * 1024;
constexpr std::size_t kVisualizationHeaderBytes = 64;
constexpr std::size_t kVisualizationMaxSamples = 4096;
constexpr std::size_t kVisualizationMaxMarkers = 128;
constexpr std::uint32_t kVisualizationAbi = 1;

// Header offsets match dolly.visualization_wire. The caller checks its
// seqlock, then passes precisely header + time bytes + camera-path bytes.
#pragma pack(push, 1)
struct VisualizationHeader {
    char magic[8];
    std::uint32_t sequence, abi, enabled, selected_camera, camera_count;
    std::uint32_t path_bytes, sample_budget, marker_budget;
    unsigned char reserved[24];
};
#pragma pack(pop)
static_assert(sizeof(VisualizationHeader) == kVisualizationHeaderBytes, "Viewer header layout");

struct VisualizationCamera {
    CameraPose pose{};
    double time = 0;
    std::uint32_t index = 0;
};

class VisualizationPath {
public:
    // Parse/sample only on the worker. Failed loads preserve the prior object.
    // Publish successful objects immutably; the draw callback never resamples.
    bool load(const void* data, std::size_t bytes, std::string& error);
    bool enabled() const noexcept { return enabled_; }
    std::uint32_t selected_camera() const noexcept { return selected_camera_; }
    std::size_t camera_count() const noexcept { return camera_count_; }
    const std::vector<CameraPose>& points() const noexcept { return points_; }
    const std::vector<VisualizationCamera>& cameras() const noexcept { return cameras_; }
    const std::vector<std::uint8_t>& breaks() const noexcept { return breaks_; }
private:
    bool enabled_ = false;
    std::uint32_t selected_camera_ = 0;
    std::size_t camera_count_ = 0;
    std::vector<CameraPose> points_;
    std::vector<std::uint8_t> breaks_;
    std::vector<VisualizationCamera> cameras_;
};

struct VisualizationView {
    // Final applied Source pose and horizontal FOV from the verified main
    // view callback. pose[6] is its projection aspect (may differ from w/h).
    CameraPose pose{};
    double horizontal_fov = 0;
    double width = 0, height = 0;
    double near_plane = 1;
};
enum class VisualizationKind : std::uint32_t { Path = 0, Camera = 1, SelectedCamera = 2 };
struct VisualizationPoint { float x = 0, y = 0; };
struct VisualizationLine {
    VisualizationPoint a{}, b{};
    VisualizationKind kind = VisualizationKind::Path;
};
struct VisualizationLabel {
    VisualizationPoint point{};
    std::uint32_t camera_index = 0;
    bool selected = false;
};
constexpr std::size_t kVisualizationMaxLines = kVisualizationMaxSamples - 1 + kVisualizationMaxMarkers * 8;
struct VisualizationGeometry {
    std::array<VisualizationLine, kVisualizationMaxLines> lines{};
    std::array<VisualizationLabel, kVisualizationMaxMarkers> labels{};
    std::size_t line_count = 0, label_count = 0;
};

// Pure projection into bounded caller-owned storage. No allocation, IPC,
// clocks, engine calls, or D3D work. Segments are clipped against the near
// plane and all four viewport planes. Guides intentionally have no occlusion.
bool project_visualization(const VisualizationPath& path, const VisualizationView& view,
                           VisualizationGeometry& out) noexcept;
bool project_visualization_point(const VisualizationView& view, const std::array<double, 3>& point,
                                 VisualizationPoint& out) noexcept;
bool project_visualization_line(const VisualizationView& view, const std::array<double, 3>& a,
                                const std::array<double, 3>& b, VisualizationPoint& screen_a,
                                VisualizationPoint& screen_b) noexcept;

} // namespace dolly
