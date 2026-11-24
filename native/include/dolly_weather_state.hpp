#pragma once
// Per-shot weather settings: presets, the editor wire block, and their resolution
// into wind-fluid and environment-render parameters.
//
// Preset ids are protocol-stable: append only, never reorder. dolly/weather.py
// mirrors the order, the defaults table and the wire layout, and tests assert the
// two stay in step.

#include "dolly_env_render_dx11.hpp"
#include "dolly_precip_dx11.hpp"
#include "dolly_wind_fluid_dx11.hpp"

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>

namespace dolly::weather {

enum class Preset : std::uint32_t {
    Off = 0,
    LightSnow = 1,
    Blizzard = 2,
    RainStorm = 3,
    Sandstorm = 4,
    DustHaze = 5,
    GroundFog = 6,
    Ash = 7,
};
constexpr std::uint32_t kPresetCount = 8;

constexpr float kIntensityMin = 0.1f, kIntensityMax = 3.0f;
constexpr float kWindSpeedMin = 0.0f, kWindSpeedMax = 900.0f;
constexpr float kGustsMin = 0.0f, kGustsMax = 2.5f;
constexpr float kCoatingMin = 0.0f, kCoatingMax = 1.0f;
constexpr float kHeightCeilingMin = 32.0f, kHeightCeilingMax = 800.0f;
constexpr float kWindDirectionDefault = 30.0f;
// Seconds from the shot start for the surface coating to settle; 0 = already settled.
constexpr float kBuildUpMin = 0.0f, kBuildUpMax = 600.0f;
// Precipitation look: flake size and density multipliers.
constexpr float kFlakeSizeMin = 0.25f, kFlakeSizeMax = 3.0f;
constexpr float kDensityMin = 0.1f, kDensityMax = 4.0f;

struct PresetDefaults {
    float wind_speed;
    float gusts;
    float coating;
    float height_ceiling;
};
// Indexed by Preset. Keep in step with _PRESET_DEFAULTS in dolly/weather.py.
constexpr PresetDefaults kPresetDefaults[kPresetCount] = {
    {180.0f, 1.0f, 0.0f, 180.0f},   // Off
    {90.0f, 0.5f, 0.35f, 260.0f},   // LightSnow
    {420.0f, 1.4f, 0.7f, 320.0f},   // Blizzard
    {260.0f, 1.1f, 0.6f, 140.0f},   // RainStorm
    {380.0f, 1.25f, 0.35f, 220.0f}, // Sandstorm
    {140.0f, 0.6f, 0.15f, 320.0f},  // DustHaze
    {45.0f, 0.25f, 0.1f, 120.0f},   // GroundFog
    {120.0f, 0.7f, 0.3f, 240.0f},   // Ash
};

// Wind direction uses the game's yaw convention: the direction the wind blows
// toward, in degrees, with 0 along world +X and 90 along world +Y.
struct Settings {
    Preset preset = Preset::Off;
    float intensity = 1.0f;
    float wind_direction = kWindDirectionDefault;
    float wind_speed = kPresetDefaults[0].wind_speed;
    float gusts = kPresetDefaults[0].gusts;
    float coating = kPresetDefaults[0].coating;
    float height_ceiling = kPresetDefaults[0].height_ceiling;
    float build_up = 0.0f;
    float flake_size = 1.0f;
    float density = 1.0f;
    bool hero_wakes = true;
    bool include_in_effects = true;

    bool enabled() const noexcept { return preset != Preset::Off; }
};

// A preset at its defaults.
Settings preset_settings(Preset preset) noexcept;

constexpr char kWireMagic[8] = {'D', 'L', 'Y', 'W', 'T', 'H', 'R', '1'};
constexpr std::uint32_t kWireAbi = 1;
constexpr std::uint32_t kWireFlagHeroWakes = 1;
constexpr std::uint32_t kWireFlagIncludeInEffects = 2;
// The editor publishes this block in the shared mapping (seqlocked: magic at 0,
// sequence at 8, ABI at 12) whenever the shot's weather or replay changes.
constexpr std::size_t kWireOffset = 2 * 1024 * 1024 + 24480;
#pragma pack(push, 1)
struct Wire {
    char magic[8];
    std::uint32_t sequence;
    std::uint32_t abi;
    std::uint32_t preset;
    std::uint32_t flags;
    float intensity;
    float wind_direction;
    float wind_speed;
    float gusts;
    float coating;
    float height_ceiling;
    float build_up;
    // Map from the replay header (for example "dl_midtown"), NUL-terminated.
    char map_name[32];
    // Shot start tick: the origin of the coating build-up.
    std::int32_t start_tick;
    float flake_size;
    float density;
};
#pragma pack(pop)
static_assert(sizeof(Wire) == 96, "Python weather wire layout must match");
static_assert(offsetof(Wire, sequence) == 8, "Seqlocked block layout");
static_assert(offsetof(Wire, intensity) == 24, "Python weather wire layout must match");
static_assert(kWireOffset + sizeof(Wire) <= 2 * 1024 * 1024 + 24576,
              "Weather block fits the gap before the object page");

Wire encode(const Settings& settings, std::uint32_t sequence, const char* map_name = "",
            std::int32_t start_tick = 0) noexcept;
// Rejects a block with the wrong magic or ABI, an unknown preset, unknown flag
// bits, or any value that is not finite or lies outside its range. A zeroed
// block (nothing published yet) is rejected the same way.
std::optional<Settings> decode(const Wire& wire) noexcept;
// The block's map name when it is a plain map identifier ([a-z0-9_], 1..31
// characters, NUL-terminated); empty otherwise.
std::string map_name(const Wire& wire);

// In-game Scene drawer controls, in EditorAction order from SetWeatherPreset:
// preset, intensity, build-up, wind direction, wind speed, gusts, hero wakes, flake size,
// density, coating, height ceiling, include in Effects. Whether `value` is valid for the
// control at `index`.
bool valid_panel_value(unsigned index, double value) noexcept;

using Precipitation = dolly::env::PrecipitationKind;
using PrecipitationParams = dolly::env::PrecipitationParams;

struct Resolved {
    dolly::env::WindFluidParams fluid{};
    dolly::env::EnvRenderParams render{};
    PrecipitationParams precipitation{};
    bool include_in_effects = true;
};

// Settings are assumed valid (from decode or preset_settings). The caller sets
// precipitation.time and render.sim_time from the replay clock.
Resolved resolve(const Settings& settings) noexcept;

// Fraction of the coating settled at seconds_since_start (from the shot start): 1 when
// build_up is 0, otherwise an ease-out reaching 1 at build_up seconds. Pure in time, so
// scrubbing and seeking reproduce it. The caller assigns it to render.coating_growth.
float coating_growth(const Settings& settings, double seconds_since_start) noexcept;

} // namespace dolly::weather
