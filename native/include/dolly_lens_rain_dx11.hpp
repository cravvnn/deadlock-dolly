#pragma once
// DX11 drawing for lens rain. Draws the drops of a lens_rain::Simulator onto
// a presented back buffer: a half-resolution water map (thickness and slope
// per pixel) is built from the drops, then one full-screen pass refracts and
// softens the scene under the water. Only the picture rectangle is written,
// so letterbox bars are never touched, and pixels without water keep their
// exact value.
//
// The pass saves and restores every piece of device-context state it
// changes. Do not wrap this header in a namespace named `dolly`.

#include <d3d11.h>
#include <wrl/client.h>

#include <cstdint>

#include "dolly_lens_rain.hpp"

namespace lens_rain {

// Picture area inside the back buffer, in pixels. Zero width or height means
// the whole back buffer.
struct PictureRect {
    std::uint32_t x = 0, y = 0, width = 0, height = 0;
};

class Renderer {
public:
    // Returns false (and leaves the back buffer unchanged) when the pass
    // cannot run, for example an unsupported back-buffer format.
    bool render(ID3D11Device* device, ID3D11DeviceContext* context, ID3D11Texture2D* back_buffer,
                const Simulator& simulator, const Settings& settings, PictureRect rect = {});
    void release();
    const char* last_error() const { return error_; }

private:
    template <typename T> using Ptr = Microsoft::WRL::ComPtr<T>;

    bool ensure_shaders(ID3D11Device* device);
    bool ensure_targets(ID3D11Device* device, ID3D11Texture2D* back_buffer,
                        const D3D11_TEXTURE2D_DESC& desc, std::uint32_t water_width,
                        std::uint32_t water_height);
    bool ensure_instances(ID3D11Device* device, std::uint32_t count);
    bool fail(const char* message);

    Ptr<ID3D11Device> device_;
    Ptr<ID3D11VertexShader> water_vs_, screen_vs_;
    Ptr<ID3D11PixelShader> water_ps_, composite_ps_;
    Ptr<ID3D11BlendState> additive_;
    Ptr<ID3D11RasterizerState> raster_;
    Ptr<ID3D11DepthStencilState> no_depth_;
    Ptr<ID3D11SamplerState> linear_;
    Ptr<ID3D11Buffer> constants_;
    Ptr<ID3D11Buffer> instances_;
    Ptr<ID3D11ShaderResourceView> instances_view_;
    std::uint32_t instance_capacity_ = 0;

    Ptr<ID3D11Texture2D> scene_;
    Ptr<ID3D11ShaderResourceView> scene_view_;
    Ptr<ID3D11Texture2D> water_;
    Ptr<ID3D11RenderTargetView> water_target_;
    Ptr<ID3D11ShaderResourceView> water_view_;
    D3D11_TEXTURE2D_DESC desc_{};
    std::uint32_t water_width_ = 0, water_height_ = 0;
    std::uint32_t scene_mips_ = 1;
    const char* error_ = "";
};

} // namespace lens_rain
