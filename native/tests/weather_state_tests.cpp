#include "dolly_weather_state.hpp"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
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
        s.quality = (i % 2) == 1 ? Quality::High : Quality::Standard;
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
        CHECK(back->quality == s.quality);
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
    w.flags |= 16u; // 1, 2, 4 and 8 are known (hero tracks is 4, high quality is 8)
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
    Settings hq = preset_settings(Preset::RainStorm);
    CHECK(!resolve(hq).render.high_quality && !resolve(hq).precipitation.high_quality);
    hq.quality = Quality::High;
    CHECK(resolve(hq).render.high_quality && resolve(hq).precipitation.high_quality);
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

trails::Wire trails_wire(std::uint32_t flags, const char* store, double start, double end) {
    trails::Wire w{};
    std::memcpy(w.magic, trails::kWireMagic, sizeof(w.magic));
    w.abi = trails::kWireAbi;
    w.flags = flags;
    w.window_start = start;
    w.window_end = end;
    std::strncpy(w.store_path, store, sizeof(w.store_path) - 1);
    if (*store)
        std::strncpy(w.replay, "139468139.dem", sizeof(w.replay) - 1);
    return w;
}

void test_trails_wire() {
    auto c = trails::decode(trails_wire(trails::kFlagStore, "a.trk", 10.0, 40.0));
    CHECK(c && c->store_path == "a.trk" && c->window_start == 10.0 && c->window_end == 40.0 &&
          c->replay == "139468139.dem");
    auto nameless = trails_wire(trails::kFlagStore, "a.trk", 10.0, 40.0);
    std::memset(nameless.replay, 0, sizeof(nameless.replay));
    CHECK(!trails::decode(nameless)); // a store always names its replay
    c = trails::decode(trails_wire(0, "", 0.0, 0.0)); // no replay open
    CHECK(c && c->store_path.empty());
    CHECK(!trails::decode(trails_wire(trails::kFlagStore, "", 0.0, 1.0))); // store needs a path
    CHECK(!trails::decode(trails_wire(8, "a", 0.0, 1.0)));                 // unknown flag
    CHECK(!trails::decode(trails_wire(trails::kFlagStore, "a", 5.0, 1.0))); // backwards window
    CHECK(!trails::decode(trails_wire(trails::kFlagStore, "a", 0.0, std::nan(""))));
    auto bad = trails_wire(0, "", 0.0, 0.0);
    bad.abi = 1;
    CHECK(!trails::decode(bad));
    auto open = trails_wire(0, "", 0.0, 0.0);
    std::memset(open.store_path, 'x', sizeof(open.store_path)); // not terminated
    CHECK(!trails::decode(open));
    CHECK(!trails::decode(trails::Wire{})); // nothing published yet
}

// Hero 3 walks +x at 300 u/s from t = 100 s; hero 5 stands, then teleports, then vanishes.
void walk(trails::Store& store, double fps, double from, double to) {
    const double dt = 1.0 / fps;
    for (double t = from; t + dt <= to + 1e-9; t += dt) {
        const float a[3] = {static_cast<float>(300.0 * (t - 100.0)), 0.0f, 256.0f};
        const float b[3] = {static_cast<float>(300.0 * (t + dt - 100.0)), 0.0f, 256.0f};
        store.add_step(t, t + dt, 3, a, b);
        store.add_seen(t, t + dt);
    }
}

bool same_segments(std::vector<dolly::env::TrailSegment> a,
                   std::vector<dolly::env::TrailSegment> b) {
    const auto order = [](const auto& x, const auto& y) {
        return std::memcmp(&x, &y, sizeof(x)) < 0;
    };
    std::sort(a.begin(), a.end(), order);
    std::sort(b.begin(), b.end(), order);
    return a.size() == b.size() &&
           (a.empty() || std::memcmp(a.data(), b.data(), a.size() * sizeof(a[0])) == 0);
}

void test_track_store() {
    // Cells sit on the 1/15 s grid whatever the frame rate: 60 fps and 144 fps agree to well
    // under a unit, and the walk's 30 cells in (100, 102] give 29 joined segments.
    trails::Store a, b;
    walk(a, 60.0, 100.0, 102.05);
    walk(b, 144.0, 100.0, 102.05);
    const auto sa = a.segments(100.0, 102.0), sb = b.segments(100.0, 102.0);
    CHECK(sa.size() == 29 && sb.size() == 29);
    for (std::size_t i = 0; i < sa.size() && i < sb.size(); ++i)
        CHECK(std::abs(sa[i].a[0] - sb[i].a[0]) < 0.01f && sa[i].time_a == sb[i].time_a);
    for (const auto& s : sa)
        CHECK(near(s.time_b - s.time_a, static_cast<float>(trails::kStep)));
    // Written once: playing the same time again (at another rate) changes nothing.
    trails::Store again = a;
    walk(again, 30.0, 100.0, 102.05);
    CHECK(same_segments(again.segments(100.0, 102.0), sa) && again.size() == a.size());
    // Seen ranges merge; the window clips segments to the shot.
    CHECK(a.seen().size() == 1 && near(static_cast<float>(a.seen()[0].first), 100.0f) &&
          std::abs(a.seen()[0].second - 102.05) < 0.02);
    a.add_seen(105.0, 106.0);
    a.add_seen(101.0, 105.0);
    CHECK(a.seen().size() == 1 && a.seen()[0].second == 106.0);
    CHECK(a.segments(101.0, 101.5).size() == 7); // cells 1515..1522
    // The live path (each new cell's segment as it arrives) equals the whole build at every
    // moment: a frame drawn while filling matches one drawn from the finished store.
    trails::Store live;
    std::vector<dolly::env::TrailSegment> streamed;
    const double dt = 1.0 / 60.0;
    for (double t = 100.0; t + dt <= 102.05 + 1e-9; t += dt) {
        const float p0[3] = {static_cast<float>(300.0 * (t - 100.0)), 0.0f, 256.0f};
        const float p1[3] = {static_cast<float>(300.0 * (t + dt - 100.0)), 0.0f, 256.0f};
        std::vector<std::int32_t> written;
        live.add_step(t, t + dt, 3, p0, p1, &written);
        for (const auto g : written) {
            CHECK(!live.has_after(3, g));
            dolly::env::TrailSegment seg{};
            if (live.segment_to(3, g, 100.0, 102.0, seg))
                streamed.push_back(seg);
        }
        CHECK(same_segments(streamed, live.segments(100.0, 102.0)));
    }
    CHECK(same_segments(streamed, sa));
    // Breaks: a teleport (too fast) and a vanishing (a gap over half a second) join nothing.
    trails::Store jumps;
    const float p0[3] = {0, 0, 0}, p1[3] = {10, 0, 0}, far[3] = {3000, 0, 0},
                p3[3] = {3010, 0, 0};
    jumps.add(5, 1500, p0);
    jumps.add(5, 1503, p1);  // 0.2 s later: joined
    jumps.add(5, 1506, far); // 2990 u in 0.2 s: a teleport
    jumps.add(5, 1530, p3);  // 1.6 s later: vanished
    CHECK(jumps.segments(0.0, 200.0).size() == 1);
    CHECK(!jumps.add(5, 1503, far) && jumps.size() == 4); // never rewritten
    // Filled in behind: has_after sees it; insert_effect says what changed.
    CHECK(jumps.has_after(5, 1501));
    trails::Store behind;
    const float q0[3] = {0, 0, 0}, q1[3] = {20, 0, 0}, q2[3] = {40, 0, 0}, q9[3] = {200, 0, 0};
    behind.add(1, 100, q0);
    behind.add(1, 120, q9); // 20 cells later: not joined to cell 100
    behind.add(1, 101, q1); // played later, behind cell 120
    auto effect = behind.insert_effect(1, 101);
    CHECK(!effect.rejoin && effect.next == 120); // adds 100->101 and 101->120's own joint
    behind.add(1, 119, q2);
    CHECK(behind.insert_effect(1, 119).next == 120);
    behind.add(1, 108, q2); // between 101 and 119, which were not joined (18 cells apart)
    CHECK(!behind.insert_effect(1, 108).rejoin);
    behind.add(1, 105, q1);
    CHECK(behind.insert_effect(1, 105).rejoin); // 101 and 108 were joined: rebuild
    CHECK(behind.insert_effect(1, 120).next == -1); // the newest cell
    // The replay file's cells replace those kept while playing inside the time they cover
    // (a dead hero's live cells go), playback adds nothing there afterwards, and cells
    // outside it stay.
    trails::Store kept, from_file;
    const float live_pos[3] = {1, 1, 1}, file_pos[3] = {2, 2, 2};
    kept.add(4, 10, live_pos);   // t 0.67: inside the file's time
    kept.add(9, 11, live_pos);   // a hero the file has no cell for there (not alive)
    kept.add(4, 900, live_pos);  // t 60: after it
    kept.add_seen(0.5, 1.0);
    from_file.add(4, 10, file_pos);
    from_file.add(4, 12, file_pos);
    from_file.add_seen(0.0, 30.0);
    kept.adopt(from_file);
    CHECK(kept.size() == 3 && kept.from_replay().size() == 1);
    const auto segs = kept.segments(0.0, 1.0);
    CHECK(segs.size() == 1 && segs[0].a[0] == 2.0f && segs[0].b[0] == 2.0f);
    CHECK(kept.segments(0.0, 100.0).size() == 1); // 12 -> 900 is far too long a gap
    const float step_a[3] = {0, 0, 0}, step_b[3] = {5, 0, 0};
    CHECK(kept.add_step(1.0, 1.1, 9, step_a, step_b) == 0); // the file gave this time
    CHECK(kept.add_step(40.0, 40.1, 9, step_a, step_b) == 1); // outside it: kept
    CHECK(kept.seen().size() == 1 && kept.seen()[0].first == 0.0 && kept.seen()[0].second == 30.0);
    // Saved and read back exactly; a missing file is an empty store; a bad one is refused.
    const auto file = std::filesystem::temp_directory_path() / "dolly_tracks_test.trk";
    std::string error;
    CHECK(a.save(file.u8string(), &error));
    trails::Store read;
    CHECK(read.load(file.u8string(), &error));
    CHECK(read.size() == a.size() && read.seen() == a.seen());
    CHECK(same_segments(read.segments(0.0, 1000.0), a.segments(0.0, 1000.0)));
    std::filesystem::remove(file);
    CHECK(read.load(file.u8string(), &error) && read.size() == 0);
    {
        std::ofstream junk(file, std::ios::binary);
        junk << "DLYTRK01 not a store";
    }
    CHECK(!read.load(file.u8string(), &error) && read.size() == 0);
    std::filesystem::remove(file);

    // Chess pieces: stored alongside heroes in DLYTRK03, slide faster than heroes (up to
    // 4000 u/s), emit narrower track-only segments (width_scale = kPieceWidthScale, kickup = 0),
    // adopt cleanly from the replay reader, and old DLYTRK02 files still load.
    {
        trails::Store mix;
        const float h0[3] = {0, 0, 256}, h1[3] = {15, 0, 256};
        const float c0[3] = {100, 50, 256}, c1[3] = {240, 50, 256}; // 140 u in 1/15 s = 2100 u/s
        CHECK(mix.add(3, 150, h0));
        CHECK(mix.add(3, 151, h1));
        CHECK(mix.add_piece(0x1000c00u, 150, c0));
        CHECK(mix.add_piece(0x1000c00u, 151, c1));
        mix.add_seen(0.0, 20.0);
        CHECK(mix.hero_size() == 2 && mix.piece_size() == 2 && mix.size() == 4);
        const auto segs_mix = mix.segments(0.0, 20.0);
        CHECK(segs_mix.size() == 2);
        CHECK(segs_mix[0].width_scale == 1.0f && segs_mix[0].kickup == 1.0f);
        CHECK(near(segs_mix[1].width_scale, trails::kPieceWidthScale) && segs_mix[1].kickup == 0.0f);
        // Round-trip DLYTRK03 save + load.
        CHECK(mix.save(file.u8string(), &error));
        trails::Store loaded;
        CHECK(loaded.load(file.u8string(), &error));
        CHECK(loaded.hero_size() == 2 && loaded.piece_size() == 2 && loaded.size() == 4);
        CHECK(same_segments(loaded.segments(0.0, 20.0), segs_mix));
        // Adopt replaces both hero and piece cells inside the replay's seen range.
        trails::Store target;
        target.add(3, 150, c0);
        target.add_piece(0x2000c00u, 150, h0);
        target.adopt(loaded);
        CHECK(target.hero_size() == 2 && target.piece_size() == 2);
        CHECK(same_segments(target.segments(0.0, 20.0), segs_mix));
        // Legacy DLYTRK02 file (heroes only, no trailing piece section) still loads cleanly.
        {
            std::ofstream v2(file, std::ios::binary | std::ios::trunc);
            const char magic[8] = {'D', 'L', 'Y', 'T', 'R', 'K', '0', '2'};
            const std::uint32_t head[2] = {2u, 1u};
            const double range[2] = {0.0, 20.0};
            const std::int32_t g0 = 150, g1 = 151;
            const std::uint32_t slot = 3u;
            v2.write(magic, 8);
            v2.write(reinterpret_cast<const char*>(head), sizeof(head));
            v2.write(reinterpret_cast<const char*>(range), sizeof(range));
            v2.write(reinterpret_cast<const char*>(&g0), 4);
            v2.write(reinterpret_cast<const char*>(&slot), 4);
            v2.write(reinterpret_cast<const char*>(h0), 12);
            v2.write(reinterpret_cast<const char*>(&g1), 4);
            v2.write(reinterpret_cast<const char*>(&slot), 4);
            v2.write(reinterpret_cast<const char*>(h1), 12);
        }
        trails::Store from_v2;
        CHECK(from_v2.load(file.u8string(), &error));
        CHECK(from_v2.hero_size() == 2 && from_v2.piece_size() == 0 && from_v2.seen().size() == 1);
        CHECK(from_v2.segments(0.0, 20.0).size() == 1);
        std::filesystem::remove(file);
    }
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
    test_track_store();
    std::printf("All weather state checks passed.\n");
    return 0;
}
