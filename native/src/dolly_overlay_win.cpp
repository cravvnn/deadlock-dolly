// DirectX 11 in-game editor. This is an overlay on the game's swapchain, not a
// second window, console-command renderer, or replacement for path evaluation.
#include "dolly_overlay.hpp"
#include "dolly_editor.hpp"
#include "MinHook.h"
#include <d3d11_1.h>
#include <dxgi.h>
#include <algorithm>
#include <atomic>
#include <cstdio>
#include <deque>
#include <mutex>
#include "imgui.h"
#include "imgui_impl_dx11.h"
#include "imgui_impl_win32.h"

extern IMGUI_IMPL_API LRESULT ImGui_ImplWin32_WndProcHandler(HWND,UINT,WPARAM,LPARAM);

namespace dolly {
namespace {
using PresentFn=HRESULT (STDMETHODCALLTYPE*)(IDXGISwapChain*,UINT,UINT);
using ResizeFn=HRESULT (STDMETHODCALLTYPE*)(IDXGISwapChain*,UINT,UINT,UINT,DXGI_FORMAT,UINT);
PresentFn original_present=nullptr;
ResizeFn original_resize=nullptr;
std::atomic<bool> installed{false},enabled{false},resizing{false};
std::atomic<const char*> last_error{"Waiting for the DirectX 11 game window."};
std::recursive_mutex render_mutex;
struct InputMessage {
 HWND window;UINT message=0;WPARAM wparam=0;LPARAM lparam=0;
 unsigned raw_kind=0;int button=0;bool down=false;float vertical=0,horizontal=0;
};
std::mutex input_mutex;
std::deque<InputMessage> pending_input;
bool input_overflow=false;
IDXGISwapChain* swapchain=nullptr; // Identity only: do not retain a dead game's swapchain.
ID3D11Device* device=nullptr;
ID3D11DeviceContext* immediate=nullptr;
ID3D11DeviceContext1* context1=nullptr;
ID3DDeviceContextState* overlay_state=nullptr;
ID3D11RenderTargetView* target=nullptr;
ImGuiContext* imgui=nullptr;
std::atomic<HWND> game_window{nullptr};
bool win32_ready=false,dx11_ready=false,last_panel=false;
ULONGLONG next_initialization=0;
constexpr wchar_t kWindowProperty[]=L"DeadlockDolly.Overlay.OriginalWindowProcedure";

// Serialize our ImGui context across Present and window-message callbacks,
// restoring the prior context around every call into our backends. Other DLLs
// embedding ImGui retain their own context and render state.
struct GuiContextScope {
 ImGuiContext* previous;
 explicit GuiContextScope(ImGuiContext* current):previous(ImGui::GetCurrentContext()) {ImGui::SetCurrentContext(current);}
 ~GuiContextScope(){ImGui::SetCurrentContext(previous);}
};

template<typename T> void release(T*& object) noexcept {if(object){object->Release();object=nullptr;}}
LRESULT CALLBACK window_proc(HWND,UINT,WPARAM,LPARAM);

void release_target() noexcept {release(target);}
void clear_pending_input() {
 std::lock_guard<std::mutex> lock(input_mutex);pending_input.clear();input_overflow=false;
}
void feed_pending_input() {
 std::deque<InputMessage> messages;bool overflow=false;
 {std::lock_guard<std::mutex> lock(input_mutex);messages.swap(pending_input);overflow=input_overflow;input_overflow=false;}
 if(overflow){ImGui::GetIO().ClearInputKeys();ImGui::GetIO().ClearInputMouse();}
 for(const auto& message:messages)if(message.window==game_window){
  auto& io=ImGui::GetIO();
  if(message.raw_kind==1)io.AddMouseButtonEvent(message.button,message.down);
  else if(message.raw_kind==2)io.AddMouseWheelEvent(message.horizontal,message.vertical);
  else{
   ImGui_ImplWin32_WndProcHandler(message.window,message.message,message.wparam,message.lparam);
   if(message.raw_kind==3){
    io.AddKeyEvent(ImGuiMod_Ctrl,(GetAsyncKeyState(VK_CONTROL)&0x8000)!=0);
    io.AddKeyEvent(ImGuiMod_Shift,(GetAsyncKeyState(VK_SHIFT)&0x8000)!=0);
    io.AddKeyEvent(ImGuiMod_Alt,(GetAsyncKeyState(VK_MENU)&0x8000)!=0);
    io.AddKeyEvent(ImGuiMod_Super,(GetAsyncKeyState(VK_LWIN)&0x8000)||(GetAsyncKeyState(VK_RWIN)&0x8000));
   }
  }
 }
}
void release_device() noexcept {
 editor_overlay_available(false);editor_text_input_active(false);
 clear_pending_input();
 release_target();
 if(imgui){
  auto* previous=ImGui::GetCurrentContext();ImGui::SetCurrentContext(imgui);
  if(dx11_ready)ImGui_ImplDX11_Shutdown();
  if(win32_ready)ImGui_ImplWin32_Shutdown();
  ImGui::DestroyContext(imgui);
  ImGui::SetCurrentContext(previous==imgui?nullptr:previous);
 }
 imgui=nullptr;dx11_ready=win32_ready=last_panel=false;
 release(overlay_state);release(context1);release(immediate);release(device);
 if(game_window && IsWindow(game_window)){
  auto previous=reinterpret_cast<WNDPROC>(GetPropW(game_window,kWindowProperty));
  // Another overlay may have chained our callback. Never overwrite its hook.
  // In that case the property stays until WM_NCDESTROY and our callback simply
  // forwards. The module is pinned so that chained callback remains valid.
  if(previous && reinterpret_cast<WNDPROC>(GetWindowLongPtrW(game_window,GWLP_WNDPROC))==window_proc){
   SetWindowLongPtrW(game_window,GWLP_WNDPROC,reinterpret_cast<LONG_PTR>(previous));
   RemovePropW(game_window,kWindowProperty);
  }
 }
 game_window=nullptr;swapchain=nullptr;
 editor_attach_window(nullptr);
}

bool suitable_window(HWND window) noexcept {
 DWORD pid=0;GetWindowThreadProcessId(window,&pid);
 RECT bounds{};
 return pid==GetCurrentProcessId() && IsWindowVisible(window) &&
        GetAncestor(window,GA_ROOT)==window && GetClientRect(window,&bounds) &&
        bounds.right-bounds.left>=320 && bounds.bottom-bounds.top>=200;
}

bool create_target(IDXGISwapChain* chain) noexcept {
 ID3D11Texture2D* buffer=nullptr;
 if(FAILED(chain->GetBuffer(0,__uuidof(ID3D11Texture2D),reinterpret_cast<void**>(&buffer))))return false;
 const HRESULT result=device->CreateRenderTargetView(buffer,nullptr,&target);
 buffer->Release();return SUCCEEDED(result);
}

void style_panel() {
 ImGui::StyleColorsDark();
 auto& style=ImGui::GetStyle();
 style.WindowPadding=ImVec2(18,16);style.FramePadding=ImVec2(10,7);
 style.ItemSpacing=ImVec2(10,9);style.WindowRounding=9;style.FrameRounding=5;
 style.GrabRounding=5;style.WindowBorderSize=1;style.FrameBorderSize=0;
 style.Colors[ImGuiCol_WindowBg]=ImVec4(.065f,.082f,.098f,.98f);
 style.Colors[ImGuiCol_TitleBg]=ImVec4(.07f,.10f,.12f,1);
 style.Colors[ImGuiCol_TitleBgActive]=ImVec4(.08f,.14f,.16f,1);
 style.Colors[ImGuiCol_Border]=ImVec4(.22f,.33f,.36f,.8f);
 style.Colors[ImGuiCol_Text]=ImVec4(.91f,.95f,.96f,1);
 style.Colors[ImGuiCol_TextDisabled]=ImVec4(.53f,.64f,.68f,1);
 style.Colors[ImGuiCol_Button]=ImVec4(.13f,.25f,.27f,1);
 style.Colors[ImGuiCol_ButtonHovered]=ImVec4(.20f,.39f,.40f,1);
 style.Colors[ImGuiCol_ButtonActive]=ImVec4(.25f,.48f,.46f,1);
 style.Colors[ImGuiCol_FrameBg]=ImVec4(.10f,.15f,.18f,1);
 style.Colors[ImGuiCol_FrameBgHovered]=ImVec4(.15f,.25f,.27f,1);
 style.Colors[ImGuiCol_FrameBgActive]=ImVec4(.19f,.32f,.34f,1);
 style.Colors[ImGuiCol_SliderGrab]=ImVec4(.41f,.75f,.69f,1);
 style.Colors[ImGuiCol_SliderGrabActive]=ImVec4(.60f,.87f,.78f,1);
 style.Colors[ImGuiCol_Header]=style.Colors[ImGuiCol_Button];
 style.Colors[ImGuiCol_HeaderHovered]=style.Colors[ImGuiCol_ButtonHovered];
 style.Colors[ImGuiCol_HeaderActive]=style.Colors[ImGuiCol_ButtonActive];
}

// A complete state swap is preferable to assuming what the game or a ReShade
// effect left bound at Present. Restore before the original Present executes.
struct DeviceStateScope {
 ID3DDeviceContextState* saved=nullptr;
 DeviceStateScope(){context1->SwapDeviceContextState(overlay_state,&saved);}
 ~DeviceStateScope(){
  // Context-state objects retain bound targets too. Clear our target before
  // swapping away so ResizeBuffers can release the last backbuffer reference.
  immediate->OMSetRenderTargets(0,nullptr,nullptr);
  context1->SwapDeviceContextState(saved,nullptr);release(saved);
 }
};


bool initialize_device(IDXGISwapChain* chain) {
 DXGI_SWAP_CHAIN_DESC description{};
 if(FAILED(chain->GetDesc(&description))||!suitable_window(description.OutputWindow))return false;
 if(FAILED(chain->GetDevice(__uuidof(ID3D11Device),reinterpret_cast<void**>(&device)))){
  last_error="The presenting game window is not using DirectX 11.";return false;
 }
 device->GetImmediateContext(&immediate);
 ID3D11Device1* device1=nullptr;
 if(!immediate || FAILED(device->QueryInterface(__uuidof(ID3D11Device1),reinterpret_cast<void**>(&device1))) ||
    FAILED(immediate->QueryInterface(__uuidof(ID3D11DeviceContext1),reinterpret_cast<void**>(&context1)))){
  last_error="The DirectX 11.1 context API is unavailable; in-game editor disabled.";
  release(device1);release_device();return false;
 }
 const D3D_FEATURE_LEVEL feature=device->GetFeatureLevel();D3D_FEATURE_LEVEL chosen{};
 const HRESULT state_result=device1->CreateDeviceContextState(0,&feature,1,D3D11_SDK_VERSION,
     __uuidof(ID3D11Device),&chosen,&overlay_state);
 device1->Release();
 if(FAILED(state_result)){last_error="Could not create an isolated DirectX state for the editor.";release_device();return false;}
 // Save ALL D3D state with the Windows 10 D3D11.1 context API. The upstream
 // renderer additionally saves its draw state, but does not restore HS/DS/CS
 // shaders or output UAVs. Context swapping covers those and other overlays.
 game_window=description.OutputWindow;swapchain=chain;
 if(!create_target(chain)){last_error="Could not create the editor render target.";release_device();return false;}
 IMGUI_CHECKVERSION();
 auto* previous=ImGui::GetCurrentContext();imgui=ImGui::CreateContext();
 {
  GuiContextScope scope(imgui);
  auto& io=ImGui::GetIO();io.IniFilename=nullptr;io.LogFilename=nullptr;
  io.ConfigFlags=ImGuiConfigFlags_NoMouseCursorChange;
  style_panel();
  const float scale=std::clamp(ImGui_ImplWin32_GetDpiScaleForHwnd(game_window),1.0f,2.5f);
  ImFontConfig font;font.SizePixels=16.0f*scale;io.Fonts->AddFontDefault(&font);
  ImGui::GetStyle().ScaleAllSizes(scale);
  win32_ready=ImGui_ImplWin32_Init(game_window);
  if(win32_ready)dx11_ready=ImGui_ImplDX11_Init(device,immediate);
 }
 ImGui::SetCurrentContext(previous);
 if(!win32_ready||!dx11_ready){last_error="Could not initialize the DirectX 11 editor UI.";release_device();return false;}
 bool gpu_ready=false;
 {GuiContextScope scope(imgui);DeviceStateScope graphics;gpu_ready=ImGui_ImplDX11_CreateDeviceObjects();}
 if(!gpu_ready){last_error="Could not compile or create the editor shaders; in-game controls remain disabled.";release_device();return false;}
 // Install exactly once on the actual swapchain window. A property retains
 // its original callback even if another overlay chains us during a resize.
 auto prior=reinterpret_cast<WNDPROC>(GetWindowLongPtrW(game_window,GWLP_WNDPROC));
 auto retained=reinterpret_cast<WNDPROC>(GetPropW(game_window,kWindowProperty));
 if(retained && prior==retained){RemovePropW(game_window,kWindowProperty);retained=nullptr;}
 // If a second overlay chained our window procedure, it remains in that
 // chain after device loss. Reuse it instead of wrapping the chain twice.
 if(!retained){
  if(!prior || !SetPropW(game_window,kWindowProperty,reinterpret_cast<HANDLE>(prior))){last_error="Could not attach the editor to the game window.";release_device();return false;}
  SetLastError(0);
  auto replaced=SetWindowLongPtrW(game_window,GWLP_WNDPROC,reinterpret_cast<LONG_PTR>(window_proc));
  if(!replaced && GetLastError()!=0){last_error="Could not attach editor input to the game window.";RemovePropW(game_window,kWindowProperty);release_device();return false;}
  // Preserve the actual previous callback if another hook raced the read.
  if(replaced)SetPropW(game_window,kWindowProperty,reinterpret_cast<HANDLE>(replaced));
 }
 last_error="";editor_attach_window(game_window);editor_overlay_available(true);
 return true;
}

const char* owner_name(EditorOwner owner) noexcept {
 switch(owner){
 case EditorOwner::Flight:return "Free camera";
 case EditorOwner::Panel:return "Dolly editor";
 case EditorOwner::GameUI:return "Deadlock replay UI";
 case EditorOwner::Console:return "Console";
 case EditorOwner::Unfocused:return "Window unfocused";
 default:return "Waiting for replay";
 }
}
void action_button(const char* label,EditorAction action,float width=0,double value=0) {
 if(ImGui::Button(label,ImVec2(width,0)))editor_enqueue(action,value);
}
void draw_panel(const EditorSnapshot& state) {
 auto& io=ImGui::GetIO();
 ImGui::SetNextWindowPos(ImVec2(24,24),ImGuiCond_FirstUseEver);
 ImGui::SetNextWindowSize(ImVec2(std::min(480.0f,io.DisplaySize.x-32),0),ImGuiCond_FirstUseEver);
 ImGui::SetNextWindowSizeConstraints(ImVec2(std::min(350.0f,io.DisplaySize.x-16),150),
                                   ImVec2(std::max(350.0f,io.DisplaySize.x-16),std::max(150.0f,io.DisplaySize.y-32)));
 bool open=true;
 if(ImGui::Begin("DEADLOCK DOLLY",&open,ImGuiWindowFlags_NoCollapse)){
  ImGui::TextColored(ImVec4(.48f,.83f,.74f,1),"%s",state.shot_name[0]?state.shot_name:"Untitled shot");
  ImGui::TextDisabled("%s  |  Replay %s",owner_name(state.owner),state.paused?"paused":"playing");
  if(state.duration>0)ImGui::Text("Shot %.2f / %.2f s",state.phase,state.duration);
  ImGui::Separator();
  ImGui::BeginDisabled(!state.ready||state.busy);
  const float half=(ImGui::GetContentRegionAvail().x-ImGui::GetStyle().ItemSpacing.x)/2;
  action_button(state.paused?"Play replay":"Pause replay",EditorAction::PlayPause,half);
  ImGui::SameLine();
  ImGui::BeginDisabled(state.camera_count<2);
  action_button("Play shot",EditorAction::PlayPath,half);ImGui::EndDisabled();
  action_button("Back 1 second",EditorAction::SeekBack,half);ImGui::SameLine();
  action_button("Forward 1 second",EditorAction::SeekForward,half);
  ImGui::SeparatorText("Camera views");
  action_button(state.camera_count?"Capture camera":"Start path here",EditorAction::Capture,half);ImGui::SameLine();
  ImGui::BeginDisabled(!state.camera_count);
  action_button("Replace selected",EditorAction::Replace,half);
  char selected[64]{};
  if(state.camera_count)std::snprintf(selected,sizeof(selected),"View %u of %u",state.selected_camera+1,state.camera_count);
  else std::snprintf(selected,sizeof(selected),"No camera views");
  ImGui::SetNextItemWidth(-1);
  if(ImGui::BeginCombo("##selected-camera",selected)){
   const std::uint32_t count=std::min<std::uint32_t>(state.camera_count,10000);
   for(std::uint32_t i=0;i<count;++i){
    char name[48]{};std::snprintf(name,sizeof(name),"View %u",i+1);
    if(ImGui::Selectable(name,i==state.selected_camera))editor_enqueue(EditorAction::SelectView,double(i));
    if(i==state.selected_camera)ImGui::SetItemDefaultFocus();
   }
   ImGui::EndCombo();
  }
  action_button("Previous view",EditorAction::PreviousView,half);ImGui::SameLine();
  action_button("Next view",EditorAction::NextView,half);
  ImGui::EndDisabled();
  ImGui::SeparatorText("Free camera");
  // Keep the user's drag local, then send one settings change on release.
  // Sending at render frequency floods the acknowledged event queue and
  // makes the launcher rewrite its settings file for every mouse movement.
  static float speed_draft=400.0f;
  static bool speed_editing=false;
  if(!speed_editing)speed_draft=std::clamp(static_cast<float>(state.speed),1.0f,10000.0f);
  ImGui::SetNextItemWidth(-1);
  ImGui::SliderFloat("##flight-speed",&speed_draft,1.0f,10000.0f,"Speed %.0f",
                     ImGuiSliderFlags_Logarithmic|ImGuiSliderFlags_AlwaysClamp);
  const bool speed_committed=ImGui::IsItemDeactivatedAfterEdit();
  speed_editing=ImGui::IsItemActive();
  if(speed_committed)editor_enqueue(EditorAction::SetSpeed,double(speed_draft));
  action_button("Fly camera",EditorAction::Flight,half);ImGui::SameLine();
  action_button("Deadlock UI / heroes",EditorAction::GameUI,half,1);
  ImGui::EndDisabled();
  action_button("Stop / restore",EditorAction::Stop,ImGui::GetContentRegionAvail().x);
  if(state.message[0]){ImGui::Spacing();ImGui::TextWrapped("%s",state.message);}
  ImGui::Spacing();ImGui::TextDisabled("F7 Console  |  Bindings are set in the launcher");
 }
 ImGui::End();
 if(!open)editor_enqueue(EditorAction::Flight);
}


bool try_initialize_device(IDXGISwapChain* chain) {
 if(GetTickCount64()<next_initialization)return false;
 if(initialize_device(chain)){next_initialization=0;return true;}
 next_initialization=GetTickCount64()+2000;return false;
}
void render_overlay(IDXGISwapChain* chain) {
 const auto state=editor_snapshot();
 if(!state.enabled||resizing.load(std::memory_order_acquire))return;
 if(!swapchain){if(!try_initialize_device(chain))return;}
 if(chain!=swapchain){
  DXGI_SWAP_CHAIN_DESC replacement{};
  if(FAILED(chain->GetDesc(&replacement))||replacement.OutputWindow!=game_window||
     !suitable_window(replacement.OutputWindow))return;
  release_device();if(!try_initialize_device(chain))return;
 }
 if(!target && !create_target(chain))return;
 GuiContextScope gui_scope(imgui);
 feed_pending_input();
 const bool panel=editor_panel_visible()&&state.focused;
 auto& io=ImGui::GetIO();
 if(panel!=last_panel){io.ClearInputKeys();io.ClearInputMouse();last_panel=panel;}
 io.ConfigFlags=panel?ImGuiConfigFlags_NavEnableKeyboard:ImGuiConfigFlags_NoMouseCursorChange;
 io.MouseDrawCursor=panel;
 // Hidden panels perform no GPU work and do not draw hints into recordings.
 if(!panel){editor_text_input_active(false);return;}
 DeviceStateScope graphics_scope;
 ImGui_ImplDX11_NewFrame();ImGui_ImplWin32_NewFrame();ImGui::NewFrame();
 draw_panel(state);
 editor_text_input_active(ImGui::GetIO().WantTextInput);
 ImGui::Render();
 immediate->OMSetRenderTargets(1,&target,nullptr);
 ImGui_ImplDX11_RenderDrawData(ImGui::GetDrawData());
}

HRESULT STDMETHODCALLTYPE present_hook(IDXGISwapChain* chain,UINT interval,UINT flags) {
 static thread_local bool entered=false;
 if(entered)return original_present(chain,interval,flags);
 entered=true;
 if(enabled.load(std::memory_order_acquire) && !(flags&DXGI_PRESENT_TEST)){
  std::unique_lock<std::recursive_mutex> lock(render_mutex,std::try_to_lock);
  if(lock.owns_lock()){
   try{render_overlay(chain);}catch(...){last_error="DirectX editor stopped after a rendering error; external controls remain available.";release_device();enabled=false;}
  }
 }
 const HRESULT result=original_present(chain,interval,flags);
 if(result==DXGI_ERROR_DEVICE_REMOVED||result==DXGI_ERROR_DEVICE_RESET){
  std::lock_guard<std::recursive_mutex> lock(render_mutex);
  if(chain==swapchain){last_error="DirectX device reset; waiting for the game renderer to recover.";release_device();}
 }
 entered=false;return result;
}
HRESULT STDMETHODCALLTYPE resize_hook(IDXGISwapChain* chain,UINT count,UINT width,UINT height,DXGI_FORMAT format,UINT flags) {
 // ResizeBuffers requires ALL references to the backbuffer to be gone first.
 // Do not hold our mutex while the game/other overlays process the resize.
 bool game_resize=false;
 {
  std::lock_guard<std::recursive_mutex> lock(render_mutex);
  if(chain==swapchain){game_resize=true;resizing=true;release_target();}
 }
 const HRESULT result=original_resize(chain,count,width,height,format,flags);
 if(game_resize){
  std::lock_guard<std::recursive_mutex> lock(render_mutex);
  if(chain==swapchain&&(result==DXGI_ERROR_DEVICE_REMOVED||result==DXGI_ERROR_DEVICE_RESET))release_device();
  resizing=false;
 }
 return result; // Recreate lazily at the next Present, including failed resizes.
}

LRESULT CALLBACK window_proc(HWND window,UINT message,WPARAM wparam,LPARAM lparam) {
 auto previous=reinterpret_cast<WNDPROC>(GetPropW(window,kWindowProperty));
 bool consumed=false;LRESULT result=0;
 // Preserve UI input when the render thread is busy without blocking the
 // game's message thread (which a graphics driver may synchronously need).
 // Raw mouse data and editor bindings are handled immediately below.
 const bool release_message=message==WM_KEYUP||message==WM_SYSKEYUP||message==WM_LBUTTONUP||
     message==WM_RBUTTONUP||message==WM_MBUTTONUP||message==WM_XBUTTONUP;
 const bool ui_message=(message>=WM_KEYFIRST&&message<=WM_KEYLAST)||
     (message>=WM_MOUSEFIRST&&message<=WM_MOUSELAST)||message==WM_SETFOCUS||
     message==WM_KILLFOCUS||message==WM_SETCURSOR;
 if(window==game_window&&ui_message&&(editor_panel_visible()||release_message||
                                    message==WM_KILLFOCUS||message==WM_SETFOCUS)){
  std::unique_lock<std::recursive_mutex> lock(render_mutex,std::try_to_lock);
  if(lock.owns_lock()&&imgui){
   GuiContextScope scope(imgui);feed_pending_input();
   ImGui_ImplWin32_WndProcHandler(window,message,wparam,lparam);
  }else{
   std::lock_guard<std::mutex> input_lock(input_mutex);
   if(pending_input.size()>=512){pending_input.clear();input_overflow=true;}
   pending_input.push_back({window,message,wparam,lparam});
  }
 }
 if(enabled.load(std::memory_order_acquire)&&window==game_window)
  consumed=editor_window_message(window,message,wparam,lparam,result);
 if(message==WM_NCDESTROY){
  std::lock_guard<std::recursive_mutex> lock(render_mutex);
  if(window==game_window)release_device();
  RemovePropW(window,kWindowProperty);
 }
 if(consumed)return result;
 return previous?CallWindowProcW(previous,window,message,wparam,lparam):DefWindowProcW(window,message,wparam,lparam);
}

// Build a temporary hidden swapchain solely to discover public DXGI method
// addresses; never create graphics resources under the loader lock. Hardware
// first, WARP fallback supports headless/CI and systems without an adapter.
bool discover_swapchain_methods(void*& present,void*& resize) noexcept {
 const wchar_t class_name[]=L"DeadlockDolly.DX11.Discovery";
 const auto instance=GetModuleHandleW(nullptr);
 WNDCLASSW wc{};wc.lpfnWndProc=DefWindowProcW;wc.hInstance=instance;wc.lpszClassName=class_name;
 const ATOM atom=RegisterClassW(&wc);
 if(!atom)return false;
 HWND window=CreateWindowExW(0,class_name,L"",WS_OVERLAPPEDWINDOW,0,0,64,64,nullptr,nullptr,instance,nullptr);
 if(!window){UnregisterClassW(class_name,instance);return false;}
 DXGI_SWAP_CHAIN_DESC desc{};desc.BufferCount=1;desc.BufferDesc.Width=64;desc.BufferDesc.Height=64;
 desc.BufferDesc.Format=DXGI_FORMAT_R8G8B8A8_UNORM;desc.BufferUsage=DXGI_USAGE_RENDER_TARGET_OUTPUT;
 desc.OutputWindow=window;desc.SampleDesc.Count=1;desc.Windowed=TRUE;desc.SwapEffect=DXGI_SWAP_EFFECT_DISCARD;
 IDXGISwapChain* dummy=nullptr;ID3D11Device* dummy_device=nullptr;ID3D11DeviceContext* dummy_context=nullptr;
 const D3D_FEATURE_LEVEL levels[]={D3D_FEATURE_LEVEL_11_0,D3D_FEATURE_LEVEL_10_1,D3D_FEATURE_LEVEL_10_0};
 HRESULT hr=D3D11CreateDeviceAndSwapChain(nullptr,D3D_DRIVER_TYPE_HARDWARE,nullptr,0,levels,3,D3D11_SDK_VERSION,
                                        &desc,&dummy,&dummy_device,nullptr,&dummy_context);
 if(FAILED(hr)){
  release(dummy_context);release(dummy_device);release(dummy);
  hr=D3D11CreateDeviceAndSwapChain(nullptr,D3D_DRIVER_TYPE_WARP,nullptr,0,levels,3,D3D11_SDK_VERSION,
                                 &desc,&dummy,&dummy_device,nullptr,&dummy_context);
 }
 if(SUCCEEDED(hr)&&dummy){auto table=*reinterpret_cast<void***>(dummy);present=table[8];resize=table[13];}
 release(dummy_context);release(dummy_device);release(dummy);DestroyWindow(window);UnregisterClassW(class_name,instance);
 return present&&resize;
}
} // namespace

bool install_overlay_hooks() noexcept {
 if(installed.load(std::memory_order_acquire)){enabled=true;return true;}
 try{
  void* present=nullptr;void* resize=nullptr;
  if(!discover_swapchain_methods(present,resize)){last_error="Could not discover DirectX 11 presentation; external controls remain available.";return false;}
  if(MH_CreateHook(present,reinterpret_cast<void*>(present_hook),reinterpret_cast<void**>(&original_present))!=MH_OK){last_error="Could not attach the DirectX 11 presentation callback.";return false;}
  if(MH_CreateHook(resize,reinterpret_cast<void*>(resize_hook),reinterpret_cast<void**>(&original_resize))!=MH_OK){
   last_error="Could not attach the DirectX 11 resize callback.";MH_RemoveHook(present);return false;
  }
  if(MH_EnableHook(resize)!=MH_OK){last_error="Could not enable the DirectX 11 resize callback.";MH_RemoveHook(resize);MH_RemoveHook(present);return false;}
  if(MH_EnableHook(present)!=MH_OK){last_error="Could not enable the DirectX 11 presentation callback.";MH_DisableHook(resize);MH_RemoveHook(present);return false;}
  installed=true;enabled=true;return true;
 }catch(...){last_error="DirectX 11 overlay initialization failed.";enabled=false;return false;}
}
const char* overlay_last_error() noexcept{return last_error.load(std::memory_order_acquire);}
void shutdown_overlay() noexcept {
 enabled=false;last_error="In-game editor stopped.";
 try{std::lock_guard<std::recursive_mutex> lock(render_mutex);release_device();}catch(...){}
}
namespace {
void queue_raw_input(InputMessage message) noexcept {
 if(!enabled.load(std::memory_order_acquire)||!editor_panel_visible())return;
 message.window=game_window.load();if(!message.window)return;
 try{
  std::lock_guard<std::mutex> lock(input_mutex);
  if(pending_input.size()>=512){pending_input.clear();input_overflow=true;}
  pending_input.push_back(message);
 }catch(...){} // An input hook must never throw into the game's event pump.
}
}
void overlay_raw_mouse_button(int button,bool down) noexcept {
 if(button<0||button>4)return;
 InputMessage message{};message.raw_kind=1;message.button=button;message.down=down;queue_raw_input(message);
}
void overlay_raw_mouse_wheel(float vertical,float horizontal) noexcept {
 InputMessage message{};message.raw_kind=2;message.vertical=vertical;message.horizontal=horizontal;queue_raw_input(message);
}
void overlay_raw_key(unsigned virtual_key,unsigned scan_code,bool down,bool extended) noexcept {
 if(virtual_key>255)return;
 InputMessage message{};message.raw_kind=3;message.message=down?WM_KEYDOWN:WM_KEYUP;message.wparam=virtual_key;
 message.lparam=LPARAM(1u|((scan_code&255u)<<16)|(extended?1u<<24:0u)|(down?0u:3u<<30));
 queue_raw_input(message);
}
} // namespace dolly
