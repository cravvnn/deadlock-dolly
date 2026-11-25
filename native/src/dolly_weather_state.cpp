#include "dolly_weather_state.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>

namespace dolly::weather {
namespace {

bool in_range(float value, float low, float high) noexcept {
    return std::isfinite(value) && value >= low && value <= high;
}

struct Recipe {
    dolly::env::AtmospherePreset atmosphere;
    float density_scale;
    float wisp_strength;
    Precipitation precipitation;
    float precipitation_rate;
};

// Indexed by Preset.
constexpr Recipe kRecipes[kPresetCount] = {
    {dolly::env::AtmospherePreset::Off, 1.0f, 1.0f, Precipitation::None, 0.0f},
    {dolly::env::AtmospherePreset::Snow, 0.45f, 0.8f, Precipitation::Snow, 0.5f},
    {dolly::env::AtmospherePreset::Snow, 1.4f, 1.2f, Precipitation::Snow, 1.6f},
    {dolly::env::AtmospherePreset::Rain, 0.6f, 0.7f, Precipitation::Rain, 1.2f},
    {dolly::env::AtmospherePreset::Sandstorm, 1.0f, 1.0f, Precipitation::Sand, 0.8f},
    {dolly::env::AtmospherePreset::DustHaze, 0.7f, 0.9f, Precipitation::Sand, 0.2f},
    {dolly::env::AtmospherePreset::GroundFog, 1.0f, 1.0f, Precipitation::None, 0.0f},
    {dolly::env::AtmospherePreset::AshEmbers, 0.8f, 1.0f, Precipitation::Ash, 0.6f},
};

} // namespace

Settings preset_settings(Preset preset) noexcept {
    const auto index = static_cast<std::uint32_t>(preset);
    const PresetDefaults& d = kPresetDefaults[index < kPresetCount ? index : 0];
    Settings s;
    s.preset = index < kPresetCount ? preset : Preset::Off;
    s.wind_speed = d.wind_speed;
    s.gusts = d.gusts;
    s.coating = d.coating;
    s.height_ceiling = d.height_ceiling;
    return s;
}

Wire encode(const Settings& settings, std::uint32_t sequence, const char* map_name,
            std::int32_t start_tick) noexcept {
    Wire w{};
    std::memcpy(w.magic, kWireMagic, sizeof(w.magic));
    w.abi = kWireAbi;
    w.sequence = sequence;
    w.preset = static_cast<std::uint32_t>(settings.preset);
    w.flags = (settings.hero_wakes ? kWireFlagHeroWakes : 0u) |
              (settings.include_in_effects ? kWireFlagIncludeInEffects : 0u) |
              (settings.hero_tracks ? kWireFlagHeroTracks : 0u);
    w.intensity = settings.intensity;
    w.wind_direction = settings.wind_direction;
    w.wind_speed = settings.wind_speed;
    w.gusts = settings.gusts;
    w.coating = settings.coating;
    w.height_ceiling = settings.height_ceiling;
    w.build_up = settings.build_up;
    w.flake_size = settings.flake_size;
    w.density = settings.density;
    if (map_name)
        for (std::size_t i = 0; i + 1 < sizeof(w.map_name) && map_name[i]; ++i)
            w.map_name[i] = map_name[i];
    w.start_tick = start_tick;
    return w;
}

std::optional<Settings> decode(const Wire& wire) noexcept {
    if (std::memcmp(wire.magic, kWireMagic, sizeof(wire.magic)) != 0 || wire.abi != kWireAbi ||
        wire.preset >= kPresetCount ||
        (wire.flags & ~(kWireFlagHeroWakes | kWireFlagIncludeInEffects | kWireFlagHeroTracks)) !=
            0 ||
        !in_range(wire.flake_size, kFlakeSizeMin, kFlakeSizeMax) ||
        !in_range(wire.density, kDensityMin, kDensityMax))
        return std::nullopt;
    if (!in_range(wire.intensity, kIntensityMin, kIntensityMax) ||
        !in_range(wire.wind_direction, 0.0f, 360.0f) ||
        !in_range(wire.wind_speed, kWindSpeedMin, kWindSpeedMax) ||
        !in_range(wire.gusts, kGustsMin, kGustsMax) ||
        !in_range(wire.coating, kCoatingMin, kCoatingMax) ||
        !in_range(wire.height_ceiling, kHeightCeilingMin, kHeightCeilingMax) ||
        !in_range(wire.build_up, kBuildUpMin, kBuildUpMax))
        return std::nullopt;
    Settings s;
    s.preset = static_cast<Preset>(wire.preset);
    s.intensity = wire.intensity;
    s.wind_direction = wire.wind_direction;
    s.wind_speed = wire.wind_speed;
    s.gusts = wire.gusts;
    s.coating = wire.coating;
    s.height_ceiling = wire.height_ceiling;
    s.build_up = wire.build_up;
    s.flake_size = wire.flake_size;
    s.density = wire.density;
    s.hero_wakes = (wire.flags & kWireFlagHeroWakes) != 0;
    s.include_in_effects = (wire.flags & kWireFlagIncludeInEffects) != 0;
    s.hero_tracks = (wire.flags & kWireFlagHeroTracks) != 0;
    return s;
}

bool takes_tracks(Preset preset) noexcept {
    return preset == Preset::LightSnow || preset == Preset::Blizzard ||
           preset == Preset::Sandstorm || preset == Preset::DustHaze || preset == Preset::Ash;
}

bool valid_panel_value(unsigned index, double value) noexcept {
    if (!std::isfinite(value))
        return false;
    const auto within = [value](double low, double high) { return value >= low && value <= high; };
    const bool flag = value == 0.0 || value == 1.0;
    switch (index) {
    case 0:
        return value == std::floor(value) && within(0, kPresetCount - 1);
    case 1:
        return within(kIntensityMin, kIntensityMax);
    case 2:
        return within(kBuildUpMin, kBuildUpMax);
    case 3:
        return within(0, 360);
    case 4:
        return within(kWindSpeedMin, kWindSpeedMax);
    case 5:
        return within(kGustsMin, kGustsMax);
    case 6:
        return flag;
    case 7:
        return within(kFlakeSizeMin, kFlakeSizeMax);
    case 8:
        return within(kDensityMin, kDensityMax);
    case 9:
        return within(kCoatingMin, kCoatingMax);
    case 10:
        return within(kHeightCeilingMin, kHeightCeilingMax);
    case 11:
        return flag;
    default:
        return false;
    }
}

std::string map_name(const Wire& wire) {
    std::string name;
    for (char c : wire.map_name) {
        if (c == '\0')
            return name;
        if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_'))
            return {};
        name.push_back(c);
    }
    return {}; // not terminated
}

Resolved resolve(const Settings& settings) noexcept {
    const auto index = static_cast<std::uint32_t>(settings.preset);
    const Recipe& recipe = kRecipes[index < kPresetCount ? index : 0];
    constexpr float kDegToRad = 3.14159265358979f / 180.0f;
    const float wind_x = settings.wind_speed * std::cos(settings.wind_direction * kDegToRad);
    const float wind_y = settings.wind_speed * std::sin(settings.wind_direction * kDegToRad);

    Resolved r;
    r.fluid.wind_x = wind_x;
    r.fluid.wind_y = wind_y;
    r.fluid.gust_strength = settings.gusts;
    r.fluid.intensity = settings.intensity;
    r.fluid.enable_hero_wakes = settings.hero_wakes;

    r.render.preset = recipe.atmosphere;
    r.render.intensity = settings.enabled() ? settings.intensity : 0.0f;
    r.render.height_ceiling = settings.height_ceiling;
    r.render.density_scale = recipe.density_scale;
    r.render.wisp_strength = recipe.wisp_strength;
    r.render.coating_strength = settings.coating;
    r.render.enable_coating = settings.enabled() && settings.coating > 0.0f;
    r.render.wind_xy[0] = wind_x;
    r.render.wind_xy[1] = wind_y;

    r.precipitation.kind = recipe.precipitation;
    r.precipitation.rate = recipe.precipitation_rate * settings.intensity * settings.density;
    // Heavier snowfall or blowing sand fills tracks in faster: about two minutes for snow and
    // one for sand at the presets' defaults.
    const bool snow = recipe.atmosphere == dolly::env::AtmospherePreset::Snow;
    r.render.trail_fill_seconds =
        (snow ? 120.0f : 60.0f) / std::max(settings.intensity * settings.density, 0.2f);
    r.render.coating_build_up = settings.build_up;
    r.render.hero_trails = settings.hero_tracks;
    r.precipitation.size_scale = settings.flake_size;
    r.precipitation.wind_xy[0] = wind_x;
    r.precipitation.wind_xy[1] = wind_y;
    r.precipitation.height_ceiling = settings.height_ceiling;
    for (int i = 0; i < 3; ++i) {
        r.precipitation.sun_dir[i] = r.render.sun_dir[i];
        r.precipitation.sun_color[i] = r.render.sun_color[i];
        r.precipitation.ambient_color[i] = r.render.ambient_color[i];
    }
    r.include_in_effects = settings.include_in_effects;
    return r;
}

float coating_growth(const Settings& settings, double seconds_since_start) noexcept {
    if (!(settings.build_up > 0.0f))
        return 1.0f;
    constexpr double kRate = 3.0;
    const double age = std::max(seconds_since_start, 0.0);
    const double grown =
        (1.0 - std::exp(-kRate * age / settings.build_up)) / (1.0 - std::exp(-kRate));
    return static_cast<float>(std::min(grown, 1.0));
}

namespace trails {
namespace {

bool terminated(const char (&text)[kPathBytes]) noexcept {
    return std::memchr(text, 0, kPathBytes) != nullptr;
}

constexpr char kFileMagic[8] = {'D', 'L', 'Y', 'T', 'R', 'K', '0', '2'};
// More than any replay needs (12 heroes, 15 cells a second, for over three hours).
constexpr std::uint32_t kMaxCells = 2u * 1024u * 1024u;
constexpr std::uint32_t kMaxRanges = 1u << 20;
// Broken over gaps longer than half a second or speeds above what a hero reaches (dashes
// included).
constexpr std::int32_t kGapCells = 7; // 7/15 s
constexpr float kMaxSpeed = 1200.0f;
// Cells cover replay times up to about two years; far beyond any replay.
constexpr double kMaxTime = 0x3fffffff * kStep;

bool joins(std::int32_t ga, const float a[3], std::int32_t gb, const float b[3]) {
    if (gb <= ga || gb - ga > kGapCells)
        return false;
    const float dx = b[0] - a[0], dy = b[1] - a[1];
    const float dt = static_cast<float>((gb - ga) * kStep);
    return std::sqrt(dx * dx + dy * dy) <= kMaxSpeed * dt + 8.0f;
}

dolly::env::TrailSegment make_segment(std::int32_t ga, const float a[3], std::int32_t gb,
                                      const float b[3]) {
    dolly::env::TrailSegment seg{};
    std::memcpy(seg.a, a, sizeof(seg.a));
    std::memcpy(seg.b, b, sizeof(seg.b));
    seg.time_a = static_cast<float>(ga * kStep);
    seg.time_b = static_cast<float>(gb * kStep);
    return seg;
}

// Grid cells whose times lie inside [start, end].
std::pair<std::int64_t, std::int64_t> grid_range(double start, double end) {
    return {static_cast<std::int64_t>(std::ceil(start / kStep - 1e-6)),
            static_cast<std::int64_t>(std::floor(end / kStep + 1e-6))};
}

} // namespace

std::optional<Controls> decode(const Wire& wire) {
    if (std::memcmp(wire.magic, kWireMagic, sizeof(kWireMagic)) != 0 || wire.abi != kWireAbi ||
        (wire.flags & ~kFlagStore) != 0 || !terminated(wire.store_path) ||
        std::memchr(wire.replay, 0, kReplayBytes) == nullptr ||
        !std::isfinite(wire.window_start) || !std::isfinite(wire.window_end) ||
        wire.window_end < wire.window_start)
        return std::nullopt;
    Controls c;
    if (wire.flags & kFlagStore) {
        c.store_path = wire.store_path;
        c.replay = wire.replay;
        if (c.store_path.empty() || c.replay.empty())
            return std::nullopt;
    }
    c.window_start = wire.window_start;
    c.window_end = wire.window_end;
    return c;
}

const Store::Hero* Store::find(std::uint32_t slot) const {
    const auto it = std::lower_bound(heroes_.begin(), heroes_.end(), slot,
                                     [](const auto& h, std::uint32_t s) { return h.first < s; });
    return it != heroes_.end() && it->first == slot ? &it->second : nullptr;
}

bool Store::add(std::uint32_t slot, std::int32_t grid, const float pos[3]) {
    if (!std::isfinite(pos[0]) || !std::isfinite(pos[1]) || !std::isfinite(pos[2]) ||
        std::abs(pos[0]) > 1e6f || std::abs(pos[1]) > 1e6f || std::abs(pos[2]) > 1e6f ||
        grid < 0 || cells_ >= kMaxCells)
        return false;
    auto hit = std::lower_bound(heroes_.begin(), heroes_.end(), slot,
                                [](const auto& h, std::uint32_t s) { return h.first < s; });
    if (hit == heroes_.end() || hit->first != slot)
        hit = heroes_.insert(hit, {slot, Hero{}});
    auto& cells = hit->second.cells;
    const std::array<float, 3> p{pos[0], pos[1], pos[2]};
    if (cells.empty() || cells.back().first < grid) { // live playback: always at the end
        cells.emplace_back(grid, p);
        ++cells_;
        return true;
    }
    const auto it = std::lower_bound(cells.begin(), cells.end(), grid,
                                     [](const auto& c, std::int32_t g) { return c.first < g; });
    if (it != cells.end() && it->first == grid)
        return false; // written once, never changed
    cells.insert(it, {grid, p});
    ++cells_;
    return true;
}

bool Store::add_seen(double t0, double t1) {
    if (!std::isfinite(t0) || !std::isfinite(t1) || t1 < t0 || t0 < 0.0 || t1 > kMaxTime)
        return false;
    // Ranges closer than half a cell are one range.
    const double join = 0.5 * kStep;
    if (!seen_.empty() && seen_.back().first <= t0 && t0 <= seen_.back().second + join) {
        if (t1 <= seen_.back().second)
            return false;
        seen_.back().second = t1; // the common case
        return true;
    }
    if (seen_.size() >= kMaxRanges)
        return false;
    const auto before = seen_;
    seen_.emplace_back(t0, t1);
    std::sort(seen_.begin(), seen_.end());
    std::vector<std::pair<double, double>> merged;
    for (const auto& r : seen_) {
        if (!merged.empty() && r.first <= merged.back().second + join)
            merged.back().second = std::max(merged.back().second, r.second);
        else
            merged.push_back(r);
    }
    seen_.swap(merged);
    return seen_ != before;
}

std::size_t Store::add_step(double t0, double t1, std::uint32_t slot, const float a[3],
                            const float b[3], std::vector<std::int32_t>* written) {
    if (!std::isfinite(t0) || !std::isfinite(t1) || !(t1 > t0) || t0 < 0.0 || t1 > kMaxTime)
        return 0;
    std::size_t n = 0;
    const auto first = static_cast<std::int64_t>(std::floor(t0 / kStep)) + 1;
    const auto last = static_cast<std::int64_t>(std::floor(t1 / kStep));
    for (std::int64_t g = first; g <= last; ++g) {
        const float f = static_cast<float>((g * kStep - t0) / (t1 - t0));
        const float p[3] = {a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f,
                            a[2] + (b[2] - a[2]) * f};
        if (add(slot, static_cast<std::int32_t>(g), p)) {
            ++n;
            if (written)
                written->push_back(static_cast<std::int32_t>(g));
        }
    }
    return n;
}

void Store::clear() {
    heroes_.clear();
    seen_.clear();
    cells_ = 0;
}

std::vector<dolly::env::TrailSegment> Store::segments(double start, double end) const {
    std::vector<dolly::env::TrailSegment> out;
    const auto [lo, hi] = grid_range(start, end);
    for (const auto& h : heroes_) {
        const auto& cells = h.second.cells;
        auto it = std::lower_bound(cells.begin(), cells.end(), lo,
                                   [](const auto& c, std::int64_t g) { return c.first < g; });
        for (; it != cells.end() && it + 1 != cells.end() && (it + 1)->first <= hi; ++it) {
            const auto& a = *it;
            const auto& b = *(it + 1);
            if (!joins(a.first, a.second.data(), b.first, b.second.data()))
                continue;
            if (out.size() >= dolly::env::EnvRendererDX11::kMaxTrailSegments)
                return out;
            out.push_back(make_segment(a.first, a.second.data(), b.first, b.second.data()));
        }
    }
    return out;
}

bool Store::segment_to(std::uint32_t slot, std::int32_t grid, double start, double end,
                       dolly::env::TrailSegment& out) const {
    const Hero* h = find(slot);
    if (!h)
        return false;
    const auto [lo, hi] = grid_range(start, end);
    const auto& cells = h->cells;
    const auto it = std::lower_bound(cells.begin(), cells.end(), grid,
                                     [](const auto& c, std::int32_t g) { return c.first < g; });
    if (it == cells.end() || it->first != grid || it == cells.begin())
        return false;
    const auto& a = *(it - 1);
    if (a.first < lo || grid > hi || !joins(a.first, a.second.data(), grid, it->second.data()))
        return false;
    out = make_segment(a.first, a.second.data(), grid, it->second.data());
    return true;
}

Store::InsertEffect Store::insert_effect(std::uint32_t slot, std::int32_t grid) const {
    InsertEffect effect;
    const Hero* h = find(slot);
    if (!h)
        return effect;
    const auto& cells = h->cells;
    const auto it = std::lower_bound(cells.begin(), cells.end(), grid,
                                     [](const auto& c, std::int32_t g) { return c.first < g; });
    if (it == cells.end() || it->first != grid || it + 1 == cells.end())
        return effect; // newest: only its own segment is new
    const auto& next = *(it + 1);
    effect.next = next.first;
    if (it != cells.begin()) {
        const auto& prev = *(it - 1);
        effect.rejoin = joins(prev.first, prev.second.data(), next.first, next.second.data());
    }
    return effect;
}

bool Store::has_after(std::uint32_t slot, std::int32_t grid) const {
    const Hero* h = find(slot);
    return h && !h->cells.empty() && h->cells.back().first > grid;
}

bool Store::save(const std::string& utf8_path, std::string* error) const {
    try {
        const auto path = std::filesystem::u8path(utf8_path);
        if (path.has_parent_path())
            std::filesystem::create_directories(path.parent_path());
        auto partial = path;
        partial += ".partial";
        {
            std::ofstream out(partial, std::ios::binary | std::ios::trunc);
            const std::uint32_t head[2] = {static_cast<std::uint32_t>(cells_),
                                           static_cast<std::uint32_t>(seen_.size())};
            out.write(kFileMagic, sizeof(kFileMagic));
            out.write(reinterpret_cast<const char*>(head), sizeof(head));
            for (const auto& r : seen_) {
                out.write(reinterpret_cast<const char*>(&r.first), 8);
                out.write(reinterpret_cast<const char*>(&r.second), 8);
            }
            for (const auto& h : heroes_)
                for (const auto& c : h.second.cells) {
                    out.write(reinterpret_cast<const char*>(&c.first), 4);
                    out.write(reinterpret_cast<const char*>(&h.first), 4);
                    out.write(reinterpret_cast<const char*>(c.second.data()), 12);
                }
            out.flush();
            if (!out) {
                if (error)
                    *error = "could not write the hero track store";
                return false;
            }
        }
        std::filesystem::rename(partial, path);
        return true;
    } catch (const std::exception& exc) {
        if (error)
            *error = exc.what();
        return false;
    }
}

bool Store::load(const std::string& utf8_path, std::string* error) {
    clear();
    try {
        const auto path = std::filesystem::u8path(utf8_path);
        if (!std::filesystem::exists(path))
            return true; // nothing seen yet
        std::ifstream in(path, std::ios::binary);
        char magic[8]{};
        std::uint32_t head[2]{};
        in.read(magic, sizeof(magic));
        in.read(reinterpret_cast<char*>(head), sizeof(head));
        if (!in || std::memcmp(magic, kFileMagic, sizeof(magic)) != 0 || head[0] > kMaxCells ||
            head[1] > kMaxRanges) {
            if (error)
                *error = "not a hero track store";
            return false;
        }
        std::vector<std::pair<double, double>> ranges(head[1]);
        for (auto& r : ranges) {
            in.read(reinterpret_cast<char*>(&r.first), 8);
            in.read(reinterpret_cast<char*>(&r.second), 8);
        }
        for (std::uint32_t i = 0; i < head[0] && in; ++i) {
            std::int32_t grid = 0;
            std::uint32_t slot = 0;
            float pos[3]{};
            in.read(reinterpret_cast<char*>(&grid), 4);
            in.read(reinterpret_cast<char*>(&slot), 4);
            in.read(reinterpret_cast<char*>(pos), 12);
            if (in)
                add(slot, grid, pos);
        }
        if (!in) {
            clear();
            if (error)
                *error = "the hero track store is truncated";
            return false;
        }
        for (const auto& r : ranges)
            add_seen(r.first, r.second);
        return true;
    } catch (const std::exception& exc) {
        clear();
        if (error)
            *error = exc.what();
        return false;
    }
}

} // namespace trails

} // namespace dolly::weather
