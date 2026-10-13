#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <d3dcompiler.h>
#include <wrl/client.h>
#include <cstdio>
#include <cstdlib>
#include <vector>
#include <cstring>
#include <algorithm>
#include "dolly_environment_source.hpp"
#include "dolly_environment_probe.hpp"
#include "dolly_environment_readback.hpp"
#include "dolly_environment_frame.hpp"
#include "dolly_environment_capture.hpp"
#include "dolly_editor.hpp"
#include "dolly_capabilities.hpp"
static_assert(dolly::kEditorCameraListOffset + sizeof(dolly::EditorCameraList) <= dolly::environment::ProbeRequestOffset);
static_assert(dolly::kEditorFramingGridOffset + sizeof(dolly::EditorFramingGridConfig) <= dolly::environment::ProbeRequestOffset);
static_assert(dolly::kCapabilitiesOffset + sizeof(dolly::CapabilitySnapshot) <= dolly::environment::ProbeRequestOffset);
using Microsoft::WRL::ComPtr;
using namespace dolly::environment;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAILED %d: %s\n", __LINE__, #x); std::exit(1); } } while (0)
int main(int argc, char** argv) {
    CHECK(argc==1 || argc==2); // optional synthetic WARP transfer fixture
    dolly::EditorEnvironmentActionConfig action{};
    std::memcpy(action.magic,"DLYENVA1",8); action.abi=1; action.flags=1; action.binding=0x77;
    CHECK(dolly::valid_environment_action(action));
    action.binding=0x76; CHECK(!dolly::valid_environment_action(action));
    action.binding=0x11; CHECK(!dolly::valid_environment_action(action));
    action.binding=0x100; CHECK(!dolly::valid_environment_action(action));
    action.binding=0; CHECK(dolly::valid_environment_action(action));
    action.flags=2; CHECK(!dolly::valid_environment_action(action));
    static_assert(dolly::kEditorEnvironmentActionOffset >= ProbeStatusOffset+sizeof(ProbeStatus));
    static_assert(dolly::kEditorEnvironmentActionOffset+sizeof(action)<=dolly::kMappingBytes);
    std::array<unsigned char, 32> digest;
    CHECK(shader_digest("abc", 3, digest));
    const std::array<unsigned char, 32> abc = {0xba,0x78,0x16,0xbf,0x8f,0x01,0xcf,0xea,
        0x41,0x41,0x40,0xde,0x5d,0xae,0x22,0x23,0xb0,0x03,0x61,0xa3,0x96,0x17,0x7a,0x9c,
        0xb4,0x10,0xff,0x61,0xf2,0x00,0x15,0xad};
    CHECK(digest == abc);
    CHECK(!shader_digest(nullptr, 3, digest));
    CHECK(!shader_digest("abc", 65537, digest));
    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> context;
    CHECK(SUCCEEDED(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_WARP, nullptr, 0,
        nullptr, 0, D3D11_SDK_VERSION, &device, nullptr, &context)));
    const char source[] = "float4 main(float4 p:SV_Position):SV_Target{return float4(2,4,8,1);}";
    ComPtr<ID3DBlob> code, errors;
    CHECK(SUCCEEDED(D3DCompile(source, sizeof(source)-1, nullptr, nullptr, nullptr,
        "main", "ps_5_0", 0, 0, &code, &errors)));
    ShaderContract contract{};
    CHECK(shader_digest(code->GetBufferPointer(), code->GetBufferSize(), contract.sha256));
    contract.bytes = static_cast<unsigned>(code->GetBufferSize());
    contract.view_cb_slot = 1;
    contract.post_cb_slot = 2;
    contract.static_id = 1;
    contract.dynamic_id = 7;
    CHECK(inspect_source(nullptr, &contract, 1).state == SourceState::NoContext);
    auto inspect = [&] { return inspect_source(context.Get(), &contract, 1); };
    CHECK(inspect().state == SourceState::NoShader);
    ComPtr<ID3D11PixelShader> pixel;
    CHECK(SUCCEEDED(device->CreatePixelShader(code->GetBufferPointer(), code->GetBufferSize(), nullptr, &pixel)));
    context->PSSetShader(pixel.Get(), nullptr, 0);
    CHECK(inspect().state == SourceState::UnknownShader);
    CHECK(inspect().identity_state == 1);
    auto unreviewed=contract;unreviewed.sha256[0]^=1;
    CHECK(!retain_reviewed_shader(pixel.Get(),code->GetBufferPointer(),contract.bytes,&unreviewed,1));
    CHECK(inspect().identity_state==1);
    CHECK(!retain_reviewed_shader(pixel.Get(),code->GetBufferPointer(),contract.bytes,&contract,65));
    CHECK(retain_reviewed_shader(pixel.Get(),code->GetBufferPointer(),contract.bytes,&contract,1));
    auto wrong = contract;
    wrong.sha256[0] ^= 1;
    CHECK(inspect_source(context.Get(), &wrong, 1).state == SourceState::UnknownShader);
    CHECK(inspect_source(context.Get(), &wrong, 1).identity_state == 4);
    CHECK(inspect_source(context.Get(), nullptr, 1).state == SourceState::UnknownShader);
    CHECK(inspect_source(context.Get(), &contract, 65).state == SourceState::UnknownShader);
    CHECK(inspect().state == SourceState::MissingTexture);
    CHECK(inspect().identity_state == 5);
    ComPtr<ID3D11Texture2D> texture;
    ComPtr<ID3D11ShaderResourceView> srv;
    auto bind_texture = [&](DXGI_FORMAT format, UINT mips = 1, DXGI_FORMAT view_format = DXGI_FORMAT_UNKNOWN, UINT width = 64) {
        srv.Reset(); texture.Reset();
        D3D11_TEXTURE2D_DESC desc{};
        desc.Width = width; desc.Height = 32;
        desc.MipLevels = mips; desc.ArraySize = 1;
        desc.Format = format; desc.SampleDesc.Count = 1;
        desc.BindFlags = D3D11_BIND_SHADER_RESOURCE;
        CHECK(SUCCEEDED(device->CreateTexture2D(&desc, nullptr, &texture)));
        D3D11_SHADER_RESOURCE_VIEW_DESC view_desc{};
        view_desc.Format = view_format;
        view_desc.ViewDimension = D3D11_SRV_DIMENSION_TEXTURE2D;
        view_desc.Texture2D.MipLevels = mips;
        CHECK(SUCCEEDED(device->CreateShaderResourceView(texture.Get(),
            view_format == DXGI_FORMAT_UNKNOWN ? nullptr : &view_desc, &srv)));
        auto raw = srv.Get();
        context->PSSetShaderResources(0, 1, &raw);
    };
    bind_texture(DXGI_FORMAT_R8G8B8A8_UNORM);
    CHECK(inspect().state == SourceState::UnsupportedTexture);
    bind_texture(DXGI_FORMAT_R16G16B16A16_FLOAT, 2);
    CHECK(inspect().state == SourceState::UnsupportedTexture);
    bind_texture(DXGI_FORMAT_R16G16B16A16_FLOAT);
    CHECK(inspect().state == SourceState::UnsupportedViewport);
    D3D11_VIEWPORT viewport{0,0,64,32,0,1};
    context->RSSetViewports(1, &viewport);
    CHECK(inspect().state == SourceState::MissingConstants);
    ComPtr<ID3D11Buffer> view, post, tinyBuffer;
    D3D11_BUFFER_DESC desc{};
    desc.ByteWidth = 1024;
    desc.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
    CHECK(SUCCEEDED(device->CreateBuffer(&desc, nullptr, &view)));
    CHECK(SUCCEEDED(device->CreateBuffer(&desc, nullptr, &post)));
    desc.ByteWidth = 16;
    CHECK(SUCCEEDED(device->CreateBuffer(&desc, nullptr, &tinyBuffer)));
    ID3D11Buffer* buffers[] = {view.Get(), post.Get()};
    context->PSSetConstantBuffers(1, 2, buffers);
    auto candidate = inspect();
    CHECK(candidate.state == SourceState::Candidate);
    CHECK(candidate.static_id == 1 && candidate.dynamic_id == 7);
    CHECK(candidate.texture.Width == 64 && candidate.texture.Height == 32);
    CHECK(candidate.view.bytes == 1024 && candidate.view.first_constant == 0);
    bind_texture(DXGI_FORMAT_R16G16B16A16_TYPELESS, 1, DXGI_FORMAT_R16G16B16A16_FLOAT);
    CHECK(inspect().state == SourceState::Candidate);
    CHECK(inspect().texture.Format == DXGI_FORMAT_R16G16B16A16_TYPELESS);
    CHECK(inspect().srv.Format == DXGI_FORMAT_R16G16B16A16_FLOAT);
    bind_texture(DXGI_FORMAT_R16G16B16A16_TYPELESS, 1, DXGI_FORMAT_R16G16B16A16_UNORM);
    CHECK(inspect().state == SourceState::UnsupportedTexture);
    bind_texture(DXGI_FORMAT_R16G16B16A16_TYPELESS, 1, DXGI_FORMAT_R16G16B16A16_UINT);
    CHECK(inspect().state == SourceState::UnsupportedTexture);
    bind_texture(DXGI_FORMAT_R16G16B16A16_TYPELESS, 2, DXGI_FORMAT_R16G16B16A16_FLOAT);
    CHECK(inspect().state == SourceState::UnsupportedTexture);
    // Real GPU readback: odd width, padded upload rows, raw half bits including
    // >1, negative and NaN values must survive without clamping or conversion.
    bind_texture(DXGI_FORMAT_R16G16B16A16_TYPELESS, 1, DXGI_FORMAT_R16G16B16A16_FLOAT, 63);
    viewport.Width = 63; context->RSSetViewports(1, &viewport);
    constexpr unsigned rowBytes=63*8, uploadPitch=rowBytes+24;
    std::vector<unsigned char> upload(uploadPitch*32,0xcd), expected(rowBytes*32);
    const unsigned short halfBits[] = {0x4873,0xbc00,0x7e01,0x3c00};
    for (unsigned y=0;y<32;++y) for (unsigned x=0;x<63;++x) {
        std::memcpy(upload.data()+y*uploadPitch+x*8,halfBits,8);
        std::memcpy(expected.data()+y*rowBytes+x*8,halfBits,8);
    }
    context->UpdateSubresource(texture.Get(),0,nullptr,upload.data(),uploadPitch,0);
    std::array<float,256> constants{};
    constants[18*4]=1.25f;context->UpdateSubresource(view.Get(),0,nullptr,constants.data(),0,0);
    constants[0]=1.865f;context->UpdateSubresource(post.Get(),0,nullptr,constants.data(),0,0);
    HdrReadback readback;
    CHECK(readback.state()==ReadbackState::Idle && readback.pixels().empty());
    CHECK(!readback.take());
    CHECK(!readback.begin(nullptr,&contract,1));
    CHECK(readback.state()==ReadbackState::Failed);
    CHECK(!readback.begin(context.Get(),&contract,1)); // explicit reset required
    readback.cancel();
    CHECK(readback.begin(context.Get(),&contract,1));
    CHECK(readback.pixels().empty()); // never expose incomplete pixels
    CHECK(!readback.take());
    CHECK(!readback.begin(context.Get(),&contract,1)); // no replacement in flight
    constants.fill(4.0f);
    context->UpdateSubresource(view.Get(),0,nullptr,constants.data(),0,0);
    context->UpdateSubresource(post.Get(),0,nullptr,constants.data(),0,0);
    context->Flush(); // test driver submission only; production never flushes
    for (unsigned i=0;i<100 && readback.state()==ReadbackState::Pending;++i) {
        readback.poll(context.Get()); Sleep(1);
    }
    CHECK(readback.state()==ReadbackState::Ready && readback.pixels()==expected);
    CHECK(readback.source().texture.Width==63);
    CHECK(readback.exposure()[0]==1.25f && readback.exposure()[1]==1.865f);
    CHECK(readback.poll(nullptr)==ReadbackState::Ready);
    CHECK(!readback.begin(context.Get(),&contract,1));
    const auto* allocation=readback.pixels().data();
    auto detached=readback.take();
    CHECK(detached && detached->pixels.data()==allocation && detached->pixels==expected);
    CHECK(readback.state()==ReadbackState::Idle && !readback.take());
    CHECK(detached->exposure[0]==1.25f && detached->exposure[1]==1.865f);
    std::array<unsigned char,16> token{}; token[0]=7;
    auto header=frame_header(*detached,token);
    CHECK(header && !std::memcmp(header->data(),"DLYHDR01",8));
    CHECK((*header)[8]==1 && (*header)[12]==88 && (*header)[16]==7);
    CHECK((*header)[32]==63 && (*header)[36]==32);
    CHECK(!std::memcmp(header->data()+56,contract.sha256.data(),32));
    CHECK(!frame_header(*detached,{}));
    auto originalExposure=detached->exposure;
    detached->exposure[0]=0; CHECK(!frame_header(*detached,token));
    detached->exposure=originalExposure;
    detached->pixels.pop_back(); CHECK(!frame_header(*detached,token));
    readback.cancel();
    CHECK(readback.pixels().empty() && readback.source().resource==0);
    ComPtr<ID3D11DeviceContext1> readbackContext1;
    CHECK(SUCCEEDED(context.As(&readbackContext1)));
    UINT readbackFirst=16,readbackCount=32;
    // Second readback uses finite RGB for the cross-language EXR export check.
    for (unsigned y=0;y<32;++y) for (unsigned x=0;x<63;++x) {
        const unsigned short blue=0x3800; // 0.5
        std::memcpy(upload.data()+y*uploadPitch+x*8+4,&blue,2);
    }
    context->UpdateSubresource(texture.Get(),0,nullptr,upload.data(),uploadPitch,0);
    constants.fill(0.0f);constants[(16+18)*4]=2.5f;
    context->UpdateSubresource(view.Get(),0,nullptr,constants.data(),0,0);
    readbackContext1->PSSetConstantBuffers1(1,1,buffers,&readbackFirst,&readbackCount);
    CHECK(readback.begin(context.Get(),&contract,1));
    context->Flush();
    for (unsigned i=0;i<100 && readback.state()==ReadbackState::Pending;++i) {
        readback.poll(context.Get());Sleep(1);
    }
    CHECK(readback.state()==ReadbackState::Ready);
    CHECK(readback.exposure()[0]==2.5f && readback.exposure()[1]==4.0f);
    {
        std::vector<unsigned char> wire(dolly::kMappingBytes,0x5a);
        CaptureRequest request{};std::memcpy(request.magic,"DLYHDRR1",8);
        request.sequence=2;request.abi=1;request.command=1;request.enabled=1;
        request.editor_pid=123;request.game_pid=456;request.camera_command=7;
        request.tick=15300;request.width=63;request.height=32;request.milliseconds=1000;request.token[0]=7;
        auto send=[&] {std::memcpy(wire.data()+CaptureRequestOffset,&request,sizeof(request));
            capture_worker(wire.data(),true,123,456,7,15300);};
        auto status=[&] {CaptureStatus s{};std::memcpy(&s,wire.data()+CaptureStatusOffset,sizeof(s));return s;};
        HANDLE transfer=CreateFileMappingW(INVALID_HANDLE_VALUE,nullptr,PAGE_READWRITE,0,88+63*32*8,
            L"Local\\DollyEnvironmentFrame-123-456-07000000000000000000000000000000");
        CHECK(transfer && GetLastError()!=ERROR_ALREADY_EXISTS);
        send();CHECK(capture_active() && status().state==1);
        capture_draw(context.Get(),&contract,1);context->Flush();
        for(unsigned i=0;i<100 && capture_active();++i) {
            capture_present(context.Get());capture_worker(wire.data(),true,123,456,7,15300);Sleep(1);
        }
        CHECK(status().state==4 && status().bytes==88+63*32*8 && !capture_active());
        auto* bytes=static_cast<const unsigned char*>(MapViewOfFile(transfer,FILE_MAP_READ,0,0,88+63*32*8));
        CHECK(bytes && !std::memcmp(bytes,"DLYHDR01",8));
        CHECK(bytes[16]==7 && bytes[32]==63 && bytes[36]==32);
        for(unsigned y=0;y<32;++y)CHECK(!std::memcmp(bytes+88+y*63*8,upload.data()+y*uploadPitch,63*8));
        UnmapViewOfFile(bytes);CloseHandle(transfer);
        // Same consumed command cannot rearm. Changed camera/ownership cancels.
        send();CHECK(!capture_active());
        ++request.command;request.sequence+=2;send();CHECK(capture_active());
        capture_worker(wire.data(),true,123,456,8,15300);CHECK(status().state==5 && !capture_active());
        capture_present(context.Get());
        ++request.command;request.sequence+=2;send();CHECK(capture_active());
        capture_worker(wire.data(),false,123,456,7,15300);CHECK(status().state==5);
        capture_present(context.Get());
        ++request.command;request.sequence+=2;request.width=4097;send();CHECK(status().state==6);
        capture_present(context.Get());
        CHECK(std::all_of(wire.begin(),wire.begin()+CaptureRequestOffset,[](auto b){return b==0x5a;}));
        CHECK(std::all_of(wire.begin()+CaptureStatusOffset+sizeof(CaptureStatus),wire.end(),[](auto b){return b==0x5a;}));
    }
    if (argc==2) {
        auto frame=readback.take(); CHECK(frame);
        const auto wire=frame_header(*frame,token); CHECK(wire);
        HANDLE output=CreateFileA(argv[1],GENERIC_WRITE,0,nullptr,CREATE_NEW,FILE_ATTRIBUTE_NORMAL,nullptr);
        CHECK(output!=INVALID_HANDLE_VALUE);
        DWORD written=0;
        CHECK(WriteFile(output,wire->data(),static_cast<DWORD>(wire->size()),&written,nullptr) && written==wire->size());
        CHECK(WriteFile(output,frame->pixels.data(),static_cast<DWORD>(frame->pixels.size()),&written,nullptr) && written==frame->pixels.size());
        CHECK(CloseHandle(output));
    }
    readback.cancel();
    context->PSSetConstantBuffers(1,2,buffers);
    // The zero value outside that CB window must now fail closed.
    CHECK(readback.begin(context.Get(),&contract,1));
    context->Flush();
    for (unsigned i=0;i<100 && readback.state()==ReadbackState::Pending;++i) {
        readback.poll(context.Get());Sleep(1);
    }
    CHECK(readback.state()==ReadbackState::Failed && readback.pixels().empty());
    CHECK(!readback.take());
    readback.cancel();
    CHECK(readback.begin(context.Get(),&contract,1));
    CHECK(readback.poll(nullptr)==ReadbackState::Failed);
    CHECK(readback.pixels().empty());
    readback.cancel();
    CHECK(readback.begin(context.Get(),&contract,1));
    readback.cancel(); // release in-flight resources safely, no stale output
    CHECK(readback.poll(context.Get())==ReadbackState::Idle);
    bind_texture(DXGI_FORMAT_R16G16B16A16_TYPELESS,1,DXGI_FORMAT_R16G16B16A16_UNORM);
    CHECK(!readback.begin(context.Get(),&contract,1));
    CHECK(readback.state()==ReadbackState::Failed && readback.pixels().empty());
    readback.cancel();
    bind_texture(DXGI_FORMAT_R16G16B16A16_FLOAT,1,DXGI_FORMAT_UNKNOWN,4097);
    viewport.Width=4097;context->RSSetViewports(1,&viewport);
    CHECK(inspect().state==SourceState::Candidate);
    CHECK(!readback.begin(context.Get(),&contract,1));
    CHECK(readback.state()==ReadbackState::Failed && readback.pixels().empty());
    readback.cancel();
    viewport.Width=64; context->RSSetViewports(1,&viewport);
    bind_texture(DXGI_FORMAT_R16G16B16A16_FLOAT);
    auto rawSmall = tinyBuffer.Get();
    context->PSSetConstantBuffers(1, 1, &rawSmall);
    CHECK(inspect().state == SourceState::UnsupportedConstants);
    context->PSSetConstantBuffers(1, 2, buffers);
    viewport.TopLeftX = 1;
    context->RSSetViewports(1, &viewport);
    CHECK(inspect().state == SourceState::UnsupportedViewport);
    viewport.TopLeftX = 0; viewport.Width = 63;
    context->RSSetViewports(1, &viewport);
    CHECK(inspect().state == SourceState::UnsupportedViewport);
    viewport.Width = 64;
    D3D11_VIEWPORT multiple[] = {viewport, viewport};
    context->RSSetViewports(2, multiple);
    CHECK(inspect().state == SourceState::UnsupportedViewport);
    context->RSSetViewports(1, &viewport);
    ComPtr<ID3D11DeviceContext1> context1;
    CHECK(SUCCEEDED(context.As(&context1)));
    UINT first = 16, count = 32;
    context1->PSSetConstantBuffers1(1, 1, buffers, &first, &count);
    candidate = inspect();
    CHECK(candidate.state == SourceState::Candidate);
    CHECK(candidate.view.first_constant == 16 && candidate.view.num_constants == 32);
    first = 64;
    context1->PSSetConstantBuffers1(1, 1, buffers, &first, &count);
    CHECK(inspect().state == SourceState::UnsupportedConstants);
    context->PSSetConstantBuffers(1, 2, buffers);
    // Repeated observation cannot replace or clear any bound state.
    for (unsigned i = 0; i < 1000; ++i)
        CHECK(inspect().state == SourceState::Candidate);
    ComPtr<ID3D11PixelShader> afterPixel;
    context->PSGetShader(&afterPixel, nullptr, nullptr);
    CHECK(afterPixel.Get() == pixel.Get());
    ComPtr<ID3D11ShaderResourceView> afterSrv;
    context->PSGetShaderResources(0, 1, &afterSrv);
    CHECK(afterSrv.Get() == srv.Get());
    ComPtr<ID3D11Buffer> afterView;
    context->PSGetConstantBuffers(1, 1, &afterView);
    CHECK(afterView.Get() == view.Get());
    UINT n = 1;
    D3D11_VIEWPORT afterViewport{};
    context->RSGetViewports(&n, &afterViewport);
    CHECK(n == 1 && afterViewport.Width == 64 && afterViewport.Height == 32);
    ComPtr<ID3D11DeviceContext> deferred;
    CHECK(SUCCEEDED(device->CreateDeferredContext(0, &deferred)));
    deferred->PSSetShader(pixel.Get(), nullptr, 0);
    CHECK(inspect_source(deferred.Get(), &contract, 1).state == SourceState::DeferredContext);
    // A fresh shader at any address cannot inherit lifetime-bound metadata.
    ComPtr<ID3D11PixelShader> fresh;
    CHECK(SUCCEEDED(device->CreatePixelShader(code->GetBufferPointer(), code->GetBufferSize(), nullptr, &fresh)));
    context->PSSetShader(fresh.Get(), nullptr, 0);
    CHECK(inspect().state == SourceState::UnknownShader);
    context->ClearState(); deferred->ClearState();
    std::vector<unsigned char> mapping(dolly::kMappingBytes);
    constexpr auto legacy_end = dolly::kControlBytes + 24576;
    std::fill_n(mapping.begin(), legacy_end, 0x5a);
    unsigned command = 0;
    auto arm = [&](unsigned frames = 8, unsigned draw_limit = 100, unsigned ms = 1000,
                   unsigned owner = 123, unsigned sequence = 2) {
        ProbeRequest r{};
        std::memcpy(r.magic, "DLYENVR1", 8);
        r.sequence = sequence; r.abi = 1; r.command = ++command; r.enabled = 1;
        r.editor_pid = owner; r.game_pid = 456;
        r.frames = frames; r.draws = draw_limit; r.milliseconds = ms;
        std::memcpy(mapping.data() + ProbeRequestOffset, &r, sizeof(r));
        probe_worker(mapping.data(), true, 123, 456);
    };
    auto state = [&] {
        probe_worker(mapping.data(), true, 123, 456);
        ProbeStatus status{};
        std::memcpy(&status, mapping.data() + ProbeStatusOffset, sizeof(status));
        CHECK(!std::memcmp(status.magic, "DLYENVS1", 8));
        CHECK(!(status.sequence & 1));
        return status.state;
    };
    CHECK(!probe_active());
    arm(8, 100, 1000, 999); CHECK(state() == 4 && !probe_active());
    arm(65); CHECK(state() == 4 && !probe_active());
    arm(8, 100001); CHECK(state() == 4 && !probe_active());
    arm(8, 100, 10001); CHECK(state() == 4 && !probe_active());
    arm(8, 100, 1000, 123, 3); CHECK(!probe_active());
    arm(2); CHECK(probe_active());
    probe_present(); CHECK(probe_active());
    probe_present(); CHECK(state() == 2 && !probe_active());
    CHECK(state() == 2 && !probe_active()); // same command cannot rearm
    arm(8, 2); CHECK(probe_active());
    probe_draw(context.Get()); CHECK(probe_active());
    probe_draw(context.Get()); CHECK(state() == 2 && !probe_active());
    arm(); CHECK(probe_active());
    probe_worker(mapping.data(), false, 123, 456);
    CHECK(state() == 3 && !probe_active()); // renewed ownership cannot rearm
    arm(); CHECK(probe_active());
    probe_hooks(false); CHECK(state() == 5 && !probe_active());
    arm(); CHECK(probe_active());
    arm(); CHECK(state() == 4 && !probe_active()); // active deadline cannot extend
    // GetTickCount64 has system timer resolution, not sub-millisecond precision.
    arm(8, 100, 1); Sleep(32); CHECK(state() == 2 && !probe_active());
    CHECK(std::all_of(mapping.begin(), mapping.begin() + legacy_end,
                     [](unsigned char byte) { return byte == 0x5a; }));
    std::puts("environment source: SHA256, identity, texture, viewport, CB windows, deferred rejection and unchanged bindings PASS");
    std::puts("bounded probe: wire ownership, torn requests, bounds, completion, cancellation, timeout and no automatic rearm PASS");
}
