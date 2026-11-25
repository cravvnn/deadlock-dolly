#pragma once
// Offline Source 2 map collision extractor and 4-floor top-down grid baker.
//
// VPK v2, compiled resource, and binary KV3 parsing rules are adapted from
// ValveResourceFormat (MIT License, Copyright (c) 2015 ValveResourceFormat
// Contributors) and SourceMVM (MIT License, Copyright (c) 2021 advancedfx.org,
// Copyright (c) 2026 Wockstajit (SOURCE:MVM)).
//
// Deadlock stores 99.8% of solid world geometry inside RnCompoundDesc_t
// (`m_compounds`) rather than top-level `m_meshes`. This baker streams shapes
// directly into a top-down grid without an intermediate CS2 vertex cap and
// separates street-deck wind obstacles from roof cover and multi-floor layers.

#include <array>
#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

namespace dolly::map_grid {

inline constexpr int kFloorLayers = 4;
inline constexpr float kNoFloorZ = -1.0e6f;

struct MapGrid {
    uint32_t width = 0;
    uint32_t height = 0;
    float origin_x = 0.0f;
    float origin_y = 0.0f;
    float span_units = 0.0f;
    float cell_units = 0.0f;
    float world_z_p05 = 0.0f;
    float world_z_p95 = 0.0f;
    float street_z_median = 0.0f;

    // Topmost solid surface height per cell (filled with p05 where empty).
    std::vector<float> tops;
    // Playable street-deck height per cell (filled with street_z_median where absent).
    std::vector<float> street_z;
    // Wind obstacle & openness map:
    //   255      = solid street-deck wind obstacle (wall or building core)
    //   0 .. 127 = round(openness * 127) for open air (0 = sheltered, 127 = open sky)
    std::vector<uint8_t> air_map;
    // 255 where a roof/overpass lies >= 140u above street_z or cell is a building core.
    std::vector<uint8_t> roof_cover;
    // Up to 4 walkable floor heights per cell, sorted highest-first; unused = kNoFloorZ.
    std::vector<std::array<float, kFloorLayers>> floor_z;
    // Number of distinct 96u upward-facing slabs (clamped to 6) for diagnostics.
    std::vector<uint8_t> floor_slab_count;

    bool valid() const noexcept {
        const size_t n = static_cast<size_t>(width) * static_cast<size_t>(height);
        return width >= 8 && height >= 8 && cell_units > 0.0f && tops.size() == n &&
               street_z.size() == n && air_map.size() == n && roof_cover.size() == n &&
               floor_z.size() == n && floor_slab_count.size() == n;
    }

    float cell_center_x(uint32_t x) const noexcept {
        return origin_x + (static_cast<float>(x) + 0.5f) * cell_units;
    }

    float cell_center_y(uint32_t y) const noexcept {
        return origin_y + (static_cast<float>(y) + 0.5f) * cell_units;
    }

    bool is_solid(uint32_t x, uint32_t y) const noexcept {
        return air_map[static_cast<size_t>(y) * width + x] > 127u;
    }

    float openness_at(uint32_t x, uint32_t y) const noexcept {
        const uint8_t m = air_map[static_cast<size_t>(y) * width + x];
        return m > 127u ? 0.0f : static_cast<float>(m) / 127.0f;
    }
};

struct BakeStats {
    uint32_t compounds_seen = 0;
    uint32_t solid_hulls_extracted = 0;
    uint32_t solid_meshes_extracted = 0;
    uint32_t non_solid_shapes_skipped = 0;
    uint64_t vertices = 0;
    uint64_t triangles = 0;
    uint32_t nav_vertices = 0;
    uint32_t nav_triangles = 0;
    std::vector<uint32_t> included_ca_indices;
    std::array<float, 3> raw_bounds_min{};
    std::array<float, 3> raw_bounds_max{};
    float geometry_coverage_pct = 0.0f;
    float street_obstacle_pct = 0.0f;
    float roof_cover_pct = 0.0f;
    float multi_floor_2plus_pct = 0.0f;
    float multi_floor_3plus_pct = 0.0f;
    float multi_floor_4plus_pct = 0.0f;
    double extract_seconds = 0.0;
    double rasterize_seconds = 0.0;
    bool from_cache = false;
};

struct BakeOptions {
    uint32_t grid_n = 512;
    bool use_nav_refinement = true;
};

// Computes a fast deterministic fingerprint of a VPK file (size + header/tree hash).
uint64_t compute_vpk_fingerprint(const std::filesystem::path& vpk_path);

// Bakes a MapGrid directly from an installed Source 2 map VPK (`dl_midtown.vpk`, etc.).
bool bake_map_grid_from_vpk(const std::filesystem::path& vpk_path, const BakeOptions& opts,
                            MapGrid& out_grid, BakeStats* out_stats = nullptr,
                            std::string* out_error = nullptr);

// Saves/loads compact `.dmapgrid` binary cache files.
bool save_map_grid_cache(const std::filesystem::path& cache_path, const MapGrid& grid,
                         uint64_t source_fingerprint, std::string* out_error = nullptr);

bool load_map_grid_cache(const std::filesystem::path& cache_path, MapGrid& out_grid,
                         uint64_t* out_fingerprint = nullptr, std::string* out_error = nullptr);

// Loads from `<cache_dir>/<map_stem>_<grid_n>.dmapgrid` when fingerprint matches,
// otherwise bakes from VPK and updates the cache.
bool load_or_bake_map_grid(const std::filesystem::path& vpk_path,
                           const std::filesystem::path& cache_dir, const BakeOptions& opts,
                           MapGrid& out_grid, BakeStats* out_stats = nullptr,
                           std::string* out_error = nullptr);

// How covered a point is (0 open sky .. 1 solid more than 24 u above), bilinear over the
// four nearest cells so leaving cover fades smoothly. 0 off the map.
float cover_above(const MapGrid& grid, float x, float y, float z) noexcept;

// Drift potential: how much snow, sand or ash settles in each cell compared with open
// ground (1), from the map and the steady wind alone, so it is the same at every replay
// time. Ground in the lee of a wall collects more, open wind-swept ground less, and cells
// beside a wall bank up. `wind_x, wind_y` is the direction the wind blows toward (any
// length); `wind_strength` 0..1 fades the wind's shaping in (0 = no lee or scour, only the
// wall banks). Returns `out_n` x `out_n` values in 0..2, row-major like the grid.
std::vector<float> bake_drift_potential(const MapGrid& grid, float wind_x, float wind_y,
                                        float wind_strength, uint32_t out_n);

// 1 for every cell with a wall (solid cell) within `reach_units` of it, with a margin of a
// few cells for bilinear lookups and the point's place inside its cell; 0 elsewhere.
// Row-major like the grid. Lets a shader skip wall searches where none can find a wall.
std::vector<uint8_t> wall_reach_mask(const MapGrid& grid, float reach_units);

// Writes a 3-panel diagnostic PNG (tops heightmap, street obstacles/roofs/openness,
// and stacked floor layers) for visual inspection.
bool write_map_grid_diagnostic_png(const std::filesystem::path& png_path, const MapGrid& grid,
                                   const BakeStats* stats = nullptr,
                                   std::string* out_error = nullptr);

// Writes a tightly-packed top-down RGB8 or RGBA8 buffer to a PNG file.
bool write_png_rgba8(const std::filesystem::path& png_path, uint32_t width, uint32_t height,
                     const uint8_t* rgba, std::string* out_error = nullptr);

} // namespace dolly::map_grid
