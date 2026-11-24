#pragma once
// Builds this frame's camera for the weather passes on the GPU, from the game's
// per-view constant buffers captured with the scene depth. The view therefore
// matches the depth buffer of the same frame exactly, with no CPU readback lag.
//
// Candidate buffers use the per-view layout validated by depth::read_per_view_projection:
// view matrix (row-vector, row-major) at 128, projection (column-vector, row-major,
// TAA jitter at [0][2] and [1][2]) at 192, viewport origin/size at 320..335 and
// camera position at 416. The first candidate that passes the same structural
// checks for the expected viewport size wins.

#include <cstdint>
#include <memory>
#include <string>

struct ID3D11Buffer;
struct ID3D11Device;
struct ID3D11DeviceContext;
struct ID3D11ShaderResourceView;

namespace dolly::env {

class GpuViewBuilderDX11 {
public:
    static constexpr uint32_t kMaxCandidates = 16;

    GpuViewBuilderDX11();
    ~GpuViewBuilderDX11();
    GpuViewBuilderDX11(const GpuViewBuilderDX11&) = delete;
    GpuViewBuilderDX11& operator=(const GpuViewBuilderDX11&) = delete;

    bool initialize(ID3D11Device* device, std::string* out_error = nullptr);
    void shutdown();

    // Copies the candidates (each at least 464 bytes) and selects the view on the GPU.
    // Returns the StructuredBuffer SRV for EnvCamera::gpu_view, or null on bad input.
    // A frame with no valid candidate still returns the SRV; its view is marked
    // invalid and the weather passes draw nothing for that frame.
    ID3D11ShaderResourceView* build(ID3D11DeviceContext* ctx, ID3D11Buffer* const* candidates,
                                    const uint32_t* sizes, uint32_t count, uint32_t width,
                                    uint32_t height);

    bool ready() const noexcept;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace dolly::env
