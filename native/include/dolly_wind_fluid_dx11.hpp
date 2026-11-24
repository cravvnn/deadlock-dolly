#pragma once
// DX11 Compute 2D Stable Fluids Wind Solver with Voronoi FoamLace sand lifting,
// sweeping gust fronts, 12 hero wakes, and up to 16 world-placed directional emitters.
//
// Fluid solver architecture, obstacle/openness conventions, FoamLace lifting,
// Catmull-Rom density advection, and 3D Perlin-Worley noise generation are
// adapted from SourceMVM (MIT License, Copyright (c) 2021 advancedfx.org,
// Copyright (c) 2026 Wockstajit (SOURCE:MVM)).

#include "dolly_map_grid.hpp"

#include <array>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

struct ID3D11Device;
struct ID3D11DeviceContext;
struct ID3D11ShaderResourceView;

namespace dolly::env {

inline constexpr uint32_t kMaxFluidBodies = 12;
inline constexpr uint32_t kMaxFluidEmitters = 16;

struct FluidBody {
    float world_pos[3] = {0.0f, 0.0f, 0.0f};
    float radius = 48.0f;
    float velocity[3] = {0.0f, 0.0f, 0.0f};
    float active = 1.0f;
};

struct DirectionalEmitter {
    float world_pos[3] = {0.0f, 0.0f, 0.0f};
    float radius = 320.0f;
    float dir_norm[3] = {1.0f, 0.0f, 0.0f};
    float jet_speed = 650.0f;
    float color[3] = {0.88f, 0.72f, 0.46f};
    float density = 1.0f;
    float cone_cos = 0.45f;     // -1 = omnidirectional, >0 = focused directional cone
    float height_min = -16.0f;  // bottom Z relative to local floor layer
    float height_max = 180.0f;  // top Z relative to local floor layer
    float active_weight = 1.0f; // smooth fade-in/out from [start_tick, end_tick]
};

struct WindFluidParams {
    float wind_x = 180.0f;
    float wind_y = 95.0f;
    float gust_strength = 1.0f;
    float swirl_strength = 3.5f;
    float intensity = 1.0f;
    float carry_decay_seconds = 3.0f;
    float wind_relax_seconds = 1.1f;
    int jacobi_iterations = 20;
    bool enable_hero_wakes = true;
    bool enable_world_collision = true;
};

struct FluidGridInfo {
    uint32_t width = 0;
    uint32_t height = 0;
    float origin_x = 0.0f;
    float origin_y = 0.0f;
    float cell_units = 0.0f;
    // Lowest and highest walkable floor or street height on the map (world Z); the floor
    // texture (R16G16B16A16_UNORM) stores 1 + (z - floor_min) / (floor_max - floor_min) * 65534,
    // and 0 for no floor.
    float floor_min = 0.0f;
    float floor_max = 0.0f;
    bool valid() const noexcept { return width >= 8 && height >= 8 && cell_units > 0.0f; }
};

struct FluidDiagnostics {
    uint32_t width = 0;
    uint32_t height = 0;
    uint32_t solid_cells = 0;
    uint32_t non_finite_cells = 0;
    float max_speed = 0.0f;
    float max_solid_speed = 0.0f;
    float mean_open_speed = 0.0f;
    float max_density = 0.0f;
    float mean_open_density = 0.0f;
    float rms_open_divergence = 0.0f;
};

// Deterministic replay-time stepper (freeze on pause, reset+prewarm on backward seek or jump).
struct ReplayFluidClock {
    double last_time = 0.0;
    bool initialized = false;
    float max_substep = 1.0f / 30.0f;
    int max_steps_per_frame = 4;
    double seek_jump_threshold = 1.5;

    struct Advance {
        bool reset = false;
        int steps = 0;
        float dt_total = 0.0f;
    };

    Advance tick(double replay_time) noexcept;
    void reset() noexcept {
        last_time = 0.0;
        initialized = false;
    }
};

// Generates a seamless 3D Perlin-Worley noise volume (size^3, 0..255) for volumetric erosion.
std::vector<uint8_t> generate_gust_noise_volume(int size = 64);

class WindFluidSolverDX11 {
public:
    WindFluidSolverDX11();
    ~WindFluidSolverDX11();
    WindFluidSolverDX11(const WindFluidSolverDX11&) = delete;
    WindFluidSolverDX11& operator=(const WindFluidSolverDX11&) = delete;

    bool initialize(ID3D11Device* device, std::string* out_error = nullptr);
    void shutdown();

    // Uploads baked MapGrid textures (air_map, tops, street_z, floor_z[4]) and allocates
    // ping-pong fluid/pressure/scalar compute surfaces.
    // If sim_resolution > 0 and != grid.width, fluid simulation runs at sim_resolution while
    // sampling the full-resolution map grid.
    bool upload_map_grid(const map_grid::MapGrid& grid, uint32_t sim_resolution = 0,
                         std::string* out_error = nullptr);

    void reset_and_prewarm(ID3D11DeviceContext* ctx, double replay_time,
                           const WindFluidParams& params, const DirectionalEmitter* emitters,
                           uint32_t emitter_count, int prewarm_steps = 40,
                           float prewarm_seconds = 2.0f);

    // Advances fluid strictly by replay_time delta. Returns number of simulation substeps executed
    // (0 when paused, >0 when playing, negative value not returned; resets+prewarms on seek).
    // The wind is simulated on fixed 60 Hz ticks of the replay timeline and the rendered
    // state is the exact blend between the two ticks around replay_time. `anchor_time`
    // (the shot start, in seconds; negative for none) fixes where history begins: two
    // seconds before it. Every path to the same replay time - playback, any export frame
    // rate, a seek - then yields the same state. Returns the ticks simulated (0 when the
    // replay time is unchanged). `bodies` (hero positions at replay_time) shape only this
    // frame's blended state, never the ticks; their velocity is not used there.
    int step_replay_time(ID3D11DeviceContext* ctx, double replay_time,
                         const WindFluidParams& params, const FluidBody* bodies,
                         uint32_t body_count, const DirectionalEmitter* emitters,
                         uint32_t emitter_count, double anchor_time = -1.0);

    // Advances fluid by one explicit dt step (used for fixed-step offline tests).
    void step_dt(ID3D11DeviceContext* ctx, float dt, double sim_time, const WindFluidParams& params,
                 const FluidBody* bodies, uint32_t body_count, const DirectionalEmitter* emitters,
                 uint32_t emitter_count);

    // Reads back the fluid field the renderers sample (the blended state once stepped) to CPU
    // for verification metrics and an optional RGBA8 preview.
    bool readback_diagnostics(ID3D11DeviceContext* ctx, FluidDiagnostics& out_diag,
                              std::vector<uint8_t>* out_topdown_rgba = nullptr,
                              const FluidBody* bodies = nullptr, uint32_t body_count = 0,
                              const DirectionalEmitter* emitters = nullptr,
                              uint32_t emitter_count = 0) const;

    bool ready() const noexcept;
    bool has_grid() const noexcept;
    FluidGridInfo sim_grid_info() const noexcept;
    FluidGridInfo map_grid_info() const noexcept;
    uint32_t total_steps() const noexcept;

    ID3D11ShaderResourceView* fluid_srv() const noexcept;
    ID3D11ShaderResourceView* air_map_srv() const noexcept;
    ID3D11ShaderResourceView* tops_srv() const noexcept;
    ID3D11ShaderResourceView* street_z_srv() const noexcept;
    ID3D11ShaderResourceView* floor_z_srv() const noexcept;
    ID3D11ShaderResourceView* noise_3d_srv() const noexcept;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace dolly::env
