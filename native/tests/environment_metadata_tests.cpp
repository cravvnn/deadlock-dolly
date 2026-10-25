#include <d3d11.h>
#include <d3dcompiler.h>
#include <wrl/client.h>
#include <cstdio>
#include <cstdlib>
#include "dolly_player_capture.hpp"
#include "dolly_environment_source.hpp"
#include "dolly_hook_init.hpp"
using Microsoft::WRL::ComPtr;
namespace capture=dolly::player_capture;
#define CHECK(x) do { if (!(x)) {std::fprintf(stderr,"FAILED %d: %s\n",__LINE__,#x);std::exit(1);} } while(0)
__declspec(noinline) int camera_test_target(int value) {
    volatile int input=value;return (input*7+3)^12345;
}
using CameraFn=int(*)(int);
CameraFn original_camera=nullptr;
int camera_test_hook(int value) { return original_camera(value)+1; }
int main() {
    ComPtr<ID3D11Device> discovery,actual;
    CHECK(SUCCEEDED(D3D11CreateDevice(nullptr,D3D_DRIVER_TYPE_WARP,nullptr,0,nullptr,0,
        D3D11_SDK_VERSION,&discovery,nullptr,nullptr)));
    CHECK(!capture::install_environment_metadata_hook(nullptr));
    CHECK(capture::install_environment_metadata_hook(discovery.Get()));
    CHECK(capture::install_environment_metadata_hook(discovery.Get()));
    CHECK(SUCCEEDED(D3D11CreateDevice(nullptr,D3D_DRIVER_TYPE_WARP,nullptr,0,nullptr,0,
        D3D11_SDK_VERSION,&actual,nullptr,nullptr)));
    CHECK(capture::pixel_metadata_ready(actual.Get()));
    // The live camera startup used to reject MH_ERROR_ALREADY_INITIALIZED
    // after early metadata installation. Exercise the shared initialization
    // boundary and a real subsequent hook, not just a mock success code.
    CHECK(dolly::initialize_hook_library());
    CHECK(dolly::initialize_hook_library());
    const int baseline=camera_test_target(3);
    CHECK(MH_CreateHook(reinterpret_cast<void*>(&camera_test_target),
        reinterpret_cast<void*>(&camera_test_hook),reinterpret_cast<void**>(&original_camera))==MH_OK);
    CHECK(MH_EnableHook(reinterpret_cast<void*>(&camera_test_target))==MH_OK);
    CHECK(camera_test_target(3)==baseline+1);
    CHECK(MH_DisableHook(reinterpret_cast<void*>(&camera_test_target))==MH_OK);
    CHECK(MH_RemoveHook(reinterpret_cast<void*>(&camera_test_target))==MH_OK);
    CHECK(camera_test_target(3)==baseline);
    ComPtr<ID3DBlob> code,error;
    const char shader[]="float4 main():SV_Target{return float4(2,3,4,1);}";
    CHECK(SUCCEEDED(D3DCompile(shader,sizeof(shader)-1,nullptr,nullptr,nullptr,"main","ps_5_0",0,0,&code,&error)));
    auto before=capture::environment_metadata_calls();
    ComPtr<ID3D11PixelShader> pixel;
    CHECK(SUCCEEDED(actual->CreatePixelShader(code->GetBufferPointer(),code->GetBufferSize(),nullptr,&pixel)));
    CHECK(capture::environment_metadata_calls()==before+1);
    UINT length=0;
    // This base tags every created pixel shader with its bytecode on any
    // device (the Players path relies on it), so the early environment hook
    // keeps that contract; review filtering happens in retain_reviewed_shader.
    CHECK(SUCCEEDED(pixel->GetPrivateData(dolly::environment::PixelBytesKey,&length,nullptr)));
    CHECK(length==code->GetBufferSize());
    // The Players layout hook can subsequently reuse the same device vtable
    // slot without a second hook or a collision.
    CHECK(capture::install_layout_hook(actual.Get()));
    pixel.Reset();
    CHECK(SUCCEEDED(actual->CreatePixelShader(code->GetBufferPointer(),code->GetBufferSize(),nullptr,&pixel)));
    CHECK(SUCCEEDED(pixel->GetPrivateData(dolly::environment::PixelBytesKey,&length,nullptr)));
    CHECK(length==code->GetBufferSize());
    CHECK(capture::pixel_metadata_ready(actual.Get()));
    std::puts("Early metadata hook, second-device detection and existing hook coexistence passed");
}
