#pragma once
#include "dolly_depth_readback.hpp"
#include <memory>

namespace dolly::depth {
// Encoder-thread-only owner of a new <video path>.depth directory. Writes
// complete FLOAT EXRs in color-frame order. No existing output is overwritten.
// Destruction discards an unfinished sequence; finish preserves it.
class Sequence {
public:
    Sequence();
    ~Sequence();
    Sequence(const Sequence&) = delete;
    Sequence& operator=(const Sequence&) = delete;
    bool begin(const wchar_t* video_path) noexcept;
    bool write(const RawFrame& frame, std::uint64_t capture_pts_100ns) noexcept;
    bool finish(std::uint64_t encoded_color_frames) noexcept;
    void discard() noexcept;
    long error() const noexcept;
    std::uint64_t count() const noexcept;

private:
    struct Impl;
    std::unique_ptr<Impl> impl;
};
}
