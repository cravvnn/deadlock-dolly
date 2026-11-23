#pragma once

#include <windows.h>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <memory>
#include <utility>

namespace dolly {
// Shared-memory blocks keep magic at 0-7, a seqlock sequence at 8-11 written only
// through Interlocked calls, and the payload from 12. The writer makes the
// sequence odd, copies everything except the sequence, then makes it even.
template <class T>
void publish_seqlocked(unsigned char* out, const T& value) noexcept {
    static_assert(offsetof(T, sequence) == 8, "Seqlocked block layout");
    auto* sequence = reinterpret_cast<volatile LONG*>(out + 8);
    const LONG old = InterlockedCompareExchange(sequence, 0, 0);
    const LONG even = (old & 1) ? old + 1 : old;
    InterlockedExchange(sequence, even + 1);
    std::memcpy(out, &value, 8);
    std::memcpy(out + 12, reinterpret_cast<const unsigned char*>(&value) + 12, sizeof(T) - 12);
    MemoryBarrier();
    InterlockedExchange(sequence, even + 2);
}

// The matching reader for a block snapshotted into `slot`: an even sequence
// that the current snapshot does not already hold, a copy, and the same
// sequence again. The copy carries the observed sequence. A copy that also
// passes `valid` replaces the snapshot and is left in `value`; returns whether
// it was accepted (even if storing the new snapshot ran out of memory).
template <class T, class Valid>
bool accept_seqlocked(const unsigned char* source, std::shared_ptr<const T>& slot, Valid&& valid,
                      T& value) noexcept {
    static_assert(offsetof(T, sequence) == 8, "Seqlocked block layout");
    auto* sequence = reinterpret_cast<volatile LONG*>(const_cast<unsigned char*>(source) + 8);
    const LONG first = InterlockedCompareExchange(sequence, 0, 0);
    const auto previous = std::atomic_load(&slot);
    if ((first & 1) || (previous && previous->sequence == std::uint32_t(first)))
        return false;
    std::memcpy(&value, source, sizeof(T));
    MemoryBarrier();
    value.sequence = std::uint32_t(first);
    if (first != InterlockedCompareExchange(sequence, 0, 0) || !valid(static_cast<const T&>(value)))
        return false;
    try {
        std::atomic_store(&slot, std::make_shared<const T>(value));
    } catch (...) {
    }
    return true;
}
template <class T, class Valid>
bool accept_seqlocked(const unsigned char* source, std::shared_ptr<const T>& slot,
                      Valid&& valid) noexcept {
    T value{};
    return accept_seqlocked(source, slot, std::forward<Valid>(valid), value);
}
} // namespace dolly
