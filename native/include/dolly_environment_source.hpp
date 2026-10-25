#pragma once
#include <array>
#include <cstddef>
#include <cstdint>
#include <d3d11_1.h>

namespace dolly::environment {
// Shared with the existing CreatePixelShader observer. Metadata belongs to the
// COM object, never a pointer-keyed cache that could outlive a shader.
inline constexpr GUID PixelBytesKey = {
    0x4fa14e83, 0xd077, 0x4773, {0x9d, 0x31, 0x31, 0x63, 0xd1, 0x5f, 0x17, 0x82}};
struct ShaderContract {
    std::array<unsigned char, 32> sha256;
    unsigned bytes, static_id, dynamic_id, srv_slot, view_cb_slot, post_cb_slot;
};
enum class SourceState : unsigned {
    NoContext, NoShader, UnknownShader, DeferredContext, MissingTexture,
    UnsupportedTexture, UnsupportedViewport, MissingConstants,
    UnsupportedConstants, Candidate
};
struct ConstantBinding {
    std::uint64_t identity = 0;
    unsigned bytes = 0, first_constant = 0, num_constants = 0;
};
struct SourceObservation {
    SourceState state = SourceState::NoContext;
    // 0 not inspected, 1 missing bytecode, 2 unreviewed size, 3 read/hash
    // failure, 4 hash mismatch, 5 exact match. These distinguish an early
    // shader missed by the creation hook from different live shader bytecode.
    unsigned identity_state = 0, metadata_bytes = 0;
    std::array<unsigned char, 32> sha256{};
    unsigned static_id = 0, dynamic_id = 0;
    std::uint64_t context = 0, shader = 0, resource = 0;
    D3D11_TEXTURE2D_DESC texture{};
    D3D11_SHADER_RESOURCE_VIEW_DESC srv{};
    D3D11_VIEWPORT viewport{};
    ConstantBinding view, post;
};
bool shader_digest(const void* data, std::size_t bytes,
                   std::array<unsigned char, 32>& output) noexcept;
bool retain_reviewed_shader(ID3D11PixelShader*, const void*, std::size_t,
                            const ShaderContract*, std::size_t) noexcept;
// Only getters and lifetime-bound shader metadata; no draws, copies, maps,
// shader replacement, state changes or retained GPU resources. Candidate means
// an exact reviewed shader and compatible bindings, NOT radiometric HDR proof.
SourceObservation inspect_source(ID3D11DeviceContext* context,
                                  const ShaderContract* contracts,
                                  std::size_t count) noexcept;
}
