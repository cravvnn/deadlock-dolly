// Windows CI exercises the real DX11 overlay on WARP with synthetic editor
// state. No Deadlock modules, private addresses, injection, or console needed.
#include "dolly_overlay.hpp"
#include "dolly_editor.hpp"
#include "MinHook.h"
#include <d3d11.h>
#include <dxgi.h>
#include <cstdio>
#include <cstring>
#include <stdexcept>

namespace {
bool available=false;
HWND attached=nullptr;
dolly::EditorSnapshot snapshot;
void require(bool condition,const char* message){if(!condition)throw std::runtime_error(message);}
}
namespace dolly {
EditorSnapshot editor_snapshot() noexcept{return snapshot;}
bool editor_enqueue(EditorAction,double) noexcept{return true;}
bool editor_panel_visible() noexcept{return snapshot.owner==EditorOwner::Panel;}
void editor_set_owner(EditorOwner value) noexcept{snapshot.owner=value;}
void editor_overlay_available(bool value) noexcept{available=value;}
void editor_text_input_active(bool) noexcept{}
void editor_attach_window(HWND value) noexcept{attached=value;}
bool editor_window_message(HWND,UINT,WPARAM,LPARAM,LRESULT&) noexcept{return false;}
}
int main(){
 try{
  const auto instance=GetModuleHandleW(nullptr);
  WNDCLASSW wc{};wc.lpfnWndProc=DefWindowProcW;wc.hInstance=instance;wc.lpszClassName=L"Dolly.Overlay.Smoke";
  require(RegisterClassW(&wc)!=0,"Could not register synthetic window");
  HWND window=CreateWindowExW(0,wc.lpszClassName,L"Dolly graphics smoke",WS_OVERLAPPEDWINDOW,
                              32,32,800,600,nullptr,nullptr,instance,nullptr);
  require(window!=nullptr,"Could not create synthetic window");
  ShowWindow(window,SW_SHOWNOACTIVATE);UpdateWindow(window);
  const auto before_proc=GetWindowLongPtrW(window,GWLP_WNDPROC);
  DXGI_SWAP_CHAIN_DESC desc{};desc.BufferCount=1;desc.BufferDesc.Width=800;desc.BufferDesc.Height=600;
  desc.BufferDesc.Format=DXGI_FORMAT_R8G8B8A8_UNORM;desc.BufferUsage=DXGI_USAGE_RENDER_TARGET_OUTPUT;
  desc.OutputWindow=window;desc.SampleDesc.Count=1;desc.Windowed=TRUE;desc.SwapEffect=DXGI_SWAP_EFFECT_SEQUENTIAL;
  IDXGISwapChain* chain=nullptr;ID3D11Device* device=nullptr;ID3D11DeviceContext* context=nullptr;
  const D3D_FEATURE_LEVEL feature=D3D_FEATURE_LEVEL_11_0;
  require(SUCCEEDED(D3D11CreateDeviceAndSwapChain(nullptr,D3D_DRIVER_TYPE_WARP,nullptr,0,&feature,1,
      D3D11_SDK_VERSION,&desc,&chain,&device,nullptr,&context)),"WARP DX11 device creation failed");
  require(MH_Initialize()==MH_OK,"MinHook initialization failed");
  require(dolly::install_overlay_hooks(),"DXGI public-method hook installation failed");
  snapshot.enabled=true;snapshot.focused=true;snapshot.ready=true;snapshot.paused=true;
  snapshot.owner=dolly::EditorOwner::Panel;snapshot.camera_count=2;snapshot.duration=3;
  std::snprintf(snapshot.shot_name,sizeof(snapshot.shot_name),"Synthetic editor smoke");
  ID3D11Texture2D* backbuffer=nullptr;ID3D11RenderTargetView* target=nullptr;
  require(SUCCEEDED(chain->GetBuffer(0,__uuidof(ID3D11Texture2D),reinterpret_cast<void**>(&backbuffer))),"Backbuffer unavailable");
  require(SUCCEEDED(device->CreateRenderTargetView(backbuffer,nullptr,&target)),"Render target creation failed");
  const FLOAT clear[4]={.04f,.08f,.95f,1};
  D3D11_VIEWPORT original_viewport{7,9,113,127,.1f,.8f};
  for(int i=0;i<3;++i){
   MSG message{};while(PeekMessageW(&message,nullptr,0,0,PM_REMOVE)){TranslateMessage(&message);DispatchMessageW(&message);}
   context->ClearRenderTargetView(target,clear);
   context->OMSetRenderTargets(1,&target,nullptr);context->RSSetViewports(1,&original_viewport);
   require(SUCCEEDED(chain->Present(0,0)),"Synthetic Present failed");
  }
  require(available&&attached==window,"Overlay did not attach to the presenting game window");
  ID3D11RenderTargetView* after_target=nullptr;context->OMGetRenderTargets(1,&after_target,nullptr);
  require(after_target==target,"Overlay did not restore the game's output target");if(after_target)after_target->Release();
  D3D11_VIEWPORT after_viewport{};UINT count=1;context->RSGetViewports(&count,&after_viewport);
  require(count==1 && std::memcmp(&after_viewport,&original_viewport,sizeof(after_viewport))==0,
          "Overlay did not restore the game's viewport");
  // A real panel must change backbuffer pixels; successful hook installation
  // alone would pass without ever rendering anything into the game.
  D3D11_TEXTURE2D_DESC texture{};backbuffer->GetDesc(&texture);
  texture.Usage=D3D11_USAGE_STAGING;texture.CPUAccessFlags=D3D11_CPU_ACCESS_READ;
  texture.BindFlags=0;texture.MiscFlags=0;ID3D11Texture2D* staging=nullptr;
  require(SUCCEEDED(device->CreateTexture2D(&texture,nullptr,&staging)),"Readback texture creation failed");
  context->CopyResource(staging,backbuffer);D3D11_MAPPED_SUBRESOURCE pixels{};
  require(SUCCEEDED(context->Map(staging,0,D3D11_MAP_READ,0,&pixels)),"Rendered panel readback failed");
  const auto* sample=static_cast<const unsigned char*>(pixels.pData)+80*pixels.RowPitch+40*4;
  const bool drawn=sample[2]<180;
  context->Unmap(staging,0);staging->Release();
  require(drawn,"Present ran but the in-game panel did not change the backbuffer");
  context->OMSetRenderTargets(0,nullptr,nullptr);target->Release();backbuffer->Release();
  require(SUCCEEDED(chain->ResizeBuffers(1,720,480,DXGI_FORMAT_UNKNOWN,0)),
          "Overlay retained a backbuffer reference across ResizeBuffers");
  require(SUCCEEDED(chain->Present(0,0))&&available,"Overlay did not recover after resize");
  dolly::shutdown_overlay();
  require(!available&&attached==nullptr,"Shutdown did not release editor ownership");
  require(GetWindowLongPtrW(window,GWLP_WNDPROC)==before_proc,"Shutdown did not restore the original window callback");
  context->Release();device->Release();chain->Release();DestroyWindow(window);UnregisterClassW(wc.lpszClassName,instance);
  std::puts("DX11 WARP overlay: actual render, state restoration and resize passed.");return 0;
 }catch(const std::exception& error){
  dolly::shutdown_overlay();std::fprintf(stderr,"Overlay smoke failed: %s\n",error.what());return 1;
 }
}
