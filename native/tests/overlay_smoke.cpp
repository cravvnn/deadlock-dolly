// Windows CI exercises the real DX11 overlay on WARP with synthetic editor
// state. No Deadlock modules, private addresses, injection, or console needed.
#include "dolly_overlay.hpp"
#include "dolly_editor.hpp"
#include "dolly_renderer_diagnostics.hpp"
#include "MinHook.h"
#include "imgui.h"
#include <d3d11.h>
#include <dxgi.h>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <memory>
#include <stdexcept>
#include <vector>
#include <vector>

namespace {
bool available=false;
HWND attached=nullptr;
dolly::EditorSnapshot snapshot;
bool observed_mouse_down[5]{};
bool present_keeps_os_cursor=false;
void require(bool condition,const char* message){if(!condition)throw std::runtime_error(message);}

struct BufferLifetime {std::atomic<bool> retired{false};};
// SetPrivateDataInterface holds one COM reference until the owning device
// child is destroyed. Observe that lifetime instead of relying on a driver's
// implementation-specific AddRef/Release return values.
// https://learn.microsoft.com/en-us/windows/win32/api/d3d11/nf-d3d11-id3d11devicechild-setprivatedatainterface
class BufferSentinel final : public IUnknown {
 std::atomic<ULONG> references{1};
 std::shared_ptr<BufferLifetime> lifetime;
 ~BufferSentinel(){lifetime->retired.store(true,std::memory_order_release);}
public:
 explicit BufferSentinel(std::shared_ptr<BufferLifetime> value):lifetime(std::move(value)){}
 HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid,void** result) override {
  if(!result)return E_POINTER;
  *result=nullptr;
  if(!IsEqualIID(iid,__uuidof(IUnknown)))return E_NOINTERFACE;
  *result=static_cast<IUnknown*>(this);AddRef();return S_OK;
 }
 ULONG STDMETHODCALLTYPE AddRef() override {return references.fetch_add(1,std::memory_order_relaxed)+1;}
 ULONG STDMETHODCALLTYPE Release() override {
  const auto remaining=references.fetch_sub(1,std::memory_order_acq_rel)-1;
  if(!remaining)delete this;
  return remaining;
 }
};

std::shared_ptr<BufferLifetime> watch_buffer(ID3D11Buffer* buffer){
 static const GUID sentinel_id={0x1622bc48,0x49b1,0x466a,{0x95,0xb3,0xa4,0x3b,0xe0,0x34,0x11,0x8f}};
 auto lifetime=std::make_shared<BufferLifetime>();
 auto* sentinel=new BufferSentinel(lifetime);
 const auto result=buffer->SetPrivateDataInterface(sentinel_id,sentinel);
 sentinel->Release();
 require(SUCCEEDED(result),"Could not attach buffer lifetime sentinel");
 require(!lifetime->retired.load(std::memory_order_acquire),"Buffer did not retain its lifetime sentinel");
 return lifetime;
}

void retire_game_buffers(ID3D11DeviceContext* context,ID3D11Query* completed,
                         const std::vector<std::shared_ptr<BufferLifetime>>& lifetimes){
 // D3D11 may defer destruction. Release game bindings, submit pending work,
 // and wait for it before checking lifetime flags; a live overlay must not
 // retain earlier game vertex/index buffers in its private context state.
 // https://learn.microsoft.com/en-us/windows/win32/api/d3d11/nf-d3d11-id3d11devicecontext-flush
 context->ClearState();context->End(completed);context->Flush();
 const auto deadline=GetTickCount64()+3000;
 for(;;){
  BOOL ready=FALSE;
  const auto result=context->GetData(completed,&ready,sizeof(ready),D3D11_ASYNC_GETDATA_DONOTFLUSH);
  require(SUCCEEDED(result),"GPU completion query failed during buffer-retirement check");
  if(result==S_OK&&ready)break;
  require(GetTickCount64()<deadline,"GPU completion timed out during buffer-retirement check");
  Sleep(1);
 }
 context->Flush();
 for(std::size_t i=0;i<lifetimes.size();++i){
  if(!lifetimes[i]->retired.load(std::memory_order_acquire)){
   char message[128]{};
   std::snprintf(message,sizeof(message),"Overlay retained retired game %s buffer at frame %zu",
                 i%2?"index":"vertex",i/2);
   throw std::runtime_error(message);
  }
 }
}

void stress_game_buffer_lifetimes(IDXGISwapChain* chain,ID3D11Device* device,
                                 ID3D11DeviceContext* context,ID3D11RenderTargetView* target){
 constexpr int frames=384;
 constexpr dolly::EditorOwner owners[]={dolly::EditorOwner::Panel,dolly::EditorOwner::Flight,
                                       dolly::EditorOwner::GameUI};
 std::vector<std::shared_ptr<BufferLifetime>> lifetimes;lifetimes.reserve(frames*2);
 D3D11_QUERY_DESC query_desc{D3D11_QUERY_EVENT,0};ID3D11Query* completed=nullptr;
 require(SUCCEEDED(device->CreateQuery(&query_desc,&completed)),"Could not create buffer-retirement query");
 try{
  for(int frame=0;frame<frames;++frame){
   MSG message{};while(PeekMessageW(&message,nullptr,0,0,PM_REMOVE)){TranslateMessage(&message);DispatchMessageW(&message);}
   snapshot.owner=owners[(frame/4)%3];
   D3D11_BUFFER_DESC buffer_desc{};buffer_desc.ByteWidth=1024;
   buffer_desc.Usage=D3D11_USAGE_DEFAULT;buffer_desc.BindFlags=D3D11_BIND_VERTEX_BUFFER;
   ID3D11Buffer* vertex=nullptr;ID3D11Buffer* index=nullptr;
   require(SUCCEEDED(device->CreateBuffer(&buffer_desc,nullptr,&vertex)),"Synthetic game vertex-buffer creation failed");
   buffer_desc.BindFlags=D3D11_BIND_INDEX_BUFFER;
   const auto index_result=device->CreateBuffer(&buffer_desc,nullptr,&index);
   if(FAILED(index_result)){vertex->Release();throw std::runtime_error("Synthetic game index-buffer creation failed");}
   lifetimes.push_back(watch_buffer(vertex));lifetimes.push_back(watch_buffer(index));
   const UINT stride=16u*(1u+static_cast<UINT>(frame%2));
   const UINT vertex_offset=16u*static_cast<UINT>(frame%4);
   const UINT index_offset=4u*static_cast<UINT>(frame%8);
   const auto index_format=frame%2?DXGI_FORMAT_R32_UINT:DXGI_FORMAT_R16_UINT;
   context->IASetVertexBuffers(0,1,&vertex,&stride,&vertex_offset);
   context->IASetIndexBuffer(index,index_format,index_offset);
   context->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
   context->OMSetRenderTargets(1,&target,nullptr);
   const auto presented=chain->Present(0,0);
   ID3D11Buffer* after_vertex=nullptr;ID3D11Buffer* after_index=nullptr;
   UINT after_stride=0,after_vertex_offset=0,after_index_offset=0;DXGI_FORMAT after_index_format{};
   context->IAGetVertexBuffers(0,1,&after_vertex,&after_stride,&after_vertex_offset);
   context->IAGetIndexBuffer(&after_index,&after_index_format,&after_index_offset);
   const bool restored=after_vertex==vertex&&after_index==index&&after_stride==stride&&
       after_vertex_offset==vertex_offset&&after_index_format==index_format&&after_index_offset==index_offset;
   if(after_vertex)after_vertex->Release();if(after_index)after_index->Release();
   vertex->Release();index->Release();
   require(SUCCEEDED(presented),"Present failed during game-buffer lifetime stress");
   require(restored,"Overlay did not restore the game's vertex/index-buffer bindings");
   if((frame+1)%32==0)retire_game_buffers(context,completed,lifetimes);
  }
 }catch(...){completed->Release();throw;}
 completed->Release();snapshot.owner=dolly::EditorOwner::Panel;
 std::puts("DX11 WARP lifetime stress: 384 owner-toggle frames, 768 game buffers retired.");
}
}
namespace dolly {
EditorSnapshot editor_snapshot() noexcept{return snapshot;}
bool editor_enqueue(EditorAction,double) noexcept{return true;}
bool editor_panel_visible() noexcept{return snapshot.owner==EditorOwner::Panel;}
void editor_set_owner(EditorOwner value) noexcept{snapshot.owner=value;}
void editor_overlay_available(bool value) noexcept{available=value;}
void editor_text_input_active(bool) noexcept{
 if(ImGui::GetCurrentContext()){
  const auto& io=ImGui::GetIO();
  for(int i=0;i<5;++i)observed_mouse_down[i]=io.MouseDown[i];
  present_keeps_os_cursor=(io.ConfigFlags&ImGuiConfigFlags_NoMouseCursorChange)!=0;
 }
}
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
  // Optional graphics diagnostics must stay inside their unused block and
  // fail closed on an unrecognized renderer, without changing camera state.
  std::vector<unsigned char> diagnostic_memory(dolly::kMappingBytes,0xa5);
  dolly::renderer_diagnostics_probe(0, "");
  dolly::renderer_diagnostics_tick(diagnostic_memory.data());
  dolly::RendererDiagnostics diagnostic{};
  std::memcpy(&diagnostic,diagnostic_memory.data()+dolly::kRendererDiagnosticsOffset,sizeof(diagnostic));
  require(std::memcmp(diagnostic.magic,"DLYGFX01",8)==0 && diagnostic.abi==1 && !(diagnostic.sequence&1),
          "Optional renderer diagnostic header is invalid");
  require(diagnostic.state==std::uint32_t(dolly::RendererProbeState::Waiting),
          "Missing renderer should leave the optional probe waiting");
  for(std::size_t i=0;i<diagnostic_memory.size();++i)
   if(i<dolly::kRendererDiagnosticsOffset||i>=dolly::kRendererDiagnosticsOffset+sizeof(diagnostic))
    require(diagnostic_memory[i]==0xa5,"Graphics diagnostics overwrote camera/editor mapping data");
  dolly::renderer_diagnostics_probe(reinterpret_cast<std::uintptr_t>(GetModuleHandleW(L"kernel32.dll")),"unknown");
  Sleep(1050); // one bounded sample interval; the probe never polls per frame
  dolly::renderer_diagnostics_tick(diagnostic_memory.data());
  std::memcpy(&diagnostic,diagnostic_memory.data()+dolly::kRendererDiagnosticsOffset,sizeof(diagnostic));
  require(diagnostic.state==std::uint32_t(dolly::RendererProbeState::Unsupported) && diagnostic.sample==2,
          "Unrecognized module must not permit private renderer reads");
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
  stress_game_buffer_lifetimes(chain,device,context,target);
  // F9 gives Deadlock its own mouse capture for hero/replay UI interaction.
  // A hidden ImGui backend used to receive every mouse-up and ReleaseCapture
  // even when Dolly did not own the click. Do not touch that capture.
  SetCapture(window);require(GetCapture()==window,"Could not establish game mouse capture");
  snapshot.owner=dolly::EditorOwner::GameUI;
  SendMessageW(window,WM_LBUTTONUP,0,0);
  require(GetCapture()==window,"Hidden overlay released the game's mouse capture");
  require(SUCCEEDED(chain->Present(0,0)),"Hidden-overlay Present failed");
  require(GetCapture()==window,"Hidden-overlay Present changed game mouse capture");
  snapshot.owner=dolly::EditorOwner::Panel;
  SendMessageW(window,WM_LBUTTONDOWN,MK_LBUTTON,MAKELPARAM(30,30));
  require(SUCCEEDED(chain->Present(0,0)),"Panel Present for legacy mouse-down failed");
  require(observed_mouse_down[0],"Legacy mouse-down did not reach the panel");
  SendMessageW(window,WM_LBUTTONUP,0,MAKELPARAM(30,30));
  require(GetCapture()==window,"Panel input changed an existing game mouse capture");
  require(SUCCEEDED(chain->Present(0,0)),"Panel Present after UI handoff failed");
  require(!observed_mouse_down[0],"Legacy mouse-up did not reach the panel");
  require(GetCapture()==window,"Panel Present changed game mouse capture");
  dolly::overlay_raw_mouse_button(0,true);
  require(SUCCEEDED(chain->Present(0,0))&&observed_mouse_down[0],"Raw-only mouse-down did not reach the panel");
  dolly::overlay_raw_mouse_button(0,false);
  require(SUCCEEDED(chain->Present(0,0))&&!observed_mouse_down[0],"Raw-only mouse-up did not reach the panel");
  require(GetCapture()==window,"Raw-only input changed game mouse capture");
  require(present_keeps_os_cursor,"Present is allowed to change the OS mouse cursor");
  ReleaseCapture();
  context->OMSetRenderTargets(0,nullptr,nullptr);target->Release();backbuffer->Release();
  require(SUCCEEDED(chain->ResizeBuffers(1,720,480,DXGI_FORMAT_UNKNOWN,0)),
          "Overlay retained a backbuffer reference across ResizeBuffers");
  require(SUCCEEDED(chain->Present(0,0))&&available,"Overlay did not recover after resize");
  dolly::shutdown_overlay();
  require(!available&&attached==nullptr,"Shutdown did not release editor ownership");
  require(GetWindowLongPtrW(window,GWLP_WNDPROC)==before_proc,"Shutdown did not restore the original window callback");
  context->Release();device->Release();chain->Release();DestroyWindow(window);UnregisterClassW(wc.lpszClassName,instance);
  std::puts("DX11 WARP overlay: render, state restoration, buffer retirement, resize and UI mouse ownership passed.");return 0;
 }catch(const std::exception& error){
  dolly::shutdown_overlay();std::fprintf(stderr,"Overlay smoke failed: %s\n",error.what());return 1;
 }
}
