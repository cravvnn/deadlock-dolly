#include "dolly_lens_rain_dx11.hpp"

#include <d3dcompiler.h>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <vector>

namespace lens_rain {
namespace {

template <typename T> using Ptr = Microsoft::WRL::ComPtr<T>;

// One capsule of water per instance: a segment (u0, v0)-(u1, v1) with a radius. Drops
// have round ends (a disc when the segment is a point, a teardrop while running);
// runoff streaks have flat ends so consecutive pieces join without bulges.
const char kWaterShaders[] = R"(
cbuffer Params : register(b0) { float4 a; float4 b; float4 c; };
struct Water { float4 ends; float4 look; }; // ends: u0 v0 u1 v1; look: radius, water, flat
StructuredBuffer<Water> drops : register(t0);
struct V {
    float4 pos : SV_Position;
    float2 local : TEXCOORD0;   // along the segment, across it (picture heights)
    float4 shape : TEXCOORD1;   // length, radius, water, flat
    float4 frame : TEXCOORD2;   // segment direction (xy) and its perpendicular (zw)
};
V vs_main(uint vid : SV_VertexID, uint iid : SV_InstanceID) {
    const float2 corners[6] = { float2(0,-1), float2(0,1), float2(1,1),
                                float2(0,-1), float2(1,1), float2(1,-1) };
    float2 k = corners[vid];
    Water w = drops[iid];
    float aspect = a.x;
    float2 p0 = w.ends.xy, p1 = w.ends.zw;
    float2 axis = p1 - p0;
    float len = length(axis);
    float2 dir = len > 1e-6 ? axis / len : float2(1.0, 0.0);
    float2 perp = float2(-dir.y, dir.x);
    float r = w.look.x;
    float pad = w.look.z > 0.5 ? 0.0 : r;      // flat ends need no room past the ends
    float along = lerp(-pad, len + pad, k.x);
    float across = k.y * r;
    float2 p = p0 + dir * along + perp * across;
    V o;
    o.pos = float4(p.x / aspect * 2.0 - 1.0, 1.0 - 2.0 * p.y, 0.0, 1.0);
    o.local = float2(along, across);
    o.shape = float4(len, r, w.look.y, w.look.z);
    o.frame = float4(dir, perp);
    return o;
}
float4 ps_main(V i) : SV_Target {
    float len = i.shape.x, r = i.shape.y;
    float along = i.local.x;
    if (i.shape.w > 0.5 && (along < 0.0 || along > len)) discard;
    float2 d = float2(along - clamp(along, 0.0, len), i.local.y) / r;
    float q = dot(d, d);
    if (q >= 1.0) discard;
    float h = sqrt(1.0 - q);                    // height of the water's rounded surface
    float t = i.shape.z * h;
    float edge = saturate((1.0 - q) * 6.0);
    float2 slope = d.x * i.frame.xy + d.y * i.frame.zw;  // back to picture axes (v down)
    // rg: slope weighted by thickness, b: thickness, a: coverage
    return float4(slope * t, t, i.shape.z * edge);
}
)";

// Full-screen triangle limited to the picture by the viewport.
const char kCompositeShaders[] = R"(
cbuffer Params : register(b0) { float4 a; float4 b; float4 c; };
Texture2D scene : register(t0);
Texture2D water : register(t1);
SamplerState linear_clamp : register(s0);
float4 vs_main(uint vid : SV_VertexID) : SV_Position {
    float2 p = float2((vid << 1) & 2, vid & 2);
    return float4(p * float2(2.0, -2.0) + float2(-1.0, 1.0), 0.0, 1.0);
}
float4 ps_main(float4 pos : SV_Position) : SV_Target {
    float refraction = a.y, softness = a.z, lod_max = a.w;
    float2 origin = b.xy, size = b.zw, scene_size = c.xy;
    int2 px = int2(pos.xy);
    float4 dry = scene.Load(int3(px, 0));
    float4 wv = water.SampleLevel(linear_clamp, (pos.xy - origin) / size, 0.0);
    float cover = saturate(wv.a);
    if (cover <= (1.0 / 512.0)) return dry;
    float thick = max(wv.b, 1e-4);
    float2 slope = wv.rg / thick;
    // A drop acts as a small lens: it shows a shrunken, flipped view of the
    // scene behind it, so the sample moves against the slope. The view stays
    // mostly sharp; softness adds a little blur where the water is thick.
    float2 offset = -slope * refraction * 0.11 * float2(size.y / scene_size.x, size.y / scene_size.y);
    float2 uv = pos.xy / scene_size + offset;
    float lod = softness * min(lod_max, 2.0) * saturate(thick * 1.2);
    float3 wet = scene.SampleLevel(linear_clamp, uv, lod).rgb;
    float edge = dot(slope, slope);
    wet *= lerp(1.0, 0.6, edge * edge * edge);        // thin dark rim at the edge
    // A small highlight from a light above and to the left of the lens.
    float3 normal = normalize(float3(slope.x, -slope.y, 0.8));
    float glint = pow(saturate(dot(normal, normalize(float3(-0.35, 0.55, 0.76)))), 48.0);
    wet += 0.35 * glint * saturate(thick * 2.0);
    return float4(lerp(dry.rgb, wet, cover), dry.a);
}
)";

// Floats per water instance (the shader's Water struct: ends and look).
constexpr std::uint32_t kFloatsPerWater = 8;

struct Constants {
    float a[4]; // aspect, refraction, softness, lod_max
    float b[4]; // picture origin xy, picture size xy (pixels)
    float c[4]; // back-buffer size xy
};

DXGI_FORMAT view_format(DXGI_FORMAT format) {
    switch (format) {
    case DXGI_FORMAT_R8G8B8A8_TYPELESS:
        return DXGI_FORMAT_R8G8B8A8_UNORM;
    case DXGI_FORMAT_B8G8R8A8_TYPELESS:
        return DXGI_FORMAT_B8G8R8A8_UNORM;
    case DXGI_FORMAT_R10G10B10A2_TYPELESS:
        return DXGI_FORMAT_R10G10B10A2_UNORM;
    case DXGI_FORMAT_R16G16B16A16_TYPELESS:
        return DXGI_FORMAT_R16G16B16A16_FLOAT;
    case DXGI_FORMAT_R8G8B8A8_UNORM:
    case DXGI_FORMAT_R8G8B8A8_UNORM_SRGB:
    case DXGI_FORMAT_B8G8R8A8_UNORM:
    case DXGI_FORMAT_B8G8R8A8_UNORM_SRGB:
    case DXGI_FORMAT_R10G10B10A2_UNORM:
    case DXGI_FORMAT_R16G16B16A16_FLOAT:
        return format;
    default:
        return DXGI_FORMAT_UNKNOWN;
    }
}

// Saves and restores exactly the context state this pass touches.
class StateScope {
public:
    explicit StateScope(ID3D11DeviceContext* context) : context_(context) {
        context->IAGetPrimitiveTopology(&topology_);
        context->IAGetInputLayout(&layout_);
        context->IAGetVertexBuffers(0, 1, &vb_, &vb_stride_, &vb_offset_);
        context->IAGetIndexBuffer(&ib_, &ib_format_, &ib_offset_);
        context->VSGetShader(&vs_, nullptr, nullptr);
        context->VSGetConstantBuffers(0, 1, &vs_cb_);
        context->VSGetShaderResources(0, 1, &vs_srv_);
        context->GSGetShader(&gs_, nullptr, nullptr);
        context->HSGetShader(&hs_, nullptr, nullptr);
        context->DSGetShader(&ds_, nullptr, nullptr);
        context->PSGetShader(&ps_, nullptr, nullptr);
        context->PSGetConstantBuffers(0, 1, &ps_cb_);
        context->PSGetShaderResources(0, 2, ps_srv_);
        context->PSGetSamplers(0, 1, &ps_sampler_);
        context->RSGetState(&raster_);
        viewport_count_ = D3D11_VIEWPORT_AND_SCISSORRECT_OBJECT_COUNT_PER_PIPELINE;
        context->RSGetViewports(&viewport_count_, viewports_);
        scissor_count_ = D3D11_VIEWPORT_AND_SCISSORRECT_OBJECT_COUNT_PER_PIPELINE;
        context->RSGetScissorRects(&scissor_count_, scissors_);
        context->OMGetRenderTargets(D3D11_SIMULTANEOUS_RENDER_TARGET_COUNT, rtvs_, &dsv_);
        context->OMGetBlendState(&blend_, blend_factor_, &sample_mask_);
        context->OMGetDepthStencilState(&depth_, &stencil_ref_);
    }
    ~StateScope() {
        auto* c = context_;
        c->IASetPrimitiveTopology(topology_);
        c->IASetInputLayout(layout_);
        c->IASetVertexBuffers(0, 1, &vb_, &vb_stride_, &vb_offset_);
        c->IASetIndexBuffer(ib_, ib_format_, ib_offset_);
        c->VSSetShader(vs_, nullptr, 0);
        c->VSSetConstantBuffers(0, 1, &vs_cb_);
        c->VSSetShaderResources(0, 1, &vs_srv_);
        c->GSSetShader(gs_, nullptr, 0);
        c->HSSetShader(hs_, nullptr, 0);
        c->DSSetShader(ds_, nullptr, 0);
        c->PSSetShader(ps_, nullptr, 0);
        c->PSSetConstantBuffers(0, 1, &ps_cb_);
        c->PSSetShaderResources(0, 2, ps_srv_);
        c->PSSetSamplers(0, 1, &ps_sampler_);
        c->RSSetState(raster_);
        c->RSSetViewports(viewport_count_, viewports_);
        c->RSSetScissorRects(scissor_count_, scissors_);
        c->OMSetRenderTargets(D3D11_SIMULTANEOUS_RENDER_TARGET_COUNT, rtvs_, dsv_);
        c->OMSetBlendState(blend_, blend_factor_, sample_mask_);
        c->OMSetDepthStencilState(depth_, stencil_ref_);
        release(layout_, vb_, ib_, vs_, vs_cb_, vs_srv_, gs_, hs_, ds_, ps_, ps_cb_, ps_srv_[0],
                ps_srv_[1], ps_sampler_, raster_, dsv_, blend_, depth_);
        for (auto*& rtv : rtvs_)
            release(rtv);
    }
    StateScope(const StateScope&) = delete;
    StateScope& operator=(const StateScope&) = delete;

private:
    template <typename... T> static void release(T*&... objects) {
        ((objects ? (objects->Release(), objects = nullptr) : nullptr), ...);
    }

    ID3D11DeviceContext* context_;
    D3D11_PRIMITIVE_TOPOLOGY topology_{};
    ID3D11InputLayout* layout_ = nullptr;
    ID3D11Buffer* vb_ = nullptr;
    UINT vb_stride_ = 0, vb_offset_ = 0;
    ID3D11Buffer* ib_ = nullptr;
    DXGI_FORMAT ib_format_{};
    UINT ib_offset_ = 0;
    ID3D11VertexShader* vs_ = nullptr;
    ID3D11Buffer* vs_cb_ = nullptr;
    ID3D11ShaderResourceView* vs_srv_ = nullptr;
    ID3D11GeometryShader* gs_ = nullptr;
    ID3D11HullShader* hs_ = nullptr;
    ID3D11DomainShader* ds_ = nullptr;
    ID3D11PixelShader* ps_ = nullptr;
    ID3D11Buffer* ps_cb_ = nullptr;
    ID3D11ShaderResourceView* ps_srv_[2] = {};
    ID3D11SamplerState* ps_sampler_ = nullptr;
    ID3D11RasterizerState* raster_ = nullptr;
    UINT viewport_count_ = 0, scissor_count_ = 0;
    D3D11_VIEWPORT viewports_[D3D11_VIEWPORT_AND_SCISSORRECT_OBJECT_COUNT_PER_PIPELINE]{};
    D3D11_RECT scissors_[D3D11_VIEWPORT_AND_SCISSORRECT_OBJECT_COUNT_PER_PIPELINE]{};
    ID3D11RenderTargetView* rtvs_[D3D11_SIMULTANEOUS_RENDER_TARGET_COUNT] = {};
    ID3D11DepthStencilView* dsv_ = nullptr;
    ID3D11BlendState* blend_ = nullptr;
    FLOAT blend_factor_[4] = {};
    UINT sample_mask_ = 0;
    ID3D11DepthStencilState* depth_ = nullptr;
    UINT stencil_ref_ = 0;
};

bool compile(const char* source, std::size_t size, const char* entry, const char* target,
             Ptr<ID3DBlob>& code) {
    Ptr<ID3DBlob> errors;
    return SUCCEEDED(D3DCompile(source, size, "lens_rain", nullptr, nullptr, entry, target,
                                D3DCOMPILE_OPTIMIZATION_LEVEL3, 0, &code, &errors));
}

} // namespace

bool Renderer::fail(const char* message) {
    error_ = message;
    return false;
}

void Renderer::release() {
    *this = Renderer{};
}

bool Renderer::ensure_shaders(ID3D11Device* device) {
    if (device_.Get() == device && composite_ps_)
        return true;
    release();
    device_ = device;
    Ptr<ID3DBlob> code;
    if (!compile(kWaterShaders, sizeof(kWaterShaders) - 1, "vs_main", "vs_5_0", code) ||
        FAILED(device->CreateVertexShader(code->GetBufferPointer(), code->GetBufferSize(), nullptr,
                                          &water_vs_)))
        return fail("lens rain: water vertex shader");
    code.Reset();
    if (!compile(kWaterShaders, sizeof(kWaterShaders) - 1, "ps_main", "ps_5_0", code) ||
        FAILED(device->CreatePixelShader(code->GetBufferPointer(), code->GetBufferSize(), nullptr,
                                         &water_ps_)))
        return fail("lens rain: water pixel shader");
    code.Reset();
    if (!compile(kCompositeShaders, sizeof(kCompositeShaders) - 1, "vs_main", "vs_5_0", code) ||
        FAILED(device->CreateVertexShader(code->GetBufferPointer(), code->GetBufferSize(), nullptr,
                                          &screen_vs_)))
        return fail("lens rain: composite vertex shader");
    code.Reset();
    if (!compile(kCompositeShaders, sizeof(kCompositeShaders) - 1, "ps_main", "ps_5_0", code) ||
        FAILED(device->CreatePixelShader(code->GetBufferPointer(), code->GetBufferSize(), nullptr,
                                         &composite_ps_)))
        return fail("lens rain: composite pixel shader");

    D3D11_BLEND_DESC blend{};
    auto& rt = blend.RenderTarget[0];
    rt.BlendEnable = TRUE;
    rt.SrcBlend = rt.DestBlend = rt.SrcBlendAlpha = rt.DestBlendAlpha = D3D11_BLEND_ONE;
    rt.BlendOp = rt.BlendOpAlpha = D3D11_BLEND_OP_ADD;
    rt.RenderTargetWriteMask = D3D11_COLOR_WRITE_ENABLE_ALL;
    D3D11_RASTERIZER_DESC raster{};
    raster.FillMode = D3D11_FILL_SOLID;
    raster.CullMode = D3D11_CULL_NONE;
    raster.DepthClipEnable = TRUE;
    D3D11_DEPTH_STENCIL_DESC depth{};
    depth.DepthEnable = FALSE;
    depth.StencilEnable = FALSE;
    D3D11_SAMPLER_DESC sampler{};
    sampler.Filter = D3D11_FILTER_MIN_MAG_MIP_LINEAR;
    sampler.AddressU = sampler.AddressV = sampler.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
    sampler.MaxLOD = D3D11_FLOAT32_MAX;
    D3D11_BUFFER_DESC constants{};
    constants.ByteWidth = sizeof(Constants);
    constants.Usage = D3D11_USAGE_DYNAMIC;
    constants.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
    constants.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
    if (FAILED(device->CreateBlendState(&blend, &additive_)) ||
        FAILED(device->CreateRasterizerState(&raster, &raster_)) ||
        FAILED(device->CreateDepthStencilState(&depth, &no_depth_)) ||
        FAILED(device->CreateSamplerState(&sampler, &linear_)) ||
        FAILED(device->CreateBuffer(&constants, nullptr, &constants_)))
        return fail("lens rain: pipeline state");
    return true;
}

bool Renderer::ensure_targets(ID3D11Device* device, ID3D11Texture2D*,
                              const D3D11_TEXTURE2D_DESC& desc, std::uint32_t water_width,
                              std::uint32_t water_height) {
    const bool same_scene = scene_ && desc_.Width == desc.Width && desc_.Height == desc.Height &&
                            desc_.Format == desc.Format;
    if (!same_scene) {
        scene_.Reset();
        scene_view_.Reset();
        std::uint32_t mips = 1;
        for (std::uint32_t edge = (std::min)(desc.Width, desc.Height); edge > 8 && mips < 6;
             edge /= 2)
            ++mips;
        D3D11_TEXTURE2D_DESC copy{};
        copy.Width = desc.Width;
        copy.Height = desc.Height;
        copy.MipLevels = mips;
        copy.ArraySize = 1;
        copy.Format = desc.Format;
        copy.SampleDesc.Count = 1;
        copy.Usage = D3D11_USAGE_DEFAULT;
        copy.BindFlags = D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_RENDER_TARGET;
        copy.MiscFlags = D3D11_RESOURCE_MISC_GENERATE_MIPS;
        D3D11_SHADER_RESOURCE_VIEW_DESC view{};
        view.Format = view_format(desc.Format);
        view.ViewDimension = D3D11_SRV_DIMENSION_TEXTURE2D;
        view.Texture2D.MipLevels = mips;
        if (FAILED(device->CreateTexture2D(&copy, nullptr, &scene_)) ||
            FAILED(device->CreateShaderResourceView(scene_.Get(), &view, &scene_view_))) {
            scene_.Reset();
            return fail("lens rain: scene copy");
        }
        desc_ = desc;
        scene_mips_ = mips;
    }
    if (!water_ || water_width_ != water_width || water_height_ != water_height) {
        water_.Reset();
        water_target_.Reset();
        water_view_.Reset();
        D3D11_TEXTURE2D_DESC map{};
        map.Width = water_width;
        map.Height = water_height;
        map.MipLevels = 1;
        map.ArraySize = 1;
        map.Format = DXGI_FORMAT_R16G16B16A16_FLOAT;
        map.SampleDesc.Count = 1;
        map.Usage = D3D11_USAGE_DEFAULT;
        map.BindFlags = D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_RENDER_TARGET;
        if (FAILED(device->CreateTexture2D(&map, nullptr, &water_)) ||
            FAILED(device->CreateRenderTargetView(water_.Get(), nullptr, &water_target_)) ||
            FAILED(device->CreateShaderResourceView(water_.Get(), nullptr, &water_view_))) {
            water_.Reset();
            return fail("lens rain: water map");
        }
        water_width_ = water_width;
        water_height_ = water_height;
    }
    return true;
}

bool Renderer::ensure_instances(ID3D11Device* device, std::uint32_t count) {
    if (instances_ && instance_capacity_ >= count)
        return true;
    instances_.Reset();
    instances_view_.Reset();
    const auto capacity = static_cast<std::uint32_t>(kMaxDrops * 2 + kMaxBeads);
    D3D11_BUFFER_DESC buffer{};
    buffer.ByteWidth = capacity * kFloatsPerWater * 4;
    buffer.Usage = D3D11_USAGE_DYNAMIC;
    buffer.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    buffer.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
    buffer.MiscFlags = D3D11_RESOURCE_MISC_BUFFER_STRUCTURED;
    buffer.StructureByteStride = kFloatsPerWater * 4;
    D3D11_SHADER_RESOURCE_VIEW_DESC view{};
    view.Format = DXGI_FORMAT_UNKNOWN;
    view.ViewDimension = D3D11_SRV_DIMENSION_BUFFER;
    view.Buffer.NumElements = capacity;
    if (FAILED(device->CreateBuffer(&buffer, nullptr, &instances_)) ||
        FAILED(device->CreateShaderResourceView(instances_.Get(), &view, &instances_view_))) {
        instances_.Reset();
        return fail("lens rain: drop buffer");
    }
    instance_capacity_ = capacity;
    return true;
}

bool Renderer::render(ID3D11Device* device, ID3D11DeviceContext* context,
                      ID3D11Texture2D* back_buffer, const Simulator& simulator,
                      const Settings& settings, PictureRect rect) {
    error_ = "";
    if (!device || !context || !back_buffer)
        return fail("lens rain: missing device or back buffer");

    // Gather the water first: no drops means nothing to draw at all.
    std::vector<float> data;
    data.reserve((simulator.drops().size() * 2 + simulator.beads().size()) * kFloatsPerWater);
    const float trail_width = 0.3f + 0.25f * (std::min)((std::max)(settings.trails, 0.0f), 1.0f);
    // Trails first, so drops sit on top of the streak they leave.
    for (const Bead& b : simulator.beads()) {
        const float left = (std::min)((std::max)(b.life / b.max_life, 0.0f), 1.0f);
        if (left > 0.0f && b.radius > 0.0f) // streaks thin as they dry
            data.insert(data.end(), {b.u0, b.v0, b.u, b.v, b.radius * (0.5f + 0.5f * left),
                                     0.7f * left, 1.0f, 0.0f});
    }
    for (const Drop& d : simulator.drops()) {
        const float water = (std::min)((std::max)(d.water, 0.0f), 1.0f);
        if (!(water > 0.0f && d.radius > 0.0f))
            continue;
        float tail_u = d.u, tail_v = d.v;
        if (d.sliding) {
            // The streak still being laid down, from its start to the drop.
            data.insert(data.end(),
                        {d.tu, d.tv, d.u, d.v, d.radius * trail_width, 0.7f, 1.0f, 0.0f});
            // A running drop stretches into a teardrop along its path.
            const float speed = std::sqrt(d.vu * d.vu + d.vv * d.vv);
            if (speed > 1e-5f) {
                const float tail = (std::min)(speed * 0.06f, d.radius * 1.2f);
                tail_u = d.u - d.vu / speed * tail;
                tail_v = d.v - d.vv / speed * tail;
            }
        }
        data.insert(data.end(), {tail_u, tail_v, d.u, d.v, d.radius, water, 0.0f, 0.0f});
    }
    const auto count = static_cast<std::uint32_t>(data.size() / kFloatsPerWater);
    if (count == 0)
        return true;

    D3D11_TEXTURE2D_DESC desc{};
    back_buffer->GetDesc(&desc);
    if (desc.SampleDesc.Count != 1 || desc.ArraySize != 1 ||
        view_format(desc.Format) == DXGI_FORMAT_UNKNOWN ||
        !(desc.BindFlags & D3D11_BIND_RENDER_TARGET))
        return fail("lens rain: unsupported back buffer");
    if (rect.width == 0 || rect.height == 0)
        rect = {0, 0, desc.Width, desc.Height};
    if (rect.x >= desc.Width || rect.y >= desc.Height)
        return fail("lens rain: picture outside the back buffer");
    rect.width = (std::min)(rect.width, desc.Width - rect.x);
    rect.height = (std::min)(rect.height, desc.Height - rect.y);
    const std::uint32_t water_width = (std::max)(1u, rect.width / 2);
    const std::uint32_t water_height = (std::max)(1u, rect.height / 2);

    if (!ensure_shaders(device) ||
        !ensure_targets(device, back_buffer, desc, water_width, water_height) ||
        !ensure_instances(device, count))
        return false;

    // The back-buffer view is made per frame so no reference to the swap
    // chain's buffer outlives the frame (that would block ResizeBuffers).
    Ptr<ID3D11RenderTargetView> back_target;
    D3D11_RENDER_TARGET_VIEW_DESC target{};
    target.Format = view_format(desc.Format);
    target.ViewDimension = D3D11_RTV_DIMENSION_TEXTURE2D;
    if (FAILED(device->CreateRenderTargetView(back_buffer, &target, &back_target)))
        return fail("lens rain: back-buffer view");

    StateScope scope(context);

    D3D11_MAPPED_SUBRESOURCE mapped{};
    if (FAILED(context->Map(instances_.Get(), 0, D3D11_MAP_WRITE_DISCARD, 0, &mapped)))
        return fail("lens rain: map drops");
    std::memcpy(mapped.pData, data.data(),
                (std::min<std::size_t>)(data.size(), instance_capacity_ * kFloatsPerWater) * 4);
    context->Unmap(instances_.Get(), 0);

    Constants constants{};
    constants.a[0] = static_cast<float>(rect.width) / static_cast<float>(rect.height);
    constants.a[1] = (std::min)((std::max)(settings.refraction, 0.0f), 2.0f);
    constants.a[2] = (std::min)((std::max)(settings.softness, 0.0f), 1.0f);
    constants.a[3] = static_cast<float>(scene_mips_ - 1);
    constants.b[0] = static_cast<float>(rect.x);
    constants.b[1] = static_cast<float>(rect.y);
    constants.b[2] = static_cast<float>(rect.width);
    constants.b[3] = static_cast<float>(rect.height);
    constants.c[0] = static_cast<float>(desc.Width);
    constants.c[1] = static_cast<float>(desc.Height);
    if (FAILED(context->Map(constants_.Get(), 0, D3D11_MAP_WRITE_DISCARD, 0, &mapped)))
        return fail("lens rain: map constants");
    std::memcpy(mapped.pData, &constants, sizeof(constants));
    context->Unmap(constants_.Get(), 0);

    // Scene copy with a short mip chain for the softened view under water.
    context->CopySubresourceRegion(scene_.Get(), 0, 0, 0, 0, back_buffer, 0, nullptr);
    context->GenerateMips(scene_view_.Get());

    ID3D11ShaderResourceView* no_views[2] = {};
    ID3D11Buffer* constants_buffer = constants_.Get();
    context->IASetInputLayout(nullptr);
    context->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    ID3D11Buffer* no_buffer = nullptr;
    UINT zero = 0;
    context->IASetVertexBuffers(0, 1, &no_buffer, &zero, &zero);
    context->GSSetShader(nullptr, nullptr, 0);
    context->HSSetShader(nullptr, nullptr, 0);
    context->DSSetShader(nullptr, nullptr, 0);
    context->RSSetState(raster_.Get());
    context->OMSetDepthStencilState(no_depth_.Get(), 0);
    context->VSSetConstantBuffers(0, 1, &constants_buffer);
    context->PSSetConstantBuffers(0, 1, &constants_buffer);

    // Pass 1: water map.
    const float clear[4] = {0.0f, 0.0f, 0.0f, 0.0f};
    ID3D11RenderTargetView* water_target = water_target_.Get();
    context->PSSetShaderResources(0, 2, no_views);
    context->OMSetRenderTargets(1, &water_target, nullptr);
    context->ClearRenderTargetView(water_target, clear);
    D3D11_VIEWPORT viewport{
        0.0f, 0.0f, static_cast<float>(water_width), static_cast<float>(water_height), 0.0f, 1.0f};
    context->RSSetViewports(1, &viewport);
    context->OMSetBlendState(additive_.Get(), nullptr, 0xffffffffu);
    ID3D11ShaderResourceView* instance_view = instances_view_.Get();
    context->VSSetShaderResources(0, 1, &instance_view);
    context->VSSetShader(water_vs_.Get(), nullptr, 0);
    context->PSSetShader(water_ps_.Get(), nullptr, 0);
    context->DrawInstanced(6, (std::min)(count, instance_capacity_), 0, 0);

    // Pass 2: refract the scene under the water, inside the picture only.
    ID3D11RenderTargetView* back = back_target.Get();
    context->OMSetRenderTargets(1, &back, nullptr);
    viewport = {static_cast<float>(rect.x),
                static_cast<float>(rect.y),
                static_cast<float>(rect.width),
                static_cast<float>(rect.height),
                0.0f,
                1.0f};
    context->RSSetViewports(1, &viewport);
    context->OMSetBlendState(nullptr, nullptr, 0xffffffffu);
    ID3D11ShaderResourceView* views[2] = {scene_view_.Get(), water_view_.Get()};
    ID3D11SamplerState* sampler = linear_.Get();
    context->PSSetShaderResources(0, 2, views);
    context->PSSetSamplers(0, 1, &sampler);
    context->VSSetShader(screen_vs_.Get(), nullptr, 0);
    context->PSSetShader(composite_ps_.Get(), nullptr, 0);
    context->Draw(3, 0);

    // Unbind our views before the saved state comes back.
    context->PSSetShaderResources(0, 2, no_views);
    ID3D11ShaderResourceView* no_view = nullptr;
    context->VSSetShaderResources(0, 1, &no_view);
    return true;
}

} // namespace lens_rain
