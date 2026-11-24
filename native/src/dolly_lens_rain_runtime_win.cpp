#include "dolly_lens_rain_runtime.hpp"

#include "dolly_lens_rain.hpp"
#include "dolly_lens_rain_dx11.hpp"
#include "dolly_lens_rain_wire.hpp"
#include "dolly_seqlock_win.hpp"
#include "dolly_video.hpp"
#include "dolly_weather_runtime.hpp"

#include <d3d11.h>
#include <wrl/client.h>

#include <algorithm>
#include <cmath>
#include <memory>
#include <mutex>

namespace dolly::lens_rain_runtime {
namespace {

// Weather rain rate that counts as full rain on the lens: the Rain storm preset at its
// default intensity and density. Stronger rain stays full.
constexpr float kFullRainRate = 1.2f;
// Steady wind speed (world units per second) that counts as strong wind on the lens.
constexpr float kStrongWind = 400.0f;

// ---- editor block (worker -> Present) ----
std::shared_ptr<const lens_rain::Wire> gBlock;

// ---- rendered view (view thread -> Present) ----
struct View {
    bool valid = false;
    double time = 0.0;
    bool seeking = false;
    double shot_phase = -1.0;
    float position[3]{};
    float angles[3]{};
};
std::mutex gViewMutex;
View gView;

// ---- Present-thread state ----
lens_rain::Simulator gSimulator;
lens_rain::Renderer gRenderer;
double gLastShotPhase = -1.0;

lens_rain::Vec3 vec(float x, float y, float z) {
    return {x, y, z};
}

// The game's AngleVectors: pitch down is positive, yaw turns left, roll banks.
void basis(const float angles[3], lens_rain::Inputs& in) {
    constexpr float kRad = 3.14159265358979f / 180.0f;
    const float sp = std::sin(angles[0] * kRad), cp = std::cos(angles[0] * kRad);
    const float sy = std::sin(angles[1] * kRad), cy = std::cos(angles[1] * kRad);
    const float sr = std::sin(angles[2] * kRad), cr = std::cos(angles[2] * kRad);
    in.forward = vec(cp * cy, cp * sy, -sp);
    in.right = vec(-sr * sp * cy + cr * sy, -sr * sp * sy - cr * cy, -sr * cp);
    in.up = vec(cr * sp * cy + sr * sy, cr * sp * sy - sr * cy, cr * cp);
}

bool draws_on_this_plate() noexcept {
    switch (video::current_plate()) {
    case video::Plate::Unspecified: // no recording, or an older editor: the live view
    case video::Plate::Color:
    case video::Plate::World:
        return true;
    default:
        return false; // Players, both Effects takes and Depth stay dry
    }
}

} // namespace

void poll_editor_block(const unsigned char* memory) noexcept {
    if (!memory)
        return;
    accept_seqlocked(memory + lens_rain::kWireOffset, gBlock,
                     [](const lens_rain::Wire& w) { return lens_rain::decode(w).has_value(); });
}

void publish_view(double replay_time, bool seeking, const float position[3], const float angles[3],
                  double shot_phase) noexcept {
    if (!std::isfinite(replay_time) || !position || !angles)
        return;
    View next;
    next.valid = true;
    next.time = replay_time;
    next.seeking = seeking;
    next.shot_phase = std::isfinite(shot_phase) ? shot_phase : -1.0;
    for (int i = 0; i < 3; ++i) {
        if (!std::isfinite(position[i]) || !std::isfinite(angles[i]))
            return;
        next.position[i] = position[i];
        next.angles[i] = angles[i];
    }
    std::lock_guard<std::mutex> lock(gViewMutex);
    gView = next;
}

bool panel_settings(lens_rain::Controls& out) noexcept {
    const auto block = std::atomic_load(&gBlock);
    if (!block)
        return false;
    const auto controls = lens_rain::decode(*block);
    if (!controls)
        return false;
    out = *controls;
    return true;
}

bool wants_render() noexcept {
    const auto block = std::atomic_load(&gBlock);
    const auto controls = block ? lens_rain::decode(*block) : std::nullopt;
    return (controls && controls->enabled) || gSimulator.initialized();
}

void render(ID3D11Device* device, ID3D11DeviceContext* context,
            ID3D11RenderTargetView* target) noexcept {
    try {
        const auto block = std::atomic_load(&gBlock);
        const auto controls = block ? lens_rain::decode(*block) : std::nullopt;
        if (!controls || !controls->enabled || !device || !context || !target) {
            if (gSimulator.initialized())
                gSimulator.clear();
            return;
        }
        View view;
        {
            std::lock_guard<std::mutex> lock(gViewMutex);
            view = gView;
        }
        if (!view.valid)
            return;

        Microsoft::WRL::ComPtr<ID3D11Resource> resource;
        target->GetResource(&resource);
        Microsoft::WRL::ComPtr<ID3D11Texture2D> back_buffer;
        if (!resource || FAILED(resource.As(&back_buffer)))
            return;
        D3D11_TEXTURE2D_DESC desc{};
        back_buffer->GetDesc(&desc);
        if (desc.Width == 0 || desc.Height == 0)
            return;

        lens_rain::Inputs in;
        in.replay_time = view.time;
        // A shot that starts playing (or starts over) restarts the lens at that frame,
        // so the lens never depends on the lead-in or on earlier playback.
        const bool shot_started = view.shot_phase >= 0.0 &&
                                  (gLastShotPhase < 0.0 || view.shot_phase + 1e-6 < gLastShotPhase);
        gLastShotPhase = view.shot_phase;
        in.seeking = view.seeking || shot_started;
        in.aspect = static_cast<float>(desc.Width) / static_cast<float>(desc.Height);
        in.position = vec(view.position[0], view.position[1], view.position[2]);
        basis(view.angles, in);
        const auto weather = weather_runtime::lens_inputs(view.position);
        if (weather.valid && weather.raining) {
            const float rain = weather.rain_rate / kFullRainRate;
            in.rain = std::isfinite(rain) ? (std::min)((std::max)(rain, 0.0f), 1.0f) : 0.0f;
            in.wind = vec(weather.wind_xy[0] / kStrongWind, weather.wind_xy[1] / kStrongWind, 0.0f);
            in.cover = weather.under_cover;
        }
        // The lens keeps simulating on every plate so separate takes of one shot see the
        // same water; only the drawing follows the plate.
        gSimulator.advance(controls->look, in);
        if (!draws_on_this_plate())
            return;
        gRenderer.render(device, context, back_buffer.Get(), gSimulator, controls->look);
    } catch (...) {
    }
}

void release() noexcept {
    gRenderer.release();
    gSimulator.clear();
    gLastShotPhase = -1.0;
}

} // namespace dolly::lens_rain_runtime
