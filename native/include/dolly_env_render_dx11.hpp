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
    // Safety net: above this many output pixels, march at half resolution and rebuild each
    // pixel from it, re-marching depth edges at full resolution. Exports turn it off.
    bool allow_half_res = true;
    uint64_t half_res_above_pixels = 3840ull * 2160ull;
    int march_steps = 64;
};

class EnvRendererDX11 {
public:
    EnvRendererDX11();
    ~EnvRendererDX11();
    EnvRendererDX11(const EnvRendererDX11&) = delete;
    EnvRendererDX11& operator=(const EnvRendererDX11&) = delete;

    bool initialize(ID3D11Device* device, std::string* out_error = nullptr);
    void shutdown();

    bool render(ID3D11DeviceContext* ctx, ID3D11ShaderResourceView* scene_color_srv,
                ID3D11ShaderResourceView* scene_depth_srv, ID3D11RenderTargetView* out_rtv,
                const EnvCamera& camera, const EnvRenderParams& params,
                const WindFluidSolverDX11& fluid, const DirectionalEmitter* emitters,
                uint32_t emitter_count);

    bool ready() const noexcept;

    // The drift potential over the map window (see map_grid::bake_drift_potential), n x n
    // values in 0..2. Until one is set (or after an empty one) the coating is even.
    bool set_drift_map(uint32_t n, const float* values);

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace dolly::env
