#include "dolly_env_render_dx11.hpp"
#include "dolly_map_grid.hpp"
#include "dolly_precip_dx11.hpp"
#include "dolly_weather_state.hpp"
#include "dolly_weather_view_dx11.hpp"
#include "dolly_wind_fluid_dx11.hpp"

#define NOMINMAX
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3d11.h>
#include <d3d11sdklayers.h>
#include <wrl/client.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <string>
#include <vector>

using Microsoft::WRL::ComPtr;

#define CHECK(expr)                                                                                \
    do {                                                                                           \
        if (!(expr)) {                                                                             \
            std::fprintf(stderr, "FAILED line %d: %s\n", __LINE__, #expr);                         \
            std::exit(1);                                                                          \
        }                                                                                          \
    } while (0)

namespace {

using Vec3 = std::array<float, 3>;

inline Vec3 sub3(Vec3 a, Vec3 b) {
    return {a[0] - b[0], a[1] - b[1], a[2] - b[2]};
}
inline Vec3 add3(Vec3 a, Vec3 b) {
    return {a[0] + b[0], a[1] + b[1], a[2] + b[2]};
}
inline Vec3 mul3(Vec3 a, float s) {
    return {a[0] * s, a[1] * s, a[2] * s};
}
inline float dot3(Vec3 a, Vec3 b) {
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}
inline Vec3 cross3(Vec3 a, Vec3 b) {
    return {a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]};
}
inline Vec3 norm3(Vec3 v) {
    const float l = std::sqrt(std::max(dot3(v, v), 1e-12f));
    return {v[0] / l, v[1] / l, v[2] / l};
}

// Builds a synthetic multi-floor city grid for environments without Deadlock installed.
dolly::map_grid::MapGrid make_synthetic_city_grid(uint32_t n = 128) {
    dolly::map_grid::MapGrid g;
    g.width = n;
    g.height = n;
    g.origin_x = -2048.0f;
    g.origin_y = -2048.0f;
    g.span_units = 4096.0f;
    g.cell_units = g.span_units / static_cast<float>(n);
    g.world_z_p05 = 0.0f;
    g.world_z_p95 = 720.0f;
    g.street_z_median = 128.0f;

    const size_t total = static_cast<size_t>(n) * n;
    g.tops.assign(total, 128.0f);
    g.street_z.assign(total, 128.0f);
    g.air_map.assign(total, 127u);
    g.roof_cover.assign(total, 0u);
    g.floor_slab_count.assign(total, 1u);
    g.floor_z.assign(total, {128.0f, dolly::map_grid::kNoFloorZ, dolly::map_grid::kNoFloorZ,
                             dolly::map_grid::kNoFloorZ});

    // Flanking buildings lining a central Mid Lane corridor, plus a skybridge over the lane
    for (uint32_t y = 0; y < n; ++y) {
        const float wy = g.cell_center_y(y);
        for (uint32_t x = 0; x < n; ++x) {
            const float wx = g.cell_center_x(x);
            const size_t idx = static_cast<size_t>(y) * n + x;
            const bool west_bldg = (wx > -1200.0f && wx < -320.0f && std::abs(wy) < 1300.0f);
            const bool east_bldg = (wx > 320.0f && wx < 1200.0f && std::abs(wy) < 1300.0f);
            const bool pillar = (std::abs(wx - 40.0f) < 120.0f && std::abs(wy - 480.0f) < 120.0f);
            const bool bridge = (std::abs(wx) <= 320.0f && wy > 180.0f && wy < 360.0f);

            if (west_bldg || east_bldg || pillar) {
                g.tops[idx] = pillar ? 460.0f : 720.0f;
                g.air_map[idx] = 255u;
                g.roof_cover[idx] = 255u;
                g.floor_slab_count[idx] = 2u;
                g.floor_z[idx] = {g.tops[idx], 128.0f, dolly::map_grid::kNoFloorZ,
                                  dolly::map_grid::kNoFloorZ};
            } else if (bridge) {
                g.tops[idx] = 440.0f;
                g.air_map[idx] = 48u; // open underneath, partially roofed
                g.roof_cover[idx] = 255u;
                g.floor_slab_count[idx] = 2u;
                g.floor_z[idx] = {440.0f, 128.0f, dolly::map_grid::kNoFloorZ,
                                  dolly::map_grid::kNoFloorZ};
            }
        }
    }
    return g;
}

// Column-vector row-major 4x4 matrix inversion (standard Gauss-Jordan)
bool invert_mat4(const float m[16], float out[16]) {
    double a[4][8]{};
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c) {
            a[r][c] = m[r * 4 + c];
            a[r][4 + c] = (r == c) ? 1.0 : 0.0;
        }
    }
    for (int i = 0; i < 4; ++i) {
        int piv = i;
        for (int r = i + 1; r < 4; ++r) {
            if (std::abs(a[r][i]) > std::abs(a[piv][i]))
                piv = r;
        }
        if (std::abs(a[piv][i]) < 1e-12)
            return false;
        if (piv != i) {
            for (int c = 0; c < 8; ++c)
                std::swap(a[i][c], a[piv][c]);
        }
        const double inv = 1.0 / a[i][i];
        for (int c = 0; c < 8; ++c)
            a[i][c] *= inv;
        for (int r = 0; r < 4; ++r) {
            if (r == i)
                continue;
            const double f = a[r][i];
            for (int c = 0; c < 8; ++c)
                a[r][c] -= f * a[i][c];
        }
    }
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c)
            out[r * 4 + c] = static_cast<float>(a[r][4 + c]);
    }
    return true;
}

// Builds a reversed-Z perspective camera (Source 2 Z-up convention).
dolly::env::EnvCamera build_camera(Vec3 eye, Vec3 target, float fov_y_deg, uint32_t width,
                                   uint32_t height, float near_clip = 8.0f,
                                   float far_clip = 14000.0f) {
    dolly::env::EnvCamera cam{};
    cam.position[0] = eye[0];
    cam.position[1] = eye[1];
    cam.position[2] = eye[2];
    cam.width = width;
    cam.height = height;
    cam.near_clip = near_clip;
    cam.far_clip = far_clip;
    cam.reversed_z = true;

    const Vec3 fwd = norm3(sub3(target, eye));
    const Vec3 right = norm3(cross3(fwd, {0.0f, 0.0f, 1.0f}));
    const Vec3 up = cross3(right, fwd);

    // View matrix maps world -> view (+X right, +Y up, +Z forward)
    float V[16] = {
        right[0], right[1], right[2], -dot3(right, eye), up[0], up[1], up[2], -dot3(up, eye),
        fwd[0],   fwd[1],   fwd[2],   -dot3(fwd, eye),   0.0f,  0.0f,  0.0f,  1.0f,
    };

    const float aspect = static_cast<float>(width) / static_cast<float>(height);
    const float fy = 1.0f / std::tan(fov_y_deg * 0.5f * 3.14159265f / 180.0f);
    const float fx = fy / aspect;
    // Reversed-Z projection: z_ndc = 1 at near_clip, 0 at far_clip
    const float a = -near_clip / (far_clip - near_clip);
    const float b = (near_clip * far_clip) / (far_clip - near_clip);

    float P[16] = {
        fx, 0.0f, 0.0f, 0.0f, 0.0f, fy, 0.0f, 0.0f, 0.0f, 0.0f, a, b, 0.0f, 0.0f, 1.0f, 0.0f,
    };

    // VP = P * V
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c) {
            float s = 0.0f;
            for (int k = 0; k < 4; ++k)
                s += P[r * 4 + k] * V[k * 4 + c];
            cam.view_proj[r * 4 + c] = s;
        }
    }
    CHECK(invert_mat4(cam.view_proj, cam.inv_view_proj));
    return cam;
}

// Finds an open street cell near desired world XY.
Vec3 find_open_street_point(const dolly::map_grid::MapGrid& g, float target_x, float target_y) {
    float best_d2 = 1e30f;
    Vec3 best = {target_x, target_y, g.street_z_median};
    for (uint32_t y = 8; y + 8 < g.height; ++y) {
        for (uint32_t x = 8; x + 8 < g.width; ++x) {
            const size_t idx = static_cast<size_t>(y) * g.width + x;
            if (g.air_map[idx] > 127u || g.openness_at(x, y) < 0.45f)
                continue;
            const float wx = g.cell_center_x(x);
            const float wy = g.cell_center_y(y);
            const float d2 = (wx - target_x) * (wx - target_x) + (wy - target_y) * (wy - target_y);
            if (d2 < best_d2) {
                best_d2 = d2;
                best = {wx, wy, g.street_z[idx]};
            }
        }
    }
    return best;
}

// Synthesizes a 3D perspective color + reversed-Z depth buffer directly from the baked MapGrid
// heightfield (real dl_midtown streets & buildings + hero silhouette) so volumetric raymarching
// and surface coating are verified against real 3D Deadlock map architecture.
void synthesize_map_scene_buffers(const dolly::map_grid::MapGrid& g,
                                  const dolly::env::EnvCamera& cam, Vec3 hero_pos,
                                  std::vector<uint8_t>& out_color_rgba,
                                  std::vector<float>& out_depth_revz) {
    const uint32_t w = cam.width, h = cam.height;
    out_color_rgba.resize(static_cast<size_t>(w) * h * 4);
    out_depth_revz.assign(static_cast<size_t>(w) * h, 0.0f);

    const Vec3 eye = {cam.position[0], cam.position[1], cam.position[2]};
    const Vec3 sun = norm3({0.52f, 0.30f, 0.80f});

    const auto world_from_px = [&](float px, float py, float d) -> Vec3 {
        const float u = (px + 0.5f) / static_cast<float>(w);
        const float v = (py + 0.5f) / static_cast<float>(h);
        const float ndc_x = u * 2.0f - 1.0f;
        const float ndc_y = 1.0f - v * 2.0f;
        const float* M = cam.inv_view_proj;
        const float rx = M[0] * ndc_x + M[1] * ndc_y + M[2] * d + M[3];
        const float ry = M[4] * ndc_x + M[5] * ndc_y + M[6] * d + M[7];
        const float rz = M[8] * ndc_x + M[9] * ndc_y + M[10] * d + M[11];
        const float rw = M[12] * ndc_x + M[13] * ndc_y + M[14] * d + M[15];
        return {rx / rw, ry / rw, rz / rw};
    };

    const auto project_depth = [&](Vec3 p) -> float {
        const float* M = cam.view_proj;
        const float cz = M[8] * p[0] + M[9] * p[1] + M[10] * p[2] + M[11];
        const float cw = M[12] * p[0] + M[13] * p[1] + M[14] * p[2] + M[15];
        return std::clamp(cz / std::max(cw, 1e-4f), 0.0f, 1.0f);
    };

    // Layered column model: building cores are solid blocks; open cells are ground up to
    // street_z plus a thin slab for every walkable floor (bridge deck, overpass, balcony)
    // well above the street, so overpasses render with open space underneath.
    constexpr float kSlabThick = 40.0f;
    constexpr int kMaxSlabs = dolly::map_grid::kFloorLayers + 1;
    struct Column {
        int gx = 0, gy = 0;
        bool bldg = false;
        float ground = 0.0f;
        int slabs = 0;
        float slab_top[kMaxSlabs] = {};
    };
    const auto column_at = [&](float wx, float wy) -> Column {
        Column c;
        c.gx = std::clamp(static_cast<int>(std::floor((wx - g.origin_x) / g.cell_units)), 0,
                          static_cast<int>(g.width) - 1);
        c.gy = std::clamp(static_cast<int>(std::floor((wy - g.origin_y) / g.cell_units)), 0,
                          static_cast<int>(g.height) - 1);
        const size_t idx = static_cast<size_t>(c.gy) * g.width + static_cast<size_t>(c.gx);
        c.bldg = (g.air_map[idx] > 127u);
        if (c.bldg) {
            c.ground = std::max(g.tops[idx], g.street_z[idx] + 220.0f);
            return c;
        }
        c.ground = g.street_z[idx];
        const auto add_slab = [&](float top) {
            if (top <= c.ground + 96.0f || c.slabs >= kMaxSlabs)
                return;
            for (int k = 0; k < c.slabs; ++k)
                if (std::abs(c.slab_top[k] - top) < kSlabThick)
                    return;
            c.slab_top[c.slabs++] = top;
        };
        for (float f : g.floor_z[idx])
            if (f > dolly::map_grid::kNoFloorZ * 0.5f)
                add_slab(f);
        if (g.roof_cover[idx] > 127u)
            add_slab(g.tops[idx]);
        return c;
    };
    // Returns the solid interval containing z, or false if z is in open air.
    const auto solid_span = [&](const Column& c, float z, float& lo, float& hi) -> bool {
        if (z <= c.ground) {
            lo = -1e9f;
            hi = c.ground;
            return true;
        }
        for (int k = 0; k < c.slabs; ++k) {
            if (z <= c.slab_top[k] && z >= c.slab_top[k] - kSlabThick) {
                lo = c.slab_top[k] - kSlabThick;
                hi = c.slab_top[k];
                return true;
            }
        }
        return false;
    };
    const auto is_solid = [&](Vec3 p) {
        float lo, hi;
        return solid_span(column_at(p[0], p[1]), p[2], lo, hi);
    };
    const auto ground_at = [&](float wx, float wy) { return column_at(wx, wy).ground; };

    for (uint32_t y = 0; y < h; ++y) {
        for (uint32_t x = 0; x < w; ++x) {
            const Vec3 far_pt = world_from_px(static_cast<float>(x), static_cast<float>(y), 1e-4f);
            const Vec3 rd = norm3(sub3(far_pt, eye));

            float hit_t = -1.0f;
            bool hit_bldg = false;
            bool hit_slab = false;
            bool hit_hero = false;
            Vec3 hit_n = {0.0f, 0.0f, 1.0f};

            // 1) Ray-cylinder/capsule intersection for moving hero silhouette
            {
                const Vec3 oc = sub3(eye, {hero_pos[0], hero_pos[1], hero_pos[2] + 42.0f});
                const float r = 24.0f;
                const float a = rd[0] * rd[0] + rd[1] * rd[1];
                const float b = 2.0f * (oc[0] * rd[0] + oc[1] * rd[1]);
                const float c = oc[0] * oc[0] + oc[1] * oc[1] - r * r;
                const float disc = b * b - 4.0f * a * c;
                if (a > 1e-6f && disc >= 0.0f) {
                    const float t_cyl = (-b - std::sqrt(disc)) / (2.0f * a);
                    if (t_cyl > 24.0f && t_cyl < 4500.0f) {
                        const Vec3 hp = add3(eye, mul3(rd, t_cyl));
                        if (hp[2] >= hero_pos[2] && hp[2] <= hero_pos[2] + 84.0f) {
                            hit_t = t_cyl;
                            hit_hero = true;
                            hit_n = norm3({hp[0] - hero_pos[0], hp[1] - hero_pos[1], 4.0f});
                        }
                    }
                }
            }

            // 2) March the layered columns. Steps stay below the slab thickness so a ray
            //    cannot pass vertically through a deck between two samples.
            float t = 18.0f;
            float prev_t = t;
            for (int step = 0; step < 900 && t < 5500.0f; ++step) {
                if (hit_t > 0.0f && t > hit_t)
                    break;
                const float dt = std::clamp(6.0f + t * 0.01f, 6.0f, kSlabThick * 0.6f);
                t += dt;
                if (!is_solid(add3(eye, mul3(rd, t)))) {
                    prev_t = t;
                    continue;
                }
                float lo = prev_t, hi = t;
                for (int k = 0; k < 8; ++k) {
                    const float mid = 0.5f * (lo + hi);
                    if (is_solid(add3(eye, mul3(rd, mid))))
                        hi = mid;
                    else
                        lo = mid;
                }
                hit_t = hi;
                hit_hero = false;
                const Vec3 hp = add3(eye, mul3(rd, hi));
                const Vec3 pp = add3(eye, mul3(rd, lo));
                const Column hc = column_at(hp[0], hp[1]);
                const Column pc = column_at(pp[0], pp[1]);
                float span_lo = 0.0f, span_hi = 0.0f;
                solid_span(hc, hp[2], span_lo, span_hi);
                hit_bldg = hc.bldg;
                hit_slab = !hc.bldg && span_lo > -1e8f;
                if (hc.gx == pc.gx && hc.gy == pc.gy) {
                    if (pp[2] < span_lo) {
                        hit_n = {0.0f, 0.0f, -1.0f}; // deck underside
                    } else if (hit_slab) {
                        hit_n = {0.0f, 0.0f, 1.0f};
                    } else {
                        const float eps = g.cell_units * 0.55f;
                        const float hx0 = ground_at(hp[0] - eps, hp[1]);
                        const float hx1 = ground_at(hp[0] + eps, hp[1]);
                        const float hy0 = ground_at(hp[0], hp[1] - eps);
                        const float hy1 = ground_at(hp[0], hp[1] + eps);
                        hit_n =
                            norm3({(hx0 - hx1) / (2.0f * eps), (hy0 - hy1) / (2.0f * eps), 1.0f});
                    }
                } else if (hc.gx != pc.gx) {
                    hit_n = norm3({rd[0] > 0.0f ? -1.0f : 1.0f, 0.0f, 0.08f}); // side face
                } else {
                    hit_n = norm3({0.0f, rd[1] > 0.0f ? -1.0f : 1.0f, 0.08f});
                }
                break;
            }

            const size_t idx = static_cast<size_t>(y) * w + x;
            Vec3 rgb = {0.0f, 0.0f, 0.0f};
            if (hit_t < 0.0f) {
                // Warm dusk sky gradient
                const float up = std::clamp(rd[2] * 0.5f + 0.5f, 0.0f, 1.0f);
                const float sun_glow = std::pow(std::max(dot3(rd, sun), 0.0f), 10.0f);
                rgb = {0.26f + (1.0f - up) * 0.36f + sun_glow * 0.55f,
                       0.30f + (1.0f - up) * 0.28f + sun_glow * 0.38f,
                       0.42f + (1.0f - up) * 0.16f + sun_glow * 0.18f};
                out_depth_revz[idx] = 0.0f;
            } else {
                const Vec3 hp = add3(eye, mul3(rd, hit_t));
                out_depth_revz[idx] = project_depth(hp);
                const float ndl = std::max(dot3(hit_n, sun), 0.0f);
                const float hemi = 0.45f + 0.55f * std::clamp(hit_n[2] * 0.5f + 0.5f, 0.0f, 1.0f);

                Vec3 albedo;
                if (hit_hero) {
                    albedo = {0.72f, 0.24f, 0.22f};
                } else if (hit_slab) {
                    // Concrete bridge deck / overpass slab
                    albedo = {0.31f, 0.30f, 0.29f};
                } else if (hit_bldg) {
                    // Art-deco warm stone/brick facade with subtle window grid
                    const float wx_band = std::fmod(std::abs(hp[0] + hp[1]), 96.0f);
                    const float wz_band = std::fmod(std::abs(hp[2]), 110.0f);
                    const bool win = (hit_n[2] < 0.4f) && (wx_band > 28.0f && wx_band < 68.0f) &&
                                     (wz_band > 32.0f && wz_band < 82.0f);
                    albedo = win ? Vec3{0.16f, 0.21f, 0.28f} : Vec3{0.34f, 0.30f, 0.27f};
                } else {
                    // Cobblestone / asphalt street with lane striping
                    const float grid_x = std::fmod(std::abs(hp[0]), 256.0f);
                    const float grid_y = std::fmod(std::abs(hp[1]), 256.0f);
                    const float tile = (grid_x < 10.0f || grid_y < 10.0f) ? 0.86f : 1.0f;
                    albedo = {0.22f * tile, 0.23f * tile, 0.26f * tile};
                }
                const float light = 0.44f * hemi + 0.88f * ndl;
                rgb = {albedo[0] * light, albedo[1] * light, albedo[2] * light};
            }

            out_color_rgba[idx * 4 + 0] =
                static_cast<uint8_t>(std::clamp(rgb[0], 0.0f, 1.0f) * 255.0f);
            out_color_rgba[idx * 4 + 1] =
                static_cast<uint8_t>(std::clamp(rgb[1], 0.0f, 1.0f) * 255.0f);
            out_color_rgba[idx * 4 + 2] =
                static_cast<uint8_t>(std::clamp(rgb[2], 0.0f, 1.0f) * 255.0f);
            out_color_rgba[idx * 4 + 3] = 255u;
        }
    }
}

// A game per-view constant buffer for a test camera, in the layout read by
// depth::read_per_view_projection: view (row-vector) at 128 with view space +X right,
// +Y up, -Z forward; projection (column-vector) at 192; viewport at 320; camera
// position, up and forward at 416, 432 and 448.
std::vector<uint8_t> make_per_view(Vec3 eye, Vec3 target, float fov_y_deg, uint32_t width,
                                   uint32_t height, float near_clip = 8.0f,
                                   float far_clip = 14000.0f) {
    std::vector<uint8_t> bytes(640, 0u);
    const auto put = [&](size_t offset, float v) { std::memcpy(bytes.data() + offset, &v, 4); };
    const Vec3 fwd = norm3(sub3(target, eye));
    const Vec3 right = norm3(cross3(fwd, {0.0f, 0.0f, 1.0f}));
    const Vec3 up = cross3(right, fwd);
    const Vec3 back = mul3(fwd, -1.0f);
    for (int r = 0; r < 3; ++r) {
        put(128 + r * 16 + 0, right[r]);
        put(128 + r * 16 + 4, up[r]);
        put(128 + r * 16 + 8, back[r]);
    }
    put(176, -dot3(right, eye));
    put(180, -dot3(up, eye));
    put(184, -dot3(back, eye));
    put(188, 1.0f);
    const float aspect = static_cast<float>(width) / static_cast<float>(height);
    const float fy = 1.0f / std::tan(fov_y_deg * 0.5f * 3.14159265f / 180.0f);
    const float a = -near_clip / (far_clip - near_clip);
    const float b = (near_clip * far_clip) / (far_clip - near_clip);
    // build_camera's projection with its view-Z column negated for -Z forward.
    put(192 + 0, fy / aspect);
    put(192 + 20, fy);
    put(192 + 40, -a);
    put(192 + 44, b);
    put(192 + 56, -1.0f);
    put(328, static_cast<float>(width));
    put(332, static_cast<float>(height));
    put(336, 1.0f / static_cast<float>(width));
    put(340, 1.0f / static_cast<float>(height));
    for (int i = 0; i < 3; ++i) {
        put(416 + i * 4, eye[i]);
        put(432 + i * 4, up[i]);
        put(448 + i * 4, fwd[i]);
    }
    return bytes;
}

std::vector<uint8_t> readback_rgba8(ID3D11Device* dev, ID3D11DeviceContext* ctx,
                                    ID3D11Texture2D* src, uint32_t w, uint32_t h) {
    D3D11_TEXTURE2D_DESC sd{};
    sd.Width = w;
    sd.Height = h;
    sd.MipLevels = 1;
    sd.ArraySize = 1;
    sd.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    sd.SampleDesc.Count = 1;
    sd.Usage = D3D11_USAGE_STAGING;
    sd.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    ComPtr<ID3D11Texture2D> staging;
    CHECK(SUCCEEDED(dev->CreateTexture2D(&sd, nullptr, &staging)));
    ctx->CopyResource(staging.Get(), src);
    D3D11_MAPPED_SUBRESOURCE mapped{};
    CHECK(SUCCEEDED(ctx->Map(staging.Get(), 0, D3D11_MAP_READ, 0, &mapped)));
    std::vector<uint8_t> out(static_cast<size_t>(w) * h * 4);
    for (uint32_t y = 0; y < h; ++y) {
        std::memcpy(out.data() + static_cast<size_t>(y) * w * 4,
                    static_cast<const uint8_t*>(mapped.pData) +
                        static_cast<size_t>(y) * mapped.RowPitch,
                    static_cast<size_t>(w) * 4);
    }
    ctx->Unmap(staging.Get(), 0);
    return out;
}

double mean_abs_diff_rgb(const std::vector<uint8_t>& a, const std::vector<uint8_t>& b) {
    CHECK(a.size() == b.size());
    double sum = 0.0;
    const size_t px = a.size() / 4;
    for (size_t i = 0; i < px; ++i) {
        sum += std::abs(int(a[i * 4 + 0]) - int(b[i * 4 + 0]));
        sum += std::abs(int(a[i * 4 + 1]) - int(b[i * 4 + 1]));
        sum += std::abs(int(a[i * 4 + 2]) - int(b[i * 4 + 2]));
    }
    return sum / static_cast<double>(px * 3);
}

} // namespace

int main(int argc, char** argv) {
    std::filesystem::path grid_cache_path;
    std::filesystem::path vpk_path;
    std::filesystem::path out_dir;

    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--grid" && i + 1 < argc)
            grid_cache_path = argv[++i];
        else if (arg == "--vpk" && i + 1 < argc)
            vpk_path = argv[++i];
        else if (arg == "--out-dir" && i + 1 < argc)
            out_dir = argv[++i];
    }

    // 1) Load real dl_midtown.dmapgrid (or bake / fallback synthetic)
    dolly::map_grid::MapGrid map_grid;
    std::string err;
    bool using_real_map = false;
    if (!grid_cache_path.empty() && std::filesystem::is_regular_file(grid_cache_path)) {
        CHECK(dolly::map_grid::load_map_grid_cache(grid_cache_path, map_grid, nullptr, &err));
        using_real_map = true;
    } else if (!vpk_path.empty() && std::filesystem::is_regular_file(vpk_path)) {
        dolly::map_grid::BakeOptions opts;
        opts.grid_n = 512;
        CHECK(dolly::map_grid::bake_map_grid_from_vpk(vpk_path, opts, map_grid, nullptr, &err));
        using_real_map = true;
    } else {
        const std::filesystem::path default_midtown =
            R"(B:\SteamLibrary\steamapps\common\Deadlock\game\citadel\maps\dl_midtown.vpk)";
        if (std::filesystem::is_regular_file(default_midtown)) {
            const auto cache_dir = std::filesystem::temp_directory_path() / "dolly_warp_map_cache";
            dolly::map_grid::BakeOptions opts;
            opts.grid_n = 512;
            CHECK(dolly::map_grid::load_or_bake_map_grid(default_midtown, cache_dir, opts, map_grid,
                                                         nullptr, &err));
            using_real_map = true;
        } else {
            map_grid = make_synthetic_city_grid(128);
        }
    }
    CHECK(map_grid.valid());
    std::printf("Loaded %s map grid: %ux%u (cell=%.2fu, street_med=%.1fu)\n",
                using_real_map ? "real dl_midtown" : "synthetic", map_grid.width, map_grid.height,
                map_grid.cell_units, map_grid.street_z_median);

    // 2) Create headless DX11 WARP device with Debug Layer
    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> ctx;
    UINT flags = D3D11_CREATE_DEVICE_DEBUG;
    HRESULT hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_WARP, nullptr, flags, nullptr, 0,
                                   D3D11_SDK_VERSION, &device, nullptr, &ctx);
    if (FAILED(hr)) {
        // Fallback if debug layer SDK is unavailable on a runner
        flags = 0;
        hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_WARP, nullptr, 0, nullptr, 0,
                               D3D11_SDK_VERSION, &device, nullptr, &ctx);
    }
    CHECK(SUCCEEDED(hr) && device && ctx);

    ComPtr<ID3D11InfoQueue> info_queue;
    if (flags & D3D11_CREATE_DEVICE_DEBUG) {
        CHECK(SUCCEEDED(device.As(&info_queue)));
        info_queue->ClearStoredMessages();
    }

    // 3) Initialize GPU Wind Fluid Solver + Volumetric Renderer
    dolly::env::WindFluidSolverDX11 solver;
    CHECK(solver.initialize(device.Get(), &err));
    const uint32_t sim_res = using_real_map ? 256u : 128u;
    CHECK(solver.upload_map_grid(map_grid, sim_res, &err));

    dolly::env::EnvRendererDX11 renderer;
    CHECK(renderer.initialize(device.Get(), &err));

    // 4) Pick an open Mid Lane vantage point on dl_midtown (or synthetic center)
    const Vec3 lane_anchor = find_open_street_point(map_grid, 0.0f, -200.0f);
    const Vec3 cam_eye = {lane_anchor[0] - 280.0f, lane_anchor[1] - 720.0f,
                          lane_anchor[2] + 150.0f};
    const Vec3 cam_target = {lane_anchor[0] + 140.0f, lane_anchor[1] + 420.0f,
                             lane_anchor[2] + 55.0f};
    const Vec3 emit1_pos =
        find_open_street_point(map_grid, lane_anchor[0] - 80.0f, lane_anchor[1] - 60.0f);
    const Vec3 emit2_pos =
        find_open_street_point(map_grid, lane_anchor[0] + 220.0f, lane_anchor[1] + 260.0f);
    const Vec3 hero_pos =
        find_open_street_point(map_grid, lane_anchor[0] + 50.0f, lane_anchor[1] + 120.0f);

    // 2 active world-placed directional emitters
    dolly::env::DirectionalEmitter emitters[2]{};
    emitters[0].world_pos[0] = emit1_pos[0];
    emitters[0].world_pos[1] = emit1_pos[1];
    emitters[0].world_pos[2] = emit1_pos[2] + 32.0f;
    emitters[0].radius = 340.0f;
    emitters[0].dir_norm[0] = 0.55f;
    emitters[0].dir_norm[1] = 0.82f;
    emitters[0].dir_norm[2] = 0.14f;
    emitters[0].jet_speed = 780.0f;
    emitters[0].color[0] = 0.96f;
    emitters[0].color[1] = 0.74f;
    emitters[0].color[2] = 0.36f;
    emitters[0].density = 1.45f;
    emitters[0].cone_cos = 0.40f;
    emitters[0].height_min = -16.0f;
    emitters[0].height_max = 190.0f;
    emitters[0].active_weight = 1.0f;

    emitters[1].world_pos[0] = emit2_pos[0];
    emitters[1].world_pos[1] = emit2_pos[1];
    emitters[1].world_pos[2] = emit2_pos[2] + 36.0f;
    emitters[1].radius = 280.0f;
    emitters[1].dir_norm[0] = -0.80f;
    emitters[1].dir_norm[1] = 0.56f;
    emitters[1].dir_norm[2] = 0.20f;
    emitters[1].jet_speed = 640.0f;
    emitters[1].color[0] = 0.98f;
    emitters[1].color[1] = 0.52f;
    emitters[1].color[2] = 0.26f;
    emitters[1].density = 1.35f;
    emitters[1].cone_cos = 0.55f;
    emitters[1].height_min = -12.0f;
    emitters[1].height_max = 175.0f;
    emitters[1].active_weight = 1.0f;

    dolly::env::WindFluidParams fluid_params;
    fluid_params.wind_x = 185.0f;
    fluid_params.wind_y = 110.0f;
    fluid_params.gust_strength = 1.15f;
    fluid_params.intensity = 1.1f;
    fluid_params.jacobi_iterations = 18;

    // 5) Step fluid with deterministic replay-time clock + moving hero wake
    dolly::env::FluidBody hero{};
    hero.radius = 68.0f;
    hero.active = 1.0f;
    hero.velocity[0] = 240.0f;
    hero.velocity[1] = 360.0f;

    // First call triggers deterministic reset + prewarm
    const int prewarm_steps =
        solver.step_replay_time(ctx.Get(), 10.0, fluid_params, &hero, 1, emitters, 2);
    CHECK(prewarm_steps > 0);

    // Pause check: identical replay_time must execute 0 steps
    const uint32_t steps_before_pause = solver.total_steps();
    const int paused_steps =
        solver.step_replay_time(ctx.Get(), 10.0, fluid_params, &hero, 1, emitters, 2);
    CHECK(paused_steps == 0 && solver.total_steps() == steps_before_pause);

    // Advance 18 frames (0.6s) with hero running through the lane
    for (int f = 1; f <= 18; ++f) {
        const float frac = static_cast<float>(f) / 18.0f;
        hero.world_pos[0] = hero_pos[0] - 180.0f * (1.0f - frac);
        hero.world_pos[1] = hero_pos[1] - 260.0f * (1.0f - frac);
        hero.world_pos[2] = hero_pos[2];
        const int s = solver.step_replay_time(ctx.Get(), 10.0 + f * (1.0 / 30.0), fluid_params,
                                              &hero, 1, emitters, 2);
        CHECK(s >= 1);
    }

    // Readback fluid diagnostics & top-down preview
    dolly::env::FluidDiagnostics diag{};
    std::vector<uint8_t> topdown_rgba;
    CHECK(solver.readback_diagnostics(ctx.Get(), diag, &topdown_rgba, &hero, 1, emitters, 2));
    std::printf("WARP Fluid Diagnostics (%ux%u): solid=%u non_finite=%u max_spd=%.1fu/s "
                "mean_open_spd=%.1fu/s max_solid_spd=%.4f max_dens=%.3f mean_open_dens=%.3f "
                "rms_open_div=%.3f\n",
                diag.width, diag.height, diag.solid_cells, diag.non_finite_cells, diag.max_speed,
                diag.mean_open_speed, diag.max_solid_speed, diag.max_density,
                diag.mean_open_density, diag.rms_open_divergence);
    CHECK(diag.non_finite_cells == 0);
    CHECK(diag.solid_cells > 0);
    CHECK(diag.max_solid_speed <= 1e-4f);
    CHECK(diag.max_speed > 200.0f);
    CHECK(diag.max_density > 0.35f);

    // Verify backward seek resets deterministically
    const int seek_steps =
        solver.step_replay_time(ctx.Get(), 4.0, fluid_params, &hero, 1, emitters, 2);
    CHECK(seek_steps > 0);
    // Restore to t=10.6 for visual rendering
    solver.step_replay_time(ctx.Get(), 10.0, fluid_params, &hero, 1, emitters, 2);
    for (int f = 1; f <= 18; ++f) {
        const float frac = static_cast<float>(f) / 18.0f;
        hero.world_pos[0] = hero_pos[0] - 180.0f * (1.0f - frac);
        hero.world_pos[1] = hero_pos[1] - 260.0f * (1.0f - frac);
        hero.world_pos[2] = hero_pos[2];
        solver.step_replay_time(ctx.Get(), 10.0 + f * (1.0 / 30.0), fluid_params, &hero, 1,
                                emitters, 2);
    }

    // 5b) Hero wakes live in the per-frame blend: a pure function of (replay time, hero
    //     positions). With and without the hero must differ; seeking away and back with the
    //     same hero must give the same field; the switch must turn wakes off.
    {
        const double t_wake = 10.6;
        const double kAnchor = 9.0; // the shot start: re-simulation from it is exact
        struct Field {
            dolly::env::FluidDiagnostics d{};
            std::vector<uint8_t> rgba;
        };
        auto field_at = [&](const dolly::env::WindFluidParams& params,
                            const dolly::env::FluidBody* body, uint32_t count) {
            // Step to a nearby time first so the blend at t_wake is made afresh.
            solver.step_replay_time(ctx.Get(), t_wake - 0.02, params, nullptr, 0, emitters, 2,
                                    kAnchor);
            solver.step_replay_time(ctx.Get(), t_wake, params, body, count, emitters, 2, kAnchor);
            Field f;
            CHECK(solver.readback_diagnostics(ctx.Get(), f.d, &f.rgba, nullptr, 0, emitters, 2));
            CHECK(f.d.non_finite_cells == 0);
            return f;
        };
        auto same = [](const Field& a, const Field& b) {
            return a.rgba == b.rgba && a.d.mean_open_speed == b.d.mean_open_speed &&
                   a.d.max_speed == b.d.max_speed && a.d.mean_open_density == b.d.mean_open_density;
        };
        dolly::env::FluidBody still{};
        std::memcpy(still.world_pos, &hero_pos[0], sizeof(float) * 3);
        still.radius = 56.0f;
        const Field without = field_at(fluid_params, nullptr, 0);
        const Field with_hero = field_at(fluid_params, &still, 1);
        solver.step_replay_time(ctx.Get(), 4.0, fluid_params, nullptr, 0, emitters, 2, kAnchor);
        const Field after_seek = field_at(fluid_params, &still, 1);
        dolly::env::WindFluidParams no_wakes = fluid_params;
        no_wakes.enable_hero_wakes = false;
        const Field off = field_at(no_wakes, &still, 1);
        std::printf("WARP hero wake: open mean speed %.3f -> %.3f u/s; seek repeat %s; off %s\n",
                    without.d.mean_open_speed, with_hero.d.mean_open_speed,
                    same(with_hero, after_seek) ? "identical" : "DIFFERENT",
                    same(off, without) ? "matches no hero" : "DIFFERENT");
        CHECK(with_hero.d.mean_open_speed < without.d.mean_open_speed);
        CHECK(same(with_hero, after_seek));
        CHECK(same(off, without));
        // Back to the state the renders below start from (the same path as above).
        solver.step_replay_time(ctx.Get(), 4.0, fluid_params, &hero, 1, emitters, 2);
        solver.step_replay_time(ctx.Get(), 10.0, fluid_params, &hero, 1, emitters, 2);
        for (int f = 1; f <= 18; ++f) {
            const float frac = static_cast<float>(f) / 18.0f;
            hero.world_pos[0] = hero_pos[0] - 180.0f * (1.0f - frac);
            hero.world_pos[1] = hero_pos[1] - 260.0f * (1.0f - frac);
            hero.world_pos[2] = hero_pos[2];
            solver.step_replay_time(ctx.Get(), 10.0 + f * (1.0 / 30.0), fluid_params, &hero, 1,
                                    emitters, 2);
        }
    }

    // 6) Synthesize 3D perspective camera color + reversed-Z depth buffer of dl_midtown Mid Lane
    const uint32_t vw = 640, vh = 360;
    const auto cam = build_camera(cam_eye, cam_target, 68.0f, vw, vh);
    std::vector<uint8_t> scene_rgba;
    std::vector<float> scene_depth;
    synthesize_map_scene_buffers(map_grid, cam, hero_pos, scene_rgba, scene_depth);

    // Upload synthetic scene color + depth SRVs and output RTV
    D3D11_TEXTURE2D_DESC cdesc{};
    cdesc.Width = vw;
    cdesc.Height = vh;
    cdesc.MipLevels = 1;
    cdesc.ArraySize = 1;
    cdesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    cdesc.SampleDesc.Count = 1;
    cdesc.Usage = D3D11_USAGE_IMMUTABLE;
    cdesc.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    D3D11_SUBRESOURCE_DATA cinit{scene_rgba.data(), vw * 4, 0};
    ComPtr<ID3D11Texture2D> scene_tex, depth_tex, out_tex;
    ComPtr<ID3D11ShaderResourceView> scene_srv, depth_srv;
    ComPtr<ID3D11RenderTargetView> out_rtv;
    CHECK(SUCCEEDED(device->CreateTexture2D(&cdesc, &cinit, &scene_tex)));
    CHECK(SUCCEEDED(device->CreateShaderResourceView(scene_tex.Get(), nullptr, &scene_srv)));

    D3D11_TEXTURE2D_DESC ddesc = cdesc;
    ddesc.Format = DXGI_FORMAT_R32_FLOAT;
    D3D11_SUBRESOURCE_DATA dinit{scene_depth.data(), vw * sizeof(float), 0};
    CHECK(SUCCEEDED(device->CreateTexture2D(&ddesc, &dinit, &depth_tex)));
    CHECK(SUCCEEDED(device->CreateShaderResourceView(depth_tex.Get(), nullptr, &depth_srv)));

    D3D11_TEXTURE2D_DESC odesc = cdesc;
    odesc.Usage = D3D11_USAGE_DEFAULT;
    odesc.BindFlags = D3D11_BIND_RENDER_TARGET;
    CHECK(SUCCEEDED(device->CreateTexture2D(&odesc, nullptr, &out_tex)));
    CHECK(SUCCEEDED(device->CreateRenderTargetView(out_tex.Get(), nullptr, &out_rtv)));

    // 7a) Render Preset::Off — must match clean scene identically
    dolly::env::EnvRenderParams rp;
    rp.preset = dolly::env::AtmospherePreset::Off;
    rp.sim_time = 10.6f;
    rp.wind_xy[0] = fluid_params.wind_x;
    rp.wind_xy[1] = fluid_params.wind_y;
    CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam, rp,
                          solver, emitters, 2));
    const auto off_px = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
    CHECK(mean_abs_diff_rgb(scene_rgba, off_px) < 0.01);

    // 7b) Render Preset::Sandstorm + directional emitters + upward-surface dust coating
    rp.preset = dolly::env::AtmospherePreset::Sandstorm;
    rp.intensity = 1.25f;
    rp.height_ceiling = 210.0f;
    rp.density_scale = 1.25f;
    rp.wisp_strength = 1.0f;
    rp.coating_strength = 0.45f;
    rp.enable_coating = true;
    rp.enable_emitter_plumes = true;
    rp.march_steps = 64;
    CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam, rp,
                          solver, emitters, 2));
    const auto sand_px = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
    const double sand_diff = mean_abs_diff_rgb(scene_rgba, sand_px);
    std::printf("WARP Sandstorm render diff vs clean scene: %.2f / 255\n", sand_diff);
    CHECK(sand_diff > 6.0 && sand_diff < 180.0);

    // 7c) Render Preset::GroundFog (multi-floor heightmap hugging)
    rp.preset = dolly::env::AtmospherePreset::GroundFog;
    rp.intensity = 1.15f;
    rp.height_ceiling = 155.0f;
    rp.density_scale = 1.15f;
    rp.coating_strength = 0.25f;
    rp.enable_emitter_plumes = false;
    CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam, rp,
                          solver, emitters, 2));
    const auto fog_px = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
    const double fog_diff = mean_abs_diff_rgb(scene_rgba, fog_px);
    std::printf("WARP GroundFog render diff vs clean scene: %.2f / 255\n", fog_diff);
    CHECK(fog_diff > 4.0 && fog_diff < 180.0);

    // 7d) Weather presets: atmosphere + wind-driven precipitation through the weather resolver.
    dolly::env::PrecipitationRendererDX11 precip;
    CHECK(precip.initialize(device.Get(), &err));
    const auto render_weather = [&](dolly::weather::Preset preset, double time, bool with_precip) {
        dolly::weather::Resolved res =
            dolly::weather::resolve(dolly::weather::preset_settings(preset));
        res.render.sim_time = static_cast<float>(time);
        res.precipitation.time = time;
        CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam,
                              res.render, solver, emitters, 0));
        if (with_precip)
            CHECK(precip.render(ctx.Get(), depth_srv.Get(), out_rtv.Get(), cam, res.precipitation,
                                solver));
        return readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
    };
    const auto changed_pixels = [&](const std::vector<uint8_t>& a, const std::vector<uint8_t>& b) {
        size_t n = 0;
        for (size_t i = 0; i < a.size(); i += 4)
            n += (std::abs(int(a[i]) - int(b[i])) + std::abs(int(a[i + 1]) - int(b[i + 1])) +
                  std::abs(int(a[i + 2]) - int(b[i + 2]))) > 6;
        return n;
    };
    const size_t frame_px = static_cast<size_t>(vw) * vh;
    struct WeatherShot {
        dolly::weather::Preset preset;
        const char* name;
        std::vector<uint8_t> px;
    };
    std::vector<WeatherShot> weather_shots = {{dolly::weather::Preset::Blizzard, "blizzard", {}},
                                              {dolly::weather::Preset::RainStorm, "rain_storm", {}},
                                              {dolly::weather::Preset::Sandstorm, "sandstorm", {}},
                                              {dolly::weather::Preset::Ash, "ash", {}}};
    for (auto& shot : weather_shots) {
        const auto bare = render_weather(shot.preset, 10.6, false);
        shot.px = render_weather(shot.preset, 10.6, true);
        const size_t flakes = changed_pixels(bare, shot.px);
        const auto resolved = dolly::weather::resolve(dolly::weather::preset_settings(shot.preset));
        std::printf("WARP %s precipitation: %u flakes, %.2f%% of pixels changed\n", shot.name,
                    dolly::env::precipitation_flake_count(resolved.precipitation),
                    100.0 * static_cast<double>(flakes) / static_cast<double>(frame_px));
        CHECK(flakes > frame_px / 500);
    }

    // Exact replay: the same replay time gives the same frame (pause), a later time moves
    // the flakes, and a seek away and back reproduces the frame byte for byte.
    const auto blizzard_a = render_weather(dolly::weather::Preset::Blizzard, 10.6, true);
    CHECK(blizzard_a == weather_shots[0].px);
    CHECK(changed_pixels(blizzard_a, render_weather(dolly::weather::Preset::Blizzard, 10.65,
                                                    true)) > frame_px / 1000);
    CHECK(solver.step_replay_time(ctx.Get(), 4.0, fluid_params, &hero, 1, emitters, 2) > 0);
    solver.step_replay_time(ctx.Get(), 10.0, fluid_params, &hero, 1, emitters, 2);
    for (int f = 1; f <= 18; ++f) {
        const float frac = static_cast<float>(f) / 18.0f;
        hero.world_pos[0] = hero_pos[0] - 180.0f * (1.0f - frac);
        hero.world_pos[1] = hero_pos[1] - 260.0f * (1.0f - frac);
        hero.world_pos[2] = hero_pos[2];
        solver.step_replay_time(ctx.Get(), 10.0 + f * (1.0 / 30.0), fluid_params, &hero, 1,
                                emitters, 2);
    }
    CHECK(render_weather(dolly::weather::Preset::Blizzard, 10.6, true) == blizzard_a);
    std::printf("WARP precipitation replay check passed: pause, advance and seek-back\n");

    // 7e) Shelter: snow never falls under a roof. Left half roofed, right half open, seen
    //     from straight above with no scene geometry in the way.
    {
        dolly::map_grid::MapGrid shelter;
        const uint32_t n = 128;
        shelter.width = shelter.height = n;
        shelter.cell_units = 32.0f;
        shelter.origin_x = shelter.origin_y = -0.5f * n * shelter.cell_units;
        shelter.span_units = n * shelter.cell_units;
        const size_t total = static_cast<size_t>(n) * n;
        shelter.tops.assign(total, 0.0f);
        shelter.street_z.assign(total, 0.0f);
        shelter.air_map.assign(total, 127u);
        shelter.roof_cover.assign(total, 0u);
        shelter.floor_z.assign(total, {0.0f, dolly::map_grid::kNoFloorZ, dolly::map_grid::kNoFloorZ,
                                       dolly::map_grid::kNoFloorZ});
        shelter.floor_slab_count.assign(total, 1u);
        for (uint32_t y = 0; y < n; ++y)
            for (uint32_t x = 0; x < n / 2; ++x) {
                shelter.tops[static_cast<size_t>(y) * n + x] = 4000.0f;
                shelter.roof_cover[static_cast<size_t>(y) * n + x] = 255u;
            }
        CHECK(shelter.valid());
        dolly::env::WindFluidSolverDX11 calm;
        CHECK(calm.initialize(device.Get(), &err));
        CHECK(calm.upload_map_grid(shelter, 0, &err));
        dolly::env::WindFluidParams still;
        still.wind_x = still.wind_y = 0.0f;
        still.gust_strength = 0.0f;
        calm.reset_and_prewarm(ctx.Get(), 0.0, still, nullptr, 0, 4, 0.2f);

        const auto top = build_camera({0.0f, 0.0f, 600.0f}, {0.0f, 1.0f, 0.0f}, 68.0f, vw, vh);
        std::vector<float> sky(frame_px, 0.0f);
        D3D11_SUBRESOURCE_DATA skyinit{sky.data(), vw * sizeof(float), 0};
        ComPtr<ID3D11Texture2D> sky_tex;
        ComPtr<ID3D11ShaderResourceView> sky_srv;
        CHECK(SUCCEEDED(device->CreateTexture2D(&ddesc, &skyinit, &sky_tex)));
        CHECK(SUCCEEDED(device->CreateShaderResourceView(sky_tex.Get(), nullptr, &sky_srv)));
        const float black[4] = {0.0f, 0.0f, 0.0f, 1.0f};
        ctx->ClearRenderTargetView(out_rtv.Get(), black);
        dolly::env::PrecipitationParams snow;
        snow.kind = dolly::env::PrecipitationKind::Snow;
        snow.rate = 1.5f;
        snow.time = 12.0;
        CHECK(precip.render(ctx.Get(), sky_srv.Get(), out_rtv.Get(), top, snow, calm));
        const auto px = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
        size_t roofed = 0, open = 0;
        const uint32_t margin = 64;
        for (uint32_t y = 0; y < vh; ++y)
            for (uint32_t x = 0; x < vw; ++x) {
                const uint8_t* p = px.data() + (static_cast<size_t>(y) * vw + x) * 4;
                if (p[0] + p[1] + p[2] <= 24)
                    continue;
                if (x + margin < vw / 2)
                    ++roofed;
                else if (x > vw / 2 + margin)
                    ++open;
            }
        std::printf("WARP shelter check: %zu lit pixels under the roof, %zu in the open\n", roofed,
                    open);
        CHECK(roofed == 0 && open > 500);
        if (!out_dir.empty())
            CHECK(dolly::map_grid::write_png_rgba8(out_dir / "warp_precip_shelter.png", vw, vh,
                                                   px.data(), &err));
        // Coating obeys the same shelter: a ground plane under the roofed half stays bare.
        std::vector<float> ground(frame_px);
        std::vector<uint8_t> grey(frame_px * 4, 96u);
        for (uint32_t y = 0; y < vh; ++y)
            for (uint32_t x = 0; x < vw; ++x) {
                const float* M = top.inv_view_proj;
                const float nx = (x + 0.5f) / vw * 2.0f - 1.0f, ny = 1.0f - (y + 0.5f) / vh * 2.0f;
                const float fw = M[12] * nx + M[13] * ny + M[14] * 1e-4f + M[15];
                const Vec3 far_pt = {(M[0] * nx + M[1] * ny + M[2] * 1e-4f + M[3]) / fw,
                                     (M[4] * nx + M[5] * ny + M[6] * 1e-4f + M[7]) / fw,
                                     (M[8] * nx + M[9] * ny + M[10] * 1e-4f + M[11]) / fw};
                const Vec3 eye = {top.position[0], top.position[1], top.position[2]};
                const Vec3 rd = norm3(sub3(far_pt, eye));
                const Vec3 hit = add3(eye, mul3(rd, -eye[2] / rd[2]));
                const float* V = top.view_proj;
                const float cz = V[8] * hit[0] + V[9] * hit[1] + V[10] * hit[2] + V[11];
                const float cw = V[12] * hit[0] + V[13] * hit[1] + V[14] * hit[2] + V[15];
                ground[static_cast<size_t>(y) * vw + x] = cz / cw;
                grey[(static_cast<size_t>(y) * vw + x) * 4 + 3] = 255u;
            }
        D3D11_SUBRESOURCE_DATA ginit{ground.data(), vw * sizeof(float), 0};
        D3D11_SUBRESOURCE_DATA cginit{grey.data(), vw * 4, 0};
        ComPtr<ID3D11Texture2D> ground_tex, grey_tex;
        ComPtr<ID3D11ShaderResourceView> ground_srv, grey_srv;
        CHECK(SUCCEEDED(device->CreateTexture2D(&ddesc, &ginit, &ground_tex)));
        CHECK(SUCCEEDED(device->CreateShaderResourceView(ground_tex.Get(), nullptr, &ground_srv)));
        CHECK(SUCCEEDED(device->CreateTexture2D(&cdesc, &cginit, &grey_tex)));
        CHECK(SUCCEEDED(device->CreateShaderResourceView(grey_tex.Get(), nullptr, &grey_srv)));
        dolly::env::EnvRenderParams coat_only;
        coat_only.preset = dolly::env::AtmospherePreset::Snow;
        coat_only.density_scale = 0.0f; // no fog: isolate the coating
        coat_only.coating_strength = 1.0f;
        coat_only.coating_drift = 0.0f;
        coat_only.enable_emitter_plumes = false;
        CHECK(renderer.render(ctx.Get(), grey_srv.Get(), ground_srv.Get(), out_rtv.Get(), top,
                              coat_only, calm, nullptr, 0));
        const auto coated = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
        size_t bare_roofed = 0, coated_open = 0;
        for (uint32_t y = 0; y < vh; ++y)
            for (uint32_t x = 0; x < vw; ++x) {
                const uint8_t* q = coated.data() + (static_cast<size_t>(y) * vw + x) * 4;
                const bool changed =
                    std::abs(int(q[0]) - 96) + std::abs(int(q[1]) - 96) + std::abs(int(q[2]) - 96) >
                    6;
                if (x + margin < vw / 2)
                    bare_roofed += changed;
                else if (x > vw / 2 + margin)
                    coated_open += changed;
            }
        std::printf("WARP coating shelter: %zu coated pixels under the roof, %zu in the open\n",
                    bare_roofed, coated_open);
        CHECK(bare_roofed == 0 && coated_open > frame_px / 20);
        if (!out_dir.empty())
            CHECK(dolly::map_grid::write_png_rgba8(out_dir / "warp_coating_shelter.png", vw, vh,
                                                   coated.data(), &err));
        // Rain wets and pools only the open half; on this flat plane puddles take part of it.
        coat_only.preset = dolly::env::AtmospherePreset::Rain;
        coat_only.sim_time = 3.0f;
        CHECK(renderer.render(ctx.Get(), grey_srv.Get(), ground_srv.Get(), out_rtv.Get(), top,
                              coat_only, calm, nullptr, 0));
        const auto rained = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
        size_t wet_roofed = 0, wet_open = 0, pooled = 0;
        for (uint32_t y = 0; y < vh; ++y)
            for (uint32_t x = 0; x < vw; ++x) {
                const uint8_t* q = rained.data() + (static_cast<size_t>(y) * vw + x) * 4;
                const int diff =
                    std::abs(int(q[0]) - 96) + std::abs(int(q[1]) - 96) + std::abs(int(q[2]) - 96);
                if (x + margin < vw / 2)
                    wet_roofed += diff > 6;
                else if (x > vw / 2 + margin) {
                    wet_open += diff > 6;
                    pooled += int(q[1]) < 52; // water reads much darker than damp ground
                }
            }
        std::printf("WARP rain shelter: %zu wet pixels under the roof, %zu in the open, %zu "
                    "pooled\n",
                    wet_roofed, wet_open, pooled);
        CHECK(wet_roofed == 0 && wet_open > frame_px / 20 && pooled > 0 && pooled < wet_open);
        if (!out_dir.empty())
            CHECK(dolly::map_grid::write_png_rgba8(out_dir / "warp_rain_shelter.png", vw, vh,
                                                   rained.data(), &err));
        // Raindrop rings move with the replay time and repeat exactly at the same time. Checked
        // on this known flat ground from low over the open half, close enough for a pixel to
        // resolve the rings (the city's per-cell steps are no stand-in for a street).
        {
            const auto low =
                build_camera({600.0f, 0.0f, 90.0f}, {600.0f, 1.0f, 0.0f}, 68.0f, vw, vh);
            std::vector<float> near_ground(frame_px);
            for (uint32_t y = 0; y < vh; ++y)
                for (uint32_t x = 0; x < vw; ++x) {
                    const float* M = low.inv_view_proj;
                    const float nx = (x + 0.5f) / vw * 2.0f - 1.0f,
                                ny = 1.0f - (y + 0.5f) / vh * 2.0f;
                    const float fw = M[12] * nx + M[13] * ny + M[14] * 1e-4f + M[15];
                    const Vec3 far_pt = {(M[0] * nx + M[1] * ny + M[2] * 1e-4f + M[3]) / fw,
                                         (M[4] * nx + M[5] * ny + M[6] * 1e-4f + M[7]) / fw,
                                         (M[8] * nx + M[9] * ny + M[10] * 1e-4f + M[11]) / fw};
                    const Vec3 eye = {low.position[0], low.position[1], low.position[2]};
                    const Vec3 rd = norm3(sub3(far_pt, eye));
                    const Vec3 hit = add3(eye, mul3(rd, -eye[2] / rd[2]));
                    const float* V = low.view_proj;
                    const float cz = V[8] * hit[0] + V[9] * hit[1] + V[10] * hit[2] + V[11];
                    const float cw = V[12] * hit[0] + V[13] * hit[1] + V[14] * hit[2] + V[15];
                    near_ground[static_cast<size_t>(y) * vw + x] = cz / cw;
                }
            D3D11_SUBRESOURCE_DATA ninit{near_ground.data(), vw * sizeof(float), 0};
            ComPtr<ID3D11Texture2D> near_tex;
            ComPtr<ID3D11ShaderResourceView> near_srv;
            CHECK(SUCCEEDED(device->CreateTexture2D(&ddesc, &ninit, &near_tex)));
            CHECK(SUCCEEDED(device->CreateShaderResourceView(near_tex.Get(), nullptr, &near_srv)));
            coat_only.sim_time = 3.0f;
            CHECK(renderer.render(ctx.Get(), grey_srv.Get(), near_srv.Get(), out_rtv.Get(), low,
                                  coat_only, calm, nullptr, 0));
            const auto rings = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
            CHECK(renderer.render(ctx.Get(), grey_srv.Get(), near_srv.Get(), out_rtv.Get(), low,
                                  coat_only, calm, nullptr, 0));
            CHECK(readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh) == rings);
            coat_only.sim_time = 3.5f;
            CHECK(renderer.render(ctx.Get(), grey_srv.Get(), near_srv.Get(), out_rtv.Get(), low,
                                  coat_only, calm, nullptr, 0));
            const size_t rippled = changed_pixels(
                rings, readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh));
            std::printf("WARP rain ripples: %zu pixels change over half a second\n", rippled);
            CHECK(rippled > 0);
        }
        // Cover creeps in under the roof from its open edge on the upwind side, and only
        // about 110 u in; in still air it stays out entirely (checked above).
        {
            auto windy = coat_only;
            windy.preset = dolly::env::AtmospherePreset::Snow;
            windy.wind_xy[0] = -320.0f; // blowing toward the roofed half (open side upwind)
            windy.wind_xy[1] = 0.0f;
            CHECK(renderer.render(ctx.Get(), grey_srv.Get(), ground_srv.Get(), out_rtv.Get(), top,
                                  windy, calm, nullptr, 0));
            const auto crept = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
            if (!out_dir.empty())
                CHECK(dolly::map_grid::write_png_rgba8(out_dir / "warp_shelter_creep.png", vw, vh, crept.data(), &err));
            size_t near_edge = 0, deep = 0;
            for (uint32_t y = 0; y < vh; ++y)
                for (uint32_t x = 0; x < vw / 2; ++x) {
                    const uint8_t* q = crept.data() + (static_cast<size_t>(y) * vw + x) * 4;
                    const bool changed = std::abs(int(q[0]) - 96) + std::abs(int(q[1]) - 96) +
                                             std::abs(int(q[2]) - 96) > 6;
                    // About 1.27 u per pixel at this height: 150 u is about 118 pixels.
                    if (x + 118 >= vw / 2)
                        near_edge += changed;
                    else
                        deep += changed;
                }
            std::printf("WARP shelter creep: %zu covered pixels near the roof's edge, %zu deeper "
                        "in\n",
                        near_edge, deep);
            CHECK(near_edge > 0 && deep == 0);
        }
        calm.shutdown();
    }

    // 7f) Coating build-up: nothing at the shot start, growing with time since the start.
    std::vector<uint8_t> coat_frames[3];
    {
        dolly::weather::Settings snow =
            dolly::weather::preset_settings(dolly::weather::Preset::Blizzard);
        snow.build_up = 60.0f;
        dolly::weather::Resolved res = dolly::weather::resolve(snow);
        res.render.sim_time = 10.6f;
        res.render.enable_coating = false;
        CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam,
                              res.render, solver, emitters, 0));
        const auto uncoated = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
        res.render.enable_coating = true;
        const double ages[3] = {0.0, 20.0, 60.0};
        size_t covered[3] = {};
        for (int k = 0; k < 3; ++k) {
            res.render.coating_growth = dolly::weather::coating_growth(snow, ages[k]);
            CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam,
                                  res.render, solver, emitters, 0));
            coat_frames[k] = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
            covered[k] = changed_pixels(uncoated, coat_frames[k]);
        }
        std::printf("WARP coating build-up: %.2f%% / %.2f%% / %.2f%% of pixels coated at 0 / 20 / "
                    "60 s\n",
                    100.0 * covered[0] / frame_px, 100.0 * covered[1] / frame_px,
                    100.0 * covered[2] / frame_px);
        CHECK(coat_frames[0] == uncoated);
        CHECK(covered[1] > frame_px / 200 && covered[2] > covered[1]);
        // Drift potential: baked from the map and the steady wind alone, so two bakes match;
        // it stays in 0..2, collects in the lee and thins on swept ground, and without wind
        // only the wall banks remain.
        constexpr uint32_t kDrift = 256;
        const float wx = res.render.wind_xy[0], wy = res.render.wind_xy[1];
        const auto drift = dolly::map_grid::bake_drift_potential(map_grid, wx, wy, 1.0f, kDrift);
        CHECK(drift.size() == size_t(kDrift) * kDrift);
        CHECK(drift == dolly::map_grid::bake_drift_potential(map_grid, wx, wy, 1.0f, kDrift));
        float lo = 2.0f, hi = 0.0f;
        for (float v : drift) {
            lo = std::min(lo, v);
            hi = std::max(hi, v);
        }
        const auto calm_drift =
            dolly::map_grid::bake_drift_potential(map_grid, wx, wy, 0.0f, kDrift);
        float calm_lo = 2.0f;
        for (float v : calm_drift)
            calm_lo = std::min(calm_lo, v);
        std::printf("WARP drift potential: %.2f..%.2f with wind, >= %.2f calm\n", lo, hi,
                    calm_lo);
        CHECK(lo >= 0.0f && hi <= 2.0f && lo < 0.8f && hi > 1.3f && calm_lo >= 0.999f);

        // The drift moves the coating: with it, snow settles elsewhere; with drift 0 the
        // coating is even again.
        CHECK(renderer.set_drift_map(kDrift, drift.data()));
        CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam,
                              res.render, solver, emitters, 0));
        const auto drifted = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
        const size_t drift_moved = changed_pixels(coat_frames[2], drifted);
        std::printf("WARP coating drift: %.2f%% of pixels differ with the drift potential\n",
                    100.0 * drift_moved / frame_px);
        CHECK(drift_moved > frame_px / 1000);
        // Living drifts: the wind's shaping grows over the shot, so a minute in the cover lies
        // differently from at the start (and differently again from no shaping at all).
        {
            auto early = res.render;
            early.shot_start_time = early.sim_time;
            CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam,
                                  early, solver, emitters, 0));
            const auto at_start = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
            auto later = early;
            later.shot_start_time = early.sim_time - 60.0f;
            CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam,
                                  later, solver, emitters, 0));
            const auto a_minute_in =
                readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
            const size_t grew = changed_pixels(at_start, a_minute_in);
            std::printf("WARP living drifts: %.2f%% of pixels change from the shot start to a "
                        "minute in\n",
                        100.0 * grew / frame_px);
            CHECK(grew > 0);
        }
        // The wall-reach map only skips wall searches that cannot find a wall: same frame.
        {
            const auto reach = dolly::map_grid::wall_reach_mask(map_grid, 60.0f);
            size_t near_walls = 0;
            for (uint8_t v : reach)
                near_walls += v;
            CHECK(reach.size() == size_t(map_grid.width) * map_grid.height &&
                  near_walls > 0 && near_walls < reach.size());
            CHECK(renderer.set_wall_reach(map_grid.width, map_grid.height, reach.data()));
            CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam,
                                  res.render, solver, emitters, 0));
            const auto skipped = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
            std::printf("WARP wall reach: %.1f%% of the map within reach, frame %s\n",
                        100.0 * near_walls / reach.size(),
                        skipped == drifted ? "identical" : "DIFFERS");
            CHECK(skipped == drifted);
        }
        res.render.coating_drift = 0.0f;
        CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam,
                              res.render, solver, emitters, 0));
        CHECK(readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh) == coat_frames[2]);
        res.render.coating_drift = 1.0f;

        // Heroes stay clean: the scene's hero loses its cover and nothing gains any.
        res.render.heroes[0][0] = hero_pos[0];
        res.render.heroes[0][1] = hero_pos[1];
        res.render.heroes[0][2] = hero_pos[2];
        res.render.hero_count = 1;
        CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam,
                              res.render, solver, emitters, 0));
        const auto masked = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
        const size_t hero_cleaned = changed_pixels(drifted, masked);
        std::printf("WARP coating hero mask: %zu pixels cleaned around the hero\n",
                    hero_cleaned);
        CHECK(hero_cleaned > 0 &&
              changed_pixels(uncoated, masked) < changed_pixels(uncoated, drifted));

        // Heroes stand in the blanket: on the hero itself, what lies within a couple of units
        // of the soles turns to snow (the boots sink in) and its body well above stays clean.
        {
            auto full = res.render;
            full.coating_strength = 1.0f;
            full.coating_growth = 1.0f;
            full.density_scale = 0.0f; // no haze: isolate the ground
            CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam,
                                  full, solver, emitters, 0));
            const auto deep = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
            full.enable_coating = false;
            CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam,
                                  full, solver, emitters, 0));
            const auto bare = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
            size_t boots = 0, boots_covered = 0, body = 0, body_covered = 0;
            for (uint32_t y = 0; y < vh; ++y)
                for (uint32_t x = 0; x < vw; ++x) {
                    const size_t idx = static_cast<size_t>(y) * vw + x;
                    const float* M = cam.inv_view_proj;
                    const float nx = (x + 0.5f) / vw * 2.0f - 1.0f,
                                ny = 1.0f - (y + 0.5f) / vh * 2.0f, d = scene_depth[idx];
                    const float w = M[12] * nx + M[13] * ny + M[14] * d + M[15];
                    if (std::abs(w) < 1e-6f)
                        continue;
                    const Vec3 p = {(M[0] * nx + M[1] * ny + M[2] * d + M[3]) / w,
                                    (M[4] * nx + M[5] * ny + M[6] * d + M[7]) / w,
                                    (M[8] * nx + M[9] * ny + M[10] * d + M[11]) / w};
                    const float across = std::hypot(p[0] - hero_pos[0], p[1] - hero_pos[1]);
                    const float up = p[2] - hero_pos[2];
                    if (across > 26.0f)
                        continue;
                    const bool changed = std::abs(int(deep[idx * 4]) - int(bare[idx * 4])) +
                                             std::abs(int(deep[idx * 4 + 1]) -
                                                      int(bare[idx * 4 + 1])) >
                                         12;
                    if (up > 0.5f && up < 2.5f) {
                        ++boots;
                        boots_covered += changed;
                    } else if (up > 30.0f && up < 90.0f) {
                        ++body;
                        body_covered += changed;
                    }
                }
            std::printf("WARP heroes in the snow: %zu of %zu boot pixels buried, %zu of %zu body "
                        "pixels covered\n",
                        boots_covered, boots, body_covered, body);
            CHECK(boots > 0 && boots_covered * 2 > boots && body > 0 && body_covered == 0);
        }
        res.render.hero_count = 0;

        // Hero trails: a path walked across the scene's hero spot over 40 s of shot time.
        // Only the part reached by a frame's time shows; the same time draws the same frame;
        // before the walk, or with trails off, the cover is untouched.
        {
            const float now = res.render.sim_time;
            const float fog = res.render.density_scale;
            res.render.density_scale = 0.0f; // no haze: isolate the ground
            const float build_up = res.render.coating_build_up;
            res.render.coating_build_up = 0.0f; // settled cover (the settling case is below)
            CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam,
                                  res.render, solver, emitters, 0));
            const auto clear_cover = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
            std::vector<dolly::env::TrailSegment> path;
            // Open street in the foreground, walked across the view.
            const Vec3 walk = find_open_street_point(map_grid, lane_anchor[0] - 110.0f,
                                                     lane_anchor[1] - 260.0f);
            for (int k = 0; k < 8; ++k) {
                dolly::env::TrailSegment s{};
                s.a[0] = walk[0] + 37.5f * (k - 4);
                s.a[1] = walk[1] - 14.0f * (k - 4);
                s.a[2] = walk[2];
                s.time_a = now - 20.0f + 5.0f * k;
                s.b[0] = s.a[0] + 37.5f;
                s.b[1] = s.a[1] - 14.0f;
                s.b[2] = walk[2];
                s.time_b = s.time_a + 5.0f;
                path.push_back(s);
            }
            CHECK(renderer.set_hero_trails(path.data(), static_cast<uint32_t>(path.size())));
            CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam,
                                  res.render, solver, emitters, 0));
            const auto trod = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
            CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam,
                                  res.render, solver, emitters, 0));
            CHECK(readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh) == trod);
            const size_t trail_px = changed_pixels(clear_cover, trod);
            size_t darker = 0;
            for (size_t i = 0; i < trod.size(); i += 4)
                darker += (int(trod[i]) + trod[i + 1] + trod[i + 2]) + 6 <
                          (int(clear_cover[i]) + clear_cover[i + 1] + clear_cover[i + 2]);
            // Tracks have height: turning the sun around relights their rims and hollows, so
            // the trodden frames differ by more than the untouched ones do.
            {
                auto turned = res.render;
                turned.sun_dir[0] = -turned.sun_dir[0];
                turned.sun_dir[1] = -turned.sun_dir[1];
                CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(),
                                      cam, turned, solver, emitters, 0));
                const auto trod_turned =
                    readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
                turned.hero_trails = false;
                CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(),
                                      cam, turned, solver, emitters, 0));
                const auto bare_turned =
                    readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
                long relit = 0;
                for (size_t i = 0; i < trod.size(); i += 4)
                    for (int c = 0; c < 3; ++c)
                        relit += std::abs((int(trod[i + c]) - int(clear_cover[i + c])) -
                                          (int(trod_turned[i + c]) - int(bare_turned[i + c])));
                std::printf("WARP hero trails relief: %ld relit by turning the sun\n", relit);
                CHECK(relit > 0);
            }
            // Walked while the snow was still settling: the track pressed less, so it shows less.
            {
                auto early = res.render;
                early.shot_start_time = now - 22.0f;
                early.coating_build_up = 120.0f;
                early.coating_growth = 1.0f;
                CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(),
                                      cam, early, solver, emitters, 0));
                const auto settling = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
                early.hero_trails = false;
                CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(),
                                      cam, early, solver, emitters, 0));
                const auto settling_bare =
                    readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
                long full = 0, faint = 0;
                for (size_t i = 0; i < trod.size(); i += 4)
                    for (int c = 0; c < 3; ++c) {
                        full += std::abs(int(trod[i + c]) - int(clear_cover[i + c]));
                        faint += std::abs(int(settling[i + c]) - int(settling_bare[i + c]));
                    }
                std::printf("WARP hero trails while settling: %ld vs %ld settled\n", faint, full);
                CHECK(faint > 0 && faint < full);
            }
            // Later, the whole walk is down.
            res.render.sim_time = now + 25.0f;
            CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam,
                                  res.render, solver, emitters, 0));
            const auto walked = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
            res.render.hero_trails = false;
            CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam,
                                  res.render, solver, emitters, 0));
            const auto later_bare = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
            res.render.hero_trails = true;
            const size_t walked_px = changed_pixels(later_bare, walked);
            // Before the walk began, nothing.
            res.render.sim_time = now - 30.0f;
            CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam,
                                  res.render, solver, emitters, 0));
            const auto before = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
            res.render.hero_trails = false;
            CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam,
                                  res.render, solver, emitters, 0));
            const auto before_bare = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
            res.render.hero_trails = true;
            res.render.sim_time = now;
            std::printf("WARP hero trails: %zu pixels trodden (%zu darker) halfway, %zu once "
                        "walked\n",
                        trail_px, darker, walked_px);
            if (!out_dir.empty()) {
                CHECK(dolly::map_grid::write_png_rgba8(out_dir / "warp_trails_half.png", vw, vh,
                                                       trod.data(), &err));
                CHECK(dolly::map_grid::write_png_rgba8(out_dir / "warp_trails_walked.png", vw,
                                                       vh, walked.data(), &err));
            }
            CHECK(trail_px > 0 && darker > trail_px / 2 && walked_px > trail_px);
            CHECK(before == before_bare);
            // In the air (feet 80 u up, mid-jump): no print on the ground below.
            {
                std::vector<dolly::env::TrailSegment> jump = path;
                for (auto& s : jump) {
                    s.a[2] += 80.0f;
                    s.b[2] += 80.0f;
                }
                CHECK(renderer.set_hero_trails(jump.data(), static_cast<uint32_t>(jump.size())));
                CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(),
                                      cam, res.render, solver, emitters, 0));
                const auto mid_air = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
                auto plain = res.render;
                plain.hero_trails = false;
                CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(),
                                      cam, plain, solver, emitters, 0));
                const size_t marked = changed_pixels(
                    readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh), mid_air);
                std::printf("WARP hero trails in the air: %zu ground pixels marked\n", marked);
                // A couple of pixels beside a curb (where the map's top stands above the
                // walk) keep a trace two or three levels strong; none is a print.
                CHECK(marked <= 2);
            }
            // Kick-up: a hero running past (300 u/s) leaves puffs that are there just after
            // and gone a few seconds later; the same time draws the same puffs.
            {
                std::vector<dolly::env::TrailSegment> run;
                for (int k = 0; k < 6; ++k) {
                    dolly::env::TrailSegment s{};
                    s.a[0] = walk[0] - 90.0f + 30.0f * k;
                    s.a[1] = walk[1] + 40.0f;
                    s.a[2] = walk[2];
                    s.time_a = now - 0.6f + 0.1f * k;
                    s.b[0] = s.a[0] + 30.0f;
                    s.b[1] = s.a[1];
                    s.b[2] = walk[2];
                    s.time_b = s.time_a + 0.1f;
                    run.push_back(s);
                }
                CHECK(renderer.set_hero_trails(run.data(), static_cast<uint32_t>(run.size())));
                auto fresh = res.render;
                CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(),
                                      cam, fresh, solver, emitters, 0));
                const auto puffed = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
                CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(),
                                      cam, fresh, solver, emitters, 0));
                CHECK(readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh) == puffed);
                // The same moment without kick-up: the puffs are the only difference. Long
                // after, they have settled and kick-up changes nothing.
                auto no_kick = fresh;
                no_kick.hero_kickup = false;
                CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(),
                                      cam, no_kick, solver, emitters, 0));
                const auto unpuffed = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
                const size_t puff_px = changed_pixels(unpuffed, puffed);
                if (!out_dir.empty()) {
                    CHECK(dolly::map_grid::write_png_rgba8(out_dir / "warp_kickup_on.png", vw, vh, puffed.data(), &err));
                    CHECK(dolly::map_grid::write_png_rgba8(out_dir / "warp_kickup_off.png", vw, vh, unpuffed.data(), &err));
                }
                auto settled = fresh;
                settled.sim_time = now + 5.0f;
                CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(),
                                      cam, settled, solver, emitters, 0));
                const auto later = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
                settled.hero_kickup = false;
                CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(),
                                      cam, settled, solver, emitters, 0));
                const size_t late_px = changed_pixels(
                    readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh), later);
                std::printf("WARP kick-up: %zu pixels of fresh puffs, %zu once settled\n",
                            puff_px, late_px);
                CHECK(puff_px > 0 && late_px == 0);
            }
            CHECK(renderer.set_hero_trails(nullptr, 0));
            CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam,
                                  res.render, solver, emitters, 0));
            CHECK(readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh) == clear_cover);
            res.render.density_scale = fog;
            res.render.coating_build_up = build_up;
        }
        CHECK(!renderer.set_drift_map(0, nullptr)); // even cover again for the later checks
    }

    // 7g) GPU-built view from game per-view constants: same frame as the CPU camera, and
    //     no weather at all when no candidate is a valid view.
    {
        dolly::env::GpuViewBuilderDX11 builder;
        CHECK(builder.initialize(device.Get(), &err));
        const auto make_buffer = [&](const std::vector<uint8_t>& bytes) {
            D3D11_BUFFER_DESC bd{};
            bd.ByteWidth = static_cast<UINT>(bytes.size());
            bd.Usage = D3D11_USAGE_DEFAULT;
            D3D11_SUBRESOURCE_DATA init{bytes.data(), 0, 0};
            ComPtr<ID3D11Buffer> buffer;
            CHECK(SUCCEEDED(device->CreateBuffer(&bd, &init, &buffer)));
            return buffer;
        };
        std::vector<uint8_t> decoy_bytes(640);
        for (size_t i = 0; i < decoy_bytes.size(); ++i)
            decoy_bytes[i] = static_cast<uint8_t>((i * 2654435761u) >> 13);
        const auto decoy = make_buffer(decoy_bytes);
        const auto real = make_buffer(make_per_view(cam_eye, cam_target, 68.0f, vw, vh));
        ID3D11Buffer* both[2] = {decoy.Get(), real.Get()};
        const uint32_t sizes[2] = {640u, 640u};

        dolly::env::EnvCamera gpu_cam{};
        gpu_cam.width = vw;
        gpu_cam.height = vh;
        gpu_cam.gpu_view = builder.build(ctx.Get(), both, sizes, 2, vw, vh);
        CHECK(gpu_cam.gpu_view != nullptr);
        dolly::weather::Resolved res = dolly::weather::resolve(
            dolly::weather::preset_settings(dolly::weather::Preset::Blizzard));
        res.render.sim_time = 10.6f;
        res.precipitation.time = 10.6;
        CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), gpu_cam,
                              res.render, solver, emitters, 0));
        CHECK(precip.render(ctx.Get(), depth_srv.Get(), out_rtv.Get(), gpu_cam, res.precipitation,
                            solver));
        const auto gpu_px = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
        const size_t view_diff = changed_pixels(blizzard_a, gpu_px);
        std::printf("WARP GPU-built view: %.3f%% of pixels differ from the CPU camera frame\n",
                    100.0 * view_diff / frame_px);
        CHECK(view_diff < frame_px / 200);

        gpu_cam.gpu_view = builder.build(ctx.Get(), both, sizes, 1, vw, vh); // decoy only
        CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), gpu_cam,
                              res.render, solver, emitters, 0));
        CHECK(precip.render(ctx.Get(), depth_srv.Get(), out_rtv.Get(), gpu_cam, res.precipitation,
                            solver));
        CHECK(readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh) == scene_rgba);
        std::printf("WARP GPU-built view without a valid candidate leaves the frame untouched\n");
        builder.shutdown();
    }

    // 7h) Exact replay across frame rates: with the shot start as the anchor, the wind at a
    //     replay time is the same whether it was reached at 30 FPS, 120 FPS or by a seek.
    {
        const double anchor = 10.0;
        const auto weather_frame = [&]() {
            dolly::weather::Resolved res = dolly::weather::resolve(
                dolly::weather::preset_settings(dolly::weather::Preset::Sandstorm));
            res.render.sim_time = 10.6f;
            res.precipitation.time = 10.6;
            CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam,
                                  res.render, solver, emitters, 0));
            CHECK(precip.render(ctx.Get(), depth_srv.Get(), out_rtv.Get(), cam, res.precipitation,
                                solver));
            return readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
        };
        const auto play = [&](int fps) {
            CHECK(solver.step_replay_time(ctx.Get(), 3.0, fluid_params, nullptr, 0, emitters, 2,
                                          anchor) >= 0);
            const int frames = static_cast<int>(0.6 * fps + 0.5);
            for (int f = 0; f <= frames; ++f)
                solver.step_replay_time(ctx.Get(), anchor + f / static_cast<double>(fps),
                                        fluid_params, nullptr, 0, emitters, 2, anchor);
            return weather_frame();
        };
        const auto at30 = play(30);
        const auto at120 = play(120);
        solver.step_replay_time(ctx.Get(), 12.0, fluid_params, nullptr, 0, emitters, 2, anchor);
        solver.step_replay_time(ctx.Get(), 10.6, fluid_params, nullptr, 0, emitters, 2, anchor);
        const auto scrubbed = weather_frame();
        std::printf("WARP fixed-tick wind: 30 FPS vs 120 FPS %s, scrub-back %s\n",
                    at30 == at120 ? "identical" : "DIFFERENT",
                    at30 == scrubbed ? "identical" : "DIFFERENT");
        CHECK(at30 == at120 && at30 == scrubbed);
    }

    // 7i) Safety net (live above 4K): forced on here at the test size. Half-resolution march
    //     with depth-aware rebuild and full-resolution re-march at edges must stay close to
    //     the full-resolution frame.
    std::vector<uint8_t> net_full, net_half;
    {
        dolly::weather::Resolved res = dolly::weather::resolve(
            dolly::weather::preset_settings(dolly::weather::Preset::Sandstorm));
        res.render.sim_time = 10.6f;
        CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam,
                              res.render, solver, emitters, 0));
        net_full = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
        res.render.half_res_above_pixels = 0;
        CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam,
                              res.render, solver, emitters, 0));
        net_half = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
        double sum = 0.0;
        size_t visible = 0;
        for (size_t i = 0; i < net_full.size(); i += 4) {
            int worst = 0;
            for (int c = 0; c < 3; ++c) {
                const int dc = std::abs(int(net_full[i + c]) - int(net_half[i + c]));
                sum += dc;
                worst = std::max(worst, dc);
            }
            visible += worst > 6;
        }
        const double mean = sum / (frame_px * 3.0);
        std::printf("WARP safety net: mean %.3f / 255, %.2f%% of pixels differ visibly\n", mean,
                    100.0 * visible / frame_px);
        CHECK(net_half != net_full); // the half-resolution path really ran
        CHECK(mean < 1.5 && visible < frame_px / 20);
    }

    // 7j) Rain on the ground: dry at the shot start, wetter as it goes on; raindrop rings move
    //     with the replay time and repeat exactly at the same time; heroes stay dry.
    {
        dolly::weather::Settings rain =
            dolly::weather::preset_settings(dolly::weather::Preset::RainStorm);
        rain.build_up = 60.0f;
        dolly::weather::Resolved res = dolly::weather::resolve(rain);
        res.render.sim_time = 10.6f;
        res.render.density_scale = 0.0f; // no haze: isolate the ground
        res.render.enable_coating = false;
        CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam,
                              res.render, solver, emitters, 0));
        const auto dry = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
        res.render.enable_coating = true;
        const double ages[3] = {0.0, 20.0, 60.0};
        size_t wet[3] = {};
        double darkened[3] = {};
        std::vector<uint8_t> frames[3];
        for (int k = 0; k < 3; ++k) {
            res.render.coating_growth = dolly::weather::coating_growth(rain, ages[k]);
            CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam,
                                  res.render, solver, emitters, 0));
            frames[k] = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
            wet[k] = changed_pixels(dry, frames[k]);
            for (size_t i = 0; i < dry.size(); i += 4)
                darkened[k] += (int(dry[i]) + dry[i + 1] + dry[i + 2]) -
                               (int(frames[k][i]) + frames[k][i + 1] + frames[k][i + 2]);
        }
        std::printf("WARP rain ground: %.2f%% / %.2f%% / %.2f%% of pixels wet at 0 / 20 / 60 s, "
                    "mean darkening %.2f / %.2f / %.2f\n",
                    100.0 * wet[0] / frame_px, 100.0 * wet[1] / frame_px,
                    100.0 * wet[2] / frame_px, darkened[0] / frame_px, darkened[1] / frame_px,
                    darkened[2] / frame_px);
        // Soaking saturates early; pools keep spreading, so the ground keeps darkening.
        CHECK(frames[0] == dry);
        CHECK(wet[1] > frame_px / 200 && darkened[2] > darkened[1] && darkened[1] > 0.0);

        // The same replay time draws the same frame (ripple motion is checked on the flat
        // shelter plane above).
        CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam,
                              res.render, solver, emitters, 0));
        CHECK(readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh) == frames[2]);

        // Puddles mirror the frame only where reflections are allowed (the Color plate);
        // without them they mirror the sky alone, and the pools themselves stay put.
        res.render.puddle_reflections = false;
        CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam,
                              res.render, solver, emitters, 0));
        const auto sky_only = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
        res.render.puddle_reflections = true;
        const size_t mirrored = changed_pixels(sky_only, frames[2]);
        std::printf("WARP rain reflections: %zu pixels mirror the frame (%zu wet without)\n",
                    mirrored, changed_pixels(dry, sky_only));
        CHECK(mirrored > 0 && mirrored < wet[2]);

        res.render.heroes[0][0] = hero_pos[0];
        res.render.heroes[0][1] = hero_pos[1];
        res.render.heroes[0][2] = hero_pos[2];
        res.render.hero_count = 1;
        CHECK(renderer.render(ctx.Get(), scene_srv.Get(), depth_srv.Get(), out_rtv.Get(), cam,
                              res.render, solver, emitters, 0));
        const auto masked = readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
        std::printf("WARP rain hero mask: %zu pixels kept dry around the hero\n",
                    changed_pixels(frames[2], masked));
        CHECK(changed_pixels(dry, masked) < wet[2]);
        if (!out_dir.empty())
            CHECK(dolly::map_grid::write_png_rgba8(out_dir / "warp_rain_ground.png", vw, vh,
                                                   frames[2].data(), &err));
    }

    // 7k) No rim around a character: a bright box on dark, snowed-over ground must leave the
    //     cover around it as it is without the box (the cover's light is read only from this
    //     pixel's own surface), while snow still lands on the box's top. Hovering just over
    //     the ground nothing of it may show in the cover; standing on it, only the pixels
    //     whose neighbourhood reaches the line where the box meets the ground may differ
    //     (there the box lies on the ground's own plane).
    {
        dolly::map_grid::MapGrid flat;
        const uint32_t n = 128;
        flat.width = flat.height = n;
        flat.cell_units = 32.0f;
        flat.origin_x = flat.origin_y = -0.5f * n * flat.cell_units;
        flat.span_units = n * flat.cell_units;
        const size_t total = static_cast<size_t>(n) * n;
        flat.tops.assign(total, 0.0f);
        flat.street_z.assign(total, 0.0f);
        flat.air_map.assign(total, 127u);
        flat.roof_cover.assign(total, 0u);
        flat.floor_z.assign(total, {0.0f, dolly::map_grid::kNoFloorZ, dolly::map_grid::kNoFloorZ,
                                    dolly::map_grid::kNoFloorZ});
        flat.floor_slab_count.assign(total, 1u);
        CHECK(flat.valid());
        dolly::env::WindFluidSolverDX11 calm;
        CHECK(calm.initialize(device.Get(), &err));
        CHECK(calm.upload_map_grid(flat, 0, &err));
        dolly::env::WindFluidParams still;
        still.wind_x = still.wind_y = 0.0f;
        still.gust_strength = 0.0f;
        calm.reset_and_prewarm(ctx.Get(), 0.0, still, nullptr, 0, 4, 0.2f);

        // (The kick-up check below moves the camera and repaints the scene.)
        auto view = build_camera({-150.0f, 20.0f, 120.0f}, {0.0f, 0.0f, 30.0f}, 68.0f, vw, vh);
        Vec3 eye = {view.position[0], view.position[1], view.position[2]};
        // Dark paving; a bright orange box (so snow on it shows in the blue).
        uint8_t ground_rgb[3] = {30, 30, 32}, box_rgb[3] = {240, 150, 40};
        // Colour, depth and a mask of the box's pixels (0 ground, 1 side, 2 top) for a box
        // whose base is `base` above the ground (negative: no box).
        const auto scene = [&](float base, std::vector<uint8_t>& rgba, std::vector<float>& depth,
                               std::vector<uint8_t>& mask) {
            const Vec3 box_lo = {-14.0f, -14.0f, base}, box_hi = {14.0f, 14.0f, base + 64.0f};
            rgba.assign(frame_px * 4, 255u);
            depth.assign(frame_px, 0.0f);
            mask.assign(frame_px, 0u);
            for (uint32_t y = 0; y < vh; ++y)
                for (uint32_t x = 0; x < vw; ++x) {
                    const float* M = view.inv_view_proj;
                    const float nx = (x + 0.5f) / vw * 2.0f - 1.0f;
                    const float ny = 1.0f - (y + 0.5f) / vh * 2.0f;
                    const float fw = M[12] * nx + M[13] * ny + M[14] * 1e-4f + M[15];
                    const Vec3 far_pt = {(M[0] * nx + M[1] * ny + M[2] * 1e-4f + M[3]) / fw,
                                         (M[4] * nx + M[5] * ny + M[6] * 1e-4f + M[7]) / fw,
                                         (M[8] * nx + M[9] * ny + M[10] * 1e-4f + M[11]) / fw};
                    const Vec3 rd = norm3(sub3(far_pt, eye));
                    const size_t i = static_cast<size_t>(y) * vw + x;
                    float t_hit = rd[2] < -1e-4f ? -eye[2] / rd[2] : -1.0f;
                    uint8_t kind = 0;
                    if (base >= 0.0f) {
                        float t0 = 0.0f, t1 = 1e9f;
                        int axis_in = 0;
                        for (int a = 0; a < 3; ++a) {
                            const float inv = 1.0f / (std::abs(rd[a]) > 1e-6f ? rd[a] : 1e-6f);
                            float ta = (box_lo[a] - eye[a]) * inv, tb = (box_hi[a] - eye[a]) * inv;
                            if (ta > tb)
                                std::swap(ta, tb);
                            if (ta > t0) {
                                t0 = ta;
                                axis_in = a;
                            }
                            t1 = std::min(t1, tb);
                        }
                        if (t0 < t1 && t0 > 0.0f && (t_hit < 0.0f || t0 < t_hit)) {
                            t_hit = t0;
                            kind = axis_in == 2 ? 2 : 1;
                        }
                    }
                    if (t_hit < 0.0f)
                        continue; // sky: depth 0, white
                    const Vec3 hit = add3(eye, mul3(rd, t_hit));
                    const float* V = view.view_proj;
                    const float cz = V[8] * hit[0] + V[9] * hit[1] + V[10] * hit[2] + V[11];
                    const float cw = V[12] * hit[0] + V[13] * hit[1] + V[14] * hit[2] + V[15];
                    depth[i] = cz / cw;
                    mask[i] = kind;
                    for (int c = 0; c < 3; ++c)
                        rgba[i * 4 + c] = kind ? box_rgb[c] : ground_rgb[c];
                }
        };
        const auto coat = [&](const std::vector<uint8_t>& rgba, const std::vector<float>& depth) {
            D3D11_SUBRESOURCE_DATA ci{rgba.data(), vw * 4, 0};
            D3D11_SUBRESOURCE_DATA di{depth.data(), vw * sizeof(float), 0};
            ComPtr<ID3D11Texture2D> ct, dt;
            ComPtr<ID3D11ShaderResourceView> cs, ds;
            CHECK(SUCCEEDED(device->CreateTexture2D(&cdesc, &ci, &ct)));
            CHECK(SUCCEEDED(device->CreateShaderResourceView(ct.Get(), nullptr, &cs)));
            CHECK(SUCCEEDED(device->CreateTexture2D(&ddesc, &di, &dt)));
            CHECK(SUCCEEDED(device->CreateShaderResourceView(dt.Get(), nullptr, &ds)));
            dolly::env::EnvRenderParams p;
            p.preset = dolly::env::AtmospherePreset::Snow;
            p.density_scale = 0.0f; // no fog: isolate the coating
            p.coating_strength = 1.0f;
            p.coating_drift = 0.0f;
            p.enable_emitter_plumes = false;
            p.wind_xy[0] = p.wind_xy[1] = 0.0f;
            CHECK(renderer.render(ctx.Get(), cs.Get(), ds.Get(), out_rtv.Get(), view, p, calm,
                                  nullptr, 0));
            return readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
        };
        std::vector<uint8_t> rgba, mask_bare;
        std::vector<float> depth;
        scene(-1.0f, rgba, depth, mask_bare);
        const auto bare = coat(rgba, depth);
        struct Rim {
            size_t ring_px = 0, visible = 0, covered = 0, top_px = 0, top_snowed = 0;
            double mean = 0.0;
            int worst = 0;
        };
        // The cover 2..20 px outside the box's outline, against the same ground without it.
        const auto rim_of = [&](float base, const char* png) {
            std::vector<uint8_t> mask;
            scene(base, rgba, depth, mask);
            const auto boxed = coat(rgba, depth);
            if (!out_dir.empty())
                CHECK(dolly::map_grid::write_png_rgba8(out_dir / png, vw, vh, boxed.data(), &err));
            Rim r;
            double sum = 0.0;
            for (int y = 0; y < int(vh); ++y)
                for (int x = 0; x < int(vw); ++x) {
                    const size_t i = static_cast<size_t>(y) * vw + x;
                    if (mask[i] == 2) {
                        ++r.top_px;
                        r.top_snowed += boxed[i * 4 + 2] > 150; // the orange top turned white
                        continue;
                    }
                    if (mask[i] != 0 || depth[i] <= 0.0f)
                        continue;
                    int nearest = 1000;
                    for (int dy = -20; dy <= 20; ++dy)
                        for (int dx = -20; dx <= 20; ++dx) {
                            const int qx = x + dx, qy = y + dy;
                            if (qx < 0 || qy < 0 || qx >= int(vw) || qy >= int(vh))
                                continue;
                            if (mask[static_cast<size_t>(qy) * vw + qx] != 0)
                                nearest = std::min(nearest, std::max(std::abs(dx), std::abs(dy)));
                        }
                    if (nearest < 2 || nearest > 20)
                        continue;
                    ++r.ring_px;
                    r.covered += bare[i * 4 + 2] > 100;
                    int worst = 0;
                    for (int c = 0; c < 3; ++c) {
                        const int d = std::abs(int(boxed[i * 4 + c]) - int(bare[i * 4 + c]));
                        sum += d;
                        worst = std::max(worst, d);
                    }
                    r.worst = std::max(r.worst, worst);
                    r.visible += worst > 6;
                }
            r.mean = r.ring_px ? sum / (r.ring_px * 3.0) : 0.0;
            return r;
        };
        const Rim over = rim_of(12.0f, "warp_rim_hovering.png");
        const Rim on = rim_of(0.0f, "warp_rim_standing.png");
        std::printf("WARP character rim: hovering box, %zu cover pixels around it differ by mean "
                    "%.3f, worst %d / 255; standing, mean %.3f with %zu of %zu visible; %zu of "
                    "%zu top pixels snowed\n",
                    over.ring_px, over.mean, over.worst, on.mean, on.visible, on.ring_px,
                    on.top_snowed, on.top_px);
        if (!out_dir.empty())
            CHECK(dolly::map_grid::write_png_rgba8(out_dir / "warp_rim_bare.png", vw, vh,
                                                   bare.data(), &err));
        CHECK(over.ring_px > 2000 && over.covered > over.ring_px * 9 / 10); // snowed-over ground
        CHECK(over.mean < 0.05 && over.worst <= 2);                         // no rim at all
        CHECK(on.ring_px > 2000 && on.mean < 0.6 && on.visible < on.ring_px / 25);
        CHECK(on.top_px > 200 && on.top_snowed > on.top_px * 8 / 10); // snow still lands on it

        // 7l) Kick-up behind a running hero, seen from a low camera in dust haze with a post
        //     standing behind the run: a soft, translucent puff. Its coverage is measured
        //     by drawing the same frame over a dark and over a light scene (what shows
        //     through changes, the puff does not). It must never be opaque, have no edges
        //     or terraces over open ground, and read the same against the post as against
        //     the ground beside it (the puff lies in front of both).
        {
            view = build_camera({-230.0f, 0.0f, 42.0f}, {0.0f, 0.0f, 10.0f}, 68.0f, vw, vh);
            eye = {view.position[0], view.position[1], view.position[2]};
            const float now = 20.0f;
            std::vector<dolly::env::TrailSegment> run;
            for (int k = 0; k < 12; ++k) { // 300 u/s along y, 60 u in front of the post
                dolly::env::TrailSegment s{};
                s.a[0] = s.b[0] = -60.0f;
                s.a[1] = -180.0f + 30.0f * k;
                s.b[1] = s.a[1] + 30.0f;
                s.time_a = now - 1.2f + 0.1f * k;
                s.time_b = s.time_a + 0.1f;
                run.push_back(s);
            }
            CHECK(renderer.set_hero_trails(run.data(), static_cast<uint32_t>(run.size())));
            std::vector<uint8_t> mask;
            const auto shot = [&](uint8_t level, bool kick) {
                for (int c = 0; c < 3; ++c)
                    ground_rgb[c] = box_rgb[c] = level;
                scene(0.0f, rgba, depth, mask);
                D3D11_SUBRESOURCE_DATA ci{rgba.data(), vw * 4, 0};
                D3D11_SUBRESOURCE_DATA di{depth.data(), vw * sizeof(float), 0};
                ComPtr<ID3D11Texture2D> ct, dt;
                ComPtr<ID3D11ShaderResourceView> cs, ds;
                CHECK(SUCCEEDED(device->CreateTexture2D(&cdesc, &ci, &ct)));
                CHECK(SUCCEEDED(device->CreateShaderResourceView(ct.Get(), nullptr, &cs)));
                CHECK(SUCCEEDED(device->CreateTexture2D(&ddesc, &di, &dt)));
                CHECK(SUCCEEDED(device->CreateShaderResourceView(dt.Get(), nullptr, &ds)));
                dolly::env::EnvRenderParams p;
                p.preset = dolly::env::AtmospherePreset::DustHaze;
                p.density_scale = 0.0f; // no haze: isolate the cover and the puffs
                p.coating_strength = 0.6f;
                p.coating_drift = 0.0f;
                p.enable_emitter_plumes = false;
                p.wind_xy[0] = p.wind_xy[1] = 0.0f;
                p.sim_time = now;
                p.hero_kickup = kick;
                CHECK(renderer.render(ctx.Get(), cs.Get(), ds.Get(), out_rtv.Get(), view, p, calm,
                                      nullptr, 0));
                return readback_rgba8(device.Get(), ctx.Get(), out_tex.Get(), vw, vh);
            };
            const auto on_dark = shot(30, true), off_dark = shot(30, false);
            const auto on_light = shot(200, true), off_light = shot(200, false);
            CHECK(shot(200, true) == on_light); // the same time draws the same puffs
            CHECK(renderer.set_hero_trails(nullptr, 0));
            // Coverage per pixel, where the two scenes differ enough to measure it.
            std::vector<float> cover(frame_px, -1.0f);
            for (size_t i = 0; i < frame_px; ++i) {
                const int span = int(off_light[i * 4 + 1]) - int(off_dark[i * 4 + 1]);
                if (depth[i] > 0.0f && span >= 40)
                    cover[i] = 1.0f - float(int(on_light[i * 4 + 1]) - int(on_dark[i * 4 + 1])) /
                                          float(span);
            }
            float peak = 0.0f, step_open = 0.0f, step_post = 0.0f;
            size_t puff_px = 0, post_pairs = 0;
            const auto near_box = [&](int x, int y) {
                for (int dy = -3; dy <= 3; ++dy)
                    for (int dx = -3; dx <= 3; ++dx) {
                        const int qx = x + dx, qy = y + dy;
                        if (qx >= 0 && qy >= 0 && qx < int(vw) && qy < int(vh) &&
                            mask[static_cast<size_t>(qy) * vw + qx] != 0)
                            return true;
                    }
                return false;
            };
            for (int y = 0; y + 1 < int(vh); ++y)
                for (int x = 0; x + 1 < int(vw); ++x) {
                    const size_t i = static_cast<size_t>(y) * vw + x;
                    const float a = cover[i], right = cover[i + 1], below = cover[i + vw];
                    if (a < 0.0f)
                        continue;
                    peak = std::max(peak, a);
                    puff_px += a > 0.05f;
                    if (right >= 0.0f && (mask[i] == 1) != (mask[i + 1] == 1) && mask[i] != 2 &&
                        mask[i + 1] != 2) {
                        // The post's side against the ground beside it.
                        step_post = std::max(step_post, std::abs(right - a));
                        post_pairs += std::max(a, right) > 0.05f;
                    }
                    if (mask[i] != 0 || near_box(x, y))
                        continue;
                    if (right >= 0.0f)
                        step_open = std::max(step_open, std::abs(right - a));
                    if (below >= 0.0f)
                        step_open = std::max(step_open, std::abs(below - a));
                }
            std::printf("WARP kick-up puff: %zu pixels, peak coverage %.2f, largest step between "
                        "neighbours %.3f on open ground, %.3f across the post's outline (%zu "
                        "pairs in the puff)\n",
                        puff_px, peak, step_open, step_post, post_pairs);
            if (!out_dir.empty()) {
                CHECK(dolly::map_grid::write_png_rgba8(out_dir / "warp_kick_puff_dark.png", vw, vh,
                                                       on_dark.data(), &err));
                CHECK(dolly::map_grid::write_png_rgba8(out_dir / "warp_kick_puff_light.png", vw,
                                                       vh, on_light.data(), &err));
                std::vector<uint8_t> cover_px(frame_px * 4, 255u);
                for (size_t i = 0; i < frame_px; ++i) {
                    const uint8_t v = cover[i] < 0.0f
                                          ? 0u
                                          : static_cast<uint8_t>(std::clamp(cover[i], 0.0f, 1.0f) * 255.0f);
                    cover_px[i * 4] = cover_px[i * 4 + 1] = v;
                    cover_px[i * 4 + 2] = cover[i] < 0.0f ? 60u : v;
                }
                CHECK(dolly::map_grid::write_png_rgba8(out_dir / "warp_kick_puff_cover.png", vw, vh,
                                                       cover_px.data(), &err));
            }
            CHECK(puff_px > 1500 && peak > 0.2f && peak < 0.9f); // there, and never opaque
            CHECK(step_open < 0.30f);                            // no edge, no terrace
            CHECK(post_pairs > 10 && step_post < 0.15f);         // not cut by the post behind
        }
    }

    // 8) Optionally export visual verification PNGs
    if (!out_dir.empty()) {
        std::error_code ec;
        std::filesystem::create_directories(out_dir, ec);
        CHECK(dolly::map_grid::write_png_rgba8(out_dir / "warp_fluid_topdown.png", diag.width,
                                               diag.height, topdown_rgba.data(), &err));
        CHECK(dolly::map_grid::write_png_rgba8(out_dir / "warp_volumetric_sandstorm.png", vw, vh,
                                               sand_px.data(), &err));
        CHECK(dolly::map_grid::write_png_rgba8(out_dir / "warp_volumetric_ground_fog.png", vw, vh,
                                               fog_px.data(), &err));

        // Side-by-side 3-panel comparison triptych: Clean | Sandstorm+Emitters+Coating | GroundFog
        const uint32_t pad = 8;
        const uint32_t tw = vw * 3 + pad * 4;
        const uint32_t th = vh + pad * 2;
        std::vector<uint8_t> trip(static_cast<size_t>(tw) * th * 4, 255u);
        for (size_t i = 0; i < static_cast<size_t>(tw) * th; ++i) {
            trip[i * 4 + 0] = 14;
            trip[i * 4 + 1] = 16;
            trip[i * 4 + 2] = 22;
            trip[i * 4 + 3] = 255;
        }
        const std::vector<uint8_t>* panels[3] = {&scene_rgba, &sand_px, &fog_px};
        for (uint32_t p = 0; p < 3; ++p) {
            const uint32_t x0 = pad + p * (vw + pad);
            for (uint32_t y = 0; y < vh; ++y) {
                std::memcpy(trip.data() + (static_cast<size_t>(pad + y) * tw + x0) * 4,
                            panels[p]->data() + static_cast<size_t>(y) * vw * 4,
                            static_cast<size_t>(vw) * 4);
            }
        }
        CHECK(dolly::map_grid::write_png_rgba8(out_dir / "warp_volumetric_comparison.png", tw, th,
                                               trip.data(), &err));
        for (const auto& shot : weather_shots)
            CHECK(dolly::map_grid::write_png_rgba8(
                out_dir / (std::string("warp_weather_") + shot.name + ".png"), vw, vh,
                shot.px.data(), &err));
        // 2x2 contact sheet of the weather presets.
        const uint32_t sw = vw * 2 + pad * 3, sh = vh * 2 + pad * 3;
        std::vector<uint8_t> sheet(static_cast<size_t>(sw) * sh * 4, 0u);
        for (size_t i = 0; i < static_cast<size_t>(sw) * sh; ++i) {
            sheet[i * 4 + 0] = 14;
            sheet[i * 4 + 1] = 16;
            sheet[i * 4 + 2] = 22;
            sheet[i * 4 + 3] = 255;
        }
        for (uint32_t k = 0; k < 4; ++k) {
            const uint32_t x0 = pad + (k % 2) * (vw + pad), y0 = pad + (k / 2) * (vh + pad);
            for (uint32_t y = 0; y < vh; ++y)
                std::memcpy(sheet.data() + (static_cast<size_t>(y0 + y) * sw + x0) * 4,
                            weather_shots[k].px.data() + static_cast<size_t>(y) * vw * 4,
                            static_cast<size_t>(vw) * 4);
        }
        CHECK(dolly::map_grid::write_png_rgba8(out_dir / "warp_weather_presets.png", sw, sh,
                                               sheet.data(), &err));
        const char* coat_names[3] = {"warp_coating_0s.png", "warp_coating_20s.png",
                                     "warp_coating_60s.png"};
        for (int k = 0; k < 3; ++k)
            CHECK(dolly::map_grid::write_png_rgba8(out_dir / coat_names[k], vw, vh,
                                                   coat_frames[k].data(), &err));
        CHECK(dolly::map_grid::write_png_rgba8(out_dir / "warp_net_full.png", vw, vh,
                                               net_full.data(), &err));
        CHECK(dolly::map_grid::write_png_rgba8(out_dir / "warp_net_half.png", vw, vh,
                                               net_half.data(), &err));
        std::printf("Wrote visual verification PNGs to %s\n", out_dir.string().c_str());
    }

    // 9) Assert ZERO D3D11 debug-layer errors or warnings!
    precip.shutdown();
    renderer.shutdown();
    solver.shutdown();
    ctx->ClearState();
    ctx->Flush();

    if (info_queue) {
        const UINT64 num = info_queue->GetNumStoredMessages();
        UINT64 bad = 0;
        for (UINT64 i = 0; i < num; ++i) {
            SIZE_T len = 0;
            info_queue->GetMessage(i, nullptr, &len);
            std::vector<uint8_t> buf(len);
            auto* m = reinterpret_cast<D3D11_MESSAGE*>(buf.data());
            info_queue->GetMessage(i, m, &len);
            if (m->Severity <= D3D11_MESSAGE_SEVERITY_WARNING) {
                std::fprintf(stderr, "D3D11 DEBUG [%d]: %s\n", int(m->Severity), m->pDescription);
                ++bad;
            }
        }
        CHECK(bad == 0);
        std::printf("D3D11 Debug Layer check passed: 0 errors, 0 warnings (%llu info msgs).\n",
                    static_cast<unsigned long long>(num));
    }

    std::printf("All Stage 2 DX11 WARP wind-fluid & volumetric raymarch checks passed.\n");
    return 0;
}
