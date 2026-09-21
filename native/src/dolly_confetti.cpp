#include "dolly_confetti.hpp"

#include <Windows.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <mutex>

namespace dolly::confetti {
namespace {

using Vec3 = Camera::Vec3;
using Angles = Camera::Angles;
constexpr float kPi = 3.14159265358979323846f;
// A real rewind is seconds; the published tick+fraction clock can wobble by
// about one replay tick (worst case a few tens of milliseconds). Keep the
// reset guard well above the wobble and well below a genuine seek back.
constexpr double kRewindTolerance = 0.5;
// Camera-following coverage and its density budget. The asset maps CP2 into a
// forward-biased local-space box (min = {2%, -100%, 5%}, max = {100%, +100%,
// 100%}), so depth also scales the FOV-limited lateral extent. Close coverage
// doubles both horizontal axes and the emission, keeping the same particle
// density: 120/s x 8 s = 960 live per effect, below the 1024-per-effect cap,
// 5,760 particles across the six colours. The above-250 regime already
// saturates its cap (~85/s effective with a 12 s lifetime), so its emission
// stays at 600/s total and the coverage grows 1.5x instead of 2x.
constexpr float kCloseDepth = 720.0f, kCloseSpread = 600.0f, kCloseEmission = 720.0f;
constexpr float kFarDepth = 1200.0f, kFarSpread = 900.0f, kFarEmission = 600.0f;
constexpr std::array<const char*, 6> kPersistentEffects{
    "particles/deadlock_cine/confetti_red.vpcf",    "particles/deadlock_cine/confetti_orange.vpcf",
    "particles/deadlock_cine/confetti_yellow.vpcf", "particles/deadlock_cine/confetti_green.vpcf",
    "particles/deadlock_cine/confetti_blue.vpcf",   "particles/deadlock_cine/confetti_purple.vpcf",
};
constexpr std::array<const char*, 6> kContactEffects{
    "particles/deadlock_cine/confetti_red_contact.vpcf",
    "particles/deadlock_cine/confetti_orange_contact.vpcf",
    "particles/deadlock_cine/confetti_yellow_contact.vpcf",
    "particles/deadlock_cine/confetti_green_contact.vpcf",
    "particles/deadlock_cine/confetti_blue_contact.vpcf",
    "particles/deadlock_cine/confetti_purple_contact.vpcf",
};

struct BuildOffsets {
    std::uintptr_t manager, create, control, transform, destroy, release;
    std::array<unsigned char, 8> manager_bytes;
    std::array<unsigned char, 16> release_bytes;
};
constexpr std::array<BuildOffsets, 4> kBuilds{{
    {0x1491620,
     0xb047f0,
     0xb3a4b0,
     0xb3a650,
     0xb054b0,
     0xb2baf0,
     {0x48, 0x8b, 0x05, 0xe9, 0x6b, 0xa5, 0x01, 0xc3},
     {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x23, 0x5b, 0x96, 0x00, 0x48, 0x85,
      0xc0}},
    {0x148cec0,
     0xb04830,
     0xb3a4f0,
     0xb3a690,
     0xb054f0,
     0xb2bb30,
     {0x48, 0x8b, 0x05, 0x59, 0x62, 0xa5, 0x01, 0xc3},
     {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x23, 0x5b, 0x96, 0x00, 0x48, 0x85,
      0xc0}},
    {0x1494580,
     0xb07340,
     0xb3d000,
     0xb3d1a0,
     0xb08000,
     0xb2e640,
     {0x48, 0x8b, 0x05, 0x09, 0x90, 0xa5, 0x01, 0xc3},
     {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0x33, 0x5f, 0x96, 0x00, 0x48, 0x85,
      0xc0}},
    {0x14945c0,
     0xb07400,
     0xb3d0c0,
     0xb3d260,
     0xb080c0,
     0xb2e700,
     {0x48, 0x8b, 0x05, 0x19, 0x8f, 0xa5, 0x01, 0xc3},
     {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xda, 0xe8, 0xb3, 0x5e, 0x96, 0x00, 0x48, 0x85,
      0xc0}},
}};
constexpr std::array<unsigned char, 16> kCreateBytes{
    0x40, 0x57, 0x41, 0x56, 0x41, 0x57, 0x48, 0x81, 0xec, 0x90, 0x00, 0x00, 0x00, 0x45, 0x8b, 0xf1};
constexpr std::array<unsigned char, 16> kControlBytes{
    0x48, 0x89, 0x5c, 0x24, 0x08, 0x48, 0x89, 0x74, 0x24, 0x10, 0x57, 0x48, 0x83, 0xec, 0x30, 0x49};
constexpr std::array<unsigned char, 16> kTransformBytes{
    0x48, 0x89, 0x5c, 0x24, 0x08, 0x48, 0x89, 0x74, 0x24, 0x10, 0x57, 0x48, 0x83, 0xec, 0x50, 0x49};
constexpr std::array<unsigned char, 16> kDestroyBytes{
    0x48, 0x89, 0x5c, 0x24, 0x08, 0x57, 0x48, 0x83, 0xec, 0x20, 0x41, 0x0f, 0xb6, 0xf8, 0x8b, 0xda};

enum class State {
    unavailable,
    ready,
    configured,
    running,
    waiting,
    seeking,
    manager_unavailable,
    create_failed
};
// Reason a live effect was released; published in diagnostics order.
enum class StopReason {
    disabled,
    reconfigured,
    seek,
    backward,
    invalid_height,
    shutdown,
    create_failed
};
struct StopCounts {
    std::uint32_t disabled{}, reconfigured{}, seek{}, backward{}, invalid_height{}, shutdown{},
        create_failed{};
};
struct Counts {
    StopCounts stops;
    std::uint32_t starts{}, start_failures{}, frames{}, running_frames{}, resets{},
        state_changes{};
    double max_backward_delta{};
};
std::mutex mutex;
std::uintptr_t client{}, manager_offset{}, create_offset{}, control_offset{};
std::uintptr_t transform_offset{}, destroy_offset{}, release_offset{};
std::array<int, 6> particles{-1, -1, -1, -1, -1, -1};
bool active{}, despawn{}, have_time{};
float height{250.0f};
double last_time{};
State state{State::unavailable};
Counts counts;

std::uint32_t& stopCount(StopReason reason) {
    switch (reason) {
    case StopReason::disabled:
        return counts.stops.disabled;
    case StopReason::reconfigured:
        return counts.stops.reconfigured;
    case StopReason::seek:
        return counts.stops.seek;
    case StopReason::backward:
        return counts.stops.backward;
    case StopReason::invalid_height:
        return counts.stops.invalid_height;
    case StopReason::shutdown:
        return counts.stops.shutdown;
    case StopReason::create_failed:
        return counts.stops.create_failed;
    }
    return counts.stops.disabled;
}
void setStateLocked(State next) {
    if (state == next)
        return;
    state = next;
    ++counts.state_changes;
}

template <std::size_t N>
bool matches(std::uintptr_t address, const std::array<unsigned char, N>& expected) {
    std::array<unsigned char, N> actual{};
    SIZE_T read{};
    return ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<const void*>(address),
                             actual.data(), N, &read) &&
           read == N && actual == expected;
}
bool matches(const BuildOffsets& build) {
    return matches(client + build.manager, build.manager_bytes) &&
           matches(client + build.create, kCreateBytes) &&
           matches(client + build.control, kControlBytes) &&
           matches(client + build.transform, kTransformBytes) &&
           matches(client + build.destroy, kDestroyBytes) &&
           matches(client + build.release, build.release_bytes);
}
void* manager() {
    using Fn = void*(__fastcall*)();
    return reinterpret_cast<Fn>(client + manager_offset)();
}
int create(const char* effect) {
    using Fn = int*(__fastcall*)(void*, int*, const char*, int, void*);
    int particle = -1;
    reinterpret_cast<Fn>(client + create_offset)(nullptr, &particle, effect, 8, nullptr);
    return particle;
}
void setControl(int particle, int control, const Vec3& value) {
    using Fn = void(__fastcall*)(void*, int, int, const Vec3*);
    reinterpret_cast<Fn>(client + control_offset)(nullptr, particle, control, &value);
}
void setTransform(int particle, const Vec3& origin, const Angles& angles) {
    using Fn = void(__fastcall*)(void*, int, int, const Vec3*, const Angles*);
    reinterpret_cast<Fn>(client + transform_offset)(nullptr, particle, 0, &origin, &angles);
}
bool stopLocked(StopReason reason) {
    using Destroy = void(__fastcall*)(void*, int, bool);
    using Release = void(__fastcall*)(void*, int);
    bool released = false;
    for (int& particle : particles) {
        if (particle < 0)
            continue;
        reinterpret_cast<Destroy>(client + destroy_offset)(nullptr, particle, true);
        reinterpret_cast<Release>(client + release_offset)(nullptr, particle);
        particle = -1;
        released = true;
    }
    if (released) {
        ++counts.resets;
        ++stopCount(reason);
    }
    return released;
}
float seededUnit(std::uint32_t& value) {
    value = value * 1664525u + 1013904223u;
    return static_cast<float>(value >> 8) / 16777216.0f;
}
void updateVolumeLocked(const Camera& camera) {
    const bool close = height <= 250.0f;
    const float duration = close ? 8.0f : 12.0f;
    const float depth = close ? kCloseDepth : kFarDepth;
    const float spread = close ? kCloseSpread : kFarSpread;
    constexpr float overscan = 64.0f;
    const float half_fov = std::clamp(camera.fov, 1.0f, 120.0f) * kPi / 360.0f;
    const Vec3 extent{depth + overscan, std::max(spread, depth * std::tan(half_fov)) + overscan,
                      height};
    const Angles orientation{0.0f, camera.angles.yaw, 0.0f};
    std::uint32_t random = 42u ^ 0x9e3779b9u;
    const float forward_phase = (seededUnit(random) - 0.5f) * overscan;
    const float right_phase = (seededUnit(random) - 0.5f) * overscan;
    const float yaw = camera.angles.yaw * kPi / 180.0f;
    const Vec3 forward{std::cos(yaw), std::sin(yaw), 0.0f};
    const Vec3 right{-std::sin(yaw), std::cos(yaw), 0.0f};
    Vec3 origin{camera.origin.x + forward.x * forward_phase + right.x * right_phase,
                camera.origin.y + forward.y * forward_phase + right.y * right_phase,
                camera.origin.z};
    if (close)
        origin.z -= height * 0.4f;
    for (int particle : particles) {
        if (particle < 0)
            continue;
        setTransform(particle, origin, orientation);
        setControl(particle, 2, extent);
        setControl(particle, 3, {duration, 0.0f, 0.0f});
    }
}
bool startLocked() {
    const auto& effects = despawn ? kContactEffects : kPersistentEffects;
    const bool close = height <= 250.0f;
    const Vec3 parameters{0.0f, (close ? kCloseEmission : kFarEmission) / effects.size(),
                          close ? 7.0f : 5.0f};
    for (std::size_t index = 0; index < effects.size(); ++index) {
        particles[index] = create(effects[index]);
        if (particles[index] < 0) {
            stopLocked(StopReason::create_failed);
            ++counts.start_failures;
            return false;
        }
        setControl(particles[index], 1, parameters);
    }
    ++counts.starts;
    return true;
}
const char* stateText(State value) {
    switch (value) {
    case State::ready:
        return "native Source 2 confetti ready";
    case State::configured:
        return "native Source 2 confetti configured";
    case State::running:
        return "native Source 2 confetti active with scene occlusion";
    case State::waiting:
        return "native Source 2 confetti waiting for replay view";
    case State::seeking:
        return "native Source 2 confetti reset for replay seek";
    case State::manager_unavailable:
        return "Source 2 particle manager unavailable";
    case State::create_failed:
        return "Source 2 rejected the packaged confetti effect";
    default:
        return "native Source 2 confetti unavailable for this game build";
    }
}
} // namespace

bool initialize(std::uintptr_t client_base) noexcept {
    std::scoped_lock lock(mutex);
    client = client_base;
    for (const auto& build : kBuilds) {
        if (!client || !matches(build))
            continue;
        manager_offset = build.manager;
        create_offset = build.create;
        control_offset = build.control;
        transform_offset = build.transform;
        destroy_offset = build.destroy;
        release_offset = build.release;
        setStateLocked(State::ready);
        return true;
    }
    client = 0;
    setStateLocked(State::unavailable);
    return false;
}
void disable() noexcept {
    std::scoped_lock lock(mutex);
    if (client)
        stopLocked(StopReason::shutdown);
    active = have_time = false;
    setStateLocked(client ? State::ready : State::unavailable);
}
void on_frame(double replay_time, bool replay_active, bool seeking, const Camera* camera,
              bool requested, float requested_height, bool requested_despawn) noexcept {
    std::scoped_lock lock(mutex);
    ++counts.frames;
    if (!client) {
        setStateLocked(State::unavailable);
        return;
    }
    if (!requested) {
        if (active)
            stopLocked(StopReason::disabled);
        active = have_time = false;
        setStateLocked(State::ready);
        return;
    }
    if (!std::isfinite(requested_height) || requested_height < 100.0f ||
        requested_height > 1500.0f) {
        stopLocked(StopReason::invalid_height);
        active = have_time = false;
        setStateLocked(State::unavailable);
        return;
    }
    if (!active || height != requested_height || despawn != requested_despawn) {
        stopLocked(StopReason::reconfigured);
        active = true;
        height = requested_height;
        despawn = requested_despawn;
        have_time = false;
        setStateLocked(State::configured);
    }
    const bool valid_time = std::isfinite(replay_time) && replay_time >= 0.0;
    if (have_time && valid_time) {
        // The published replay clock is rebuilt from the current tick and the
        // renderer's interpolation fraction, so a fraction wobble can step the
        // value back by up to a tick. Only a real rewind may reset the effects;
        // every observed backward delta is kept for diagnostics.
        const double backward = last_time - replay_time;
        if (backward > counts.max_backward_delta)
            counts.max_backward_delta = backward;
        if (backward > kRewindTolerance)
            stopLocked(StopReason::backward);
    }
    if (valid_time) {
        last_time = replay_time;
        have_time = true;
    }
    if (seeking) {
        stopLocked(StopReason::seek);
        setStateLocked(State::seeking);
        return;
    }
    if (!valid_time || !replay_active || !camera) {
        setStateLocked(State::waiting);
        return;
    }
    if (!manager()) {
        setStateLocked(State::manager_unavailable);
        return;
    }
    if (particles[0] < 0 && !startLocked()) {
        setStateLocked(State::create_failed);
        return;
    }
    updateVolumeLocked(*camera);
    ++counts.running_frames;
    setStateLocked(State::running);
}
const char* status() noexcept {
    std::scoped_lock lock(mutex);
    return stateText(state);
}
Diagnostics diagnostics() noexcept {
    std::scoped_lock lock(mutex);
    Diagnostics result;
    result.state = static_cast<std::uint32_t>(state);
    result.handles = static_cast<std::uint32_t>(
        std::count_if(particles.begin(), particles.end(), [](int particle) { return particle >= 0; }));
    result.starts = counts.starts;
    result.start_failures = counts.start_failures;
    result.frames = counts.frames;
    result.running_frames = counts.running_frames;
    result.resets = counts.resets;
    result.stop_disabled = counts.stops.disabled;
    result.stop_reconfigured = counts.stops.reconfigured;
    result.stop_seek = counts.stops.seek;
    result.stop_backward = counts.stops.backward;
    result.stop_invalid = counts.stops.invalid_height;
    result.stop_shutdown = counts.stops.shutdown;
    result.stop_create_failed = counts.stops.create_failed;
    result.state_changes = counts.state_changes;
    result.max_backward_delta = counts.max_backward_delta;
    return result;
}

} // namespace dolly::confetti
