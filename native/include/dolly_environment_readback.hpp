#pragma once
#include "dolly_environment_source.hpp"
#include <wrl/client.h>
#include <vector>
#include <optional>

namespace dolly::environment {
enum class ReadbackState { Idle, Pending, Ready, Failed };
// Detached CPU payload. Numeric source identities are diagnostics, never COM
// references. A worker may own this after the render thread publishes it using
// external synchronization; it must not access HdrReadback itself.
struct HdrFrame {
    SourceObservation source{};
    std::array<float,2> exposure{};
    std::vector<unsigned char> pixels;
};
// Single render-thread owner. No files, automatic rearm, flush, wait or draw-state
// mutations. Not wired into live capture until source/ownership gates are ready.
class HdrReadback {
public:
    HdrReadback() = default;
    HdrReadback(const HdrReadback&) = delete;
    HdrReadback& operator=(const HdrReadback&) = delete;
    bool begin(ID3D11DeviceContext*, const ShaderContract*, std::size_t count) noexcept;
    ReadbackState poll(ID3D11DeviceContext*) noexcept;
    void cancel() noexcept;
    // Consume exactly once without copying a full frame or retaining GPU state.
    std::optional<HdrFrame> take() noexcept;
    ReadbackState state() const noexcept { return state_; }
    const std::vector<unsigned char>& pixels() const noexcept {
        static const std::vector<unsigned char> empty;
        return state_ == ReadbackState::Ready ? pixels_ : empty;
    }
    const SourceObservation& source() const noexcept { return source_; }
    std::array<float,2> exposure() const noexcept {
        return state_ == ReadbackState::Ready ? exposure_ : std::array<float,2>{};
    }
private:
    void release_gpu() noexcept;
    ReadbackState fail() noexcept;
    ReadbackState state_ = ReadbackState::Idle;
    SourceObservation source_{};
    Microsoft::WRL::ComPtr<ID3D11DeviceContext> context_;
    Microsoft::WRL::ComPtr<ID3D11Texture2D> staging_;
    Microsoft::WRL::ComPtr<ID3D11Query> done_;
    std::array<Microsoft::WRL::ComPtr<ID3D11Buffer>,2> constants_;
    std::array<float,2> exposure_{};
    std::vector<unsigned char> pixels_;
    unsigned polls_ = 0;
    std::uint64_t started_ = 0;
};
}
