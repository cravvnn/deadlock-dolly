#pragma once
// DX11 wind-driven precipitation: snow, rain, blowing sand and ash drawn as soft,
// motion-stretched flakes that follow the wind-fluid field, stay out from under
// roofs and bridge decks, and are occluded by scene depth.
//
// Every flake is a pure function of replay time, its index, the camera and the
// current fluid state; nothing is carried between frames. Pausing, scrubbing,
// seeking and fixed-step export therefore reproduce the same frame exactly.

#include "dolly_env_render_dx11.hpp"
#include "dolly_wind_fluid_dx11.hpp"

#include <cstdint>
#include <memory>
#include <string>

struct ID3D11Device;
struct ID3D11DeviceContext;
struct ID3D11ShaderResourceView;
struct ID3D11RenderTargetView;

namespace dolly::env {

enum class PrecipitationKind : uint32_t { None = 0, Snow = 1, Rain = 2, Sand = 3, Ash = 4 };

inline constexpr uint32_t kMaxPrecipitationFlakes = 131072;
// Particle looks (see PrecipitationParams::look). Look 2 ships; 0 (the first look) and 1 are
// kept for the offline tests. Looks 1 and 2 draw more, finer particles.
inline constexpr uint32_t kPrecipitationLooks = 3;
inline constexpr uint32_t kShippedPrecipitationLook = 2;
inline constexpr uint32_t kMaxPrecipitationFlakesStyled = 262144;

struct PrecipitationParams {
    PrecipitationKind kind = PrecipitationKind::None;
    // Relative emission; 1.0 is a moderate fall at intensity 1.0.
    float rate = 0.0f;
    // Flake size multiplier (Scene drawer).
    float size_scale = 1.0f;
    // Replay time in seconds; the only clock the pass reads.
    double time = 0.0;
    // Steady wind (units/s) used above the street layer the fluid simulates.
    float wind_xy[2] = {0.0f, 0.0f};
    // Sand stays within this band above the street.
    float height_ceiling = 180.0f;
    float sun_dir[3] = {0.52f, 0.30f, 0.80f};
    float sun_color[3] = {1.05f, 0.92f, 0.72f};
    float ambient_color[3] = {0.32f, 0.34f, 0.42f};
    uint32_t seed = 0x2545F491u;
    // 0: the first look. 1: varied sizes, streaks that dim and taper, particles that
    // dissolve into the haze with distance. 2: look 1 as mostly fine specks with fewer big
    // flakes or grains. Larger values are treated as 2. Rain looks the same in all three.
    uint32_t look = kShippedPrecipitationLook;
};

// Flakes drawn for these parameters (0 when off), capped at kMaxPrecipitationFlakes.
uint32_t precipitation_flake_count(const PrecipitationParams& params) noexcept;

class PrecipitationRendererDX11 {
public:
    PrecipitationRendererDX11();
    ~PrecipitationRendererDX11();
    PrecipitationRendererDX11(const PrecipitationRendererDX11&) = delete;
    PrecipitationRendererDX11& operator=(const PrecipitationRendererDX11&) = delete;

    bool initialize(ID3D11Device* device, std::string* out_error = nullptr);
    void shutdown();

    // Alpha-blends flakes over out_rtv (normally the atmosphere composite). Returns
    // false only for invalid input; an Off kind or zero rate draws nothing and
    // returns true.
    bool render(ID3D11DeviceContext* ctx, ID3D11ShaderResourceView* scene_depth_srv,
                ID3D11RenderTargetView* out_rtv, const EnvCamera& camera,
                const PrecipitationParams& params, const WindFluidSolverDX11& fluid);

    bool ready() const noexcept;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace dolly::env
