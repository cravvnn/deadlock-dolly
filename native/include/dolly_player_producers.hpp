#pragma once
#include <array>
#include <cstddef>
#include <utility>
#include <cstdint>
#include <mutex>
#include <optional>

namespace dolly::player_capture {
struct ProducerEntry {
    std::uint64_t frame = 0, instance = 0, records = 0, event = 0;
    std::uint32_t owner = 0;
    std::uint32_t record[8]{};
};

// Producer callbacks run on several game workers. Readers receive a copy so
// another worker cannot change the owner or record after a successful lookup.
template <unsigned Capacity> class ProducerTable {
public:
    struct Snapshot {
        std::array<ProducerEntry, Capacity> entries{};
        unsigned count = 0;
    };
    void store(const ProducerEntry& entry) {
        std::lock_guard<std::mutex> lock(mutex_);
        for (unsigned i = 0; i < state_.count; ++i) {
            auto& prior = state_.entries[i];
            if (prior.frame == entry.frame && prior.instance == entry.instance &&
                prior.records == entry.records) {
                prior = entry;
                return;
            }
        }
        unsigned slot = state_.count;
        if (slot < Capacity)
            ++state_.count;
        else {
            slot = rotate_;
            rotate_ = (rotate_ + 1) % Capacity;
        }
        state_.entries[slot] = entry;
    }
    std::optional<ProducerEntry> lookup(std::uint64_t frame, std::uint64_t instance,
                                        std::uint64_t records) const {
        std::lock_guard<std::mutex> lock(mutex_);
        // A reused slot from another generation is not ownership proof.
        for (unsigned i = state_.count; i-- > 0;) {
            const auto& entry = state_.entries[i];
            if (entry.frame == frame && entry.instance == instance && entry.records == records)
                return entry; // owner=0 intentionally masks a replaced owner.
        }
        return std::nullopt;
    }
    Snapshot snapshot() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return state_;
    }

private:
    mutable std::mutex mutex_;
    Snapshot state_;
    unsigned rotate_ = 0;
};

inline bool record_matches(std::uint32_t actual_id, std::uint32_t expected_id,
                           const std::uint32_t* actual, const std::uint32_t* expected) {
    if (actual_id != expected_id)
        return false;
    for (unsigned i = 0; i < 8; ++i)
        if (actual[i] != expected[i])
            return false;
    return true;
}

// A draw consumes the last submitted upload on its context, not necessarily
// the generation that CPU workers are currently preparing. Lifetime pins the
// resource so pointer reuse cannot revive old ownership.
template <unsigned Capacity, class Lifetime = std::nullptr_t> class SubmittedOwners {
public:
    std::uint64_t invalidate() {
        std::lock_guard<std::mutex> lock(mutex_);
        count_ = 0;
        lifetime_ = Lifetime{};
        return ++revision_;
    }
    bool publish(std::uint64_t revision, std::uint64_t context, std::uint64_t resource,
                 const ProducerEntry* entries, unsigned count, Lifetime lifetime = {}) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (revision != revision_ || count > Capacity)
            return false;
        context_ = context;
        resource_ = resource;
        lifetime_ = std::move(lifetime);
        for (unsigned i = 0; i < count; ++i)
            entries_[i] = entries[i];
        count_ = count;
        return true;
    }
    std::optional<ProducerEntry> lookup(std::uint64_t context, std::uint64_t resource,
                                        std::uint64_t instance) const {
        std::lock_guard<std::mutex> lock(mutex_);
        if (context != context_ || resource != resource_)
            return std::nullopt;
        for (unsigned i = 0; i < count_; ++i)
            if (entries_[i].instance == instance)
                return entries_[i];
        return std::nullopt;
    }

private:
    mutable std::mutex mutex_;
    std::array<ProducerEntry, Capacity> entries_{};
    Lifetime lifetime_{};
    std::uint64_t context_ = 0, resource_ = 0, revision_ = 0;
    unsigned count_ = 0;
};
}
