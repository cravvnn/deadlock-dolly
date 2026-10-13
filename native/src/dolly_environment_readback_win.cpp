#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <cstring>
#include <cmath>
#include "dolly_environment_readback.hpp"

namespace dolly::environment {
using Microsoft::WRL::ComPtr;
void HdrReadback::release_gpu() noexcept {
    done_.Reset(); staging_.Reset(); context_.Reset();
    for (auto& buffer:constants_) buffer.Reset();
}
ReadbackState HdrReadback::fail() noexcept {
    release_gpu(); std::vector<unsigned char>().swap(pixels_);
    state_ = ReadbackState::Failed; return state_;
}
void HdrReadback::cancel() noexcept {
    release_gpu(); std::vector<unsigned char>().swap(pixels_);
    source_ = {}; exposure_ = {}; polls_ = 0; started_ = 0; state_ = ReadbackState::Idle;
}
std::optional<HdrFrame> HdrReadback::take() noexcept {
    if (state_ != ReadbackState::Ready) return std::nullopt;
    HdrFrame frame{source_, exposure_, std::move(pixels_)};
    cancel();
    return frame;
}
bool HdrReadback::begin(ID3D11DeviceContext* context, const ShaderContract* contracts,
                        std::size_t count) noexcept {
    if (state_ != ReadbackState::Idle) return false;
    try {
        source_ = inspect_source(context, contracts, count);
        const auto& t = source_.texture;
        if (source_.state != SourceState::Candidate ||
            source_.srv.Format != DXGI_FORMAT_R16G16B16A16_FLOAT ||
            t.Width > 4096 || t.Height > 4096) { fail(); return false; }
        // Exact validated binding, acquired on the same immediate/render thread.
        const ShaderContract* contract = nullptr;
        for (std::size_t i=0; i<count; ++i)
            if (contracts[i].sha256 == source_.sha256) { contract=&contracts[i]; break; }
        if (!contract) { fail(); return false; }
        ComPtr<ID3D11ShaderResourceView> view;
        context->PSGetShaderResources(contract->srv_slot,1,&view);
        ComPtr<ID3D11Resource> resource;
        if (view) view->GetResource(&resource);
        if (reinterpret_cast<std::uintptr_t>(resource.Get()) != source_.resource) { fail(); return false; }
        ComPtr<ID3D11Device> device; context->GetDevice(&device);
        auto desc=t; desc.Usage=D3D11_USAGE_STAGING; desc.BindFlags=0;
        desc.CPUAccessFlags=D3D11_CPU_ACCESS_READ; desc.MiscFlags=0;
        D3D11_QUERY_DESC query{D3D11_QUERY_EVENT,0};
        if (FAILED(device->CreateTexture2D(&desc,nullptr,&staging_)) ||
            FAILED(device->CreateQuery(&query,&done_))) { fail(); return false; }
        const unsigned slots[]={contract->view_cb_slot,contract->post_cb_slot};
        const ConstantBinding bindings[]={source_.view,source_.post};
        const unsigned registers[]={18,0};
        std::array<ComPtr<ID3D11Buffer>,2> inputs;
        D3D11_BUFFER_DESC buffer_desc{};buffer_desc.ByteWidth=16;
        buffer_desc.Usage=D3D11_USAGE_STAGING;buffer_desc.CPUAccessFlags=D3D11_CPU_ACCESS_READ;
        for (unsigned i=0;i<2;++i) {
            context->PSGetConstantBuffers(slots[i],1,&inputs[i]);
            if (reinterpret_cast<std::uintptr_t>(inputs[i].Get())!=bindings[i].identity ||
                FAILED(device->CreateBuffer(&buffer_desc,nullptr,&constants_[i]))) { fail(); return false; }
        }
        // Allocate before queuing GPU work. Hard ceiling: 128MiB CPU + staging.
        pixels_.resize(std::size_t(t.Width)*t.Height*8);
        context_=context; polls_=0; started_=GetTickCount64();
        context->CopyResource(staging_.Get(),resource.Get());
        for (unsigned i=0;i<2;++i) {
            const unsigned offset=(bindings[i].first_constant+registers[i])*16;
            D3D11_BOX box{offset,0,0,offset+16,1,1};
            context->CopySubresourceRegion(constants_[i].Get(),0,0,0,0,inputs[i].Get(),0,&box);
        }
        context->End(done_.Get()); state_=ReadbackState::Pending;
        return true;
    } catch (...) { fail(); return false; }
}
ReadbackState HdrReadback::poll(ID3D11DeviceContext* context) noexcept {
    if (state_ != ReadbackState::Pending) return state_;
    if (!context || context!=context_.Get() || ++polls_>120 || GetTickCount64()-started_>5000)
        return fail();
    BOOL finished=FALSE;
    const HRESULT query=context->GetData(done_.Get(),&finished,sizeof(finished),D3D11_ASYNC_GETDATA_DONOTFLUSH);
    if (FAILED(query)) return fail();
    if (query==S_FALSE || !finished) return state_;
    D3D11_MAPPED_SUBRESOURCE mapped{};
    const HRESULT result=context->Map(staging_.Get(),0,D3D11_MAP_READ,D3D11_MAP_FLAG_DO_NOT_WAIT,&mapped);
    if (result==DXGI_ERROR_WAS_STILL_DRAWING) return state_;
    if (FAILED(result)) return fail();
    const std::size_t row=std::size_t(source_.texture.Width)*8;
    const bool valid=mapped.pData && mapped.RowPitch>=row;
    if (valid)
        for (unsigned y=0;y<source_.texture.Height;++y)
            std::memcpy(pixels_.data()+y*row,static_cast<const unsigned char*>(mapped.pData)+std::size_t(y)*mapped.RowPitch,row);
    context->Unmap(staging_.Get(),0);
    if (!valid) return fail();
    for (unsigned i=0;i<2;++i) {
        mapped={};
        const auto status=context->Map(constants_[i].Get(),0,D3D11_MAP_READ,D3D11_MAP_FLAG_DO_NOT_WAIT,&mapped);
        if (status==DXGI_ERROR_WAS_STILL_DRAWING) return state_;
        if (FAILED(status)) return fail();
        const bool readable=mapped.pData!=nullptr;
        if (readable) std::memcpy(&exposure_[i],mapped.pData,sizeof(float));
        context->Unmap(constants_[i].Get(),0);
        if (!readable || !std::isfinite(exposure_[i]) || exposure_[i]<=0) return fail();
    }
    release_gpu(); state_=ReadbackState::Ready; return state_;
}
}
