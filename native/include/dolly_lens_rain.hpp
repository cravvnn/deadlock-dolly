#pragma once
// Lens rain: water drops on the camera lens.
//
// The simulation is engine-free and deterministic. It steps on a fixed
// replay-time quantum (never frame time), spawns drops from a hash of the
// shot seed and the absolute step index, and resets on a seek, on backward
// time or on a large jump. A reset simulates a short warm-up so a shot that
// opens in the rain starts with water already on the glass.
//
// Coordinates are in picture space: u runs 0..aspect, v runs 0..1 downwards,
// and every size is a fraction of the picture height. The picture is the
// image area without letterbox bars.
//
// Do not wrap this header in a namespace named `dolly`: it may be included
// inside another namespace (see AGENTS.md rule 1).

#include <cstddef>
#include <cstdint>
#include <vector>

namespace lens_rain {

constexpr double kStepSeconds = 1.0 / 120.0;
constexpr double kWarmupSeconds = 1.5;
constexpr double kMaxJumpSeconds = 2.0;
constexpr std::size_t kMaxDrops = 384;
constexpr std::size_t kMaxBeads = 1024;
// Runoff streaks are a channel nearly as wide as the drop that leaves them (radius ratio).
constexpr float kTrailWidth = 0.85f;

// Per-shot look controls. Ranges are clamped on use.
struct Settings {
    float amount = 0.35f;     // 0..1   how many drops land
    float size = 1.0f;        // 0.3..3 drop size scale
    float slide = 1.0f;       // 0..2   how readily and how fast water runs
    float trails = 0.35f;     // 0..1   length and persistence of runoff trails
    float refraction = 0.75f; // 0..2   how far water displaces the image
    float softness = 0.5f;    // 0..1   how much detail blurs under water
    std::uint32_t seed = 1;   // per-shot seed; a change resets the lens
};

struct Vec3 {
    float x = 0.0f, y = 0.0f, z = 0.0f;
};

// Everything the lens needs from the world for one presented frame. The
// caller fills it from the replay clock, the weather and the camera; the
// simulation never reads engine or weather state itself.
struct Inputs {
    double replay_time = 0.0; // seconds on the replay clock
    bool seeking = false;     // a seek is in progress or just finished
    float rain = 0.0f;        // 0 = no rain, 1 = heavy rain
    Vec3 wind;                // world-space wind; length 1 = strong wind
    float cover = 0.0f;       // 0 = open sky, 1 = camera fully under cover
    Vec3 position;            // camera position in game units
    Vec3 forward{1.0f, 0.0f, 0.0f};
    Vec3 right{0.0f, -1.0f, 0.0f};
    Vec3 up{0.0f, 0.0f, 1.0f};
    float aspect = 16.0f / 9.0f; // picture width / height
};

// Optional exact source of inputs at any replay time, for example the
// authored camera path evaluated at each simulation step. With one, the lens
// is identical at every frame rate. Without one, inputs are interpolated
// between presented frames and snapped to a coarse grid. Only rain, cover, wind
// and the camera's orientation (for gravity and wind direction on the glass) are
// used; camera turns and speed are not, so a re-render gives the same drops.
class InputSource {
public:
    virtual ~InputSource() = default;
    virtual Inputs at(double replay_time) const = 0;
};

struct Drop {
    float u = 0.0f, v = 0.0f;   // centre
    float vu = 0.0f, vv = 0.0f; // velocity, picture heights per second
    float radius = 0.0f;
    float water = 1.0f;         // 1 = fresh, thins to 0
    float travel = 0.0f;        // distance since the last trail segment
    float tu = 0.0f, tv = 0.0f; // where the current trail segment starts
    std::uint32_t id = 0;
    std::uint32_t sliding = 0;
};

// A piece of runoff trail: a thin streak of water from (u0, v0) to (u, v).
struct Bead {
    float u = 0.0f, v = 0.0f, radius = 0.0f;
    float life = 0.0f, max_life = 1.0f;
    float u0 = 0.0f, v0 = 0.0f;
};

class Simulator {
public:
    // Brings the lens to `inputs.replay_time`. Same time twice is a no-op.
    // `exact`, when given, supplies the inputs at every step time.
    void advance(const Settings& settings, const Inputs& inputs,
                 const InputSource* exact = nullptr);
    void clear();

    const std::vector<Drop>& drops() const { return drops_; }
    const std::vector<Bead>& beads() const { return beads_; }
    std::int64_t step_index() const { return step_; }
    std::uint64_t resets() const { return resets_; }
    std::uint64_t steps_run() const { return steps_run_; }
    std::uint32_t next_id() const { return next_id_; }
    float aspect() const { return aspect_; }
    bool initialized() const { return initialized_; }

    // Hash of the complete simulation state (not of the counters above).
    std::uint64_t state_hash() const;

    static std::int64_t step_for(double replay_time);

private:
    struct Frame {
        float rain = 0.0f, cover = 0.0f, aspect = 1.0f;
        float gravity_u = 0.0f, gravity_v = 1.0f; // screen-space down
        float wind_u = 0.0f, wind_v = 0.0f;       // screen-space wind push
        float wind_in = 0.0f;                     // wind blowing into the lens
    };

    void reset(const Settings& settings, const Inputs& inputs);
    void step(const Settings& settings, const Frame& frame, std::int64_t index);
    void spawn(const Settings& settings, const Frame& frame, std::int64_t index);
    static Frame frame_from(const Inputs& current);
    Inputs interpolated(const Inputs& now, double at) const;

    std::vector<Drop> drops_;
    std::vector<Bead> beads_;
    Inputs last_{};
    double last_time_ = 0.0;
    std::int64_t step_ = 0;
    std::uint32_t seed_ = 0;
    std::uint32_t next_id_ = 1;
    std::uint64_t resets_ = 0;
    std::uint64_t steps_run_ = 0;
    float aspect_ = 16.0f / 9.0f;
    bool initialized_ = false;
};

} // namespace lens_rain
