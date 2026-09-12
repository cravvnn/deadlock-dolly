// DirectX 11 in-game editor. This is an overlay on the game's swapchain, not a
// second window, console-command renderer, or replacement for path evaluation.
#include "dolly_overlay.hpp"
#include "dolly_editor.hpp"
#include "dolly_visualization.hpp"
#include "dolly_visualization_runtime.hpp"
#include "dolly_video.hpp"
#include "dolly_media.hpp"
#include "dolly_reshade.hpp"
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

extern IMGUI_IMPL_API LRESULT ImGui_ImplWin32_WndProcHandler(HWND, UINT, WPARAM, LPARAM);

namespace dolly {
namespace {
using PresentFn = HRESULT(STDMETHODCALLTYPE*)(IDXGISwapChain*, UINT, UINT);
using ResizeFn = HRESULT(STDMETHODCALLTYPE*)(IDXGISwapChain*, UINT, UINT, UINT, DXGI_FORMAT, UINT);
PresentFn original_present = nullptr;
ResizeFn original_resize = nullptr;
std::atomic<bool> installed{false}, enabled{false}, resizing{false};
// Set by the renderer probe when the engine's vertex-buffer retirement queue
// nears its 16-bit capacity. Stops Dolly's optional draw submissions.
std::atomic<bool> renderer_pressure{false};
std::atomic<std::uint64_t> diagnostic_present{0}, diagnostic_panel{0}, diagnostic_init_attempts{0},
    diagnostic_init_successes{0}, diagnostic_releases{0}, diagnostic_resizes{0};
std::atomic<std::uint64_t> diagnostic_draw{0}, diagnostic_guides{0}, diagnostic_overlay_last_us{0},
    diagnostic_overlay_max_us{0}, diagnostic_present_last_us{0}, diagnostic_present_max_us{0},
    diagnostic_lock_skips{0}, diagnostic_overlay_active_ms{0}, diagnostic_present_active_ms{0},
    diagnostic_guide_lines{0}, diagnostic_guide_labels{0};
std::atomic<const char*> last_error{"Waiting for the DirectX 11 game window."};
std::recursive_mutex render_mutex;
struct InputMessage {
    HWND window;
    UINT message = 0;
    WPARAM wparam = 0;
    LPARAM lparam = 0;
    unsigned raw_kind = 0;
    int button = 0;
    bool down = false;
    float vertical = 0, horizontal = 0;
};
std::mutex input_mutex;
std::deque<InputMessage> pending_input;
bool input_overflow = false;
IDXGISwapChain* swapchain = nullptr; // Identity only: do not retain a dead game's swapchain.
ID3D11Device* device = nullptr;
ID3D11DeviceContext* immediate = nullptr;
ID3D11DeviceContext1* context1 = nullptr;
ID3DDeviceContextState* overlay_state = nullptr;
ID3D11RenderTargetView* target = nullptr;
ImGuiContext* imgui = nullptr;
ImFont* panel_font = nullptr;
ImFont* heading_font = nullptr;
ImFont* title_font = nullptr;
float panel_scale = 1.0f;
std::atomic<HWND> game_window{nullptr};
bool win32_ready = false, dx11_ready = false, last_panel = false;
bool media_suspended = false;
bool show_path_guides = true;
VisualizationGeometry guide_geometry;
ULONGLONG next_initialization = 0;
constexpr wchar_t kWindowProperty[] = L"DeadlockDolly.Overlay.OriginalWindowProcedure";

// Serialize our ImGui context across Present and window-message callbacks,
// restoring the prior context around every call into our backends. Other DLLs
// embedding ImGui retain their own context and render state.
struct GuiContextScope {
    ImGuiContext* previous;
    explicit GuiContextScope(ImGuiContext* current) : previous(ImGui::GetCurrentContext()) {
        ImGui::SetCurrentContext(current);
    }
    ~GuiContextScope() { ImGui::SetCurrentContext(previous); }
};

// Distinguish work inside Dolly from a slow original Present. These counters
// are observational only: they never skip graphics work, flush the device, or
// change replay speed. The active timestamp also survives a stalled call.
struct DiagnosticTimer {
    std::atomic<std::uint64_t>& last;
    std::atomic<std::uint64_t>& maximum;
    std::atomic<std::uint64_t>& active_since;
    LARGE_INTEGER begin{};
    static LONGLONG frequency() noexcept {
        static const LONGLONG value = []() {
            LARGE_INTEGER f{};
            QueryPerformanceFrequency(&f);
            return f.QuadPart;
        }();
        return value;
    }
    DiagnosticTimer(std::atomic<std::uint64_t>& last_value,
                    std::atomic<std::uint64_t>& maximum_value,
                    std::atomic<std::uint64_t>& active_value) noexcept
        : last(last_value), maximum(maximum_value), active_since(active_value) {
        active_since.store(GetTickCount64(), std::memory_order_relaxed);
        QueryPerformanceCounter(&begin);
    }
    ~DiagnosticTimer() {
        LARGE_INTEGER end{};
        QueryPerformanceCounter(&end);
        const auto hz = frequency();
        const auto duration =
            hz > 0 && end.QuadPart >= begin.QuadPart
                ? std::uint64_t(double(end.QuadPart - begin.QuadPart) * 1000000.0 / double(hz))
                : 0;
        last.store(duration, std::memory_order_relaxed);
        auto prior = maximum.load(std::memory_order_relaxed);
        while (prior < duration &&
               !maximum.compare_exchange_weak(prior, duration, std::memory_order_relaxed)) {
        }
        active_since.store(0, std::memory_order_relaxed);
    }
};

template <typename T> void release(T*& object) noexcept {
    if (object) {
        object->Release();
        object = nullptr;
    }
}
LRESULT CALLBACK window_proc(HWND, UINT, WPARAM, LPARAM);

void release_target() noexcept {
    release(target);
}
void clear_pending_input() {
    std::lock_guard<std::mutex> lock(input_mutex);
    pending_input.clear();
    input_overflow = false;
}
void feed_window_input(const InputMessage& message) {
    // The official Win32 mouse handler changes OS capture and cursor ownership.
    // Never replay those side effects from Present: the game's window thread
    // may be waiting for this frame. Dolly also must not release a mouse capture
    // that belongs to Deadlock's hero/replay UI. Mouse events only update ImGui.
    auto& io = ImGui::GetIO();
    const auto msg = message.message;
    int button = -1;
    bool down = false;
    switch (msg) {
    case WM_LBUTTONDOWN:
    case WM_LBUTTONDBLCLK:
        button = 0;
        down = true;
        break;
    case WM_RBUTTONDOWN:
    case WM_RBUTTONDBLCLK:
        button = 1;
        down = true;
        break;
    case WM_MBUTTONDOWN:
    case WM_MBUTTONDBLCLK:
        button = 2;
        down = true;
        break;
    case WM_XBUTTONDOWN:
    case WM_XBUTTONDBLCLK:
        button = HIWORD(message.wparam) == XBUTTON1 ? 3 : 4;
        down = true;
        break;
    case WM_LBUTTONUP:
        button = 0;
        break;
    case WM_RBUTTONUP:
        button = 1;
        break;
    case WM_MBUTTONUP:
        button = 2;
        break;
    case WM_XBUTTONUP:
        button = HIWORD(message.wparam) == XBUTTON1 ? 3 : 4;
        break;
    case WM_MOUSEMOVE:
        io.AddMousePosEvent(float(static_cast<short>(LOWORD(message.lparam))),
                            float(static_cast<short>(HIWORD(message.lparam))));
        return;
    case WM_MOUSEWHEEL:
        io.AddMouseWheelEvent(0, float(static_cast<short>(HIWORD(message.wparam))) / WHEEL_DELTA);
        return;
    case WM_MOUSEHWHEEL:
        io.AddMouseWheelEvent(-float(static_cast<short>(HIWORD(message.wparam))) / WHEEL_DELTA, 0);
        return;
    case WM_SETCURSOR:
        return; // Native window-message handling owns the OS cursor.
    default:
        break;
    }
    if (button >= 0) {
        io.AddMouseButtonEvent(button, down);
        return;
    }
    // Keyboard, text and focus handling do not change Win32 mouse ownership.
    if ((msg >= WM_KEYFIRST && msg <= WM_KEYLAST) || msg == WM_SETFOCUS || msg == WM_KILLFOCUS)
        ImGui_ImplWin32_WndProcHandler(message.window, msg, message.wparam, message.lparam);
}
void feed_pending_input() {
    std::deque<InputMessage> messages;
    bool overflow = false;
    {
        std::lock_guard<std::mutex> lock(input_mutex);
        messages.swap(pending_input);
        overflow = input_overflow;
        input_overflow = false;
    }
    if (overflow) {
        ImGui::GetIO().ClearInputKeys();
        ImGui::GetIO().ClearInputMouse();
    }
    for (const auto& message : messages)
        if (message.window == game_window) {
            auto& io = ImGui::GetIO();
            if (message.raw_kind == 1)
                io.AddMouseButtonEvent(message.button, message.down);
            else if (message.raw_kind == 2)
                io.AddMouseWheelEvent(message.horizontal, message.vertical);
            else {
                feed_window_input(message);
                if (message.raw_kind == 3) {
                    io.AddKeyEvent(ImGuiMod_Ctrl, (GetAsyncKeyState(VK_CONTROL) & 0x8000) != 0);
                    io.AddKeyEvent(ImGuiMod_Shift, (GetAsyncKeyState(VK_SHIFT) & 0x8000) != 0);
                    io.AddKeyEvent(ImGuiMod_Alt, (GetAsyncKeyState(VK_MENU) & 0x8000) != 0);
                    io.AddKeyEvent(ImGuiMod_Super, (GetAsyncKeyState(VK_LWIN) & 0x8000) ||
                                                       (GetAsyncKeyState(VK_RWIN) & 0x8000));
                }
            }
        }
}
void release_device() noexcept {
    video::reset_resources();
    reshade_release_device();
    if (device || imgui)
        diagnostic_releases.fetch_add(1, std::memory_order_relaxed);
    editor_overlay_available(false);
    editor_text_input_active(false);
    clear_pending_input();
    release_target();
    if (imgui) {
        auto* previous = ImGui::GetCurrentContext();
        ImGui::SetCurrentContext(imgui);
        if (dx11_ready)
            ImGui_ImplDX11_Shutdown();
        if (win32_ready)
            ImGui_ImplWin32_Shutdown();
        ImGui::DestroyContext(imgui);
        ImGui::SetCurrentContext(previous == imgui ? nullptr : previous);
    }
    imgui = nullptr;
    panel_font = heading_font = title_font = nullptr;
    panel_scale = 1.0f;
    dx11_ready = win32_ready = last_panel = false;
    release(overlay_state);
    release(context1);
    release(immediate);
    release(device);
    if (game_window && IsWindow(game_window)) {
        auto previous = reinterpret_cast<WNDPROC>(GetPropW(game_window, kWindowProperty));
        // Another overlay may have chained our callback. Never overwrite its hook.
        // In that case the property stays until WM_NCDESTROY and our callback simply
        // forwards. The module is pinned so that chained callback remains valid.
        if (previous && reinterpret_cast<WNDPROC>(GetWindowLongPtrW(game_window, GWLP_WNDPROC)) ==
                            window_proc) {
            SetWindowLongPtrW(game_window, GWLP_WNDPROC, reinterpret_cast<LONG_PTR>(previous));
            RemovePropW(game_window, kWindowProperty);
        }
    }
    game_window = nullptr;
    swapchain = nullptr;
    editor_attach_window(nullptr);
}

bool suitable_window(HWND window) noexcept {
    DWORD pid = 0;
    GetWindowThreadProcessId(window, &pid);
    RECT bounds{};
    return pid == GetCurrentProcessId() && IsWindowVisible(window) &&
           GetAncestor(window, GA_ROOT) == window && GetClientRect(window, &bounds) &&
           bounds.right - bounds.left >= 320 && bounds.bottom - bounds.top >= 200;
}

bool create_target(IDXGISwapChain* chain) noexcept {
    ID3D11Texture2D* buffer = nullptr;
    if (FAILED(chain->GetBuffer(0, __uuidof(ID3D11Texture2D), reinterpret_cast<void**>(&buffer))))
        return false;
    const HRESULT result = device->CreateRenderTargetView(buffer, nullptr, &target);
    buffer->Release();
    return SUCCEEDED(result);
}

ImVec4 panel_color(unsigned rgb, float alpha = 1.0f) {
    return ImVec4(float((rgb >> 16) & 255) / 255.0f, float((rgb >> 8) & 255) / 255.0f,
                  float(rgb & 255) / 255.0f, alpha);
}
void style_panel() {
    ImGui::StyleColorsDark();
    auto& style = ImGui::GetStyle();
    // Keep the in-game editor on the launcher's slate/teal palette.
    style.WindowPadding = ImVec2(20, 18);
    style.FramePadding = ImVec2(12, 8);
    style.ItemSpacing = ImVec2(10, 8);
    style.ItemInnerSpacing = ImVec2(8, 6);
    style.WindowRounding = 12;
    style.ChildRounding = 8;
    style.FrameRounding = 5;
    style.PopupRounding = 6;
    style.GrabRounding = 5;
    style.ScrollbarRounding = 6;
    style.WindowBorderSize = 1;
    style.ChildBorderSize = 0;
    style.FrameBorderSize = 0;
    style.ScrollbarSize = 10;
    style.GrabMinSize = 14;
    style.DisabledAlpha = .45f;
    style.Colors[ImGuiCol_WindowBg] = panel_color(0x10151c, .985f);
    style.Colors[ImGuiCol_ChildBg] = panel_color(0x191f28);
    style.Colors[ImGuiCol_PopupBg] = panel_color(0x191f28);
    style.Colors[ImGuiCol_Border] = panel_color(0x303c49);
    style.Colors[ImGuiCol_Text] = panel_color(0xe8edf3);
    style.Colors[ImGuiCol_TextDisabled] = panel_color(0x8f9eae);
    style.Colors[ImGuiCol_Button] = panel_color(0x28323f);
    style.Colors[ImGuiCol_ButtonHovered] = panel_color(0x374757);
    style.Colors[ImGuiCol_ButtonActive] = panel_color(0x435768);
    style.Colors[ImGuiCol_FrameBg] = panel_color(0x0e131a);
    style.Colors[ImGuiCol_FrameBgHovered] = panel_color(0x25313e);
    style.Colors[ImGuiCol_FrameBgActive] = panel_color(0x2f4150);
    style.Colors[ImGuiCol_SliderGrab] = panel_color(0x64d6c3);
    style.Colors[ImGuiCol_SliderGrabActive] = panel_color(0x8ee7d9);
    style.Colors[ImGuiCol_CheckMark] = panel_color(0x64d6c3);
    style.Colors[ImGuiCol_Header] = panel_color(0x274e4b);
    style.Colors[ImGuiCol_HeaderHovered] = style.Colors[ImGuiCol_ButtonHovered];
    style.Colors[ImGuiCol_HeaderActive] = style.Colors[ImGuiCol_ButtonActive];
    style.Colors[ImGuiCol_Separator] = panel_color(0x303c49);
    style.Colors[ImGuiCol_ScrollbarBg] = panel_color(0x10151c, 0);
    style.Colors[ImGuiCol_ScrollbarGrab] = panel_color(0x354150);
    style.Colors[ImGuiCol_ScrollbarGrabHovered] = panel_color(0x526577);
    style.Colors[ImGuiCol_ScrollbarGrabActive] = panel_color(0x64d6c3);
    style.Colors[ImGuiCol_ResizeGrip] = panel_color(0x64d6c3, .15f);
    style.Colors[ImGuiCol_ResizeGripHovered] = panel_color(0x64d6c3, .45f);
    style.Colors[ImGuiCol_ResizeGripActive] = panel_color(0x64d6c3, .75f);
    style.Colors[ImGuiCol_NavCursor] = panel_color(0x64d6c3);
    style.Colors[ImGuiCol_TextSelectedBg] = panel_color(0x274e4b);
    style.Colors[ImGuiCol_PlotHistogram] = panel_color(0x64d6c3);
    style.Colors[ImGuiCol_Tab] = panel_color(0x1b232d);
    style.Colors[ImGuiCol_TabHovered] = panel_color(0x2b3a47);
    style.Colors[ImGuiCol_TabSelected] = panel_color(0x274e4b);
    style.Colors[ImGuiCol_TabSelectedOverline] = panel_color(0x64d6c3);
    style.Colors[ImGuiCol_TabDimmed] = panel_color(0x161d25);
    style.Colors[ImGuiCol_TabDimmedSelected] = panel_color(0x203a39);
    style.Colors[ImGuiCol_TabDimmedSelectedOverline] = panel_color(0x3f6f68);
}
ImFont* installed_font(const char* filename, float size) {
    char windows[MAX_PATH]{}, path[MAX_PATH]{};
    const UINT length = GetWindowsDirectoryA(windows, MAX_PATH);
    if (!length || length >= MAX_PATH)
        return nullptr;
    const int written = std::snprintf(path, sizeof(path), "%s\\Fonts\\%s", windows, filename);
    if (written < 0 || written >= int(sizeof(path)))
        return nullptr;
    const DWORD attributes = GetFileAttributesA(path);
    if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & FILE_ATTRIBUTE_DIRECTORY))
        return nullptr;
    ImFontConfig config;
    config.OversampleH = 2;
    config.OversampleV = 2;
    return ImGui::GetIO().Fonts->AddFontFromFileTTF(path, size, &config);
}
void load_panel_fonts(float scale) {
    // Read the fonts already installed with Windows; no system fonts are
    // distributed with Dolly. Keep a working fallback on minimal Windows images.
    panel_font = installed_font("segoeui.ttf", 16.0f * scale);
    if (!panel_font)
        panel_font = installed_font("arial.ttf", 16.0f * scale);
    if (!panel_font) {
        ImFontConfig config;
        config.SizePixels = 16.0f * scale;
        panel_font = ImGui::GetIO().Fonts->AddFontDefault(&config);
    }
    heading_font = installed_font("seguisb.ttf", 16.0f * scale);
    if (!heading_font)
        heading_font = panel_font;
    title_font = installed_font("seguisb.ttf", 22.0f * scale);
    if (!title_font)
        title_font = heading_font;
    ImGui::GetIO().FontDefault = panel_font;
}

// A complete state swap is preferable to assuming what the game or a ReShade
// effect left bound at Present. Restore before the original Present executes.
struct DeviceStateScope {
    ID3DDeviceContextState* saved = nullptr;
    DeviceStateScope() { context1->SwapDeviceContextState(overlay_state, &saved); }
    ~DeviceStateScope() {
        // Context-state objects retain bound targets too. Clear our target before
        // swapping away so ResizeBuffers can release the last backbuffer reference.
        immediate->OMSetRenderTargets(0, nullptr, nullptr);
        context1->SwapDeviceContextState(saved, nullptr);
        release(saved);
    }
};

bool initialize_device(IDXGISwapChain* chain) {
    diagnostic_init_attempts.fetch_add(1, std::memory_order_relaxed);
    DXGI_SWAP_CHAIN_DESC description{};
    if (FAILED(chain->GetDesc(&description)) || !suitable_window(description.OutputWindow))
        return false;
    if (FAILED(chain->GetDevice(__uuidof(ID3D11Device), reinterpret_cast<void**>(&device)))) {
        last_error = "The presenting game window is not using DirectX 11.";
        return false;
    }
    device->GetImmediateContext(&immediate);
    ID3D11Device1* device1 = nullptr;
    if (!immediate ||
        FAILED(
            device->QueryInterface(__uuidof(ID3D11Device1), reinterpret_cast<void**>(&device1))) ||
        FAILED(immediate->QueryInterface(__uuidof(ID3D11DeviceContext1),
                                         reinterpret_cast<void**>(&context1)))) {
        last_error = "The DirectX 11.1 context API is unavailable; in-game editor disabled.";
        release(device1);
        release_device();
        return false;
    }
    const D3D_FEATURE_LEVEL feature = device->GetFeatureLevel();
    D3D_FEATURE_LEVEL chosen{};
    const HRESULT state_result = device1->CreateDeviceContextState(
        0, &feature, 1, D3D11_SDK_VERSION, __uuidof(ID3D11Device), &chosen, &overlay_state);
    device1->Release();
    if (FAILED(state_result)) {
        last_error = "Could not create an isolated DirectX state for the editor.";
        release_device();
        return false;
    }
    // Save ALL D3D state with the Windows 10 D3D11.1 context API. The upstream
    // renderer additionally saves its draw state, but does not restore HS/DS/CS
    // shaders or output UAVs. Context swapping covers those and other overlays.
    game_window = description.OutputWindow;
    swapchain = chain;
    if (!create_target(chain)) {
        last_error = "Could not create the editor render target.";
        release_device();
        return false;
    }
    IMGUI_CHECKVERSION();
    auto* previous = ImGui::GetCurrentContext();
    imgui = ImGui::CreateContext();
    {
        GuiContextScope scope(imgui);
        auto& io = ImGui::GetIO();
        io.IniFilename = nullptr;
        io.LogFilename = nullptr;
        io.ConfigFlags = ImGuiConfigFlags_NoMouseCursorChange;
        style_panel();
        panel_scale = std::clamp(ImGui_ImplWin32_GetDpiScaleForHwnd(game_window), 1.0f, 2.5f);
        load_panel_fonts(panel_scale);
        ImGui::GetStyle().ScaleAllSizes(panel_scale);
        win32_ready = ImGui_ImplWin32_Init(game_window);
        if (win32_ready)
            dx11_ready = ImGui_ImplDX11_Init(device, immediate);
    }
    ImGui::SetCurrentContext(previous);
    if (!win32_ready || !dx11_ready) {
        last_error = "Could not initialize the DirectX 11 editor UI.";
        release_device();
        return false;
    }
    bool gpu_ready = false;
    {
        GuiContextScope scope(imgui);
        DeviceStateScope graphics;
        gpu_ready = ImGui_ImplDX11_CreateDeviceObjects();
    }
    if (!gpu_ready) {
        last_error =
            "Could not compile or create the editor shaders; in-game controls remain disabled.";
        release_device();
        return false;
    }
    // Install exactly once on the actual swapchain window. A property retains
    // its original callback even if another overlay chains us during a resize.
    auto prior = reinterpret_cast<WNDPROC>(GetWindowLongPtrW(game_window, GWLP_WNDPROC));
    auto retained = reinterpret_cast<WNDPROC>(GetPropW(game_window, kWindowProperty));
    if (retained && prior == retained) {
        RemovePropW(game_window, kWindowProperty);
        retained = nullptr;
    }
    // If a second overlay chained our window procedure, it remains in that
    // chain after device loss. Reuse it instead of wrapping the chain twice.
    if (!retained) {
        if (!prior || !SetPropW(game_window, kWindowProperty, reinterpret_cast<HANDLE>(prior))) {
            last_error = "Could not attach the editor to the game window.";
            release_device();
            return false;
        }
        SetLastError(0);
        auto replaced =
            SetWindowLongPtrW(game_window, GWLP_WNDPROC, reinterpret_cast<LONG_PTR>(window_proc));
        if (!replaced && GetLastError() != 0) {
            last_error = "Could not attach editor input to the game window.";
            RemovePropW(game_window, kWindowProperty);
            release_device();
            return false;
        }
        // Preserve the actual previous callback if another hook raced the read.
        if (replaced)
            SetPropW(game_window, kWindowProperty, reinterpret_cast<HANDLE>(replaced));
    }
    last_error = "";
    editor_attach_window(game_window);
    editor_overlay_available(true);
    diagnostic_init_successes.fetch_add(1, std::memory_order_relaxed);
    return true;
}

void action_button(const char* label, EditorAction action, float width = 0, double value = 0,
                   bool primary = false) {
    if (primary) {
        ImGui::PushStyleColor(ImGuiCol_Button, panel_color(0x64d6c3));
        ImGui::PushStyleColor(ImGuiCol_ButtonHovered, panel_color(0x8ee7d9));
        ImGui::PushStyleColor(ImGuiCol_ButtonActive, panel_color(0x44baa7));
        ImGui::PushStyleColor(ImGuiCol_Text, panel_color(0x092620));
        ImGui::PushFont(heading_font);
    }
    if (ImGui::Button(label, ImVec2(width, 0)))
        editor_enqueue(action, value);
    if (primary) {
        ImGui::PopFont();
        ImGui::PopStyleColor(4);
    }
}
void section_title(const char* title, const char* detail = nullptr) {
    ImGui::PushFont(heading_font);
    ImGui::TextUnformatted(title);
    ImGui::PopFont();
    if (detail) {
        const float width = ImGui::CalcTextSize(detail).x;
        const float right = ImGui::GetWindowContentRegionMax().x;
        if (ImGui::GetItemRectSize().x + width + 24 * panel_scale <
            ImGui::GetContentRegionAvail().x) {
            ImGui::SameLine(right - width);
            ImGui::TextDisabled("%s", detail);
        }
    }
}
bool begin_panel_card(const char* name) {
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(14 * panel_scale, 12 * panel_scale));
    ImGui::PushStyleColor(ImGuiCol_ChildBg, panel_color(0x191f28));
    return ImGui::BeginChild(name, ImVec2(0, 0),
                             ImGuiChildFlags_AutoResizeY | ImGuiChildFlags_AlwaysUseWindowPadding,
                             ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse);
}
void end_panel_card() {
    ImGui::EndChild();
    ImGui::PopStyleColor();
    ImGui::PopStyleVar();
}
void replay_badge(const EditorSnapshot& state) {
    const char* text = state.busy      ? "Working"
                       : !state.ready  ? "Waiting for replay"
                       : state.playing ? "Playing shot"
                       : state.paused  ? "Paused"
                                       : "Playing";
    const ImVec2 start = ImGui::GetCursorScreenPos();
    const ImVec2 text_size = ImGui::CalcTextSize(text);
    const ImVec2 size(text_size.x + 30 * panel_scale, text_size.y + 10 * panel_scale);
    ImGui::GetWindowDrawList()->AddRectFilled(start, ImVec2(start.x + size.x, start.y + size.y),
                                              ImGui::GetColorU32(panel_color(0x203a39)),
                                              5 * panel_scale);
    ImGui::GetWindowDrawList()->AddCircleFilled(
        ImVec2(start.x + 11 * panel_scale, start.y + size.y / 2), 3 * panel_scale,
        ImGui::GetColorU32(panel_color(0x64d6c3)));
    ImGui::GetWindowDrawList()->AddText(
        ImVec2(start.x + 20 * panel_scale, start.y + 5 * panel_scale),
        ImGui::GetColorU32(panel_color(0x9be5d9)), text);
    ImGui::Dummy(size);
    if (state.tick > 0) {
        char tick[48]{};
        std::snprintf(tick, sizeof(tick), "Replay tick %d", state.tick);
        const float width = ImGui::CalcTextSize(tick).x;
        if (size.x + width + 12 * panel_scale < ImGui::GetContentRegionAvail().x) {
            ImGui::SameLine(ImGui::GetWindowContentRegionMax().x - width);
            ImGui::SetCursorPosY(ImGui::GetCursorPosY() + 5 * panel_scale);
            ImGui::TextDisabled("%s", tick);
        }
    }
}
bool guides_visible(const EditorSnapshot& state) noexcept {
    return show_path_guides && state.enabled && state.focused && state.ready && state.paused &&
           state.manual_active && !state.playing && !state.busy && state.view_width > 0 &&
           state.view_height > 0 &&
           (state.owner == EditorOwner::Flight || state.owner == EditorOwner::Panel);
}
void draw_path_guides(const EditorSnapshot& state,
                      const std::shared_ptr<const VisualizationPath>& path) {
    if (!path || !path->enabled() || !guides_visible(state))
        return;
    const auto size = ImGui::GetIO().DisplaySize;
    VisualizationView view{state.pose, state.horizontal_fov, double(size.x), double(size.y), 1};
    if (!project_visualization(*path, view, guide_geometry))
        return;
    auto* draw = ImGui::GetBackgroundDrawList();
    draw->PushClipRect(ImVec2(0, 0), size, true);
    for (std::size_t i = 0; i < guide_geometry.line_count; ++i) {
        const auto& line = guide_geometry.lines[i];
        const bool selected = line.kind == VisualizationKind::SelectedCamera;
        const auto color = selected                               ? IM_COL32(255, 205, 115, 245)
                           : line.kind == VisualizationKind::Path ? IM_COL32(100, 214, 195, 205)
                                                                  : IM_COL32(153, 185, 226, 220);
        const ImVec2 a(line.a.x, line.a.y), b(line.b.x, line.b.y);
        const float width = (selected ? 2.0f : 1.3f) * panel_scale;
        draw->AddLine(a, b, IM_COL32(8, 12, 17, 170), width + 2 * panel_scale);
        draw->AddLine(a, b, color, width);
    }
    for (std::size_t i = 0; i < guide_geometry.label_count; ++i) {
        const auto& label = guide_geometry.labels[i];
        char text[32]{};
        std::snprintf(text, sizeof(text), "%u", label.camera_index + 1);
        const ImVec2 point(label.point.x + 5 * panel_scale, label.point.y + 5 * panel_scale);
        const auto measured = ImGui::CalcTextSize(text);
        draw->AddRectFilled(
            ImVec2(point.x - 3 * panel_scale, point.y - 2 * panel_scale),
            ImVec2(point.x + measured.x + 3 * panel_scale, point.y + measured.y + 2 * panel_scale),
            IM_COL32(14, 19, 26, 210), 3 * panel_scale);
        draw->AddText(point,
                      label.selected ? IM_COL32(255, 205, 115, 255) : IM_COL32(208, 223, 243, 255),
                      text);
    }
    draw->PopClipRect();
}
// Export encoder labels match the desktop Export tab's codec list; the numeric
// IDs are the same dolly::video::Codec values sent over the editor wire.
const char* video_codec_label(std::uint32_t id) noexcept {
    switch (id) {
    case 1:
        return "NVIDIA H.264 (NVENC)";
    case 2:
        return "NVIDIA HEVC (NVENC)";
    case 3:
        return "H.264 (Media Foundation)";
    case 4:
        return "Software H.264 (x264)";
    case 5:
        return "Software HEVC (x265)";
    case 6:
        return "Intel H.264 (Quick Sync)";
    case 7:
        return "Intel HEVC (Quick Sync)";
    case 8:
        return "AMD H.264 (AMF)";
    case 9:
        return "AMD HEVC (AMF)";
    case 10:
        return "Lossless FFV1 (.mkv)";
    default:
        return "Auto (hardware when available)";
    }
}
void draw_panel(const EditorSnapshot& state) {
    auto& io = ImGui::GetIO();
    const float margin =
        std::min(20.0f * panel_scale, std::min(io.DisplaySize.x, io.DisplaySize.y) * .04f);
    const ImVec2 maximum(std::max(1.0f, io.DisplaySize.x - 2 * margin),
                         std::max(1.0f, io.DisplaySize.y - 2 * margin));
    ImGui::SetNextWindowPos(ImVec2(margin, margin), ImGuiCond_FirstUseEver);
    // Open large enough for the fullest page (Export) so nothing needs a manual
    // resize; the window stays resizable and is clamped to the game window. The
    // width stays clear of the guide overlay's right-hand region.
    ImGui::SetNextWindowSize(ImVec2(std::min(500.0f * panel_scale, maximum.x), maximum.y),
                             ImGuiCond_FirstUseEver);
    ImGui::SetNextWindowSizeConstraints(ImVec2(std::min(380.0f * panel_scale, maximum.x),
                                               std::min(360.0f * panel_scale, maximum.y)),
                                        maximum);
    bool close = false;
    if (ImGui::Begin("DEADLOCK DOLLY", nullptr,
                     ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoCollapse |
                         ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse)) {
        // Keep the panel reachable when the game changes resolution while it is
        // open, but never fight an active drag or resize: clamping mid-interaction
        // makes the panel stick to one axis and only settle once the mouse is
        // released. Clamp on the frame after the button comes up.
        if (!ImGui::IsMouseDown(ImGuiMouseButton_Left)) {
            const auto position = ImGui::GetWindowPos();
            const auto size = ImGui::GetWindowSize();
            ImGui::SetWindowPos(
                ImVec2(std::clamp(position.x, margin,
                                  std::max(margin, io.DisplaySize.x - size.x - margin)),
                       std::clamp(position.y, margin,
                                  std::max(margin, io.DisplaySize.y - size.y - margin))));
        }
        const float close_width =
            ImGui::CalcTextSize("Close").x + ImGui::GetStyle().FramePadding.x * 2;
        const float right = ImGui::GetWindowContentRegionMax().x;
        ImGui::PushFont(title_font);
        const bool compact_title =
            ImGui::CalcTextSize("DEADLOCK DOLLY").x + close_width + 12 * panel_scale >
            ImGui::GetContentRegionAvail().x;
        if (compact_title) {
            ImGui::PopFont();
            ImGui::PushFont(heading_font);
        }
        ImGui::AlignTextToFramePadding();
        ImGui::TextUnformatted("DEADLOCK DOLLY");
        ImGui::PopFont();
        ImGui::SameLine(right - close_width);
        close = ImGui::Button("Close");
        ImGui::TextWrapped("%s", state.shot_name[0] ? state.shot_name : "Untitled shot");
        replay_badge(state);
        ImGui::Spacing();
        // One vertical scroll region holds the cards; essential exit/stop controls
        // stay visible. There are no nested horizontal scrollbars at small sizes.
        const float message_height = state.message[0]
                                         ? ImGui::CalcTextSize(state.message, nullptr, false,
                                                               ImGui::GetContentRegionAvail().x)
                                                   .y +
                                               ImGui::GetStyle().ItemSpacing.y
                                         : 0;
        const float footer_height = ImGui::GetFrameHeightWithSpacing() +
                                    ImGui::GetTextLineHeightWithSpacing() + message_height +
                                    8 * panel_scale;
        ImGui::PushStyleColor(ImGuiCol_ChildBg, ImVec4(0, 0, 0, 0));
        if (ImGui::BeginChild("##editor-content", ImVec2(0, -footer_height),
                              ImGuiChildFlags_None)) {
            ImGui::BeginTabBar("##dolly-pages", ImGuiTabBarFlags_None);
            if (ImGui::BeginTabItem("Editor")) {
                ImGui::BeginDisabled(!state.ready || state.busy);
                if (begin_panel_card("##cameras-card")) {
                    char count[32]{};
                    std::snprintf(count, sizeof(count), "%u saved", state.camera_count);
                    section_title("Cameras", count);
                    action_button(state.camera_count ? "Capture camera here" : "Start path here",
                                  EditorAction::Capture, ImGui::GetContentRegionAvail().x, 0, true);
                    ImGui::BeginDisabled(!state.camera_count);
                    char selected[64]{};
                    if (state.camera_count)
                        std::snprintf(selected, sizeof(selected), "View %u of %u",
                                      state.selected_camera + 1, state.camera_count);
                    else
                        std::snprintf(selected, sizeof(selected), "No camera views yet");
                    const float replace_width =
                        ImGui::CalcTextSize("Replace").x + ImGui::GetStyle().FramePadding.x * 2;
                    ImGui::SetNextItemWidth(std::max(1.0f, ImGui::GetContentRegionAvail().x -
                                                               replace_width -
                                                               ImGui::GetStyle().ItemSpacing.x));
                    if (ImGui::BeginCombo("##selected-camera", selected)) {
                        const std::uint32_t count =
                            std::min<std::uint32_t>(state.camera_count, 10000);
                        for (std::uint32_t i = 0; i < count; ++i) {
                            char name[48]{};
                            std::snprintf(name, sizeof(name), "View %u", i + 1);
                            if (ImGui::Selectable(name, i == state.selected_camera))
                                editor_enqueue(EditorAction::SelectView, double(i));
                            if (i == state.selected_camera)
                                ImGui::SetItemDefaultFocus();
                        }
                        ImGui::EndCombo();
                    }
                    ImGui::SameLine();
                    action_button("Replace", EditorAction::Replace, replace_width);
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip("Replace the selected view with the current camera.");
                    const float half =
                        (ImGui::GetContentRegionAvail().x - ImGui::GetStyle().ItemSpacing.x) / 2;
                    action_button("Previous view", EditorAction::PreviousView, half);
                    ImGui::SameLine();
                    action_button("Next view", EditorAction::NextView, half);
                    ImGui::EndDisabled();
                    ImGui::Spacing();
                    ImGui::Checkbox("Show path guides", &show_path_guides);
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip(
                            "Camera positions and spline while editing a paused replay. The selected camera is gold. Guides show through walls and hide during playback.");
                    const auto guides = visualization_snapshot();
                    if (show_path_guides && state.camera_count && !guides) {
                        const auto viewer_state = visualization_runtime_state();
                        if (viewer_state == VisualizationRuntimeState::Invalid ||
                            viewer_state == VisualizationRuntimeState::Unavailable)
                            ImGui::TextWrapped(
                                "Path guides unavailable. Export diagnostics from the desktop editor.");
                    }
                    if (show_path_guides && guides &&
                        guides->camera_count() > guides->cameras().size())
                        ImGui::TextDisabled("%u of %u camera markers; selected included",
                                            unsigned(guides->cameras().size()),
                                            unsigned(guides->camera_count()));
                }
                end_panel_card();
                if (begin_panel_card("##replay-card")) {
                    char timing[64]{};
                    if (state.duration > 0)
                        std::snprintf(timing, sizeof(timing), "%.2f / %.2f s", state.phase,
                                      state.duration);
                    section_title("Replay", timing[0] ? timing : nullptr);
                    if (state.duration > 0) {
                        ImGui::ProgressBar(
                            std::clamp(float(state.phase / state.duration), 0.0f, 1.0f),
                            ImVec2(-1, 4 * panel_scale), "");
                        ImGui::Spacing();
                    }
                    const float half =
                        (ImGui::GetContentRegionAvail().x - ImGui::GetStyle().ItemSpacing.x) / 2;
                    ImGui::BeginDisabled(state.camera_count < 2);
                    action_button("Play shot", EditorAction::PlayPath, half);
                    ImGui::EndDisabled();
                    ImGui::SameLine();
                    action_button(state.paused ? "Play replay" : "Pause replay",
                                  EditorAction::PlayPause, half);
                    action_button("Back 1 second", EditorAction::SeekBack, half);
                    ImGui::SameLine();
                    action_button("Forward 1 second", EditorAction::SeekForward, half);
                    ImGui::Spacing();
                    ImGui::BeginDisabled(state.playing);
                    ImGui::TextUnformatted("Playback speed");
                    ImGui::SetNextItemWidth(-1);
                    char playback_speed[32]{};
                    std::snprintf(playback_speed, sizeof(playback_speed), "%.3g x",
                                  state.playback_speed);
                    if (ImGui::BeginCombo("##playback-speed", playback_speed)) {
                        for (double value : {.05, .1, .25, .5, 1.0, 2.0, 4.0}) {
                            char label[32]{};
                            std::snprintf(label, sizeof(label), "%.3g x", value);
                            if (ImGui::Selectable(label, value == state.playback_speed))
                                editor_enqueue(EditorAction::SetPlaybackSpeed, value);
                        }
                        ImGui::EndCombo();
                    }
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip(
                            "Playback speed for the next Play shot. Shared with the desktop controls.");
                    ImGui::TextUnformatted("Updates / s");
                    ImGui::SetNextItemWidth(-1);
                    char playback_rate[32]{};
                    std::snprintf(playback_rate, sizeof(playback_rate), "%u", state.playback_rate);
                    if (ImGui::BeginCombo("##playback-rate", playback_rate)) {
                        for (unsigned value : {30u, 60u, 120u}) {
                            char label[32]{};
                            std::snprintf(label, sizeof(label), "%u", value);
                            if (ImGui::Selectable(label, value == state.playback_rate))
                                editor_enqueue(EditorAction::SetPlaybackRate, double(value));
                        }
                        ImGui::EndCombo();
                    }
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip(
                            "Native monitoring frequency. Camera and supported effects follow each rendered frame; this is not an output FPS setting.");
                    ImGui::EndDisabled();
                }
                end_panel_card();
                if (begin_panel_card("##flight-card")) {
                    // Send one change at the end of a drag, not a settings write per frame.
                    static float speed_draft = 400.0f;
                    static bool speed_editing = false;
                    if (!speed_editing)
                        speed_draft = std::clamp(static_cast<float>(state.speed), 1.0f, 10000.0f);
                    char speed_label[48]{};
                    std::snprintf(speed_label, sizeof(speed_label), "Speed %.0f", speed_draft);
                    section_title("Free camera", speed_label);
                    ImGui::SetNextItemWidth(-1);
                    ImGui::PushStyleVar(ImGuiStyleVar_FramePadding,
                                        ImVec2(12 * panel_scale, 3 * panel_scale));
                    ImGui::SliderFloat("##flight-speed", &speed_draft, 1.0f, 10000.0f, "",
                                       ImGuiSliderFlags_Logarithmic | ImGuiSliderFlags_AlwaysClamp);
                    ImGui::PopStyleVar();
                    const bool speed_committed = ImGui::IsItemDeactivatedAfterEdit();
                    speed_editing = ImGui::IsItemActive();
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip(
                            "Movement speed in world units per second. Ctrl+click to enter a value.");
                    if (speed_committed)
                        editor_enqueue(EditorAction::SetSpeed, double(speed_draft));
                    const float half =
                        (ImGui::GetContentRegionAvail().x - ImGui::GetStyle().ItemSpacing.x) / 2;
                    action_button("Fly camera", EditorAction::Flight, half);
                    ImGui::SameLine();
                    action_button("Heroes / game UI", EditorAction::GameUI, half, 1);
                    ImGui::Spacing();
                    ImGui::BeginDisabled(state.playing);
                    action_button("Clear ragdolls", EditorAction::DestroyRagdolls,
                                  ImGui::GetContentRegionAvail().x);
                    ImGui::EndDisabled();
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip("Clear accumulated ragdolls after repeated shot playback.");
                }
                end_panel_card();
                ImGui::EndDisabled();
                ImGui::EndTabItem();
            }
            if (ImGui::BeginTabItem("Export")) {
                if (begin_panel_card("##video-card")) {
                    section_title("Recording", "MP4");
                    const auto recording = video::status();
                    const bool active = recording.state == video::State::starting ||
                                        recording.state == video::State::recording;
                    ImGui::BeginDisabled(!state.ready || state.busy);
                    ImGui::BeginDisabled(active || recording.state == video::State::finalizing ||
                                         state.camera_count < 2 || state.playing);
                    action_button("Play shot", EditorAction::PlayPath,
                                  ImGui::GetContentRegionAvail().x);
                    ImGui::EndDisabled();
                    if (active) {
                        ImGui::Text("%.1f s  |  %llu frames",
                                    double(recording.duration_100ns) / 1e7,
                                    static_cast<unsigned long long>(recording.frames_written));
                        action_button("Finish recording", EditorAction::StopVideo,
                                      ImGui::GetContentRegionAvail().x);
                    } else {
                        ImGui::BeginDisabled(recording.state == video::State::finalizing);
                        action_button(recording.state == video::State::finalizing
                                          ? "Finalizing MP4..."
                                          : "Record video",
                                      EditorAction::StartVideo, ImGui::GetContentRegionAvail().x);
                        ImGui::EndDisabled();
                    }
                    ImGui::EndDisabled();
                    if (recording.frames_dropped)
                        ImGui::Text("Missed capture slots: %llu",
                                    static_cast<unsigned long long>(recording.frames_dropped));
                    ImGui::TextDisabled("Output folder and FFmpeg runtime: desktop Export tab.");
                }
                end_panel_card();
                if (begin_panel_card("##export-settings")) {
                    char bitrate[64]{};
                    std::snprintf(bitrate, sizeof(bitrate), "%u Mbps", state.video_bitrate_mbps);
                    section_title("Export settings", bitrate);
                    ImGui::TextUnformatted("Video FPS");
                    ImGui::SetNextItemWidth(-1);
                    char fps_label[16]{};
                    std::snprintf(fps_label, sizeof(fps_label), "%u", state.video_fps);
                    if (ImGui::BeginCombo("##export-fps", fps_label)) {
                        for (unsigned value : {30u, 60u, 120u, 300u, 600u}) {
                            char label[16]{};
                            std::snprintf(label, sizeof(label), "%u", value);
                            if (ImGui::Selectable(label, value == state.video_fps))
                                editor_enqueue(EditorAction::SetVideoFps, double(value));
                        }
                        ImGui::EndCombo();
                    }
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip(
                            "30-120 record in real time; 300 and 600 need Fixed-step export.");
                    ImGui::TextUnformatted("Bitrate");
                    ImGui::SetNextItemWidth(-1);
                    char bitrate_label[24]{};
                    std::snprintf(bitrate_label, sizeof(bitrate_label), "%u Mbps",
                                  state.video_bitrate_mbps);
                    if (ImGui::BeginCombo("##export-bitrate", bitrate_label)) {
                        for (unsigned value : {10u, 20u, 40u}) {
                            char label[24]{};
                            std::snprintf(label, sizeof(label), "%u Mbps", value);
                            if (ImGui::Selectable(label, value == state.video_bitrate_mbps))
                                editor_enqueue(EditorAction::SetVideoBitrate, double(value));
                        }
                        ImGui::EndCombo();
                    }
                    ImGui::TextUnformatted("Encoder");
                    ImGui::SetNextItemWidth(-1);
                    if (ImGui::BeginCombo("##export-encoder",
                                          video_codec_label(state.video_codec))) {
                        for (std::uint32_t id = 0; id <= 10; ++id) {
                            if (ImGui::Selectable(video_codec_label(id), id == state.video_codec))
                                editor_enqueue(EditorAction::SetVideoEncoder, double(id));
                        }
                        ImGui::EndCombo();
                    }
                    bool fixed_step = state.video_fixed_step;
                    if (ImGui::Checkbox("Fixed-step export (frame-accurate)", &fixed_step))
                        editor_enqueue(EditorAction::SetVideoFixedStep, fixed_step ? 1.0 : 0.0);
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip(
                            "Render exactly one frame per output frame. Required for 300/600 FPS and removes real-time encoder hitching.");
                    ImGui::TextUnformatted("Export speed");
                    ImGui::SetNextItemWidth(-1);
                    char export_speed[32]{};
                    std::snprintf(export_speed, sizeof(export_speed), "%.3g x", state.video_speed);
                    if (ImGui::BeginCombo("##export-speed", export_speed)) {
                        for (double value : {.05, .1, .25, .5, 1.0, 2.0, 4.0}) {
                            char label[32]{};
                            std::snprintf(label, sizeof(label), "%.3g x", value);
                            if (ImGui::Selectable(label, value == state.video_speed))
                                editor_enqueue(EditorAction::SetVideoSpeed, value);
                        }
                        ImGui::EndCombo();
                    }
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip(
                            "Slow-motion playback rate for fixed-step export. Shared with the desktop Export tab.");
                }
                end_panel_card();
                if (begin_panel_card("##reshade-card")) {
                    section_title("Effects", "ReShade");
                    if (reshade_available()) {
                        if (ImGui::Button("ReShade menu", ImVec2(-1, 0)))
                            editor_enqueue(EditorAction::ReShade);
                    } else {
                        ImGui::TextWrapped(
                            "Select the ReShade runtime in the desktop Export tab to enable it.");
                    }
                }
                end_panel_card();
                ImGui::EndTabItem();
            }
            ImGui::EndTabBar();
        }
        ImGui::EndChild();
        ImGui::PopStyleColor();
        ImGui::Spacing();
        action_button("Stop / restore", EditorAction::Stop, ImGui::GetContentRegionAvail().x);
        if (state.message[0])
            ImGui::TextWrapped("%s", state.message);
        ImGui::TextDisabled("F7  Console");
        if (ImGui::IsItemHovered())
            ImGui::SetTooltip(
                "Open Deadlock's console. Customize editor shortcuts in the launcher.");
    }
    ImGui::End();
    if (close)
        editor_enqueue(EditorAction::Flight);
}

bool try_initialize_device(IDXGISwapChain* chain) {
    if (GetTickCount64() < next_initialization)
        return false;
    if (initialize_device(chain)) {
        next_initialization = 0;
        return true;
    }
    next_initialization = GetTickCount64() + 2000;
    return false;
}
void render_overlay(IDXGISwapChain* chain) {
    const auto state = editor_snapshot();
    if (resizing.load(std::memory_order_acquire))
        return;
    const bool media_live = media_session_active();
    if (!state.enabled && !media_live) {
        // Playback may disable manual editor input. Only a lost session retires
        // media here; control ownership and camera readiness are transient.
        if (!media_suspended && chain == swapchain && immediate) {
            video::reset_resources();
            reshade_set_enabled(false);
            if (context1 && overlay_state) {
                DeviceStateScope graphics_scope;
                reshade_release_device();
            } else
                reshade_release_device();
            media_suspended = true;
        }
        return;
    }
    if (renderer_pressure.load(std::memory_order_acquire)) {
        // The engine's DirectX 11 vertex-buffer retirement queue is near its
        // 16-bit capacity. Stop all optional Dolly rendering (effects, capture
        // and the editor) so the engine can retire queued buffers and avoid the
        // fatal "EnsureCapacity allocation count overflow". The editor itself
        // stays alive and resumes automatically once pressure clears.
        if (!media_suspended && chain == swapchain && immediate) {
            video::reset_resources();
            reshade_set_enabled(false);
            if (context1 && overlay_state) {
                DeviceStateScope graphics_scope;
                reshade_release_device();
            } else {
                reshade_release_device();
            }
            media_suspended = true;
        }
        return;
    }
    media_suspended = false;
    if (!swapchain) {
        if (!try_initialize_device(chain))
            return;
    }
    if (chain != swapchain) {
        DXGI_SWAP_CHAIN_DESC replacement{};
        if (FAILED(chain->GetDesc(&replacement)) || replacement.OutputWindow != game_window ||
            !suitable_window(replacement.OutputWindow))
            return;
        release_device();
        if (!try_initialize_device(chain))
            return;
    }
    if (!target && !create_target(chain))
        return;
    // Both optional effects and capture share this one real game Present.
    // Capture runs after effects but before either editor's UI or path guides.
    const auto clean_frame = [](IDXGISwapChain* capture_chain, ID3D11Device* capture_device,
                                ID3D11DeviceContext* capture_context, void*) {
        const auto editor = editor_snapshot();
        const auto recording = video::status().state;
        // Focus gates the first frame only. Desktop controls and a transient
        // missing editor pose must not finish an already running MP4.
        if (media_session_active() && (recording == video::State::recording || editor.focused))
            video::capture(capture_chain, capture_device, capture_context);
    };
    bool effects_handled = false;
    if (reshade_enabled() || reshade_overlay_pending()) {
        // The manual ReShade API changes graphics state. Restore the exact
        // engine context before Dolly draws or the real Present continues.
        DeviceStateScope effects_scope;
        effects_handled = reshade_render(chain, device, immediate, clean_frame, nullptr);
    } else {
        // Applies pending disable/teardown without adding a state swap to the
        // ordinary camera path when ReShade has never been configured.
        effects_handled = reshade_render(chain, device, immediate, clean_frame, nullptr);
    }
    if (!effects_handled)
        clean_frame(chain, device, immediate, nullptr);
    if (reshade_overlay_open() || reshade_overlay_pending()) {
        clear_pending_input();
        editor_text_input_active(false);
        return;
    }
    if (!state.enabled) {
        clear_pending_input();
        editor_text_input_active(false);
        return;
    }
    GuiContextScope gui_scope(imgui);
    const bool panel = editor_panel_visible() && state.focused;
    const auto guides = guides_visible(state) ? visualization_snapshot() : nullptr;
    const bool draw_guides = guides && guides->enabled();
    auto& io = ImGui::GetIO();
    if (panel != last_panel) {
        io.ClearInputKeys();
        io.ClearInputMouse();
        last_panel = panel;
    }
    // Present must never perform OS cursor updates on the game's behalf.
    io.ConfigFlags =
        ImGuiConfigFlags_NoMouseCursorChange | (panel ? ImGuiConfigFlags_NavEnableKeyboard : 0);
    io.ConfigNavMoveSetMousePos = false;
    io.WantSetMousePos = false;
    io.MouseDrawCursor = panel;
    // Paused editing may show guides without the panel. Playback, game UI,
    // console and unfocused windows do not draw editing guides into the scene.
    if (!panel) {
        clear_pending_input();
        io.ClearEventsQueue();
        editor_text_input_active(false);
        if (!draw_guides)
            return;
    } else
        feed_pending_input();
    DeviceStateScope graphics_scope;
    ImGui_ImplDX11_NewFrame();
    ImGui_ImplWin32_NewFrame();
    ImGui::NewFrame();
    guide_geometry.line_count = guide_geometry.label_count = 0;
    if (draw_guides)
        draw_path_guides(state, guides);
    if (panel)
        draw_panel(state);
    editor_text_input_active(panel && ImGui::GetIO().WantTextInput);
    ImGui::Render();
    immediate->OMSetRenderTargets(1, &target, nullptr);
    ImGui_ImplDX11_RenderDrawData(ImGui::GetDrawData());
    diagnostic_draw.fetch_add(1, std::memory_order_relaxed);
    if (draw_guides)
        diagnostic_guides.fetch_add(1, std::memory_order_relaxed);
    diagnostic_guide_lines.store(guide_geometry.line_count, std::memory_order_relaxed);
    diagnostic_guide_labels.store(guide_geometry.label_count, std::memory_order_relaxed);
    if (panel)
        diagnostic_panel.fetch_add(1, std::memory_order_relaxed);
}

HRESULT STDMETHODCALLTYPE present_hook(IDXGISwapChain* chain, UINT interval, UINT flags) {
    static thread_local bool entered = false;
    if (entered)
        return original_present(chain, interval, flags);
    entered = true;
    if (enabled.load(std::memory_order_acquire) && !(flags & DXGI_PRESENT_TEST)) {
        diagnostic_present.fetch_add(1, std::memory_order_relaxed);
        std::unique_lock<std::recursive_mutex> lock(render_mutex, std::try_to_lock);
        if (lock.owns_lock()) {
            DiagnosticTimer timer(diagnostic_overlay_last_us, diagnostic_overlay_max_us,
                                  diagnostic_overlay_active_ms);
            try {
                render_overlay(chain);
            } catch (...) {
                last_error =
                    "DirectX editor stopped after a rendering error; external controls remain available.";
                release_device();
                enabled = false;
            }
        } else
            diagnostic_lock_skips.fetch_add(1, std::memory_order_relaxed);
    }
    HRESULT result;
    {
        DiagnosticTimer timer(diagnostic_present_last_us, diagnostic_present_max_us,
                              diagnostic_present_active_ms);
        result = original_present(chain, interval, flags);
    }
    if (result == DXGI_ERROR_DEVICE_REMOVED || result == DXGI_ERROR_DEVICE_RESET) {
        std::lock_guard<std::recursive_mutex> lock(render_mutex);
        if (chain == swapchain) {
            last_error = "DirectX device reset; waiting for the game renderer to recover.";
            release_device();
        }
    }
    entered = false;
    return result;
}
HRESULT STDMETHODCALLTYPE resize_hook(IDXGISwapChain* chain, UINT count, UINT width, UINT height,
                                      DXGI_FORMAT format, UINT flags) {
    // ResizeBuffers requires ALL references to the backbuffer to be gone first.
    // Do not hold our mutex while the game/other overlays process the resize.
    bool game_resize = false;
    {
        std::lock_guard<std::recursive_mutex> lock(render_mutex);
        if (chain == swapchain) {
            diagnostic_resizes.fetch_add(1, std::memory_order_relaxed);
            game_resize = true;
            resizing = true;
            video::reset_resources();
            reshade_release_device();
            release_target();
        }
    }
    const HRESULT result = original_resize(chain, count, width, height, format, flags);
    if (game_resize) {
        std::lock_guard<std::recursive_mutex> lock(render_mutex);
        if (chain == swapchain &&
            (result == DXGI_ERROR_DEVICE_REMOVED || result == DXGI_ERROR_DEVICE_RESET))
            release_device();
        resizing = false;
    }
    return result; // Recreate lazily at the next Present, including failed resizes.
}

LRESULT CALLBACK window_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    auto previous = reinterpret_cast<WNDPROC>(GetPropW(window, kWindowProperty));
    bool consumed = false;
    LRESULT result = 0;
    // Preserve UI input when the render thread is busy without blocking the
    // game's message thread (which a graphics driver may synchronously need).
    // Raw mouse data and editor bindings are handled immediately below.
    const bool ui_message = (message >= WM_KEYFIRST && message <= WM_KEYLAST) ||
                            (message >= WM_MOUSEFIRST && message <= WM_MOUSELAST) ||
                            message == WM_SETFOCUS || message == WM_KILLFOCUS ||
                            message == WM_SETCURSOR;
    if (window == game_window && ui_message && editor_panel_visible()) {
        std::unique_lock<std::recursive_mutex> lock(render_mutex, std::try_to_lock);
        if (lock.owns_lock() && imgui) {
            GuiContextScope scope(imgui);
            feed_pending_input();
            feed_window_input({window, message, wparam, lparam});
        } else {
            std::lock_guard<std::mutex> input_lock(input_mutex);
            if (pending_input.size() >= 512) {
                pending_input.clear();
                input_overflow = true;
            }
            pending_input.push_back({window, message, wparam, lparam});
        }
    }
    if (enabled.load(std::memory_order_acquire) && window == game_window)
        consumed = editor_window_message(window, message, wparam, lparam, result);
    if (message == WM_NCDESTROY) {
        std::lock_guard<std::recursive_mutex> lock(render_mutex);
        if (window == game_window)
            release_device();
        RemovePropW(window, kWindowProperty);
    }
    if (consumed)
        return result;
    return previous ? CallWindowProcW(previous, window, message, wparam, lparam)
                    : DefWindowProcW(window, message, wparam, lparam);
}

// Build a temporary hidden swapchain solely to discover public DXGI method
// addresses; never create graphics resources under the loader lock. Hardware
// first, WARP fallback supports headless/CI and systems without an adapter.
bool discover_swapchain_methods(void*& present, void*& resize) noexcept {
    const wchar_t class_name[] = L"DeadlockDolly.DX11.Discovery";
    const auto instance = GetModuleHandleW(nullptr);
    WNDCLASSW wc{};
    wc.lpfnWndProc = DefWindowProcW;
    wc.hInstance = instance;
    wc.lpszClassName = class_name;
    const ATOM atom = RegisterClassW(&wc);
    if (!atom)
        return false;
    HWND window = CreateWindowExW(0, class_name, L"", WS_OVERLAPPEDWINDOW, 0, 0, 64, 64, nullptr,
                                  nullptr, instance, nullptr);
    if (!window) {
        UnregisterClassW(class_name, instance);
        return false;
    }
    DXGI_SWAP_CHAIN_DESC desc{};
    desc.BufferCount = 1;
    desc.BufferDesc.Width = 64;
    desc.BufferDesc.Height = 64;
    desc.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    desc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    desc.OutputWindow = window;
    desc.SampleDesc.Count = 1;
    desc.Windowed = TRUE;
    desc.SwapEffect = DXGI_SWAP_EFFECT_DISCARD;
    IDXGISwapChain* dummy = nullptr;
    ID3D11Device* dummy_device = nullptr;
    ID3D11DeviceContext* dummy_context = nullptr;
    const D3D_FEATURE_LEVEL levels[] = {D3D_FEATURE_LEVEL_11_0, D3D_FEATURE_LEVEL_10_1,
                                        D3D_FEATURE_LEVEL_10_0};
    HRESULT hr = D3D11CreateDeviceAndSwapChain(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0,
                                               levels, 3, D3D11_SDK_VERSION, &desc, &dummy,
                                               &dummy_device, nullptr, &dummy_context);
    if (FAILED(hr)) {
        release(dummy_context);
        release(dummy_device);
        release(dummy);
        hr = D3D11CreateDeviceAndSwapChain(nullptr, D3D_DRIVER_TYPE_WARP, nullptr, 0, levels, 3,
                                           D3D11_SDK_VERSION, &desc, &dummy, &dummy_device, nullptr,
                                           &dummy_context);
    }
    if (SUCCEEDED(hr) && dummy) {
        auto table = *reinterpret_cast<void***>(dummy);
        present = table[8];
        resize = table[13];
    }
    release(dummy_context);
    release(dummy_device);
    release(dummy);
    DestroyWindow(window);
    UnregisterClassW(class_name, instance);
    return present && resize;
}
} // namespace

bool install_overlay_hooks() noexcept {
    if (installed.load(std::memory_order_acquire)) {
        enabled = true;
        return true;
    }
    try {
        void* present = nullptr;
        void* resize = nullptr;
        if (!discover_swapchain_methods(present, resize)) {
            last_error =
                "Could not discover DirectX 11 presentation; external controls remain available.";
            return false;
        }
        if (MH_CreateHook(present, reinterpret_cast<void*>(present_hook),
                          reinterpret_cast<void**>(&original_present)) != MH_OK) {
            last_error = "Could not attach the DirectX 11 presentation callback.";
            return false;
        }
        if (MH_CreateHook(resize, reinterpret_cast<void*>(resize_hook),
                          reinterpret_cast<void**>(&original_resize)) != MH_OK) {
            last_error = "Could not attach the DirectX 11 resize callback.";
            MH_RemoveHook(present);
            return false;
        }
        if (MH_EnableHook(resize) != MH_OK) {
            last_error = "Could not enable the DirectX 11 resize callback.";
            MH_RemoveHook(resize);
            MH_RemoveHook(present);
            return false;
        }
        if (MH_EnableHook(present) != MH_OK) {
            last_error = "Could not enable the DirectX 11 presentation callback.";
            MH_DisableHook(resize);
            MH_RemoveHook(present);
            return false;
        }
        installed = true;
        enabled = true;
        return true;
    } catch (...) {
        last_error = "DirectX 11 overlay initialization failed.";
        enabled = false;
        return false;
    }
}
OverlayDiagnostics overlay_diagnostics() noexcept {
    return {diagnostic_present.load(std::memory_order_relaxed),
            diagnostic_panel.load(std::memory_order_relaxed),
            diagnostic_init_attempts.load(std::memory_order_relaxed),
            diagnostic_init_successes.load(std::memory_order_relaxed),
            diagnostic_releases.load(std::memory_order_relaxed),
            diagnostic_resizes.load(std::memory_order_relaxed),
            diagnostic_draw.load(std::memory_order_relaxed),
            diagnostic_guides.load(std::memory_order_relaxed),
            diagnostic_overlay_last_us.load(std::memory_order_relaxed),
            diagnostic_overlay_max_us.load(std::memory_order_relaxed),
            diagnostic_present_last_us.load(std::memory_order_relaxed),
            diagnostic_present_max_us.load(std::memory_order_relaxed),
            diagnostic_lock_skips.load(std::memory_order_relaxed),
            diagnostic_overlay_active_ms.load(std::memory_order_relaxed),
            diagnostic_present_active_ms.load(std::memory_order_relaxed),
            diagnostic_guide_lines.load(std::memory_order_relaxed),
            diagnostic_guide_labels.load(std::memory_order_relaxed)};
}
const char* overlay_last_error() noexcept {
    return last_error.load(std::memory_order_acquire);
}
void overlay_set_renderer_pressure(bool high) noexcept {
    renderer_pressure.store(high, std::memory_order_release);
}
bool overlay_renderer_pressure() noexcept {
    return renderer_pressure.load(std::memory_order_acquire);
}
void shutdown_overlay() noexcept {
    enabled = false;
    last_error = "In-game editor stopped.";
    try {
        std::lock_guard<std::recursive_mutex> lock(render_mutex);
        release_device();
    } catch (...) {
    }
}
namespace {
void queue_raw_input(InputMessage message) noexcept {
    if (!enabled.load(std::memory_order_acquire) || !editor_panel_visible())
        return;
    message.window = game_window.load();
    if (!message.window)
        return;
    try {
        std::lock_guard<std::mutex> lock(input_mutex);
        if (pending_input.size() >= 512) {
            pending_input.clear();
            input_overflow = true;
        }
        pending_input.push_back(message);
    } catch (...) {
    } // An input hook must never throw into the game's event pump.
}
}
void overlay_raw_mouse_button(int button, bool down) noexcept {
    if (button < 0 || button > 4)
        return;
    InputMessage message{};
    message.raw_kind = 1;
    message.button = button;
    message.down = down;
    queue_raw_input(message);
}
void overlay_raw_mouse_wheel(float vertical, float horizontal) noexcept {
    InputMessage message{};
    message.raw_kind = 2;
    message.vertical = vertical;
    message.horizontal = horizontal;
    queue_raw_input(message);
}
void overlay_raw_key(unsigned virtual_key, unsigned scan_code, bool down, bool extended) noexcept {
    if (virtual_key > 255)
        return;
    InputMessage message{};
    message.raw_kind = 3;
    message.message = down ? WM_KEYDOWN : WM_KEYUP;
    message.wparam = virtual_key;
    message.lparam = LPARAM(1u | ((scan_code & 255u) << 16) | (extended ? 1u << 24 : 0u) |
                            (down ? 0u : 3u << 30));
    queue_raw_input(message);
}
} // namespace dolly
