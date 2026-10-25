#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <bcrypt.h>
#include <wrl/client.h>
#include <vector>
#include <cmath>
#include "dolly_environment_source.hpp"

namespace dolly::environment {
using Microsoft::WRL::ComPtr;
namespace {
constexpr GUID DigestKey = {
    0x148b6e72, 0xa8e5, 0x4dd8, {0x91, 0x70, 0xea, 0x80, 0x62, 0x3a, 0xa1, 0x05}};
struct Digest { unsigned bytes; std::array<unsigned char, 32> sha256; };
template<class T> std::uint64_t identity(T* object) noexcept {
    return static_cast<std::uint64_t>(reinterpret_cast<std::uintptr_t>(object));
}
const ShaderContract* identify(ID3D11PixelShader* shader, const ShaderContract* contracts,
                               std::size_t count, SourceObservation& out) {
    if (!contracts || !count || count > 64)
        return nullptr;
    Digest digest{};
    UINT size = sizeof(digest);
    if (FAILED(shader->GetPrivateData(DigestKey, &size, &digest)) || size != sizeof(digest)) {
        size = 0;
        out.identity_state = 1;
        if (FAILED(shader->GetPrivateData(PixelBytesKey, &size, nullptr)))
            return nullptr;
        out.metadata_bytes = size;
        out.identity_state = 2;
        if (size < 36 || size > 65536)
            return nullptr;
        // Bound allocation to a reviewed size; arbitrary game shaders need no hash.
        bool known_size = false;
        for (std::size_t i = 0; i < count; ++i)
            known_size |= contracts[i].bytes == size;
        if (!known_size)
            return nullptr;
        std::vector<unsigned char> bytes(size);
        digest.bytes = size;
        out.identity_state = 3;
        if (FAILED(shader->GetPrivateData(PixelBytesKey, &size, bytes.data())) || size != digest.bytes ||
            !shader_digest(bytes.data(), bytes.size(), digest.sha256))
            return nullptr;
        shader->SetPrivateData(DigestKey, sizeof(digest), &digest);
    }
    out.metadata_bytes = digest.bytes;
    out.sha256 = digest.sha256;
    out.identity_state = 4;
    for (std::size_t i = 0; i < count; ++i) {
        const auto& candidate = contracts[i];
        if (candidate.bytes == digest.bytes && candidate.sha256 == digest.sha256 &&
            candidate.srv_slot < D3D11_COMMONSHADER_INPUT_RESOURCE_SLOT_COUNT &&
            candidate.view_cb_slot < D3D11_COMMONSHADER_CONSTANT_BUFFER_API_SLOT_COUNT &&
            candidate.post_cb_slot < D3D11_COMMONSHADER_CONSTANT_BUFFER_API_SLOT_COUNT &&
            candidate.view_cb_slot != candidate.post_cb_slot) {
            out.identity_state = 5;
            return &candidate;
        }
    }
    return nullptr;
}
bool constants(ID3D11DeviceContext* context, ID3D11DeviceContext1* context1,
               unsigned slot, unsigned needed, ConstantBinding& out) {
    ComPtr<ID3D11Buffer> buffer;
    UINT first = 0, count = 4096;
    if (context1)
        context1->PSGetConstantBuffers1(slot, 1, &buffer, &first, &count);
    else
        context->PSGetConstantBuffers(slot, 1, &buffer);
    if (!buffer)
        return false;
    D3D11_BUFFER_DESC desc{};
    buffer->GetDesc(&desc);
    out = {identity(buffer.Get()), desc.ByteWidth, first, count};
    // D3D11.1 can bind a window of a larger buffer. CB register 18 is relative
    // to that window, not the beginning of the underlying resource.
    return (desc.BindFlags & D3D11_BIND_CONSTANT_BUFFER) && count >= needed &&
           std::uint64_t(first) * 16 + std::uint64_t(needed) * 16 <= desc.ByteWidth;
}
}
bool shader_digest(const void* data, std::size_t bytes,
                   std::array<unsigned char, 32>& output) noexcept {
    output = {};
    if (!data || !bytes || bytes > 65536)
        return false;
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) < 0)
        return false;
    const auto status = BCryptHash(algorithm, nullptr, 0,
        const_cast<PUCHAR>(static_cast<const unsigned char*>(data)), static_cast<ULONG>(bytes),
        output.data(), static_cast<ULONG>(output.size()));
    BCryptCloseAlgorithmProvider(algorithm, 0);
    return status >= 0;
}
bool retain_reviewed_shader(ID3D11PixelShader* shader,const void* bytes,std::size_t length,
                            const ShaderContract* contracts,std::size_t count) noexcept {
    if (!shader || !bytes || !contracts || !count || count>64 || length<36 || length>65536) return false;
    bool size_known=false;
    for (std::size_t i=0;i<count;++i) size_known|=contracts[i].bytes==length;
    if (!size_known) return false;
    std::array<unsigned char,32> digest{};
    if (!shader_digest(bytes,length,digest)) return false;
    for (std::size_t i=0;i<count;++i)
        if (contracts[i].bytes==length && contracts[i].sha256==digest)
            return SUCCEEDED(shader->SetPrivateData(PixelBytesKey,static_cast<UINT>(length),bytes));
    return false;
}
SourceObservation inspect_source(ID3D11DeviceContext* context,
                                  const ShaderContract* contracts,
                                  std::size_t count) noexcept {
    SourceObservation out;
    if (!context)
        return out;
    out.context = identity(context);
    try {
        ComPtr<ID3D11PixelShader> pixel;
        context->PSGetShader(&pixel, nullptr, nullptr);
        out.state = SourceState::NoShader;
        if (!pixel)
            return out;
        out.shader = identity(pixel.Get());
        const auto* contract = identify(pixel.Get(), contracts, count, out);
        out.state = SourceState::UnknownShader;
        if (!contract)
            return out;
        out.static_id = contract->static_id;
        out.dynamic_id = contract->dynamic_id;
        out.state = SourceState::DeferredContext;
        if (context->GetType() != D3D11_DEVICE_CONTEXT_IMMEDIATE)
            return out;
        ComPtr<ID3D11ShaderResourceView> srv;
        context->PSGetShaderResources(contract->srv_slot, 1, &srv);
        out.state = SourceState::MissingTexture;
        if (!srv)
            return out;
        srv->GetDesc(&out.srv);
        ComPtr<ID3D11Resource> resource;
        srv->GetResource(&resource);
        ComPtr<ID3D11Texture2D> texture;
        out.state = SourceState::UnsupportedTexture;
        if (!resource || FAILED(resource.As(&texture)))
            return out;
        out.resource = identity(resource.Get());
        texture->GetDesc(&out.texture);
        const auto& t = out.texture;
        const auto& s = out.srv;
        // The captured renderer uses typeless RGBA16 storage with a FLOAT SRV.
        // Accept that exact family pairing; reject other reinterpretations.
        const bool floating = s.Format == DXGI_FORMAT_R16G16B16A16_FLOAT ||
            s.Format == DXGI_FORMAT_R32G32B32A32_FLOAT || s.Format == DXGI_FORMAT_R11G11B10_FLOAT;
        const bool compatible = t.Format == s.Format ||
            (t.Format == DXGI_FORMAT_R16G16B16A16_TYPELESS && s.Format == DXGI_FORMAT_R16G16B16A16_FLOAT);
        if (!floating || !compatible || t.ArraySize != 1 || t.MipLevels != 1 ||
            t.SampleDesc.Count != 1 || t.Width == 0 || t.Height == 0 || t.Width > 16384 ||
            t.Height > 16384 || s.ViewDimension != D3D11_SRV_DIMENSION_TEXTURE2D ||
            s.Texture2D.MostDetailedMip != 0 || s.Texture2D.MipLevels != 1)
            return out;
        UINT viewport_count = 0;
        context->RSGetViewports(&viewport_count, nullptr);
        out.state = SourceState::UnsupportedViewport;
        if (viewport_count != 1)
            return out;
        context->RSGetViewports(&viewport_count, &out.viewport);
        const auto& v = out.viewport;
        if (viewport_count != 1 || v.TopLeftX != 0 || v.TopLeftY != 0 ||
            v.Width != static_cast<float>(t.Width) || v.Height != static_cast<float>(t.Height) ||
            v.MinDepth != 0 || v.MaxDepth != 1)
            return out;
        ComPtr<ID3D11DeviceContext1> context1;
        context->QueryInterface(IID_PPV_ARGS(&context1));
        const bool view_ok = constants(context, context1.Get(), contract->view_cb_slot, 19, out.view);
        const bool post_ok = constants(context, context1.Get(), contract->post_cb_slot, 1, out.post);
        out.state = (!out.view.identity || !out.post.identity) ? SourceState::MissingConstants :
            ((!view_ok || !post_ok) ? SourceState::UnsupportedConstants : SourceState::Candidate);
    } catch (...) {
        out.state = SourceState::UnknownShader;
    }
    return out;
}
}
