#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <atomic>
#include <mutex>
#include <cstring>
#include <cstdio>
#include <iterator>
#include <array>
#include <cmath>
#include "dolly_environment_probe.hpp"
#include "dolly_environment_shaders_generated.hpp"

namespace dolly::environment {
namespace {
std::atomic<bool> enabled{false};
std::mutex mutex;
ProbeRequest request{};
unsigned last_command = 0, state = 0;
unsigned draws = 0, frames = 0, matches = 0, candidates = 0, ambiguous = 0, in_frame = 0;
std::atomic<unsigned> skipped{0};
ULONGLONG deadline = 0;
SourceObservation latest{};
std::array<unsigned, 6> identity_counts{};
void stop(unsigned reason) { enabled.store(false, std::memory_order_release); state = reason; }
bool expired() { return GetTickCount64() >= deadline; }
bool valid(const ProbeRequest& r, unsigned editor_pid, unsigned game_pid) {
    return !std::memcmp(r.magic, "DLYENVR1", 8) && r.abi == 1 && r.command &&
        r.enabled <= 1 && r.editor_pid == editor_pid && r.game_pid == game_pid && !r.reserved &&
        (!r.enabled || (r.frames >= 1 && r.frames <= 64 && r.draws >= 1 && r.draws <= 100000 &&
                        r.milliseconds >= 1 && r.milliseconds <= 10000));
}
void publish(unsigned char* mapping) {
    ProbeStatus out{};
    std::memcpy(out.magic, "DLYENVS1", 8);
    out.abi = 1; out.command = last_command; out.state = state;
    const auto& s = latest;
    const auto finite = [](float v) { return std::isfinite(v) ? v : 0.0f; };
    std::snprintf(out.json, sizeof(out.json),
        "{\"draws\":%u,\"frames\":%u,\"matches\":%u,\"candidates\":%u,\"ambiguous_frames\":%u,"
        "\"lock_skips\":%u,\"source_state\":%u,\"static_id\":%u,\"dynamic_id\":%u,"
        "\"context\":%llu,\"shader\":%llu,\"resource\":%llu,"
        "\"width\":%u,\"height\":%u,\"format\":%u,\"srv_format\":%u,\"mips\":%u,\"samples\":%u,"
        "\"viewport\":[%.9g,%.9g,%.9g,%.9g],\"view_cb\":[%llu,%u,%u,%u],\"post_cb\":[%llu,%u,%u,%u],"
        "\"shader_identity_counts\":[%u,%u,%u,%u,%u,%u]}",
        draws, frames, matches, candidates, ambiguous, skipped.load(), unsigned(s.state),
        s.static_id, s.dynamic_id, s.context, s.shader, s.resource,
        s.texture.Width, s.texture.Height, unsigned(s.texture.Format), unsigned(s.srv.Format),
        s.texture.MipLevels, s.texture.SampleDesc.Count,
        finite(s.viewport.TopLeftX),finite(s.viewport.TopLeftY),finite(s.viewport.Width),finite(s.viewport.Height),
        s.view.identity,s.view.bytes,s.view.first_constant,s.view.num_constants,
        s.post.identity,s.post.bytes,s.post.first_constant,s.post.num_constants,
        identity_counts[0],identity_counts[1],identity_counts[2],identity_counts[3],identity_counts[4],identity_counts[5]);
    auto* destination = mapping + ProbeStatusOffset;
    auto* sequence = reinterpret_cast<volatile LONG*>(destination + 8);
    LONG previous = InterlockedCompareExchange(sequence, 0, 0);
    LONG even = (previous & 1) ? previous + 1 : previous;
    InterlockedExchange(sequence, even + 1);
    std::memcpy(destination, &out, 8);
    std::memcpy(destination + 12, reinterpret_cast<unsigned char*>(&out) + 12, sizeof(out) - 12);
    InterlockedExchange(sequence, even + 2);
}
}
bool probe_active() noexcept { return enabled.load(std::memory_order_acquire); }
void probe_worker(unsigned char* mapping, bool owned, unsigned editor_pid, unsigned game_pid) noexcept {
    std::lock_guard<std::mutex> lock(mutex);
    if (!owned || !mapping) {
        if (probe_active()) stop(3);
    } else {
        ProbeRequest next{};
        auto* input = mapping + ProbeRequestOffset;
        auto* sequence = reinterpret_cast<volatile LONG*>(input + 8);
        LONG before = InterlockedCompareExchange(sequence, 0, 0);
        if (!(before & 1)) {
            std::memcpy(&next, input, sizeof(next));
            LONG after = InterlockedCompareExchange(sequence, 0, 0);
            if (before == after && next.sequence == unsigned(before) && next.command && next.command != last_command) {
                last_command = next.command;
                if (!valid(next, editor_pid, game_pid)) {
                    stop(4);
                } else if (!next.enabled) {
                    stop(3);
                } else if (probe_active()) {
                    // No extend/rearm while active. A new run requires a fresh
                    // explicit command after the previous bounded run finishes.
                    stop(4);
                } else {
                    request = next;
                    draws = frames = matches = candidates = ambiguous = in_frame = 0;
                    latest = {}; identity_counts = {}; skipped.store(0);
                    deadline = GetTickCount64() + next.milliseconds;
                    state = 1;
                    enabled.store(true, std::memory_order_release);
                }
            }
        }
        if (probe_active() && expired()) stop(2);
    }
    if (mapping) publish(mapping);
}
void probe_hooks(bool success) noexcept {
    if (success || !probe_active()) return;
    std::lock_guard<std::mutex> lock(mutex);
    if (probe_active()) stop(5);
}
void probe_present() noexcept {
    if (!probe_active()) return;
    std::lock_guard<std::mutex> lock(mutex);
    if (!probe_active()) return;
    in_frame = 0;
    if (++frames >= request.frames || expired()) stop(2);
}
void probe_draw(ID3D11DeviceContext* context) noexcept {
    if (!probe_active()) return;
    std::unique_lock<std::mutex> lock(mutex, std::try_to_lock);
    if (!lock.owns_lock()) { skipped.fetch_add(1); return; }
    if (!probe_active()) return;
    if (expired() || draws >= request.draws) { stop(2); return; }
    ++draws;
    const auto sample = inspect_source(context, ReviewedPostProcess, std::size(ReviewedPostProcess));
    if (sample.identity_state < identity_counts.size()) ++identity_counts[sample.identity_state];
    if (sample.state >= SourceState::DeferredContext) {
        ++matches;
        latest = sample;
        if (sample.state == SourceState::Candidate) {
            ++candidates;
            if (++in_frame == 2) ++ambiguous;
        }
    }
    if (draws >= request.draws) stop(2);
}
}
