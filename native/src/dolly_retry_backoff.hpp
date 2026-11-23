#pragma once

#include <algorithm>
#include <cstdint>

namespace dolly {
// Retry pacing for expensive work repeated on the game's Present thread. Each
// failure doubles the wait from `first` up to `limit`; success, or a new
// identity (another swapchain, a changed payload), lets the work run at once.
struct RetryBackoff {
    double first = 1, limit = 60;
    double delay = 0, next = 0;
    std::uint64_t identity = 0;

    bool waiting(double now) const noexcept { return now < next; }
    bool due(double now, std::uint64_t key) noexcept {
        if (key != identity) {
            identity = key;
            clear();
        }
        return !waiting(now);
    }
    void failed(double now) noexcept {
        delay = delay > 0 ? std::min(delay * 2, limit) : first;
        next = now + delay;
    }
    // Wait a fixed time without growing the backoff (a cheap, transient failure).
    void defer(double now, double seconds) noexcept { next = now + seconds; }
    void clear() noexcept { delay = next = 0; }
};
} // namespace dolly
