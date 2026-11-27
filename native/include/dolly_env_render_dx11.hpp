#pragma once
// DX11 Screen-Space Volumetric Sandstorm / Ground-Fog Raymarch & Surface Coating Pass.
//
// Volumetric raymarch, floor-layer height sampling, flow-mapped 3D billow erosion,
// and edge-preserving depth normal reconstruction are adapted from SourceMVM
// (MIT License, Copyright (c) 2021 advancedfx.org, Copyright (c) 2026 Wockstajit (SOURCE:MVM)).

#include "dolly_wind_fluid_dx11.hpp"

#include <cstdint>
#include <memory>
#include <string>

struct ID3D11Device;
struct ID3D11DeviceContext;
struct ID3D11ShaderResourceView;
struct ID3D11RenderTargetView;

namespace dolly::env {

enum class AtmospherePreset : uint32_t {
    Off = 0,
    Sandstorm = 1,
    DustHaze = 2,
    GroundFog = 3,
    AshEmbers = 4,
    Rain = 5,
    Snow = 6,
};

struct EnvCamera {
    float position[3] = {0.0f, 0.0f, 0.0f};
    // Row-major 4x4 matrices using column-vector convention: clip = mul(view_proj, float4(world, 1))
    float view_proj[16] = {};
    float inv_view_proj[16] = {};
    float near_clip = 7.0f;
    float far_clip = 16000.0f;
    uint32_t width = 0;
    uint32_t height = 0;
    bool reversed_z = true;
    // Optional StructuredBuffer<ViewData> SRV with this frame's camera, built on the GPU
    // from the game's view constants. When set it replaces position and the matrices.
    ID3D11ShaderResourceView* gpu_view = nullptr;
};

// One straight piece of a hero's recorded path (feet, world units), with the shot time at
// each end.
struct TrailSegment {
    float a[3];
    float time_a;
    float b[3];
    float time_b;
};

struct EnvRenderParams {
    AtmospherePreset preset = AtmospherePreset::Sandstorm;
    float intensity = 1.0f;
    float height_ceiling = 180.0f;
    float density_scale = 1.0f;
    float wisp_strength = 1.0f;
    float coating_strength = 0.35f;
    float max_distance = 3600.0f;
    float sim_time = 0.0f;
    float wind_xy[2] = {180.0f, 95.0f};
    float sun_dir[3] = {0.52f, 0.30f, 0.80f};
    float sun_color[3] = {1.05f, 0.92f, 0.72f};
    float ambient_color[3] = {0.32f, 0.34f, 0.42f};
    bool enable_coating = true;
    bool enable_emitter_plumes = true;
    // Fraction of the coating that has settled (0..1); see weather::coating_growth.
    float coating_growth = 1.0f;
    // How strongly wind moves the coating: drifts in still air, scoured in fast air.
    float coating_drift = 1.0f;
    // Heroes the coating leaves clean: feet positions (world), at most kMaxCoatingHeroes.
    static constexpr uint32_t kMaxCoatingHeroes = 12;
    float heroes[kMaxCoatingHeroes][3] = {};
    uint32_t hero_count = 0;
    // Rain puddles mirror the frame itself (screen-space reflections) only when this is
    // set; otherwise they mirror the sky alone. Off for layer plates other than Color.
    bool puddle_reflections = true;
    // Heroes leave trails in snow, sand and ash when paths have been set (set_hero_trails).
    bool hero_trails = true;
    // Running heroes kick up puffs of snow, sand or dust (with hero_trails).
    bool hero_kickup = true;
    // Seconds for falling snow or blowing sand to fill a fresh track in (0: by preset and
    // intensity). See weather::resolve.
    float trail_fill_seconds = 0.0f;
    // The shot's build-up (s; 0 = settled from the start) and its start on the sim_time
    // clock: a track made before the cover had settled only pressed what lay there then.
    float coating_build_up = 0.0f;
    float shot_start_time = 0.0f;
    // Hero tracks: segments ending after this time are not drawn yet (the newest 1/15 s grid
    // time reached, computed in double so a store filled live and one loaded from disk give
    // the same frame). Negative: use sim_time.
    float trails_through = -1.0f;
    // Above this many output pixels (1440p and up; 1080p stays whole), the haze and the
    // kick-up puffs are marched at half resolution and each pixel is rebuilt from that,
    // re-marching depth edges at full resolution so outlines stay exact. Exports turn it
    // off and always march every pixel.
    bool allow_half_res = true;
    uint64_t half_res_above_pixels = 1920ull * 1080ull;
    int march_steps = 64;
    // High quality adds richer ripples, splashes and puddle reflections; exports always set it.
    bool high_quality = false;
};

class EnvRendererDX11 {
public:
    EnvRendererDX11();
    ~EnvRendererDX11();
    EnvRendererDX11(const EnvRendererDX11&) = delete;
    EnvRendererDX11& operator=(const EnvRendererDX11&) = delete;

    bool initialize(ID3D11Device* device, std::string* out_error = nullptr);
    void shutdown();

    // Compiles the shaders (once per process; several seconds). Safe on any thread: call it
    // on a worker before the first initialize so the render thread never waits for it.
    static void precompile() noexcept;
    static bool precompiled() noexcept;

    bool render(ID3D11DeviceContext* ctx, ID3D11ShaderResourceView* scene_color_srv,
                ID3D11ShaderResourceView* scene_depth_srv, ID3D11RenderTargetView* out_rtv,
                const EnvCamera& camera, const EnvRenderParams& params,
                const WindFluidSolverDX11& fluid, const DirectionalEmitter* emitters,
                uint32_t emitter_count);

    bool ready() const noexcept;

    // The drift potential over the map window (see map_grid::bake_drift_potential), n x n
    // values in 0..2. Until one is set (or after an empty one) the coating is even.
    bool set_drift_map(uint32_t n, const float* values);

    // Where a wall is within the wall search's reach (map_grid::wall_reach_mask, same size
    // as the map grid): elsewhere the search is skipped, which changes nothing it returns.
    // Without one (or after an empty one) every pixel searches.
    bool set_wall_reach(uint32_t width, uint32_t height, const uint8_t* mask);

    // The heroes' tracks as straight segments, each with the replay time (s, the renderer's
    // sim_time clock) at its two ends. Each frame stamps those walked to their end by its
    // time; an empty set (or none) means no trails. At most kMaxTrailSegments.
    // Light snow keeps tracks for up to an hour of replay: room for 12 heroes over that.
    static constexpr uint32_t kMaxTrailSegments = 1u << 20;
    bool set_hero_trails(const TrailSegment* segments, uint32_t count);
    // Adds segments after those set (the order does not matter to the stamp). False when the
    // buffer has no room left: set them all again instead.
    bool append_hero_trails(ID3D11DeviceContext* ctx, const TrailSegment* segments,
                            uint32_t count);

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace dolly::env
