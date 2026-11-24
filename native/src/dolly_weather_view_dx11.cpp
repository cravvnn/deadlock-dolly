#include "dolly_weather_view_dx11.hpp"

#define NOMINMAX
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3d11.h>
#include <d3dcompiler.h>
#include <wrl/client.h>

#include <algorithm>

namespace dolly::env {
using Microsoft::WRL::ComPtr;

namespace {

constexpr uint32_t kStride = 1024;  // bytes reserved per candidate copy
constexpr uint32_t kMinBytes = 464; // through the camera forward vector at 448

constexpr char kBuildViewHlsl[] = R"HLSL(
struct ViewData {
    row_major float4x4 viewProj;
    row_major float4x4 invViewProj;
    float4 cameraPos;
    float4 right;
    float4 up;
};

cbuffer BuildConstants : register(b0) {
    uint count;
    uint width;
    uint height;
    uint stride;
};

ByteAddressBuffer candidates : register(t0);
RWStructuredBuffer<ViewData> outView : register(u0);

float At(uint base, uint offset) { return asfloat(candidates.Load(base + offset)); }

bool Near(float a, float b, float tolerance) {
    return abs(a - b) <= tolerance * max(1.0, max(abs(a), abs(b)));
}

float4x4 LoadMatrix(uint base) {
    float4x4 m;
    [unroll] for (uint r = 0; r < 4; ++r)
        [unroll] for (uint c = 0; c < 4; ++c)
            m[r][c] = At(base, r * 16 + c * 4);
    return m;
}

float4x4 Inverse(float4x4 m) {
    float a00 = m[0][0], a01 = m[0][1], a02 = m[0][2], a03 = m[0][3];
    float a10 = m[1][0], a11 = m[1][1], a12 = m[1][2], a13 = m[1][3];
    float a20 = m[2][0], a21 = m[2][1], a22 = m[2][2], a23 = m[2][3];
    float a30 = m[3][0], a31 = m[3][1], a32 = m[3][2], a33 = m[3][3];
    float b00 = a00 * a11 - a01 * a10, b01 = a00 * a12 - a02 * a10;
    float b02 = a00 * a13 - a03 * a10, b03 = a01 * a12 - a02 * a11;
    float b04 = a01 * a13 - a03 * a11, b05 = a02 * a13 - a03 * a12;
    float b06 = a20 * a31 - a21 * a30, b07 = a20 * a32 - a22 * a30;
    float b08 = a20 * a33 - a23 * a30, b09 = a21 * a32 - a22 * a31;
    float b10 = a21 * a33 - a23 * a31, b11 = a22 * a33 - a23 * a32;
    float det = b00 * b11 - b01 * b10 + b02 * b09 + b03 * b08 - b04 * b07 + b05 * b06;
    float inv = abs(det) > 1e-30 ? 1.0 / det : 0.0;
    float4x4 r;
    r[0][0] = (a11 * b11 - a12 * b10 + a13 * b09) * inv;
    r[0][1] = (a02 * b10 - a01 * b11 - a03 * b09) * inv;
    r[0][2] = (a31 * b05 - a32 * b04 + a33 * b03) * inv;
    r[0][3] = (a22 * b04 - a21 * b05 - a23 * b03) * inv;
    r[1][0] = (a12 * b08 - a10 * b11 - a13 * b07) * inv;
    r[1][1] = (a00 * b11 - a02 * b08 + a03 * b07) * inv;
    r[1][2] = (a32 * b02 - a30 * b05 - a33 * b01) * inv;
    r[1][3] = (a20 * b05 - a22 * b02 + a23 * b01) * inv;
    r[2][0] = (a10 * b10 - a11 * b08 + a13 * b06) * inv;
    r[2][1] = (a01 * b08 - a00 * b10 - a03 * b06) * inv;
    r[2][2] = (a30 * b04 - a31 * b02 + a33 * b00) * inv;
    r[2][3] = (a21 * b02 - a20 * b04 - a23 * b00) * inv;
    r[3][0] = (a11 * b07 - a10 * b09 - a12 * b06) * inv;
    r[3][1] = (a00 * b09 - a01 * b07 + a02 * b06) * inv;
    r[3][2] = (a31 * b01 - a30 * b03 - a32 * b00) * inv;
    r[3][3] = (a20 * b03 - a21 * b01 + a22 * b00) * inv;
    return r;
}

bool ValidView(uint b) {
    if (!Near(At(b, 320), 0.0, 1e-4) || !Near(At(b, 324), 0.0, 1e-4) ||
        abs(At(b, 328) - float(width)) > 0.5 || abs(At(b, 332) - float(height)) > 0.5)
        return false;
    // Affine row-vector view: last column (0, 0, 0, 1).
    if (!Near(At(b, 140), 0.0, 1e-4) || !Near(At(b, 156), 0.0, 1e-4) ||
        !Near(At(b, 172), 0.0, 1e-4) || !Near(At(b, 188), 1.0, 1e-4))
        return false;
    // Projection structural zeros (jitter at [0][2] and [1][2] is allowed).
    static const uint zeros[8] = {1, 3, 4, 7, 8, 9, 12, 13};
    [unroll] for (uint z = 0; z < 8; ++z)
        if (!Near(At(b, 192 + zeros[z] * 4), 0.0, 1e-4))
            return false;
    if (!(abs(At(b, 248)) > 1e-6) || !(abs(At(b, 192)) > 1e-6) || !(abs(At(b, 212)) > 1e-6))
        return false;
    // Orthonormal view axes.
    [unroll] for (uint i = 0; i < 3; ++i)
        [unroll] for (uint j = 0; j < 3; ++j) {
            float d = 0.0;
            [unroll] for (uint k = 0; k < 3; ++k)
                d += At(b, 128 + k * 16 + i * 4) * At(b, 128 + k * 16 + j * 4);
            if (!Near(d, i == j ? 1.0 : 0.0, 1e-3))
                return false;
        }
    return true;
}

[numthreads(1, 1, 1)]
void BuildViewCS() {
    ViewData o = (ViewData)0;
    for (uint i = 0; i < count; ++i) {
        uint b = i * stride;
        if (!ValidView(b))
            continue;
        float4x4 V = LoadMatrix(b + 128);
        float4x4 P = LoadMatrix(b + 192);
        float4x4 Vt = transpose(V); // row-vector view -> column-vector
        float4x4 steady = P;
        steady[0][2] = 0.0;
        steady[1][2] = 0.0;
        o.viewProj = mul(steady, Vt);
        o.invViewProj = Inverse(mul(P, Vt));
        o.cameraPos = float4(At(b, 416), At(b, 420), At(b, 424), 1.0);
        o.right = float4(V[0][0], V[1][0], V[2][0], 0.0);
        o.up = float4(V[0][1], V[1][1], V[2][1], 0.0);
        break;
    }
    outView[0] = o;
}
)HLSL";

struct BuildConstants {
    uint32_t count, width, height, stride;
};
constexpr uint32_t kViewDataBytes = 2 * 64 + 3 * 16;

} // namespace

struct GpuViewBuilderDX11::Impl {
    ComPtr<ID3D11ComputeShader> cs;
    ComPtr<ID3D11Buffer> constants;
    ComPtr<ID3D11Buffer> candidates;
    ComPtr<ID3D11ShaderResourceView> candidates_srv;
    ComPtr<ID3D11Buffer> view;
    ComPtr<ID3D11UnorderedAccessView> view_uav;
    ComPtr<ID3D11ShaderResourceView> view_srv;
    bool initialized = false;
};

GpuViewBuilderDX11::GpuViewBuilderDX11() : impl_(std::make_unique<Impl>()) {}
GpuViewBuilderDX11::~GpuViewBuilderDX11() = default;

bool GpuViewBuilderDX11::initialize(ID3D11Device* device, std::string* out_error) {
    shutdown();
    if (!device) {
        if (out_error)
            *out_error = "null D3D11 device";
        return false;
    }
    ComPtr<ID3DBlob> code, err;
    const UINT flags = D3DCOMPILE_ENABLE_STRICTNESS | D3DCOMPILE_OPTIMIZATION_LEVEL3;
    if (FAILED(D3DCompile(kBuildViewHlsl, sizeof(kBuildViewHlsl) - 1, "dolly_weather_view.hlsl",
                          nullptr, nullptr, "BuildViewCS", "cs_5_0", flags, 0, &code, &err))) {
        if (out_error)
            *out_error = std::string("BuildViewCS compile failed: ") +
                         (err ? static_cast<const char*>(err->GetBufferPointer()) : "unknown");
        return false;
    }
    bool ok = SUCCEEDED(device->CreateComputeShader(code->GetBufferPointer(), code->GetBufferSize(),
                                                    nullptr, &impl_->cs));

    D3D11_BUFFER_DESC cb{};
    cb.ByteWidth = sizeof(BuildConstants);
    cb.Usage = D3D11_USAGE_DEFAULT;
    cb.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
    ok = ok && SUCCEEDED(device->CreateBuffer(&cb, nullptr, &impl_->constants));

    D3D11_BUFFER_DESC raw{};
    raw.ByteWidth = kStride * kMaxCandidates;
    raw.Usage = D3D11_USAGE_DEFAULT;
    raw.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    raw.MiscFlags = D3D11_RESOURCE_MISC_BUFFER_ALLOW_RAW_VIEWS;
    ok = ok && SUCCEEDED(device->CreateBuffer(&raw, nullptr, &impl_->candidates));
    D3D11_SHADER_RESOURCE_VIEW_DESC rsv{};
    rsv.Format = DXGI_FORMAT_R32_TYPELESS;
    rsv.ViewDimension = D3D11_SRV_DIMENSION_BUFFEREX;
    rsv.BufferEx.NumElements = raw.ByteWidth / 4;
    rsv.BufferEx.Flags = D3D11_BUFFEREX_SRV_FLAG_RAW;
    ok = ok && SUCCEEDED(device->CreateShaderResourceView(impl_->candidates.Get(), &rsv,
                                                          &impl_->candidates_srv));

    D3D11_BUFFER_DESC vd{};
    vd.ByteWidth = kViewDataBytes;
    vd.Usage = D3D11_USAGE_DEFAULT;
    vd.BindFlags = D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_UNORDERED_ACCESS;
    vd.MiscFlags = D3D11_RESOURCE_MISC_BUFFER_STRUCTURED;
    vd.StructureByteStride = kViewDataBytes;
    ok = ok && SUCCEEDED(device->CreateBuffer(&vd, nullptr, &impl_->view));
    ok = ok &&
         SUCCEEDED(device->CreateUnorderedAccessView(impl_->view.Get(), nullptr, &impl_->view_uav));
    ok = ok &&
         SUCCEEDED(device->CreateShaderResourceView(impl_->view.Get(), nullptr, &impl_->view_srv));
    if (!ok) {
        if (out_error)
            *out_error = "failed to create GPU view builder resources";
        shutdown();
        return false;
    }
    impl_->initialized = true;
    return true;
}

void GpuViewBuilderDX11::shutdown() {
    impl_ = std::make_unique<Impl>();
}

bool GpuViewBuilderDX11::ready() const noexcept {
    return impl_->initialized;
}

ID3D11ShaderResourceView* GpuViewBuilderDX11::build(ID3D11DeviceContext* ctx,
                                                    ID3D11Buffer* const* candidates,
                                                    const uint32_t* sizes, uint32_t count,
                                                    uint32_t width, uint32_t height) {
    if (!ctx || !impl_->initialized || (count && (!candidates || !sizes)) || !width || !height)
        return nullptr;
    uint32_t used = 0;
    for (uint32_t i = 0; i < count && used < kMaxCandidates; ++i) {
        if (!candidates[i] || sizes[i] < kMinBytes)
            continue;
        const D3D11_BOX box{0, 0, 0, std::min(sizes[i], kStride), 1, 1};
        ctx->CopySubresourceRegion(impl_->candidates.Get(), 0, used * kStride, 0, 0, candidates[i],
                                   0, &box);
        ++used;
    }
    const BuildConstants c{used, width, height, kStride};
    ctx->UpdateSubresource(impl_->constants.Get(), 0, nullptr, &c, 0, 0);
    ID3D11ShaderResourceView* srvs[1] = {impl_->candidates_srv.Get()};
    ID3D11UnorderedAccessView* uavs[1] = {impl_->view_uav.Get()};
    ID3D11Buffer* cbs[1] = {impl_->constants.Get()};
    ctx->CSSetShader(impl_->cs.Get(), nullptr, 0);
    ctx->CSSetConstantBuffers(0, 1, cbs);
    ctx->CSSetShaderResources(0, 1, srvs);
    ctx->CSSetUnorderedAccessViews(0, 1, uavs, nullptr);
    ctx->Dispatch(1, 1, 1);
    ID3D11ShaderResourceView* null_srv[1] = {};
    ID3D11UnorderedAccessView* null_uav[1] = {};
    ctx->CSSetShaderResources(0, 1, null_srv);
    ctx->CSSetUnorderedAccessViews(0, 1, null_uav, nullptr);
    ctx->CSSetShader(nullptr, nullptr, 0);
    return impl_->view_srv.Get();
}

} // namespace dolly::env
