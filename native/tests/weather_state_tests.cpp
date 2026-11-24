#include "dolly_weather_state.hpp"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <limits>
#include <string>
#include <vector>

#define CHECK(expr)                                                                                \
    do {                                                                                           \
        if (!(expr)) {                                                                             \
            std::fprintf(stderr, "FAILED line %d: %s\n", __LINE__, #expr);                         \
            std::exit(1);                                                                          \
        }                                                                                          \
    } while (0)

using namespace dolly::weather;

namespace {

bool near(float a, float b, float eps = 1e-3f) {
    return std::fabs(a - b) <= eps;
}

void test_round_trip_every_preset() {
    for (std::uint32_t i = 0; i < kPresetCount; ++i) {
        Settings s = preset_settings(static_cast<Preset>(i));
        s.intensity = 1.75f;
        s.wind_direction = 271.5f;
        s.build_up = 12.5f + static_cast<float>(i);
        s.hero_wakes = (i % 2) == 0;
        s.include_in_effects = (i % 3) != 0;
        const Wire w = encode(s, 41u + i);
        CHECK(w.sequence == 41u + i);
        const auto back = decode(w);
        CHECK(back.has_value());
        CHECK(back->preset == s.preset);
        CHECK(back->intensity == s.intensity && back->wind_direction == s.wind_direction);
        CHECK(back->wind_speed == s.wind_speed && back->gusts == s.gusts);
        CHECK(back->coating == s.coating && back->height_ceiling == s.height_ceiling);
        CHECK(back->build_up == s.build_up);
        CHECK(back->hero_wakes == s.hero_wakes);
        CHECK(back->include_in_effects == s.include_in_effects);
    }
}

void test_defaults_are_in_range() {
    for (std::uint32_t i = 0; i < kPresetCount; ++i) {
        const PresetDefaults& d = kPresetDefaults[i];
        CHECK(d.wind_speed >= kWindSpeedMin && d.wind_speed <= kWindSpeedMax);
        CHECK(d.gusts >= kGustsMin && d.gusts <= kGustsMax);
        CHECK(d.coating >= kCoatingMin && d.coating <= kCoatingMax);
        CHECK(d.height_ceiling >= kHeightCeilingMin && d.height_ceiling <= kHeightCeilingMax);
    }
}

void test_decode_rejects_bad_blocks() {
    const Wire good = encode(preset_settings(Preset::Blizzard), 7u);
    CHECK(decode(good).has_value());

    Wire zeroed{};
    CHECK(!decode(zeroed).has_value());
    Wire flakes = good;
    flakes.flake_size = 0.0f;
    CHECK(!decode(flakes).has_value());

    Wire w = good;
    w.magic[7] = '2';
    CHECK(!decode(w).has_value());
    w = good;
    w.abi = kWireAbi + 1;
    CHECK(!decode(w).has_value());
    w = good;
    w.preset = kPresetCount;
    CHECK(!decode(w).has_value());
    w = good;
    w.flags |= 4u;
    CHECK(!decode(w).has_value());

    float Wire::*fields[] = {&Wire::intensity, &Wire::wind_direction, &Wire::wind_speed,
                             &Wire::gusts,     &Wire::coating,        &Wire::height_ceiling,
                             &Wire::build_up,  &Wire::flake_size,     &Wire::density};
    for (float Wire::*field : fields) {
        for (float bad : {std::numeric_limits<float>::quiet_NaN(),
                          std::numeric_limits<float>::infinity(), -1.0f, 5000.0f}) {
            w = good;
            w.*field = bad;
            CHECK(!decode(w).has_value());
        }
    }
}

void test_resolve_maps_presets_and_wind() {
    Settings s = preset_settings(Preset::Blizzard);
    s.wind_direction = 90.0f;
    s.wind_speed = 400.0f;
    s.intensity = 2.0f;
    s.hero_wakes = false;
    const Resolved r = resolve(s);
    CHECK(near(r.fluid.wind_x, 0.0f) && near(r.fluid.wind_y, 400.0f));
    CHECK(near(r.render.wind_xy[0], 0.0f) && near(r.render.wind_xy[1], 400.0f));
    CHECK(r.fluid.intensity == 2.0f && r.fluid.gust_strength == s.gusts);
    CHECK(!r.fluid.enable_hero_wakes);
    CHECK(r.render.preset == dolly::env::AtmospherePreset::Snow);
    CHECK(r.render.height_ceiling == s.height_ceiling);
    CHECK(r.render.enable_coating && r.render.coating_strength == s.coating);
    CHECK(r.precipitation.kind == Precipitation::Snow);
    CHECK(near(r.precipitation.rate, 3.2f));

    const Resolved off = resolve(preset_settings(Preset::Off));
    CHECK(off.render.preset == dolly::env::AtmospherePreset::Off);
    CHECK(off.render.intensity == 0.0f && !off.render.enable_coating);
    CHECK(off.precipitation.kind == Precipitation::None && off.precipitation.rate == 0.0f);

    const Resolved fog = resolve(preset_settings(Preset::GroundFog));
    CHECK(fog.render.preset == dolly::env::AtmospherePreset::GroundFog);
    CHECK(fog.precipitation.kind == Precipitation::None);
    CHECK(resolve(preset_settings(Preset::RainStorm)).precipitation.kind == Precipitation::Rain);
    CHECK(resolve(preset_settings(Preset::Ash)).render.preset ==
          dolly::env::AtmospherePreset::AshEmbers);
}

void test_map_name() {
    Wire w = encode(preset_settings(Preset::Ash), 1u, "dl_midtown", 640);
    CHECK(map_name(w) == "dl_midtown" && w.start_tick == 640);
    CHECK(decode(w).has_value());
    std::memcpy(w.map_name, "DL_MIDTOWN", 11);
    CHECK(map_name(w).empty());
    std::memset(w.map_name, 'a', sizeof(w.map_name)); // not terminated
    CHECK(map_name(w).empty());
    CHECK(map_name(encode(preset_settings(Preset::Ash), 1u, "../x")).empty());
    CHECK(map_name(encode(preset_settings(Preset::Ash), 1u)).empty());
}

void test_coating_growth() {
    Settings s = preset_settings(Preset::LightSnow);
    CHECK(coating_growth(s, 0.0) == 1.0f); // build_up 0: already settled
    s.build_up = 60.0f;
    CHECK(coating_growth(s, -5.0) == 0.0f && coating_growth(s, 0.0) == 0.0f);
    const float a = coating_growth(s, 10.0), b = coating_growth(s, 30.0);
    CHECK(a > 0.0f && a < b && b < 1.0f);
    CHECK(near(coating_growth(s, 60.0), 1.0f) && coating_growth(s, 600.0) == 1.0f);
    CHECK(coating_growth(s, 30.0) == b); // pure in time
}

trails::Wire trails_wire(std::uint32_t flags, const char* record, const char* show) {
    trails::Wire w{};
    std::memcpy(w.magic, trails::kWireMagic, sizeof(w.magic));
    w.abi = trails::kWireAbi;
    w.flags = flags;
    w.recording_id = 7;
    std::strncpy(w.record_path, record, sizeof(w.record_path) - 1);
    std::strncpy(w.show_path, show, sizeof(w.show_path) - 1);
    return w;
}

void test_trails_wire() {
    auto c = trails::decode(trails_wire(trails::kFlagRecord | trails::kFlagShow, "a.trk", "b.trk"));
    CHECK(c && c->record && c->show && c->recording_id == 7 && c->record_path == "a.trk" &&
          c->show_path == "b.trk");
    CHECK(trails::decode(trails_wire(0, "", "")).has_value()); // idle
    CHECK(!trails::decode(trails_wire(trails::kFlagRecord, "", "")));  // record needs a path
    CHECK(!trails::decode(trails_wire(trails::kFlagShow, "", "")));    // show needs a path
    CHECK(!trails::decode(trails_wire(8, "a", "b")));                  // unknown flag
    auto bad = trails_wire(0, "", "");
    bad.abi = 2;
    CHECK(!trails::decode(bad));
    auto open = trails_wire(0, "", "");
    std::memset(open.show_path, 'x', sizeof(open.show_path)); // not terminated
    CHECK(!trails::decode(open));
    CHECK(!trails::decode(trails::Wire{})); // nothing published yet
}

trails::Sample sample(double t, std::uint32_t slot, float x, float y) {
    trails::Sample s;
    s.time = t;
    s.slot = slot;
    s.pos[0] = x;
    s.pos[1] = y;
    s.pos[2] = 256.0f;
    return s;
}

void test_trail_segments() {
    // Hero 3 walks +x at 300 u/s sampled at 60 fps for 2 s; hero 5 stands, then teleports.
    std::vector<trails::Sample> samples;
    for (int i = 0; i <= 120; ++i)
        samples.push_back(sample(100.0 + i / 60.0, 3, 5.0f * i, 0.0f));
    samples.push_back(sample(100.0, 5, 0.0f, 0.0f));
    samples.push_back(sample(100.2, 5, 10.0f, 0.0f));
    samples.push_back(sample(100.4, 5, 3000.0f, 0.0f)); // too fast: a teleport, no segment
    samples.push_back(sample(101.5, 5, 3010.0f, 0.0f)); // after a gap: no segment
    const auto segs = trails::build_segments(samples);
    std::size_t hero3 = 0;
    for (const auto& s : segs) {
        CHECK(s.time_b > s.time_a);
        if (s.a[1] == 0.0f && s.time_a >= 100.0f && s.a[0] >= 0.0f && s.b[0] <= 600.0f &&
            s.b[0] - s.a[0] >= 0.0f)
            ++hero3;
    }
    // 2 s at 15 per second, plus hero 5's one short walk.
    CHECK(segs.size() == 31);
    CHECK(hero3 == 31);
    // Thinned steps are about 1/15 s; segments chain end to start per hero.
    CHECK(near(segs[1].a[0], segs[0].b[0]) && near(segs[1].time_a, segs[0].time_b));
    // Any order of the same samples gives the same segments.
    std::vector<trails::Sample> shuffled(samples.rbegin(), samples.rend());
    const auto again = trails::build_segments(shuffled);
    CHECK(again.size() == segs.size() &&
          std::memcmp(again.data(), segs.data(), segs.size() * sizeof(segs[0])) == 0);
    // Saved and read back exactly.
    const auto file = std::filesystem::temp_directory_path() / "dolly_trails_test.trk";
    std::string error;
    CHECK(trails::write_samples(file.u8string(), samples, &error));
    std::vector<trails::Sample> read;
    CHECK(trails::read_samples(file.u8string(), read, &error));
    CHECK(read.size() == samples.size());
    for (std::size_t i = 0; i < read.size(); ++i)
        CHECK(read[i].time == samples[i].time && read[i].slot == samples[i].slot &&
              std::memcmp(read[i].pos, samples[i].pos, sizeof(read[i].pos)) == 0);
    std::filesystem::remove(file);
    CHECK(!trails::read_samples(file.u8string(), read, &error) && read.empty());
}

} // namespace

int main() {
    test_round_trip_every_preset();
    test_defaults_are_in_range();
    test_decode_rejects_bad_blocks();
    test_resolve_maps_presets_and_wind();
    test_coating_growth();
    test_map_name();
    test_trails_wire();
    test_trail_segments();
    std::printf("All weather state checks passed.\n");
    return 0;
}
