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
              (settings.include_in_effects ? kWireFlagIncludeInEffects : 0u);
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
        (wire.flags & ~(kWireFlagHeroWakes | kWireFlagIncludeInEffects)) != 0 ||
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
    return s;
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

constexpr char kFileMagic[8] = {'D', 'L', 'Y', 'T', 'R', 'K', '0', '1'};
constexpr std::size_t kSampleBytes = 8 + 4 + 12;
// More samples than any shot needs (12 heroes at 240 fps for over half an hour).
constexpr std::uint32_t kMaxSamples = 8u * 1024u * 1024u;
// Thinned to 15 per second; broken over gaps longer than half a second or speeds above
// what a hero reaches (dashes included).
constexpr double kStep = 1.0 / 15.0;
constexpr double kGap = 0.5;
constexpr float kMaxSpeed = 1200.0f;

} // namespace

std::optional<Controls> decode(const Wire& wire) {
    if (std::memcmp(wire.magic, kWireMagic, sizeof(kWireMagic)) != 0 || wire.abi != kWireAbi ||
        (wire.flags & ~(kFlagRecord | kFlagShow)) != 0 || !terminated(wire.record_path) ||
        !terminated(wire.show_path))
        return std::nullopt;
    Controls c;
    c.record = (wire.flags & kFlagRecord) != 0;
    c.show = (wire.flags & kFlagShow) != 0;
    c.recording_id = wire.recording_id;
    c.record_path = wire.record_path;
    c.show_path = wire.show_path;
    if ((c.record && c.record_path.empty()) || (c.show && c.show_path.empty()))
        return std::nullopt;
    return c;
}

bool write_samples(const std::string& utf8_path, const std::vector<Sample>& samples,
                   std::string* error) {
    try {
        if (samples.size() > kMaxSamples) {
            if (error)
                *error = "too many hero path samples";
            return false;
        }
        const auto path = std::filesystem::u8path(utf8_path);
        auto partial = path;
        partial += ".partial";
        {
            std::ofstream out(partial, std::ios::binary | std::ios::trunc);
            const std::uint32_t head[2] = {static_cast<std::uint32_t>(samples.size()), 0u};
            out.write(kFileMagic, sizeof(kFileMagic));
            out.write(reinterpret_cast<const char*>(head), sizeof(head));
            for (const Sample& s : samples) {
                out.write(reinterpret_cast<const char*>(&s.time), 8);
                out.write(reinterpret_cast<const char*>(&s.slot), 4);
                out.write(reinterpret_cast<const char*>(s.pos), 12);
            }
            out.flush();
            if (!out) {
                if (error)
                    *error = "could not write the hero path file";
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

bool read_samples(const std::string& utf8_path, std::vector<Sample>& samples, std::string* error) {
    samples.clear();
    try {
        std::ifstream in(std::filesystem::u8path(utf8_path), std::ios::binary);
        char magic[8]{};
        std::uint32_t head[2]{};
        in.read(magic, sizeof(magic));
        in.read(reinterpret_cast<char*>(head), sizeof(head));
        if (!in || std::memcmp(magic, kFileMagic, sizeof(magic)) != 0 || head[0] > kMaxSamples) {
            if (error)
                *error = "not a hero path file";
            return false;
        }
        samples.resize(head[0]);
        for (Sample& s : samples) {
            in.read(reinterpret_cast<char*>(&s.time), 8);
            in.read(reinterpret_cast<char*>(&s.slot), 4);
            in.read(reinterpret_cast<char*>(s.pos), 12);
        }
        if (!in) {
            samples.clear();
            if (error)
                *error = "the hero path file is truncated";
            return false;
        }
        return true;
    } catch (const std::exception& exc) {
        samples.clear();
        if (error)
            *error = exc.what();
        return false;
    }
}

std::vector<dolly::env::TrailSegment> build_segments(std::vector<Sample> samples) {
    samples.erase(std::remove_if(samples.begin(), samples.end(),
                                 [](const Sample& s) {
                                     return !std::isfinite(s.time) || !std::isfinite(s.pos[0]) ||
                                            !std::isfinite(s.pos[1]) || !std::isfinite(s.pos[2]);
                                 }),
                  samples.end());
    // A total order, so samples recorded in any order give the same segments.
    std::sort(samples.begin(), samples.end(), [](const Sample& a, const Sample& b) {
        if (a.slot != b.slot)
            return a.slot < b.slot;
        if (a.time != b.time)
            return a.time < b.time;
        return std::memcmp(a.pos, b.pos, sizeof(a.pos)) < 0;
    });
    std::vector<dolly::env::TrailSegment> out;
    const Sample* kept = nullptr;
    for (std::size_t i = 0; i < samples.size(); ++i) {
        const Sample& s = samples[i];
        if (kept && kept->slot != s.slot)
            kept = nullptr;
        if (!kept) {
            kept = &s;
            continue;
        }
        const double dt = s.time - kept->time;
        const bool last = i + 1 == samples.size() || samples[i + 1].slot != s.slot;
        // The first sample in each 1/15 s slot of the replay clock (and the hero's last), so
        // the thinning is the same whatever frame rate recorded the samples.
        if (std::floor(s.time / kStep) == std::floor(kept->time / kStep) && !last)
            continue;
        const float dx = s.pos[0] - kept->pos[0], dy = s.pos[1] - kept->pos[1];
        const float dist = std::sqrt(dx * dx + dy * dy);
        if (dt > 0.0 && dt <= kGap && dist <= kMaxSpeed * static_cast<float>(dt) + 8.0f) {
            dolly::env::TrailSegment seg{};
            std::memcpy(seg.a, kept->pos, sizeof(seg.a));
            std::memcpy(seg.b, s.pos, sizeof(seg.b));
            seg.time_a = static_cast<float>(kept->time);
            seg.time_b = static_cast<float>(s.time);
            if (seg.time_b > seg.time_a)
                out.push_back(seg);
        }
        kept = &s;
        if (out.size() >= dolly::env::EnvRendererDX11::kMaxTrailSegments)
            break;
    }
    return out;
}

} // namespace trails

} // namespace dolly::weather
