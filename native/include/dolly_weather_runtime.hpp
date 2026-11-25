#pragma once
// Live weather in the game overlay: the editor's weather block, the replay clock,
// the map collision grid, and the per-Present weather pass.
//
// Threads: the bridge worker polls the editor block, the game's view thread publishes
// the replay clock, a background thread bakes the map grid, and the game's Present
// thread renders. Every entry point is noexcept and does nothing when weather is off.

struct ID3D11Device;
struct ID3D11DeviceContext;
struct ID3D11RenderTargetView;
struct IDXGISwapChain;

namespace dolly::depth {
struct SceneFrame;
}
#include "dolly_weather_state.hpp"

namespace dolly::weather_runtime {

// Bridge worker: accept a newly published weather block from the shared mapping.
void poll_editor_block(const unsigned char* memory) noexcept;

// View thread: the replay clock for this view (seconds, tick interval), whether the
// replay is playing (a loaded demo), paused or seeking, and the heroes' world origins seen
// at that time (for hero wakes; at most 12 are used, in the order given), with each hero's
// stable slot (entity index) when known, for the replay's hero tracks.
// `replay_name` is the loaded replay's name as the game reports it: tracks are kept and
// drawn only when it is the replay whose store the editor named.
void publish_clock(double replay_time, double tick_interval, bool playing, bool paused,
                   bool seeking, const float (*heroes)[3] = nullptr, uint32_t hero_count = 0,
                   const uint32_t* slots = nullptr, const char* replay_name = nullptr) noexcept;

// View thread: whether hero positions are wanted (a replay's track store is open, or the
// weather is on with hero wakes or coating).
bool wants_heroes() noexcept;

// Present thread: whether the weather pass needs this frame's scene depth.
bool wants_scene_depth() noexcept;

// The shot's weather as last published by the editor, for the in-game Scene drawer.
// False before the editor has published any weather.
bool panel_settings(weather::Settings& out) noexcept;

// How much of the shot's window the replay's hero track store has seen (0..1), or -1 when
// no replay's store is open. For the Scene drawer's tracks line.
double hero_track_coverage() noexcept;

// Read-only weather facts for other screen passes (lens rain). Present thread only.
struct LensInputs {
    bool valid = false;       // weather on and a map grid ready
    bool raining = false;     // the shot's precipitation is rain
    float rain_rate = 0.0f;   // relative rate: preset rate x intensity x density
    float wind_xy[2] = {};    // steady wind, world units per second
    float under_cover = 0.0f; // 0 open sky above the camera .. 1 under a roof or deck
};
// `camera` is the rendered camera position in world units.
LensInputs lens_inputs(const float camera[3]) noexcept;

// Present thread, inside a dedicated device-state scope before ReShade and capture.
// Composites the weather into `target` (the back buffer) using the frame's verified
// scene depth and the game's per-view constants captured with it.
void render(ID3D11Device* device, ID3D11DeviceContext* context, IDXGISwapChain* chain,
            ID3D11RenderTargetView* target, const depth::SceneFrame* frame) noexcept;

// Present thread: drop every GPU resource (device loss, resize, shutdown).
void release() noexcept;

// One-line status for diagnostics.
const char* status() noexcept;

} // namespace dolly::weather_runtime
