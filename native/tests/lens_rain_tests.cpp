// Lens rain simulation checks: determinism, resets, pause, cover and bounds.
#include "dolly_lens_rain.hpp"
#include "dolly_lens_rain_wire.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace {

int failures = 0;

void expect(bool ok, const char* what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what);
        ++failures;
    }
}

// A camera that pans and moves forward at a constant rate, in steady rain.
lens_rain::Inputs scene_at(double t) {
    lens_rain::Inputs in;
    in.replay_time = t;
    in.rain = 0.8f;
    in.wind = {0.3f, 0.2f, 0.0f};
    const float yaw = static_cast<float>(0.25 * t); // radians
    in.forward = {std::cos(yaw), std::sin(yaw), 0.0f};
    in.right = {std::sin(yaw), -std::cos(yaw), 0.0f};
    in.up = {0.0f, 0.0f, 1.0f};
    in.position = {static_cast<float>(120.0 * t), 0.0f, 64.0f};
    return in;
}

struct Scene : lens_rain::InputSource {
    lens_rain::Inputs at(double t) const override { return scene_at(t); }
};

// The same camera without the pan: straight-line motion only.
lens_rain::Inputs straight_at(double t) {
    lens_rain::Inputs in = scene_at(t);
    in.forward = {1.0f, 0.0f, 0.0f};
    in.right = {0.0f, -1.0f, 0.0f};
    return in;
}

lens_rain::Settings shot() {
    lens_rain::Settings s;
    s.amount = 0.6f;
    s.seed = 0xC0FFEEu;
    return s;
}

enum class Mode { exact, interpolated, straight };

// Plays [start, end] at `fps`, starting with a seek to `start`.
lens_rain::Simulator play(double start, double end, int fps, Mode mode = Mode::exact) {
    lens_rain::Simulator sim;
    const lens_rain::Settings s = shot();
    const Scene scene;
    const lens_rain::InputSource* exact = mode == Mode::exact ? &scene : nullptr;
    auto sample = [mode](double t) {
        return mode == Mode::straight ? straight_at(t) : scene_at(t);
    };
    lens_rain::Inputs first = sample(start);
    first.seeking = true;
    sim.advance(s, first, exact);
    const long long frames = std::llround((end - start) * fps);
    for (long long i = 1; i <= frames; ++i)
        sim.advance(s, sample(start + static_cast<double>(i) / fps), exact);
    return sim;
}

void frame_rate_independence() {
    const auto a = play(10.0, 14.0, 30);
    const auto b = play(10.0, 14.0, 120);
    const auto c = play(10.0, 14.0, 60);
    expect(a.step_index() == b.step_index(), "30/120 fps reach the same step");
    expect(a.state_hash() == b.state_hash(), "30 and 120 fps give the same lens");
    expect(a.state_hash() == c.state_hash(), "30 and 60 fps give the same lens");
    expect(!a.drops().empty(), "steady rain leaves drops on the lens");

    // Without an exact source, straight-line motion still matches exactly.
    const auto d = play(10.0, 14.0, 30, Mode::straight);
    const auto e = play(10.0, 14.0, 120, Mode::straight);
    expect(d.state_hash() == e.state_hash(),
           "interpolated straight motion matches at 30 and 120 fps");

    // A turning camera without an exact source is only approximately equal.
    const auto f = play(10.0, 14.0, 30, Mode::interpolated);
    const auto g = play(10.0, 14.0, 120, Mode::interpolated);
    const double fc = static_cast<double>(f.drops().size()),
                 gc = static_cast<double>(g.drops().size());
    expect(std::fabs(fc - gc) <= 0.25 * std::max(fc, gc) + 2.0, "interpolated preview stays close");
    std::printf("fps: %zu drops, %zu beads, hash %016llx\n", a.drops().size(), a.beads().size(),
                static_cast<unsigned long long>(a.state_hash()));
}

void warm_up_opens_wet() {
    lens_rain::Simulator sim;
    lens_rain::Inputs in = scene_at(5.0);
    in.seeking = true;
    sim.advance(shot(), in);
    expect(sim.drops().size() >= 10, "a shot opening in rain starts wet");
}

void seek_reproduces_state() {
    // Play forward to 6 s, then seek back to 2 s: must equal a fresh seek to 2 s.
    auto sim = play(0.0, 6.0, 60);
    lens_rain::Inputs back = scene_at(2.0);
    back.seeking = true;
    sim.advance(shot(), back);
    lens_rain::Simulator fresh;
    fresh.advance(shot(), back);
    expect(sim.state_hash() == fresh.state_hash(), "seek reproduces the fresh state");

    // Backward time without a seek flag also resets to the same state.
    auto rewound = play(0.0, 6.0, 60);
    lens_rain::Inputs earlier = scene_at(2.0);
    const auto before = rewound.resets();
    rewound.advance(shot(), earlier);
    expect(rewound.resets() == before + 1, "backward time resets");
    expect(rewound.state_hash() == fresh.state_hash(), "backward time matches a seek");

    // A jump beyond the limit resets too.
    auto jumped = play(0.0, 1.0, 60);
    const auto jumps = jumped.resets();
    jumped.advance(shot(), scene_at(1.0 + lens_rain::kMaxJumpSeconds + 0.5));
    expect(jumped.resets() == jumps + 1, "a large jump resets");

    // A new seed resets.
    auto reseeded = play(0.0, 1.0, 60);
    const auto seeds = reseeded.resets();
    lens_rain::Settings other = shot();
    other.seed ^= 1u;
    reseeded.advance(other, scene_at(1.0 + 1.0 / 60.0));
    expect(reseeded.resets() == seeds + 1, "a new shot seed resets");
}

void pause_is_a_no_op() {
    auto sim = play(3.0, 5.0, 60);
    const auto hash = sim.state_hash();
    const auto steps = sim.steps_run();
    for (int i = 0; i < 20; ++i)
        sim.advance(shot(), scene_at(5.0));
    expect(sim.state_hash() == hash, "pausing keeps the lens unchanged");
    expect(sim.steps_run() == steps, "pausing runs no steps");
    // Camera moving while paused (frozen replay clock) changes nothing either.
    lens_rain::Inputs moved = scene_at(5.0);
    moved.position.x += 500.0f;
    sim.advance(shot(), moved);
    expect(sim.state_hash() == hash, "camera moves on a frozen clock leave the lens alone");
}

void cover_stops_new_drops() {
    auto sim = play(0.0, 3.0, 60);
    expect(!sim.drops().empty(), "drops exist before going under cover");
    // The first frame under cover blends cover in from 0, so a few drops may
    // still land during it. Count from the first fully covered frame.
    lens_rain::Inputs entering = scene_at(3.0 + 1.0 / 60.0);
    entering.cover = 1.0f;
    sim.advance(shot(), entering);
    const std::uint32_t last_id = sim.next_id();
    for (int i = 2; i <= 120; ++i) { // two seconds under cover
        lens_rain::Inputs in = scene_at(3.0 + i / 60.0);
        in.cover = 1.0f;
        sim.advance(shot(), in);
    }
    expect(sim.next_id() == last_id, "no new drops land under cover");
    expect(!sim.drops().empty(), "drops already on the lens persist under cover");
}

void rain_stop_drains() {
    auto sim = play(0.0, 2.0, 60);
    for (int i = 1; i <= 60 * 30; ++i) {
        lens_rain::Inputs in = scene_at(2.0 + i / 60.0);
        in.rain = 0.0f;
        sim.advance(shot(), in);
    }
    expect(sim.drops().empty(), "the lens drains after the rain stops");
}

void drops_stay_in_picture() {
    lens_rain::Settings s = shot();
    s.amount = 1.0f;
    s.size = 3.0f;
    lens_rain::Simulator sim;
    lens_rain::Inputs in = scene_at(0.0);
    in.aspect = 2.39f; // a letterboxed picture
    in.seeking = true;
    sim.advance(s, in);
    bool inside = true;
    for (int i = 1; i <= 600; ++i) {
        lens_rain::Inputs next = scene_at(i / 60.0);
        next.aspect = 2.39f;
        sim.advance(s, next);
        for (const auto& d : sim.drops())
            inside =
                inside && d.u >= -0.05f && d.u <= 2.39f + 0.05f && d.v >= -0.05f && d.v <= 1.05f;
        inside = inside && sim.drops().size() <= lens_rain::kMaxDrops &&
                 sim.beads().size() <= lens_rain::kMaxBeads;
    }
    expect(inside, "drops stay inside the picture and within capacity");
}

void bad_inputs_are_safe() {
    lens_rain::Simulator sim;
    lens_rain::Inputs in = scene_at(1.0);
    in.rain = NAN;
    in.wind = {INFINITY, 0.0f, 0.0f};
    in.forward = {0.0f, 0.0f, 0.0f};
    in.seeking = true;
    sim.advance(shot(), in);
    lens_rain::Inputs nan_time = scene_at(1.0);
    nan_time.replay_time = NAN;
    sim.advance(shot(), nan_time);
    expect(sim.initialized(), "non-finite inputs do not break the simulation");
}

void editor_block() {
    lens_rain::Wire wire{};
    expect(!lens_rain::decode(wire).has_value(), "an unpublished (zeroed) block is refused");
    std::memcpy(wire.magic, lens_rain::kWireMagic, sizeof(wire.magic));
    wire.abi = lens_rain::kWireAbi;
    wire.flags = lens_rain::kWireFlagEnabled;
    wire.start_tick = 4242;
    wire.amount = 0.5f;
    wire.size = 1.5f;
    wire.slide = 1.0f;
    wire.trails = 0.2f;
    wire.refraction = 0.75f;
    wire.softness = 0.5f;
    const auto good = lens_rain::decode(wire);
    expect(good && good->enabled && good->look.size == 1.5f, "a valid block decodes");
    lens_rain::Wire other = wire;
    other.start_tick = 4243;
    const auto next = lens_rain::decode(other);
    expect(good && next && good->look.seed != next->look.seed,
           "each shot start gets its own drops");

    lens_rain::Wire bad = wire;
    bad.size = 9.0f;
    expect(!lens_rain::decode(bad), "an out-of-range size is refused");
    bad = wire;
    bad.flags = 2;
    expect(!lens_rain::decode(bad), "unknown flags are refused");
    bad = wire;
    bad.softness = NAN;
    expect(!lens_rain::decode(bad), "a non-finite value is refused");
    bad = wire;
    bad.abi = 2;
    expect(!lens_rain::decode(bad), "another ABI is refused");

    expect(lens_rain::valid_panel_value(0, 1.0) && !lens_rain::valid_panel_value(0, 0.5),
           "the switch takes 0 or 1");
    expect(lens_rain::valid_panel_value(2, 3.0) && !lens_rain::valid_panel_value(2, 0.1),
           "drop size follows its range");
    expect(!lens_rain::valid_panel_value(7, 0.0), "no control past softness");
}

} // namespace

int main() {
    frame_rate_independence();
    warm_up_opens_wet();
    seek_reproduces_state();
    pause_is_a_no_op();
    cover_stops_new_drops();
    rain_stop_drains();
    drops_stay_in_picture();
    bad_inputs_are_safe();
    editor_block();
    if (failures) {
        std::printf("%d lens rain check(s) failed\n", failures);
        return 1;
    }
    std::printf("lens rain checks passed\n");
    return 0;
}
