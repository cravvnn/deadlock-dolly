#pragma once
#include "dolly_depth_readback.hpp"
#include <memory>

namespace dolly::depth {
// Encoder-thread-only owner of a new depth directory. The paired ProRes
// depth.mov is written by the video writer through its own encoder pipe; this
// class records the float EXR precision master when requested, always writes
// one normalized half-resolution grayscale preview_<w>x<h>.raw stream for the
// caller to encode, and owns manifest.json plus cancellation cleanup. No
// existing output is overwritten. Destruction discards an unfinished
// sequence; finish preserves it.
class Sequence {
public:
    Sequence();
    ~Sequence();
    Sequence(const Sequence&) = delete;
    Sequence& operator=(const Sequence&) = delete;
    // Creates a new depth directory. write_exr adds the float EXR sequence
    // under exr/; write_mov records that the caller supplies depth.mov.
    bool begin(const wchar_t* directory, bool write_exr, bool write_mov) noexcept;
    bool write(const RawFrame& frame, std::uint64_t capture_pts_100ns) noexcept;
    bool finish(std::uint64_t encoded_color_frames) noexcept;
    void discard() noexcept;
    long error() const noexcept;
    std::uint64_t count() const noexcept;
    // Linear camera-axis values converted by the last successful write();
    // valid until the next write, discard or destruction.
    const float* linear_data() const noexcept;
    std::size_t linear_count() const noexcept;
    bool exr() const noexcept;
    bool mov() const noexcept;

private:
    struct Impl;
    std::unique_ptr<Impl> impl;
};
}
