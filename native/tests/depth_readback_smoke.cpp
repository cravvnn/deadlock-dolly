#include "dolly_depth_readback.hpp"
#include <d3d11.h>
#include <chrono>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <thread>

namespace {
void require(bool value, const char* message) {
    if (!value)
        throw std::runtime_error(message);
}
template <class T> struct Com {
    T* p = nullptr;
    ~Com() {
        if (p)
            p->Release();
    }
};
void check(D3D_DRIVER_TYPE driver, DXGI_FORMAT format, DXGI_FORMAT view_format) {
    using namespace dolly::depth;
    Com<ID3D11Device> device;
    Com<ID3D11DeviceContext> context;
    D3D_FEATURE_LEVEL level;
    require(SUCCEEDED(D3D11CreateDevice(nullptr, driver, nullptr, 0, nullptr, 0, D3D11_SDK_VERSION,
                                        &device.p, &level, &context.p)),
            "Create DX11 device failed");
    D3D11_TEXTURE2D_DESC desc{};
    desc.Width = 17; // Forces row padding on typical drivers.
    desc.Height = 3;
    desc.MipLevels = desc.ArraySize = desc.SampleDesc.Count = 1;
    desc.Format = format;
    desc.Usage = D3D11_USAGE_DEFAULT;
    desc.BindFlags = D3D11_BIND_DEPTH_STENCIL;
    Com<ID3D11Texture2D> source;
    require(SUCCEEDED(device.p->CreateTexture2D(&desc, nullptr, &source.p)), "Create depth failed");
    D3D11_DEPTH_STENCIL_VIEW_DESC view{};
    view.Format = view_format;
    view.ViewDimension = D3D11_DSV_DIMENSION_TEXTURE2D;
    Com<ID3D11DepthStencilView> depth;
    require(SUCCEEDED(device.p->CreateDepthStencilView(source.p, &view, &depth.p)),
            "Create DSV failed");
    Frame frame{17, 3, 0, 10.0, {{0, -1, 1.0 / 7, 0}, 0, 1}};
    // A suballocated per-view buffer and an unrelated buffer. No fixed slot
    // or CPU-provided projection can identify/linearize this sequence.
    frame.projection = {};
    D3D11_BUFFER_DESC buffer_desc{};
    buffer_desc.ByteWidth = 1024;
    buffer_desc.Usage = D3D11_USAGE_DEFAULT;
    buffer_desc.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
    Com<ID3D11Buffer> calibration, unrelated;
    require(SUCCEEDED(device.p->CreateBuffer(&buffer_desc, nullptr, &calibration.p)),
            "Create calibration buffer failed");
    const std::array<unsigned char, 1024> zeroes{};
    D3D11_SUBRESOURCE_DATA initial{};
    initial.pSysMem = zeroes.data();
    require(SUCCEEDED(device.p->CreateBuffer(&buffer_desc, &initial, &unrelated.p)),
            "Create unrelated buffer failed");
    ProjectionBuffer bindings[] = {{unrelated.p, 0, 640}, {calibration.p, 128, 640}};
    const auto calibration_data = [](float near_clip) {
        std::array<unsigned char, 1024> bytes{};
        const auto put = [&bytes](unsigned offset, float value) {
            std::memcpy(bytes.data() + 128 + offset, &value, 4);
        };
        for (unsigned offset : {128u, 148u, 168u, 188u, 192u, 212u, 372u, 436u})
            put(offset, 1);
        put(236, near_clip);
        put(248, -1);
        put(260, -1);
        put(264, 1 / near_clip);
        put(328, 17);
        put(332, 3);
        put(336, 1.0f / 17);
        put(340, 1.0f / 3);
        put(376, near_clip);
        put(380, std::numeric_limits<float>::infinity());
        put(456, -1);
        return bytes;
    };
    Readback queue;
    for (unsigned sample = 0; sample < 3; ++sample) {
        frame.sample = sample;
        const auto bytes = calibration_data(7.0f * (sample + 1));
        context.p->UpdateSubresource(calibration.p, 0, nullptr, bytes.data(), 0, 0);
        const auto flags = view_format == DXGI_FORMAT_D24_UNORM_S8_UINT
                               ? D3D11_CLEAR_DEPTH | D3D11_CLEAR_STENCIL
                               : D3D11_CLEAR_DEPTH;
        context.p->ClearDepthStencilView(depth.p, flags, 1.0f / (sample + 1), 222);
        require(queue.enqueue(device.p, context.p, source.p, frame, bindings, 2) ==
                    ReadbackResult::ready,
                "Queueing actual GPU depth failed");
    }
    require(queue.pending() == 3, "GPU queue count incorrect");
    require(queue.enqueue(device.p, context.p, source.p, frame, bindings, 2) ==
                ReadbackResult::full,
            "Full GPU queue overwrote a sample");
    // Flush only belongs to this smoke-test harness. Production polling never
    // flushes/waits; the normal game's Present submits the graphics commands.
    context.p->Flush();
    for (unsigned sample = 0; sample < 3; ++sample) {
        RawFrame raw;
        auto result = ReadbackResult::pending;
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
        while (result == ReadbackResult::pending && std::chrono::steady_clock::now() < deadline) {
            result = queue.poll(context.p, raw);
            if (result == ReadbackResult::pending)
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        require(result == ReadbackResult::ready, "GPU readback did not finish");
        require(raw.frame.sample == sample && raw.frame.replay_time == 10,
                "Readback mixed sample IDs or times");
        require(raw.pixels.size() == 17 * 3 * 4, "Padded GPU rows leaked to CPU frame");
        std::vector<float> distance(17 * 3);
        require(convert(raw.pixels.data(), raw.pixels.size(), 17 * 4, 17, 3, raw.format,
                        raw.frame.projection, distance.data(), distance.size()),
                "GPU decode failed");
        for (float z : distance)
            require(std::abs(z - 7.0f * (sample + 1) * (sample + 1)) < .0001f,
                    "GPU depth/projection copies did not preserve the same queued frame");
    }
    RawFrame empty;
    require(queue.poll(context.p, empty) == ReadbackResult::empty, "Drained queue not empty");
    auto wrong = frame;
    wrong.width = 16;
    require(queue.enqueue(device.p, context.p, source.p, wrong, bindings, 2) ==
                ReadbackResult::invalid,
            "Mismatched scene dimensions accepted");
    Com<ID3D11DeviceContext> deferred;
    require(SUCCEEDED(device.p->CreateDeferredContext(0, &deferred.p)),
            "Create deferred context failed");
    require(queue.enqueue(device.p, deferred.p, source.p, frame, bindings, 2) ==
                ReadbackResult::invalid,
            "Deferred-context readback accepted");
    const auto conflict = calibration_data(99);
    context.p->UpdateSubresource(unrelated.p, 0, nullptr, conflict.data(), 0, 0);
    bindings[0].offset = 128;
    require(queue.enqueue(device.p, context.p, source.p, frame, bindings, 2) ==
                ReadbackResult::ready,
            "Could not queue conflicting calibration test");
    context.p->Flush();
    auto conflict_result = ReadbackResult::pending;
    const auto limit = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    while (conflict_result == ReadbackResult::pending && std::chrono::steady_clock::now() < limit) {
        conflict_result = queue.poll(context.p, empty);
        if (conflict_result == ReadbackResult::pending)
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    require(conflict_result == ReadbackResult::failed,
            "Conflicting per-view calibrations were resolved by binding order");
    queue.reset();
    require(queue.pending() == 0 && queue.error() == 0, "Reset retained stale GPU slots");
}
}
int main(int argc, char**) {
    try {
        check(D3D_DRIVER_TYPE_WARP, DXGI_FORMAT_R24G8_TYPELESS, DXGI_FORMAT_D24_UNORM_S8_UINT);
        check(D3D_DRIVER_TYPE_WARP, DXGI_FORMAT_R32_TYPELESS, DXGI_FORMAT_D32_FLOAT);
        if (argc > 1) {
            check(D3D_DRIVER_TYPE_HARDWARE, DXGI_FORMAT_R24G8_TYPELESS,
                  DXGI_FORMAT_D24_UNORM_S8_UINT);
            check(D3D_DRIVER_TYPE_HARDWARE, DXGI_FORMAT_R32_TYPELESS, DXGI_FORMAT_D32_FLOAT);
        }
        std::puts("DX11 depth readback, queue bounds, row pitch and frame identity passed.");
        return 0;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "%s\n", error.what());
        return 1;
    }
}
