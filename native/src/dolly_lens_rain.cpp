#include "dolly_lens_rain.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>

namespace lens_rain {
namespace {

// Base drop arrival rate at amount 1 and heavy rain, drops per second.
constexpr float kBaseRate = 70.0f;
// Interpolated inputs are snapped to this grid so the same moment produces
// the same simulation inputs whether frames arrive at 30 or 120 per second.
constexpr float kInputGrid = 256.0f;

std::uint64_t mix(std::uint64_t x) {
    x += 0x9e3779b97f4a7c15ull;
    x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ull;
    x = (x ^ (x >> 27)) * 0x94d049bb133111ebull;
    return x ^ (x >> 31);
}

std::uint64_t hash3(std::uint64_t a, std::uint64_t b, std::uint64_t c) {
    return mix(mix(mix(a) ^ b) ^ c);
}

// Uniform in [0, 1).
float unit(std::uint64_t h) {
    return static_cast<float>((h >> 40) & 0xffffffull) / 16777216.0f;
}

float snap(float value) {
    if (!std::isfinite(value))
        return 0.0f;
    return std::round(value * kInputGrid) / kInputGrid;
}

float clampf(float value, float lo, float hi) {
    if (!std::isfinite(value))
        return lo;
    return std::min(std::max(value, lo), hi);
}

float dot(const Vec3& a, const Vec3& b) {
    return a.x * b.x + a.y * b.y + a.z * b.z;
}

Vec3 lerp(const Vec3& a, const Vec3& b, float f) {
    return {a.x + (b.x - a.x) * f, a.y + (b.y - a.y) * f, a.z + (b.z - a.z) * f};
}

Vec3 normalized(const Vec3& v, const Vec3& fallback) {
    const float length = std::sqrt(dot(v, v));
    if (!(length > 1e-6f) || !std::isfinite(length))
        return fallback;
    return {v.x / length, v.y / length, v.z / length};
}

Settings clamped(const Settings& s) {
    Settings out = s;
    out.amount = clampf(s.amount, 0.0f, 1.0f);
    out.size = clampf(s.size, 0.3f, 3.0f);
    out.slide = clampf(s.slide, 0.0f, 2.0f);
    out.trails = clampf(s.trails, 0.0f, 1.0f);
    out.refraction = clampf(s.refraction, 0.0f, 2.0f);
    out.softness = clampf(s.softness, 0.0f, 1.0f);
    return out;
}

void hash_bytes(std::uint64_t& h, const void* data, std::size_t size) {
    const auto* bytes = static_cast<const unsigned char*>(data);
    for (std::size_t i = 0; i < size; ++i) {
        h ^= bytes[i];
        h *= 0x100000001b3ull;
    }
}

template <typename T> void hash_value(std::uint64_t& h, const T& value) {
    hash_bytes(h, &value, sizeof(value));
}

// A drop starts to run once it outgrows this radius.
float slide_radius(const Settings& s) {
    return 0.0105f * s.size;
}

} // namespace

std::int64_t Simulator::step_for(double replay_time) {
    return static_cast<std::int64_t>(std::floor(replay_time / kStepSeconds + 1e-6));
}

void Simulator::clear() {
    drops_.clear();
    beads_.clear();
    initialized_ = false;
    step_ = 0;
    next_id_ = 1;
}

Simulator::Frame Simulator::frame_from(const Inputs& current) {
    Frame frame;
    frame.rain = snap(clampf(current.rain, 0.0f, 1.0f));
    frame.cover = snap(clampf(current.cover, 0.0f, 1.0f));
    frame.aspect = std::round(clampf(current.aspect, 0.2f, 8.0f) * 10000.0f) / 10000.0f;

    const Vec3 forward = normalized(current.forward, {1.0f, 0.0f, 0.0f});
    const Vec3 right = normalized(current.right, {0.0f, -1.0f, 0.0f});
    const Vec3 up = normalized(current.up, {0.0f, 0.0f, 1.0f});

    // World down projected onto the lens: gravity follows the camera roll and
    // weakens when the camera looks straight up or down.
    const Vec3 down{0.0f, 0.0f, -1.0f};
    frame.gravity_u = snap(dot(down, right));
    frame.gravity_v = snap(-dot(down, up));

    const Vec3& wind = current.wind;
    frame.wind_u = snap(clampf(dot(wind, right), -2.0f, 2.0f));
    frame.wind_v = snap(clampf(-dot(wind, up), -2.0f, 2.0f));
    frame.wind_in = snap(clampf(-dot(wind, forward), 0.0f, 2.0f));
    // Camera turns and forward speed are deliberately not inputs: they would come from
    // per-frame samples, and a re-render must give the same drops.
    return frame;
}

Inputs Simulator::interpolated(const Inputs& now, double at) const {
    const double dt = now.replay_time - last_time_;
    float f = 1.0f;
    if (dt > 1e-9)
        f = static_cast<float>(std::min(std::max((at - last_time_) / dt, 0.0), 1.0));
    Inputs in = now;
    in.replay_time = at;
    in.rain = last_.rain + (now.rain - last_.rain) * f;
    in.cover = last_.cover + (now.cover - last_.cover) * f;
    in.wind = lerp(last_.wind, now.wind, f);
    in.position = lerp(last_.position, now.position, f);
    in.forward = lerp(last_.forward, now.forward, f);
    in.right = lerp(last_.right, now.right, f);
    in.up = lerp(last_.up, now.up, f);
    return in;
}

void Simulator::reset(const Settings& settings, const Inputs& inputs) {
    drops_.clear();
    beads_.clear();
    next_id_ = 1;
    seed_ = settings.seed;
    ++resets_;
    initialized_ = true;

    // Warm up with the inputs of this moment held still, so the lens opens
    // already wet and the result only depends on the reset time.
    const std::int64_t target = step_for(inputs.replay_time);
    const std::int64_t first = step_for(inputs.replay_time - kWarmupSeconds);
    const Frame frame = frame_from(inputs);
    aspect_ = frame.aspect;
    for (std::int64_t index = first + 1; index <= target; ++index)
        step(settings, frame, index);
    step_ = target;
    last_ = inputs;
    last_time_ = inputs.replay_time;
}

void Simulator::advance(const Settings& raw, const Inputs& inputs, const InputSource* exact) {
    const Settings settings = clamped(raw);
    const double now = inputs.replay_time;
    if (!std::isfinite(now))
        return;
    const bool reset_needed = !initialized_ || inputs.seeking || settings.seed != seed_ ||
                              now < last_time_ - 1e-9 || now - last_time_ > kMaxJumpSeconds;
    if (reset_needed) {
        reset(settings, inputs);
        return;
    }
    const std::int64_t target = step_for(now);
    const double dt = now - last_time_;
    for (std::int64_t index = step_ + 1; index <= target; ++index) {
        const double at = static_cast<double>(index) * kStepSeconds;
        const Inputs current = exact ? exact->at(at) : interpolated(inputs, at);
        const Frame frame = frame_from(current);
        aspect_ = frame.aspect;
        step(settings, frame, index);
    }
    if (target > step_)
        step_ = target;
    if (dt > 0.0) {
        last_ = inputs;
        last_time_ = now;
    }
}

void Simulator::spawn(const Settings& s, const Frame& frame, std::int64_t index) {
    const float impact = 1.0f + 0.6f * frame.wind_in;
    const float rate = s.amount * frame.rain * (1.0f - frame.cover) * kBaseRate * impact;
    const float expected = rate * static_cast<float>(kStepSeconds);
    if (!(expected > 0.0f))
        return;
    const auto key = static_cast<std::uint64_t>(index);
    int count = static_cast<int>(expected);
    if (unit(hash3(s.seed, key, 0)) < expected - static_cast<float>(count))
        ++count;
    for (int k = 0; k < count; ++k) {
        const std::uint64_t h = hash3(s.seed, key, static_cast<std::uint64_t>(k) + 1);
        Drop drop;
        drop.u = unit(mix(h ^ 1)) * frame.aspect;
        drop.v = unit(mix(h ^ 2));
        const float grain = unit(mix(h ^ 3));
        drop.radius = s.size * (0.0045f + 0.0170f * grain * grain * grain);
        drop.water = 1.0f;
        drop.tu = drop.u;
        drop.tv = drop.v;

        // A drop landing on another one joins it, keeping the volume.
        bool merged = false;
        for (Drop& other : drops_) {
            const float du = other.u - drop.u, dv = other.v - drop.v;
            const float reach = (other.radius + drop.radius) * 0.8f;
            if (du * du + dv * dv < reach * reach) {
                const float a = other.radius * other.radius * other.radius;
                const float b = drop.radius * drop.radius * drop.radius;
                const float total = a + b;
                other.u = (other.u * a + drop.u * b) / total;
                other.v = (other.v * a + drop.v * b) / total;
                other.radius = std::cbrt(total);
                other.water = 1.0f;
                merged = true;
                break;
            }
        }
        if (merged || drops_.size() >= kMaxDrops)
            continue;
        drop.id = next_id_++;
        drops_.push_back(drop);
    }
}

void Simulator::step(const Settings& s, const Frame& frame, std::int64_t index) {
    ++steps_run_;
    const float dt = static_cast<float>(kStepSeconds);
    spawn(s, frame, index);

    // Water thins on the glass; it drains faster once the rain stops.
    const float drop_life = frame.rain > 0.0f ? 22.0f : 5.0f;
    const float threshold = slide_radius(s);
    const float g_len =
        std::sqrt(frame.gravity_u * frame.gravity_u + frame.gravity_v * frame.gravity_v);
    const float bead_life = 0.6f + 5.0f * s.trails;

    for (Drop& drop : drops_) {
        drop.water -= dt / drop_life;
        const bool was_sliding = drop.sliding != 0;
        drop.sliding = 0;
        if (s.slide <= 0.0f || drop.radius <= threshold || g_len < 0.05f) {
            drop.vu = drop.vv = 0.0f;
            continue;
        }
        drop.sliding = 1;
        if (!was_sliding) {
            // Just started running: its trail starts here.
            drop.tu = drop.u;
            drop.tv = drop.v;
            drop.travel = 0.0f;
        }
        // Heavier drops run faster. The sideways wander changes every eighth
        // of a second.
        const float weight = std::min((drop.radius - threshold) / threshold, 3.0f);
        const std::uint64_t wiggle_key = static_cast<std::uint64_t>(index / 15);
        const float wander =
            (unit(hash3(s.seed ^ 0x5a17u, drop.id, wiggle_key)) * 2.0f - 1.0f) * 0.35f;
        const float side_u = -frame.gravity_v, side_v = frame.gravity_u;
        const float accel = 0.9f * s.slide * weight;
        drop.vu += (frame.gravity_u + 0.4f * frame.wind_u + wander * side_u) * accel * dt;
        drop.vv += (frame.gravity_v + 0.4f * frame.wind_v + wander * side_v) * accel * dt;
        const float friction = 1.0f - 4.0f * dt;
        drop.vu *= friction;
        drop.vv *= friction;
        const float speed = std::sqrt(drop.vu * drop.vu + drop.vv * drop.vv);
        const float max_speed = 0.8f * s.slide;
        if (speed > max_speed) {
            drop.vu *= max_speed / speed;
            drop.vv *= max_speed / speed;
        }
        const float old_u = drop.u, old_v = drop.v;
        drop.u += drop.vu * dt;
        drop.v += drop.vv * dt;
        drop.travel += speed * dt;

        // Running water leaves a continuous streak behind it and loses volume. Each
        // segment starts where the previous one ended, so the trail has no gaps.
        if (s.trails > 0.0f && drop.travel > drop.radius * 1.2f) {
            drop.travel = 0.0f;
            if (beads_.size() < kMaxBeads) {
                Bead bead;
                bead.u0 = drop.tu;
                bead.v0 = drop.tv;
                bead.u = old_u;
                bead.v = old_v;
                bead.radius = drop.radius * (0.3f + 0.25f * s.trails);
                bead.life = bead.max_life = bead_life;
                beads_.push_back(bead);
                const float du = bead.u - bead.u0, dv = bead.v - bead.v0;
                const float length = std::sqrt(du * du + dv * dv);
                const float lost = bead.radius * bead.radius * length * 0.12f;
                const float volume = drop.radius * drop.radius * drop.radius - lost;
                drop.radius = volume > 0.0f ? std::cbrt(volume) : 0.0f;
            }
            drop.tu = old_u;
            drop.tv = old_v;
        }
    }

    // A running drop sweeps up the water it touches.
    for (std::size_t i = 0; i < drops_.size(); ++i) {
        Drop& runner = drops_[i];
        if (!runner.sliding || runner.radius <= 0.0f)
            continue;
        for (std::size_t j = 0; j < drops_.size(); ++j) {
            Drop& other = drops_[j];
            if (i == j || other.radius <= 0.0f || other.radius > runner.radius)
                continue;
            const float du = other.u - runner.u, dv = other.v - runner.v;
            const float reach = (other.radius + runner.radius) * 0.75f;
            if (du * du + dv * dv >= reach * reach)
                continue;
            const float a = runner.radius * runner.radius * runner.radius;
            const float b = other.radius * other.radius * other.radius;
            runner.radius = std::cbrt(a + b);
            runner.water = std::max(runner.water, other.water);
            other.radius = 0.0f;
        }
    }

    const float aspect = frame.aspect;
    drops_.erase(std::remove_if(drops_.begin(), drops_.end(),
                                [aspect](const Drop& d) {
                                    return d.water <= 0.0f || d.radius < 0.0015f || d.v > 1.05f ||
                                           d.v < -0.05f || d.u < -0.05f || d.u > aspect + 0.05f;
                                }),
                 drops_.end());
    for (Bead& bead : beads_)
        bead.life -= dt;
    beads_.erase(
        std::remove_if(beads_.begin(), beads_.end(), [](const Bead& b) { return b.life <= 0.0f; }),
        beads_.end());
}

std::uint64_t Simulator::state_hash() const {
    std::uint64_t h = 0xcbf29ce484222325ull;
    hash_value(h, step_);
    hash_value(h, next_id_);
    hash_value(h, aspect_);
    const std::uint64_t counts[2] = {drops_.size(), beads_.size()};
    hash_value(h, counts);
    for (const Drop& d : drops_) {
        const float f[9] = {d.u, d.v, d.vu, d.vv, d.radius, d.water, d.travel, d.tu, d.tv};
        hash_value(h, f);
        hash_value(h, d.id);
        hash_value(h, d.sliding);
    }
    for (const Bead& b : beads_) {
        const float f[7] = {b.u, b.v, b.radius, b.life, b.max_life, b.u0, b.v0};
        hash_value(h, f);
    }
    return h;
}

} // namespace lens_rain
