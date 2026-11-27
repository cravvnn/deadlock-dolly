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
#include <array>
#include <optional>
#include <string>
#include <utility>
#include <vector>

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

enum class Quality : std::uint32_t {
    Standard = 0,
    High = 1,
};

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
    bool hero_tracks = true; // footprint tracks in snow, sand, dust and ash
    Quality quality = Quality::Standard;

    bool enabled() const noexcept { return preset != Preset::Off; }
};

// A preset at its defaults.
Settings preset_settings(Preset preset) noexcept;

constexpr char kWireMagic[8] = {'D', 'L', 'Y', 'W', 'T', 'H', 'R', '1'};
constexpr std::uint32_t kWireAbi = 1;
constexpr std::uint32_t kWireFlagHeroWakes = 1;
constexpr std::uint32_t kWireFlagIncludeInEffects = 2;
constexpr std::uint32_t kWireFlagHeroTracks = 4;
constexpr std::uint32_t kWireFlagHighQuality = 8;
// Presets whose ground cover takes hero tracks (snow, sand, dust and ash).
bool takes_tracks(Preset preset) noexcept;
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

// ---- Hero trails ----
// The editor asks the game to record the heroes' paths while it plays a shot through (the
// prepare pass), and names the recorded file whose trails the weather draws. The paths
// are recorded once and saved, so live view and every export draw the same trails.
namespace trails {

// The editor names the replay's hero track store and the shot's time window. The game fills
// the store while the replay plays (never while paused or seeking) and draws the window's
// tracks from it; the store is a file beside Dolly's map grids, never the replay itself.
constexpr char kWireMagic[8] = {'D', 'L', 'Y', 'T', 'R', 'L', 'S', '2'};
constexpr std::uint32_t kWireAbi = 2;
constexpr std::uint32_t kFlagStore = 1; // store_path names this replay's track store
constexpr std::size_t kPathBytes = 240;
constexpr std::size_t kReplayBytes = 128;
// Seqlocked (magic at 0, sequence at 8, ABI at 12), after the lens rain block and before
// the cinematic status.
constexpr std::size_t kWireOffset = 2 * 1024 * 1024 + 59456;
#pragma pack(push, 1)
struct Wire {
    char magic[8];
    std::uint32_t sequence;
    std::uint32_t abi;
    std::uint32_t flags;
    std::uint32_t reserved;
    double window_start; // the shot's replay time range (s): tracks are drawn from here on
    double window_end;
    char store_path[kPathBytes]; // UTF-8, NUL-terminated
    // The replay the store belongs to (its file name; folders and extension are ignored).
    // The game keeps and draws tracks only while it plays that replay.
    char replay[kReplayBytes];
};
#pragma pack(pop)
static_assert(sizeof(Wire) == 280 + kReplayBytes, "Python hero trails wire layout must match");
static_assert(offsetof(Wire, sequence) == 8, "Seqlocked block layout");
static_assert(kWireOffset + sizeof(Wire) <= 2 * 1024 * 1024 + 60000,
              "Hero trails block fits before the cinematic status");

struct Controls {
    std::string store_path; // empty: no store (no replay open)
    std::string replay;     // the replay it belongs to (as published)
    double window_start = 0.0;
    double window_end = 0.0;
};
// Rejects a wrong magic or ABI, unknown flags, a path or replay that is not NUL-terminated,
// the store flag without a path and replay, and a window that is not finite or runs
// backwards.
std::optional<Controls> decode(const Wire& wire);

// Hero and Solomon chess-piece positions on a fixed grid of 1/15 s replay-time cells:
// each cell is written once (whoever fills it first: live playback or the replay reader) and
// never changed, so every frame drawn from the store depends only on its time.
constexpr double kStep = 1.0 / 15.0;
// Chess pieces leave a narrower drag mark (~55% of a hero footpath) and no kick-up puffs.
constexpr float kPieceWidthScale = 0.55f;
struct Cell {
    std::int32_t grid = 0; // replay time = grid * kStep
    std::uint32_t slot = 0;
    float pos[3] = {};
};

class Store {
public:
    // Writes a hero cell unless that hero already has one there. Returns true when written.
    bool add(std::uint32_t slot, std::int32_t grid, const float pos[3]);
    // Writes a chess-piece cell unless that piece already has one there. Returns true when written.
    bool add_piece(std::uint32_t slot, std::int32_t grid, const float pos[3]);
    // Marks [t0, t1] as seen (played or read): merged with the ranges already seen. Returns
    // true when that changed them.
    bool add_seen(double t0, double t1);
    // A hero across one forward playback step from (t0, a) to (t1, b): fills the cells whose
    // times lie in (t0, t1], each from the line between the two frames. Returns how many
    // cells were written (their grids appended to `written` when given).
    std::size_t add_step(double t0, double t1, std::uint32_t slot, const float a[3],
                         const float b[3], std::vector<std::int32_t>* written = nullptr);
    const std::vector<std::pair<double, double>>& seen() const { return seen_; }
    std::size_t size() const { return cells_ + piece_cells_; }
    std::size_t hero_size() const { return cells_; }
    std::size_t piece_size() const { return piece_cells_; }
    void clear();

    // Trail segments of the cells inside [start, end]: per hero and per chess piece,
    // consecutive cells joined unless the entity vanished (over half a second without a cell)
    // or teleported. Chess-piece segments use kPieceWidthScale and kickup = 0.
    std::vector<dolly::env::TrailSegment> segments(double start, double end) const;
    // The segment that joins `slot`'s cell at `grid` to its previous cell, when the two join
    // and both lie inside [start, end].
    bool segment_to(std::uint32_t slot, std::int32_t grid, double start, double end,
                    dolly::env::TrailSegment& out) const;
    // True when `slot` has a cell after `grid` (a cell filled in behind others).
    bool has_after(std::uint32_t slot, std::int32_t grid) const;
    // What writing `slot`'s cell at `grid` changed in the joined segments: `rejoin` when the
    // cells before and after it were already joined (that segment is gone: rebuild all),
    // else `next` is the grid of the cell after it (whose segment now starts here), or -1.
    struct InsertEffect {
        bool rejoin = false;
        std::int32_t next = -1;
    };
    InsertEffect insert_effect(std::uint32_t slot, std::int32_t grid) const;

    // File "DLYTRK03" (also loads older "DLYTRK02" files): hero cell count, seen-range count
    // (u32 each), the ranges (f64 pairs), the hero cells (grid i32, slot u32, x, y, z f32),
    // then piece cell count (u32) and the piece cells. Saved through a temporary file.
    bool save(const std::string& utf8_path, std::string* error = nullptr) const;
    // Loads a store file; a missing file is an empty store.
    bool load(const std::string& utf8_path, std::string* error = nullptr);

    // Takes the cells read from the replay file itself (DollyReplayTracks: the same format):
    // inside the time it covers they replace every cell kept while playing (the replay is
    // exact where a playback frame can hitch, and leaves out entities that are not alive), and
    // playback adds nothing there afterwards (add_step skips that time).
    void adopt(const Store& from_replay);
    const std::vector<std::pair<double, double>>& from_replay() const { return fixed_; }

private:
    struct Hero {
        std::vector<std::pair<std::int32_t, std::array<float, 3>>> cells; // sorted by grid
    };
    std::vector<std::pair<std::uint32_t, Hero>> heroes_; // sorted by slot
    std::vector<std::pair<std::uint32_t, Hero>> pieces_; // sorted by slot (chess pieces)
    std::vector<std::pair<double, double>> seen_;          // sorted, disjoint
    std::vector<std::pair<double, double>> fixed_;         // read from the replay file
    std::size_t cells_ = 0;
    std::size_t piece_cells_ = 0;
    bool fixed_at(double t) const;
    const Hero* find(std::uint32_t slot) const;
};

} // namespace trails

} // namespace dolly::weather
