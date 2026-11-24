// Lens rain drawing on DX11 WARP: device state is restored, letterbox bars and
// dry pixels are untouched, water changes the picture, output is repeatable.
#include "dolly_lens_rain_dx11.hpp"

#include <d3d11.h>
#include <wrl/client.h>

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>

using Microsoft::WRL::ComPtr;

namespace {

int failures = 0;

void expect(bool ok, const char* what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what);
        ++failures;
    }
}

constexpr UINT kWidth = 320, kHeight = 180;
constexpr UINT kBar = 22; // letterbox bar height, top and bottom

struct Gpu {
    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> context;
};

bool make_gpu(Gpu& gpu) {
    const D3D_FEATURE_LEVEL levels[] = {D3D_FEATURE_LEVEL_11_0};
    return SUCCEEDED(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_WARP, nullptr, 0, levels, 1,
                                       D3D11_SDK_VERSION, &gpu.device, nullptr, &gpu.context));
}

ComPtr<ID3D11Texture2D> make_target(Gpu& gpu, DXGI_FORMAT format) {
    std::vector<std::uint32_t> pixels(kWidth * kHeight);
    for (UINT y = 0; y < kHeight; ++y)
        for (UINT x = 0; x < kWidth; ++x) {
            const std::uint32_t r = (x * 255) / kWidth, g = (y * 255) / kHeight,
                                b = ((x / 16 + y / 16) & 1) * 200;
            pixels[y * kWidth + x] = r | (g << 8) | (b << 16) | 0xff000000u;
        }
    D3D11_TEXTURE2D_DESC desc{};
    desc.Width = kWidth;
    desc.Height = kHeight;
    desc.MipLevels = 1;
    desc.ArraySize = 1;
    desc.Format = format;
    desc.SampleDesc.Count = 1;
    desc.Usage = D3D11_USAGE_DEFAULT;
    desc.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;
    D3D11_SUBRESOURCE_DATA init{pixels.data(), kWidth * 4, 0};
    ComPtr<ID3D11Texture2D> texture;
    gpu.device->CreateTexture2D(&desc, &init, &texture);
    return texture;
}

std::vector<std::uint32_t> read(Gpu& gpu, ID3D11Texture2D* texture) {
    D3D11_TEXTURE2D_DESC desc{};
    texture->GetDesc(&desc);
    desc.Usage = D3D11_USAGE_STAGING;
    desc.BindFlags = 0;
    desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    desc.MiscFlags = 0;
    ComPtr<ID3D11Texture2D> staging;
    gpu.device->CreateTexture2D(&desc, nullptr, &staging);
    gpu.context->CopyResource(staging.Get(), texture);
    std::vector<std::uint32_t> out(kWidth * kHeight);
    D3D11_MAPPED_SUBRESOURCE mapped{};
    if (SUCCEEDED(gpu.context->Map(staging.Get(), 0, D3D11_MAP_READ, 0, &mapped))) {
        for (UINT y = 0; y < kHeight; ++y)
            std::memcpy(&out[y * kWidth],
                        static_cast<const char*>(mapped.pData) + y * mapped.RowPitch, kWidth * 4);
        gpu.context->Unmap(staging.Get(), 0);
    }
    return out;
}

lens_rain::Settings settings() {
    lens_rain::Settings s;
    s.amount = 1.0f;
    s.size = 2.5f;
    s.seed = 7;
    return s;
}

lens_rain::Simulator wet_lens() {
    lens_rain::Simulator sim;
    lens_rain::Inputs in;
    in.replay_time = 4.0;
    in.rain = 1.0f;
    in.seeking = true;
    in.aspect = static_cast<float>(kWidth) / static_cast<float>(kHeight - 2 * kBar);
    sim.advance(settings(), in);
    return sim;
}

// Puts unusual but valid state on the context so a restore can be checked.
struct KnownState {
    ComPtr<ID3D11Texture2D> other, input;
    ComPtr<ID3D11RenderTargetView> rtv;
    ComPtr<ID3D11BlendState> blend;
    ComPtr<ID3D11RasterizerState> raster;
    ComPtr<ID3D11SamplerState> sampler;
    ComPtr<ID3D11ShaderResourceView> srv;
    D3D11_VIEWPORT viewport{3.0f, 5.0f, 77.0f, 41.0f, 0.1f, 0.9f};
    D3D11_RECT scissor{1, 2, 30, 40};
};

void apply(Gpu& gpu, KnownState& s) {
    s.other = make_target(gpu, DXGI_FORMAT_R8G8B8A8_UNORM);
    gpu.device->CreateRenderTargetView(s.other.Get(), nullptr, &s.rtv);
    s.input = make_target(gpu, DXGI_FORMAT_R8G8B8A8_UNORM);
    gpu.device->CreateShaderResourceView(s.input.Get(), nullptr, &s.srv);
    D3D11_BLEND_DESC blend{};
    blend.RenderTarget[0].RenderTargetWriteMask = D3D11_COLOR_WRITE_ENABLE_RED;
    gpu.device->CreateBlendState(&blend, &s.blend);
    D3D11_RASTERIZER_DESC raster{};
    raster.FillMode = D3D11_FILL_WIREFRAME;
    raster.CullMode = D3D11_CULL_FRONT;
    raster.ScissorEnable = TRUE;
    gpu.device->CreateRasterizerState(&raster, &s.raster);
    D3D11_SAMPLER_DESC sampler{};
    sampler.Filter = D3D11_FILTER_MIN_MAG_MIP_POINT;
    sampler.AddressU = sampler.AddressV = sampler.AddressW = D3D11_TEXTURE_ADDRESS_WRAP;
    gpu.device->CreateSamplerState(&sampler, &s.sampler);

    ID3D11RenderTargetView* rtv = s.rtv.Get();
    gpu.context->OMSetRenderTargets(1, &rtv, nullptr);
    const float factor[4] = {0.1f, 0.2f, 0.3f, 0.4f};
    gpu.context->OMSetBlendState(s.blend.Get(), factor, 0x0f0f0f0fu);
    gpu.context->RSSetState(s.raster.Get());
    gpu.context->RSSetViewports(1, &s.viewport);
    gpu.context->RSSetScissorRects(1, &s.scissor);
    ID3D11SamplerState* sampler_ptr = s.sampler.Get();
    gpu.context->PSSetSamplers(0, 1, &sampler_ptr);
    ID3D11ShaderResourceView* srv = s.srv.Get();
    gpu.context->PSSetShaderResources(1, 1, &srv);
    gpu.context->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_LINESTRIP);
}

void check_state(Gpu& gpu, KnownState& s) {
    auto* c = gpu.context.Get();
    ComPtr<ID3D11RenderTargetView> rtv;
    c->OMGetRenderTargets(1, &rtv, nullptr);
    expect(rtv.Get() == s.rtv.Get(), "render target restored");
    ComPtr<ID3D11BlendState> blend;
    float factor[4]{};
    UINT mask = 0;
    c->OMGetBlendState(&blend, factor, &mask);
    expect(blend.Get() == s.blend.Get() && mask == 0x0f0f0f0fu && factor[2] == 0.3f,
           "blend state restored");
    ComPtr<ID3D11RasterizerState> raster;
    c->RSGetState(&raster);
    expect(raster.Get() == s.raster.Get(), "rasterizer state restored");
    UINT count = 1;
    D3D11_VIEWPORT vp{};
    c->RSGetViewports(&count, &vp);
    expect(count == 1 && vp.TopLeftX == 3.0f && vp.Width == 77.0f && vp.MaxDepth == 0.9f,
           "viewport restored");
    D3D11_RECT sc{};
    count = 1;
    c->RSGetScissorRects(&count, &sc);
    expect(count == 1 && sc.right == 30 && sc.bottom == 40, "scissor restored");
    ComPtr<ID3D11SamplerState> sampler;
    c->PSGetSamplers(0, 1, &sampler);
    expect(sampler.Get() == s.sampler.Get(), "sampler restored");
    ComPtr<ID3D11ShaderResourceView> srv0, srv1;
    c->PSGetShaderResources(0, 1, &srv0);
    c->PSGetShaderResources(1, 1, &srv1);
    expect(!srv0 && srv1.Get() == s.srv.Get(), "shader resources restored");
    ComPtr<ID3D11VertexShader> vs;
    c->VSGetShader(&vs, nullptr, nullptr);
    ComPtr<ID3D11PixelShader> ps;
    c->PSGetShader(&ps, nullptr, nullptr);
    expect(!vs && !ps, "shaders restored");
    D3D11_PRIMITIVE_TOPOLOGY topology{};
    c->IAGetPrimitiveTopology(&topology);
    expect(topology == D3D11_PRIMITIVE_TOPOLOGY_LINESTRIP, "topology restored");
}

void run(Gpu& gpu, DXGI_FORMAT format, bool exact_pixels) {
    auto target = make_target(gpu, format);
    const auto before = read(gpu, target.Get());
    const lens_rain::PictureRect picture{0, kBar, kWidth, kHeight - 2 * kBar};
    lens_rain::Renderer renderer;

    // A dry lens draws nothing at all.
    lens_rain::Simulator dry;
    expect(renderer.render(gpu.device.Get(), gpu.context.Get(), target.Get(), dry, settings(),
                           picture),
           "dry lens renders");
    expect(read(gpu, target.Get()) == before, "a dry lens leaves the picture untouched");

    KnownState state;
    apply(gpu, state);
    const auto sim = wet_lens();
    const bool ok = renderer.render(gpu.device.Get(), gpu.context.Get(), target.Get(), sim,
                                    settings(), picture);
    if (!ok)
        std::printf("render error: %s\n", renderer.last_error());
    expect(ok, "wet lens renders");
    check_state(gpu, state);

    const auto after = read(gpu, target.Get());
    std::size_t changed = 0, bar_changed = 0;
    for (UINT y = 0; y < kHeight; ++y)
        for (UINT x = 0; x < kWidth; ++x) {
            const bool differs = after[y * kWidth + x] != before[y * kWidth + x];
            if (y < kBar || y >= kHeight - kBar)
                bar_changed += differs;
            else
                changed += differs;
        }
    std::printf("format %d: %zu drops, %zu picture pixels changed\n", static_cast<int>(format),
                sim.drops().size(), changed);
    expect(bar_changed == 0, "letterbox bars untouched");
    expect(changed > 200, "water changes the picture");
    if (exact_pixels)
        expect(changed < static_cast<std::size_t>(kWidth) * (kHeight - 2 * kBar),
               "dry areas keep their pixels");

    // Same lens on the same picture gives the same image.
    auto again = make_target(gpu, format);
    renderer.render(gpu.device.Get(), gpu.context.Get(), again.Get(), sim, settings(), picture);
    expect(read(gpu, again.Get()) == after, "drawing is repeatable");

    // A smaller picture after a resize still works.
    renderer.render(gpu.device.Get(), gpu.context.Get(), again.Get(), sim, settings(),
                    lens_rain::PictureRect{40, 30, 200, 100});
    expect(std::strlen(renderer.last_error()) == 0, "picture resize recreates the water map");
    renderer.release();
}

} // namespace

int main() {
    Gpu gpu;
    if (!make_gpu(gpu)) {
        std::printf("WARP device unavailable\n");
        return 1;
    }
    run(gpu, DXGI_FORMAT_R8G8B8A8_UNORM, true);
    run(gpu, DXGI_FORMAT_B8G8R8A8_UNORM, true);
    run(gpu, DXGI_FORMAT_R8G8B8A8_UNORM_SRGB, false);

    lens_rain::Renderer renderer;
    lens_rain::Settings s = settings();
    expect(!renderer.render(gpu.device.Get(), gpu.context.Get(), nullptr, wet_lens(), s),
           "missing target refused");

    if (failures) {
        std::printf("%d lens rain WARP check(s) failed\n", failures);
        return 1;
    }
    std::printf("lens rain WARP checks passed\n");
    return 0;
}
