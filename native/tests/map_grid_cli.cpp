#include "dolly_map_grid.hpp"

#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

#define CHECK(expr)                                                                                \
    do {                                                                                           \
        if (!(expr)) {                                                                             \
            std::fprintf(stderr, "FAILED line %d: %s\n", __LINE__, #expr);                         \
            std::exit(1);                                                                          \
        }                                                                                          \
    } while (0)

namespace {

void test_cache_roundtrip() {
    dolly::map_grid::MapGrid g;
    g.width = 64;
    g.height = 64;
    g.origin_x = -1024.0f;
    g.origin_y = -1024.0f;
    g.span_units = 2048.0f;
    g.cell_units = 32.0f;
    g.world_z_p05 = 128.0f;
    g.world_z_p95 = 960.0f;
    g.street_z_median = 256.0f;

    const size_t n = 64u * 64u;
    g.tops.assign(n, 256.0f);
    g.street_z.assign(n, 256.0f);
    g.air_map.assign(n, 127u);
    g.roof_cover.assign(n, 0u);
    g.floor_slab_count.assign(n, 1u);
    g.floor_z.assign(n, {256.0f, dolly::map_grid::kNoFloorZ, dolly::map_grid::kNoFloorZ,
                         dolly::map_grid::kNoFloorZ});

    // Place a multi-floor building core & tunnel
    g.tops[100] = 812.5f;
    g.air_map[100] = 255u;
    g.roof_cover[100] = 255u;
    g.floor_slab_count[100] = 3u;
    g.floor_z[100] = {812.5f, 384.0f, -128.5f, dolly::map_grid::kNoFloorZ};

    const auto tmp = std::filesystem::temp_directory_path() / "dolly_map_grid_roundtrip.dmapgrid";
    std::string err;
    CHECK(dolly::map_grid::save_map_grid_cache(tmp, g, 0x123456789ABCDEF0ULL, &err));

    dolly::map_grid::MapGrid loaded;
    uint64_t fp = 0;
    CHECK(dolly::map_grid::load_map_grid_cache(tmp, loaded, &fp, &err));
    CHECK(fp == 0x123456789ABCDEF0ULL);
    CHECK(loaded.valid());
    CHECK(loaded.width == 64 && loaded.height == 64);
    CHECK(std::abs(loaded.tops[100] - 812.5f) < 0.01f);
    CHECK(loaded.air_map[100] == 255u);
    CHECK(loaded.roof_cover[100] == 255u);
    CHECK(loaded.floor_slab_count[100] == 3u);
    CHECK(std::abs(loaded.floor_z[100][2] - (-128.5f)) < 0.01f);
    CHECK(loaded.floor_z[100][3] <= dolly::map_grid::kNoFloorZ * 0.5f);
    // Cover: cell 100 (x 36, y 1) has a roof at 812.5; a camera under it is covered, one far
    // away is in the open, and one above the roof is not covered.
    {
        const float cx = g.cell_center_x(100 % 64), cy = g.cell_center_y(100 / 64);
        CHECK(dolly::map_grid::cover_above(g, cx, cy, 300.0f) == 1.0f);
        CHECK(dolly::map_grid::cover_above(g, cx, cy, 900.0f) == 0.0f);
        CHECK(dolly::map_grid::cover_above(g, g.cell_center_x(10), g.cell_center_y(40), 300.0f) ==
              0.0f);
        const float edge = dolly::map_grid::cover_above(g, cx + g.cell_units * 0.5f, cy, 300.0f);
        CHECK(edge > 0.0f && edge < 1.0f);
        CHECK(dolly::map_grid::cover_above(g, -1.0e6f, cy, 300.0f) == 0.0f);
    }

    std::error_code ec;
    std::filesystem::remove(tmp, ec);
    std::printf("Synthetic cache round-trip passed.\n");
}

void print_stats_summary(const std::string& label, const dolly::map_grid::MapGrid& grid,
                         const dolly::map_grid::BakeStats& s) {
    std::printf("[%s] grid=%ux%u origin=(%.2f, %.2f) span=%.2fu cell=%.3fu\n"
                "  compounds=%u solid_hulls=%u solid_meshes=%u skipped=%u verts=%llu tris=%llu\n"
                "  nav=(%u verts, %u tris) raw_min=(%.1f, %.1f, %.1f) raw_max=(%.1f, %.1f, %.1f)\n"
                "  z_p05..p95=[%.1f, %.1f] street_med=%.1f cov=%.2f%% obs=%.2f%% roof=%.2f%%\n"
                "  multi-floor: 2+=%.2f%% 3+=%.2f%% 4+=%.2f%%\n"
                "  timing: extract=%.3fs rasterize=%.3fs total=%.3fs (from_cache=%d)\n",
                label.c_str(), grid.width, grid.height, grid.origin_x, grid.origin_y,
                grid.span_units, grid.cell_units, s.compounds_seen, s.solid_hulls_extracted,
                s.solid_meshes_extracted, s.non_solid_shapes_skipped,
                static_cast<unsigned long long>(s.vertices),
                static_cast<unsigned long long>(s.triangles), s.nav_vertices, s.nav_triangles,
                s.raw_bounds_min[0], s.raw_bounds_min[1], s.raw_bounds_min[2], s.raw_bounds_max[0],
                s.raw_bounds_max[1], s.raw_bounds_max[2], grid.world_z_p05, grid.world_z_p95,
                grid.street_z_median, s.geometry_coverage_pct, s.street_obstacle_pct,
                s.roof_cover_pct, s.multi_floor_2plus_pct, s.multi_floor_3plus_pct,
                s.multi_floor_4plus_pct, s.extract_seconds, s.rasterize_seconds,
                s.extract_seconds + s.rasterize_seconds, s.from_cache ? 1 : 0);
}

int run_cli_bake(const std::filesystem::path& vpk, uint32_t grid_n,
                 const std::filesystem::path& cache_dir, const std::filesystem::path& out_png,
                 bool use_nav) {
    dolly::map_grid::BakeOptions opts;
    opts.grid_n = grid_n;
    opts.use_nav_refinement = use_nav;

    dolly::map_grid::MapGrid grid;
    dolly::map_grid::BakeStats stats;
    std::string err;

    const bool ok =
        cache_dir.empty()
            ? dolly::map_grid::bake_map_grid_from_vpk(vpk, opts, grid, &stats, &err)
            : dolly::map_grid::load_or_bake_map_grid(vpk, cache_dir, opts, grid, &stats, &err);
    if (!ok) {
        std::fprintf(stderr, "Bake failed for %s: %s\n", vpk.string().c_str(), err.c_str());
        return 1;
    }

    print_stats_summary(vpk.filename().string(), grid, stats);

    if (!out_png.empty()) {
        if (!dolly::map_grid::write_map_grid_diagnostic_png(out_png, grid, &stats, &err)) {
            std::fprintf(stderr, "Diagnostic PNG failed: %s\n", err.c_str());
            return 1;
        }
        std::printf("  wrote diagnostic PNG: %s\n", out_png.string().c_str());
    }
    return 0;
}

} // namespace

// Fingerprint of the dl_midtown.vpk build the exact counts below were recorded from.
constexpr uint64_t kMidtownRecordedFingerprint = 0xF9B644F050350EECULL;

int main(int argc, char** argv) {
    if (argc > 1) {
        std::filesystem::path vpk, cache_dir, out_png;
        uint32_t grid_n = 512;
        bool use_nav = true;
        for (int i = 1; i < argc; ++i) {
            const std::string arg = argv[i];
            if (arg == "--vpk" && i + 1 < argc)
                vpk = argv[++i];
            else if (arg == "--cache-dir" && i + 1 < argc)
                cache_dir = argv[++i];
            else if (arg == "--out-png" && i + 1 < argc)
                out_png = argv[++i];
            else if (arg == "--grid" && i + 1 < argc)
                grid_n = static_cast<uint32_t>(std::strtoul(argv[++i], nullptr, 10));
            else if (arg == "--no-nav")
                use_nav = false;
        }
        if (vpk.empty()) {
            std::fprintf(stderr,
                         "Usage: dolly_map_grid_cli --vpk <map.vpk> [--grid 512] [--cache-dir dir] "
                         "[--out-png out.png] [--no-nav]\n");
            return 2;
        }
        return run_cli_bake(vpk, grid_n, cache_dir, out_png, use_nav);
    }

    // Default test suite mode (runs under CTest)
    test_cache_roundtrip();

    const std::filesystem::path maps_dir =
        R"(B:\SteamLibrary\steamapps\common\Deadlock\game\citadel\maps)";
    const auto midtown = maps_dir / "dl_midtown.vpk";
    if (!std::filesystem::is_regular_file(midtown)) {
        std::printf("Deadlock maps directory not present; skipping installed VPK verification.\n");
        return 0;
    }

    const auto cache_dir = std::filesystem::temp_directory_path() / "dolly_map_grid_test_cache";
    std::error_code ec;
    std::filesystem::remove_all(cache_dir, ec);

    // 1) dl_midtown.vpk full verification against wind-fluid-offline-results.json
    {
        dolly::map_grid::MapGrid grid;
        dolly::map_grid::BakeStats stats;
        std::string err;
        dolly::map_grid::BakeOptions opts;
        opts.grid_n = 512;
        opts.use_nav_refinement = true;
        CHECK(dolly::map_grid::load_or_bake_map_grid(midtown, cache_dir, opts, grid, &stats, &err));
        print_stats_summary("dl_midtown (cold bake)", grid, stats);
        CHECK(!stats.from_cache);
        // Exact counts are tied to one build of the map. A Deadlock update changes the VPK
        // fingerprint; then only sanity ranges apply until the recorded build is refreshed.
        const uint64_t fp = dolly::map_grid::compute_vpk_fingerprint(midtown);
        if (fp == kMidtownRecordedFingerprint) {
            CHECK(stats.compounds_seen == 4);
            CHECK(stats.solid_hulls_extracted == 38789);
            CHECK(stats.solid_meshes_extracted == 5);
            CHECK(stats.vertices == 1629193);
            CHECK(stats.triangles == 2808953);
        } else {
            std::printf(
                "dl_midtown.vpk build differs from the recorded one (fingerprint 0x%016llX); "
                "checking ranges only. Refresh kMidtownRecordedFingerprint and the exact "
                "counts after verifying a new build.\n",
                static_cast<unsigned long long>(fp));
            CHECK(stats.compounds_seen >= 1);
            CHECK(stats.solid_hulls_extracted > 30000 && stats.solid_hulls_extracted < 50000);
            CHECK(stats.vertices > 1200000 && stats.vertices < 2200000);
            CHECK(stats.triangles > 2000000 && stats.triangles < 3800000);
        }
        CHECK(stats.nav_vertices > 100000);
        CHECK(std::abs(stats.raw_bounds_min[0] - (-35585.0f)) < 1.0f);
        CHECK(std::abs(stats.raw_bounds_max[0] - 32945.0f) < 1.0f);
        CHECK(stats.street_obstacle_pct > 15.0f && stats.street_obstacle_pct < 50.0f);
        CHECK(stats.multi_floor_2plus_pct > 30.0f);

        // Verify cache hit speed & exact reload
        dolly::map_grid::MapGrid cached_grid;
        dolly::map_grid::BakeStats cached_stats;
        CHECK(dolly::map_grid::load_or_bake_map_grid(midtown, cache_dir, opts, cached_grid,
                                                     &cached_stats, &err));
        print_stats_summary("dl_midtown (cache hit)", cached_grid, cached_stats);
        CHECK(cached_stats.from_cache);
        CHECK(cached_stats.extract_seconds < 0.25);
        CHECK(cached_grid.air_map == grid.air_map);
        CHECK(cached_grid.roof_cover == grid.roof_cover);
    }

    // 2) Verify dl_hideout.vpk and hero_testing.vpk
    for (const char* name : {"dl_hideout.vpk", "hero_testing.vpk"}) {
        const auto p = maps_dir / name;
        if (!std::filesystem::is_regular_file(p))
            continue;
        dolly::map_grid::MapGrid grid;
        dolly::map_grid::BakeStats stats;
        std::string err;
        dolly::map_grid::BakeOptions opts;
        opts.grid_n = 512;
        CHECK(dolly::map_grid::load_or_bake_map_grid(p, cache_dir, opts, grid, &stats, &err));
        print_stats_summary(name, grid, stats);
        CHECK(grid.valid());
        CHECK(stats.triangles > 10000);
        CHECK(stats.street_obstacle_pct > 1.0f && stats.street_obstacle_pct < 85.0f);
    }

    std::filesystem::remove_all(cache_dir, ec);
    std::printf("All Stage 1 map grid checks passed.\n");
    return 0;
}
