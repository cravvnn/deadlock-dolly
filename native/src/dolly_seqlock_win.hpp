#pragma once

#include <windows.h>
#include <cstddef>
#include <cstring>

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
} // namespace dolly
