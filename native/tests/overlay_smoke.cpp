// Windows CI exercises the real DX11 overlay on WARP with synthetic editor
// state. No Deadlock modules, private addresses, injection, or console needed.
#include "dolly_overlay.hpp"
#include "dolly_editor.hpp"
#include "dolly_renderer_diagnostics.hpp"
#include "dolly_visualization_runtime.hpp"
#include "dolly_reshade.hpp"
#include "dolly_media.hpp"
#include "dolly_video.hpp"
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
#include <string>

namespace {
bool available = false;
HWND attached = nullptr;
dolly::EditorSnapshot snapshot;
bool observed_mouse_down[5]{};
bool present_keeps_os_cursor = false;
void require(bool condition, const char* message) {
    if (!condition)
        throw std::runtime_error(message);
}

void append_u32(std::vector<unsigned char>& out, std::uint32_t value) {
    for (unsigned i = 0; i < 4; ++i)
        out.push_back(static_cast<unsigned char>(value >> (8 * i)));
}
void append_double(std::vector<unsigned char>& out, double value) {
    std::uint64_t bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));
    for (unsigned i = 0; i < 8; ++i)
        out.push_back(static_cast<unsigned char>(bits >> (8 * i)));
}
std::vector<unsigned char> viewer_packet() {
    // These two synthetic cameras project to the right of the panel. Their
    // connecting segment and glyphs must remain visible in paused flight.
    const dolly::CameraPose a{100, -60, 0, 0, 0, 0, 4.0 / 3.0};
    const dolly::CameraPose b{100, -90, 15, 0, 0, 0, 4.0 / 3.0};
    std::vector<unsigned char> path{'D', 'L', 'Y', 'P', 'A', 'T', 'H', 0};
    append_u32(path, 1);
    append_u32(path, 1);
    append_u32(path, 7);
    append_u32(path, 0);
    append_double(path, 3);
    append_double(path, 0);
    append_double(path, 3);
    for (double value : a)
        append_double(path, value);
    for (double value : b)
        append_double(path, value);
    append_double(path, 0);
    append_double(path, 3);
    for (unsigned i = 0; i < 7; ++i) {
        append_u32(path, 1);
        append_u32(path, i >= 3 ? 1 : 0);
        append_double(path, a[i]);
        append_double(path, b[i]);
        append_double(path, 0);
        append_double(path, 0);
    }
    std::vector<unsigned char> packet{'D', 'L', 'Y', 'V', 'I', 'S', '0', '1'};
    append_u32(packet, 2);
    append_u32(packet, 1);
    append_u32(packet, 1);
    append_u32(packet, 1);
    append_u32(packet, 2);
    append_u32(packet, static_cast<std::uint32_t>(path.size()));
    append_u32(packet, 128);
    append_u32(packet, 8);
    packet.resize(dolly::kVisualizationHeaderBytes);
    append_double(packet, 0);
    append_double(packet, 3);
    packet.insert(packet.end(), path.begin(), path.end());
    return packet;
}
class SyntheticViewer {
    HANDLE mapping = nullptr;
    unsigned char* memory = nullptr;
    std::wstring name, viewer_name;
    std::uint32_t sequence = 0;

public:
    SyntheticViewer() {
        name = L"Local\\Dolly.Overlay.Smoke." + std::to_wstring(GetCurrentProcessId()) + L"." +
               std::to_wstring(GetTickCount64());
        viewer_name = name + L".viewer";
        dolly::visualization_worker_tick(name.c_str(), true);
        require(dolly::visualization_runtime_state() == dolly::VisualizationRuntimeState::Waiting &&
                    !dolly::visualization_snapshot(),
                "Missing optional viewer mapping must not publish guides");
        dolly::visualization_worker_tick(name.c_str(), false);
        mapping = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0,
                                     static_cast<DWORD>(dolly::kVisualizationMappingBytes),
                                     viewer_name.c_str());
        require(mapping != nullptr, "Could not create synthetic viewer mapping");
        memory = static_cast<unsigned char*>(
            MapViewOfFile(mapping, FILE_MAP_ALL_ACCESS, 0, 0, dolly::kVisualizationMappingBytes));
        if (!memory) {
            CloseHandle(mapping);
            mapping = nullptr;
            throw std::runtime_error("Could not map synthetic viewer packet");
        }
        publish(viewer_packet());
        dolly::visualization_worker_tick(name.c_str(), true);
        const auto path = dolly::visualization_snapshot();
        require(dolly::visualization_runtime_state() == dolly::VisualizationRuntimeState::Ready &&
                    path && path->enabled() && path->camera_count() == 2,
                "Valid optional viewer mapping did not publish cameras");
    }
    ~SyntheticViewer() { close(); }
    void close() noexcept {
        dolly::visualization_worker_tick(name.c_str(), false);
        if (memory) {
            UnmapViewOfFile(memory);
            memory = nullptr;
        }
        if (mapping) {
            CloseHandle(mapping);
            mapping = nullptr;
        }
    }
    void publish(const std::vector<unsigned char>& packet) {
        require(packet.size() >= 12 && packet.size() <= dolly::kVisualizationMappingBytes,
                "Invalid synthetic viewer packet size");
        sequence += 2;
        InterlockedExchange(reinterpret_cast<volatile LONG*>(memory + 8),
                            static_cast<LONG>(sequence - 1));
        std::memcpy(memory, packet.data(), 8);
        std::memcpy(memory + 12, packet.data() + 12, packet.size() - 12);
        MemoryBarrier();
        InterlockedExchange(reinterpret_cast<volatile LONG*>(memory + 8),
                            static_cast<LONG>(sequence));
    }
    void tick() {
        Sleep(110);
        dolly::visualization_worker_tick(name.c_str(), true);
    }
    void check_lifecycle() {
        const auto before = dolly::visualization_snapshot();
        InterlockedExchange(reinterpret_cast<volatile LONG*>(memory + 8),
                            static_cast<LONG>(sequence + 1));
        tick();
        require(dolly::visualization_runtime_state() ==
                        dolly::VisualizationRuntimeState::Updating &&
                    dolly::visualization_snapshot() == before,
                "In-progress viewer write must retain the last complete snapshot");
        auto invalid = viewer_packet();
        invalid[0] = 'X';
        publish(invalid);
        tick();
        require(dolly::visualization_runtime_state() == dolly::VisualizationRuntimeState::Invalid &&
                    !dolly::visualization_snapshot(),
                "Invalid viewer packet left stale guides active");
        publish(viewer_packet());
        tick();
        require(dolly::visualization_runtime_state() == dolly::VisualizationRuntimeState::Ready &&
                    dolly::visualization_snapshot(),
                "Viewer did not recover after a valid replacement packet");
        close();
        require(dolly::visualization_runtime_state() ==
                        dolly::VisualizationRuntimeState::Disconnected &&
                    !dolly::visualization_snapshot(),
                "Disconnected editor left guides active");
        HANDLE remaining = OpenFileMappingW(FILE_MAP_READ, FALSE, viewer_name.c_str());
        if (remaining)
            CloseHandle(remaining);
        require(remaining == nullptr, "Viewer retained its mapping after disconnect");
    }
};

struct DrawRegions {
    std::size_t panel = 0, guides = 0;
};
DrawRegions render_regions(IDXGISwapChain* chain, ID3D11Device* device,
                           ID3D11DeviceContext* context, ID3D11RenderTargetView* target,
                           ID3D11Texture2D* backbuffer) {
    // Compare broad regions rather than exact antialiased pixels or UI text
    // positions. Clear each frame so hidden guides cannot pass with old pixels.
    const FLOAT clear[4] = {.04f, .08f, .95f, 1};
    context->ClearRenderTargetView(target, clear);
    context->OMSetRenderTargets(1, &target, nullptr);
    require(SUCCEEDED(chain->Present(0, 0)), "Viewer visibility Present failed");
    D3D11_TEXTURE2D_DESC desc{};
    backbuffer->GetDesc(&desc);
    desc.Usage = D3D11_USAGE_STAGING;
    desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    desc.BindFlags = 0;
    desc.MiscFlags = 0;
    ID3D11Texture2D* staging = nullptr;
    require(SUCCEEDED(device->CreateTexture2D(&desc, nullptr, &staging)),
            "Viewer readback texture creation failed");
    context->CopyResource(staging, backbuffer);
    D3D11_MAPPED_SUBRESOURCE pixels{};
    if (FAILED(context->Map(staging, 0, D3D11_MAP_READ, 0, &pixels))) {
        staging->Release();
        throw std::runtime_error("Viewer backbuffer readback failed");
    }
    DrawRegions result;
    for (UINT y = 40; y < desc.Height - 40; ++y) {
        const auto* row = static_cast<const unsigned char*>(pixels.pData) + y * pixels.RowPitch;
        for (UINT x = 30; x < desc.Width - 10; ++x) {
            const auto* pixel = row + x * 4;
            const bool changed = pixel[0] < 7 || pixel[0] > 13 || pixel[1] < 17 || pixel[1] > 23 ||
                                 pixel[2] < 239 || pixel[2] > 245;
            if (changed) {
                if (x < 450)
                    ++result.panel;
                else if (x > 540)
                    ++result.guides;
            }
        }
    }
    context->Unmap(staging, 0);
    staging->Release();
    return result;
}
void check_guide_visibility(IDXGISwapChain* chain, ID3D11Device* device,
                            ID3D11DeviceContext* context, ID3D11RenderTargetView* target,
                            ID3D11Texture2D* backbuffer) {
    const auto original = snapshot;
    snapshot.owner = dolly::EditorOwner::Flight;
    const auto flight = snapshot;
    auto draw = [&]() { return render_regions(chain, device, context, target, backbuffer); };
    const auto counters_before = dolly::overlay_diagnostics();
    auto pixels = draw();
    const auto counters_flight = dolly::overlay_diagnostics();
    require(counters_flight.draw_frames == counters_before.draw_frames + 1 &&
                counters_flight.guide_frames == counters_before.guide_frames + 1 &&
                counters_flight.panel_frames == counters_before.panel_frames,
            "Guide-only drawing must have its own counter without counting a panel frame");
    require(counters_flight.guide_lines > 0 && counters_flight.guide_labels > 0,
            "Guide diagnostics omitted the projected geometry");
    require(!counters_flight.overlay_active_since_ms && !counters_flight.present_active_since_ms,
            "Completed Present left an active rendering timer");
    require(counters_flight.overlay_max_us >= counters_flight.overlay_last_us &&
                counters_flight.present_max_us >= counters_flight.present_last_us,
            "Overlay and original-Present timings are inconsistent");
    require(pixels.guides > 30 && pixels.panel == 0,
            "Paused flight did not render visible camera guides without the panel");
    snapshot.owner = dolly::EditorOwner::Panel;
    pixels = draw();
    require(pixels.panel > 100 && pixels.guides > 30,
            "Opening the panel hid the paused camera guides");
    snapshot.playing = true;
    const auto guides_before_playback = dolly::overlay_diagnostics().guide_frames;
    pixels = draw();
    require(dolly::overlay_diagnostics().guide_frames == guides_before_playback,
            "Playback without guides incorrectly counted a guide frame");
    require(pixels.panel > 100 && pixels.guides == 0,
            "Playback must hide guides while keeping the open panel visible");
    struct HiddenCase {
        const char* reason;
        unsigned field;
    };
    const HiddenCase cases[] = {
        {"Shot playback", 0},     {"Running replay", 1},         {"Game UI", 2},
        {"Console", 3},           {"Unfocused owner", 4},        {"Lost game focus", 5},
        {"Unready replay", 6},    {"Native flight inactive", 7}, {"Busy editor", 8},
        {"Unknown viewport", 9},  {"Disabled editor", 10},       {"Camera facing away", 11},
        {"Unknown view lens", 12}};
    for (const auto& test : cases) {
        snapshot = flight;
        switch (test.field) {
        case 0:
            snapshot.playing = true;
            break;
        case 1:
            snapshot.paused = false;
            break;
        case 2:
            snapshot.owner = dolly::EditorOwner::GameUI;
            break;
        case 3:
            snapshot.owner = dolly::EditorOwner::Console;
            break;
        case 4:
            snapshot.owner = dolly::EditorOwner::Unfocused;
            break;
        case 5:
            snapshot.focused = false;
            break;
        case 6:
            snapshot.ready = false;
            break;
        case 7:
            snapshot.manual_active = false;
            break;
        case 8:
            snapshot.busy = true;
            break;
        case 9:
            snapshot.view_width = 0;
            break;
        case 10:
            snapshot.enabled = false;
            break;
        case 11:
            snapshot.pose[4] = 180;
            break;
        case 12:
            snapshot.horizontal_fov = 0;
            break;
        }
        pixels = draw();
        if (pixels.panel || pixels.guides) {
            char message[128]{};
            std::snprintf(message, sizeof(message), "%s left editing graphics visible",
                          test.reason);
            throw std::runtime_error(message);
        }
    }
    snapshot = original;
    std::puts(
        "DX11 WARP viewer: paused flight/panel drawing and playback/UI/focus visibility gates passed.");
}

struct BufferLifetime {
    std::atomic<bool> retired{false};
};
// SetPrivateDataInterface holds one COM reference until the owning device
// child is destroyed. Observe that lifetime instead of relying on a driver's
// implementation-specific AddRef/Release return values.
// https://learn.microsoft.com/en-us/windows/win32/api/d3d11/nf-d3d11-id3d11devicechild-setprivatedatainterface
class BufferSentinel final : public IUnknown {
    std::atomic<ULONG> references{1};
    std::shared_ptr<BufferLifetime> lifetime;
    ~BufferSentinel() { lifetime->retired.store(true, std::memory_order_release); }

public:
    explicit BufferSentinel(std::shared_ptr<BufferLifetime> value) : lifetime(std::move(value)) {}
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** result) override {
        if (!result)
            return E_POINTER;
        *result = nullptr;
        if (!IsEqualIID(iid, __uuidof(IUnknown)))
            return E_NOINTERFACE;
        *result = static_cast<IUnknown*>(this);
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override {
        return references.fetch_add(1, std::memory_order_relaxed) + 1;
    }
    ULONG STDMETHODCALLTYPE Release() override {
        const auto remaining = references.fetch_sub(1, std::memory_order_acq_rel) - 1;
        if (!remaining)
            delete this;
        return remaining;
    }
};

std::shared_ptr<BufferLifetime> watch_buffer(ID3D11Buffer* buffer) {
    static const GUID sentinel_id = {
        0x1622bc48, 0x49b1, 0x466a, {0x95, 0xb3, 0xa4, 0x3b, 0xe0, 0x34, 0x11, 0x8f}};
    auto lifetime = std::make_shared<BufferLifetime>();
    auto* sentinel = new BufferSentinel(lifetime);
    const auto result = buffer->SetPrivateDataInterface(sentinel_id, sentinel);
    sentinel->Release();
    require(SUCCEEDED(result), "Could not attach buffer lifetime sentinel");
    require(!lifetime->retired.load(std::memory_order_acquire),
            "Buffer did not retain its lifetime sentinel");
    return lifetime;
}

void retire_game_buffers(ID3D11DeviceContext* context, ID3D11Query* completed,
                         const std::vector<std::shared_ptr<BufferLifetime>>& lifetimes) {
    // D3D11 may defer destruction. Release game bindings, submit pending work,
    // and wait for it before checking lifetime flags; a live overlay must not
    // retain earlier game vertex/index buffers in its private context state.
    // https://learn.microsoft.com/en-us/windows/win32/api/d3d11/nf-d3d11-id3d11devicecontext-flush
    context->ClearState();
    context->End(completed);
    context->Flush();
    const auto deadline = GetTickCount64() + 3000;
    for (;;) {
        BOOL ready = FALSE;
        const auto result =
            context->GetData(completed, &ready, sizeof(ready), D3D11_ASYNC_GETDATA_DONOTFLUSH);
        require(SUCCEEDED(result), "GPU completion query failed during buffer-retirement check");
        if (result == S_OK && ready)
            break;
        require(GetTickCount64() < deadline,
                "GPU completion timed out during buffer-retirement check");
        Sleep(1);
    }
    context->Flush();
    for (std::size_t i = 0; i < lifetimes.size(); ++i) {
        if (!lifetimes[i]->retired.load(std::memory_order_acquire)) {
            char message[128]{};
            std::snprintf(message, sizeof(message),
                          "Overlay retained retired game %s buffer at frame %zu",
                          i % 2 ? "index" : "vertex", i / 2);
            throw std::runtime_error(message);
        }
    }
}

void stress_game_buffer_lifetimes(IDXGISwapChain* chain, ID3D11Device* device,
                                  ID3D11DeviceContext* context, ID3D11RenderTargetView* target) {
    constexpr int frames = 384;
    constexpr dolly::EditorOwner owners[] = {dolly::EditorOwner::Panel, dolly::EditorOwner::Flight,
                                             dolly::EditorOwner::GameUI};
    std::vector<std::shared_ptr<BufferLifetime>> lifetimes;
    lifetimes.reserve(frames * 2);
    D3D11_QUERY_DESC query_desc{D3D11_QUERY_EVENT, 0};
    ID3D11Query* completed = nullptr;
    require(SUCCEEDED(device->CreateQuery(&query_desc, &completed)),
            "Could not create buffer-retirement query");
    try {
        for (int frame = 0; frame < frames; ++frame) {
            MSG message{};
            while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) {
                TranslateMessage(&message);
                DispatchMessageW(&message);
            }
            snapshot.owner = owners[(frame / 4) % 3];
            require(dolly::visualization_snapshot() != nullptr,
                    "Buffer lifetime stress lost its camera guides");
            D3D11_BUFFER_DESC buffer_desc{};
            buffer_desc.ByteWidth = 1024;
            buffer_desc.Usage = D3D11_USAGE_DEFAULT;
            buffer_desc.BindFlags = D3D11_BIND_VERTEX_BUFFER;
            ID3D11Buffer* vertex = nullptr;
            ID3D11Buffer* index = nullptr;
            require(SUCCEEDED(device->CreateBuffer(&buffer_desc, nullptr, &vertex)),
                    "Synthetic game vertex-buffer creation failed");
            buffer_desc.BindFlags = D3D11_BIND_INDEX_BUFFER;
            const auto index_result = device->CreateBuffer(&buffer_desc, nullptr, &index);
            if (FAILED(index_result)) {
                vertex->Release();
                throw std::runtime_error("Synthetic game index-buffer creation failed");
            }
            lifetimes.push_back(watch_buffer(vertex));
            lifetimes.push_back(watch_buffer(index));
            const UINT stride = 16u * (1u + static_cast<UINT>(frame % 2));
            const UINT vertex_offset = 16u * static_cast<UINT>(frame % 4);
            const UINT index_offset = 4u * static_cast<UINT>(frame % 8);
            const auto index_format = frame % 2 ? DXGI_FORMAT_R32_UINT : DXGI_FORMAT_R16_UINT;
            context->IASetVertexBuffers(0, 1, &vertex, &stride, &vertex_offset);
            context->IASetIndexBuffer(index, index_format, index_offset);
            context->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
            context->OMSetRenderTargets(1, &target, nullptr);
            const auto presented = chain->Present(0, 0);
            ID3D11Buffer* after_vertex = nullptr;
            ID3D11Buffer* after_index = nullptr;
            UINT after_stride = 0, after_vertex_offset = 0, after_index_offset = 0;
            DXGI_FORMAT after_index_format{};
            context->IAGetVertexBuffers(0, 1, &after_vertex, &after_stride, &after_vertex_offset);
            context->IAGetIndexBuffer(&after_index, &after_index_format, &after_index_offset);
            const bool restored = after_vertex == vertex && after_index == index &&
                                  after_stride == stride && after_vertex_offset == vertex_offset &&
                                  after_index_format == index_format &&
                                  after_index_offset == index_offset;
            if (after_vertex)
                after_vertex->Release();
            if (after_index)
                after_index->Release();
            vertex->Release();
            index->Release();
            require(SUCCEEDED(presented), "Present failed during game-buffer lifetime stress");
            require(restored, "Overlay did not restore the game's vertex/index-buffer bindings");
            if ((frame + 1) % 32 == 0)
                retire_game_buffers(context, completed, lifetimes);
        }
    } catch (...) {
        completed->Release();
        throw;
    }
    completed->Release();
    snapshot.owner = dolly::EditorOwner::Panel;
    std::puts(
        "DX11 WARP lifetime stress: 384 panel/guide/game-UI frames, 768 game buffers retired.");
}
}
namespace dolly {
EditorSnapshot editor_snapshot() noexcept {
    return snapshot;
}
bool editor_enqueue(EditorAction, double, const CameraPose*) noexcept {
    return true;
}
bool editor_panel_visible() noexcept {
    return snapshot.owner == EditorOwner::Panel;
}
void editor_set_owner(EditorOwner value) noexcept {
    snapshot.owner = value;
}
void editor_overlay_available(bool value) noexcept {
    available = value;
}
void editor_text_input_active(bool) noexcept {
    if (ImGui::GetCurrentContext()) {
        const auto& io = ImGui::GetIO();
        for (int i = 0; i < 5; ++i)
            observed_mouse_down[i] = io.MouseDown[i];
        present_keeps_os_cursor = (io.ConfigFlags & ImGuiConfigFlags_NoMouseCursorChange) != 0;
    }
}
void editor_attach_window(HWND value) noexcept {
    attached = value;
}
bool editor_window_message(HWND, UINT, WPARAM, LPARAM, LRESULT&) noexcept {
    return false;
}
}
int main() {
    try {
        dolly::reshade_set_enabled(false);
        require(!dolly::reshade_overlay_pending() && !dolly::reshade_overlay_open() &&
                    !dolly::reshade_available(),
                "An unconfigured optional ReShade runtime must not hide the editor");
        require(!dolly::reshade_request_overlay(true),
                "Opening absent ReShade must leave ordinary editor input available");
        const auto instance = GetModuleHandleW(nullptr);
        WNDCLASSW wc{};
        wc.lpfnWndProc = DefWindowProcW;
        wc.hInstance = instance;
        wc.lpszClassName = L"Dolly.Overlay.Smoke";
        require(RegisterClassW(&wc) != 0, "Could not register synthetic window");
        HWND window =
            CreateWindowExW(0, wc.lpszClassName, L"Dolly graphics smoke", WS_OVERLAPPEDWINDOW, 32,
                            32, 800, 600, nullptr, nullptr, instance, nullptr);
        require(window != nullptr, "Could not create synthetic window");
        ShowWindow(window, SW_SHOWNOACTIVATE);
        UpdateWindow(window);
        const auto before_proc = GetWindowLongPtrW(window, GWLP_WNDPROC);
        DXGI_SWAP_CHAIN_DESC desc{};
        desc.BufferCount = 1;
        desc.BufferDesc.Width = 800;
        desc.BufferDesc.Height = 600;
        desc.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
        desc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
        desc.OutputWindow = window;
        desc.SampleDesc.Count = 1;
        desc.Windowed = TRUE;
        desc.SwapEffect = DXGI_SWAP_EFFECT_SEQUENTIAL;
        IDXGISwapChain* chain = nullptr;
        ID3D11Device* device = nullptr;
        ID3D11DeviceContext* context = nullptr;
        const D3D_FEATURE_LEVEL feature = D3D_FEATURE_LEVEL_11_0;
        require(SUCCEEDED(D3D11CreateDeviceAndSwapChain(nullptr, D3D_DRIVER_TYPE_WARP, nullptr, 0,
                                                        &feature, 1, D3D11_SDK_VERSION, &desc,
                                                        &chain, &device, nullptr, &context)),
                "WARP DX11 device creation failed");
        // Optional graphics diagnostics must stay inside their unused block and
        // fail closed on an unrecognized renderer, without changing camera state.
        std::vector<unsigned char> diagnostic_memory(dolly::kMappingBytes, 0xa5);
        dolly::renderer_diagnostics_probe(0, "");
        dolly::renderer_diagnostics_tick(diagnostic_memory.data());
        dolly::RendererDiagnostics diagnostic{};
        std::memcpy(&diagnostic, diagnostic_memory.data() + dolly::kRendererDiagnosticsOffset,
                    sizeof(diagnostic));
        require(std::memcmp(diagnostic.magic, "DLYGFX01", 8) == 0 &&
                    diagnostic.abi == dolly::kRendererDiagnosticsAbi && !(diagnostic.sequence & 1),
                "Optional renderer diagnostic header is invalid");
        require(diagnostic.state == std::uint32_t(dolly::RendererProbeState::Waiting),
                "Missing renderer should leave the optional probe waiting");
        for (std::size_t i = 0; i < diagnostic_memory.size(); ++i)
            if (i < dolly::kRendererDiagnosticsOffset ||
                i >= dolly::kRendererDiagnosticsOffset + sizeof(diagnostic))
                require(diagnostic_memory[i] == 0xa5,
                        "Graphics diagnostics overwrote camera/editor mapping data");
        dolly::renderer_diagnostics_probe(
            reinterpret_cast<std::uintptr_t>(GetModuleHandleW(L"kernel32.dll")), "unknown");
        Sleep(1050); // one bounded sample interval; the probe never polls per frame
        dolly::renderer_diagnostics_tick(diagnostic_memory.data());
        std::memcpy(&diagnostic, diagnostic_memory.data() + dolly::kRendererDiagnosticsOffset,
                    sizeof(diagnostic));
        require(diagnostic.state == std::uint32_t(dolly::RendererProbeState::Unsupported) &&
                    diagnostic.sample == 2,
                "Unrecognized module must not permit private renderer reads");
        require(MH_Initialize() == MH_OK, "MinHook initialization failed");
        require(dolly::install_overlay_hooks(), "DXGI public-method hook installation failed");
        snapshot.enabled = true;
        snapshot.focused = true;
        snapshot.ready = true;
        snapshot.paused = true;
        snapshot.manual_active = true;
        snapshot.horizontal_fov = 90;
        snapshot.view_width = 800;
        snapshot.view_height = 600;
        snapshot.pose = {0, 0, 0, 0, 0, 0, 4.0 / 3.0};
        SyntheticViewer viewer;
        snapshot.owner = dolly::EditorOwner::Panel;
        snapshot.camera_count = 2;
        snapshot.duration = 3;
        std::snprintf(snapshot.shot_name, sizeof(snapshot.shot_name), "Synthetic editor smoke");
        ID3D11Texture2D* backbuffer = nullptr;
        ID3D11RenderTargetView* target = nullptr;
        require(SUCCEEDED(chain->GetBuffer(0, __uuidof(ID3D11Texture2D),
                                           reinterpret_cast<void**>(&backbuffer))),
                "Backbuffer unavailable");
        require(SUCCEEDED(device->CreateRenderTargetView(backbuffer, nullptr, &target)),
                "Render target creation failed");
        const FLOAT clear[4] = {.04f, .08f, .95f, 1};
        D3D11_VIEWPORT original_viewport{7, 9, 113, 127, .1f, .8f};
        for (int i = 0; i < 3; ++i) {
            MSG message{};
            while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) {
                TranslateMessage(&message);
                DispatchMessageW(&message);
            }
            context->ClearRenderTargetView(target, clear);
            context->OMSetRenderTargets(1, &target, nullptr);
            context->RSSetViewports(1, &original_viewport);
            require(SUCCEEDED(chain->Present(0, 0)), "Synthetic Present failed");
        }
        require(available && attached == window,
                "Overlay did not attach to the presenting game window");
        ID3D11RenderTargetView* after_target = nullptr;
        context->OMGetRenderTargets(1, &after_target, nullptr);
        require(after_target == target, "Overlay did not restore the game's output target");
        if (after_target)
            after_target->Release();
        D3D11_VIEWPORT after_viewport{};
        UINT count = 1;
        context->RSGetViewports(&count, &after_viewport);
        require(count == 1 &&
                    std::memcmp(&after_viewport, &original_viewport, sizeof(after_viewport)) == 0,
                "Overlay did not restore the game's viewport");
        // A real panel must change backbuffer pixels; successful hook installation
        // alone would pass without ever rendering anything into the game.
        D3D11_TEXTURE2D_DESC texture{};
        backbuffer->GetDesc(&texture);
        texture.Usage = D3D11_USAGE_STAGING;
        texture.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
        texture.BindFlags = 0;
        texture.MiscFlags = 0;
        ID3D11Texture2D* staging = nullptr;
        require(SUCCEEDED(device->CreateTexture2D(&texture, nullptr, &staging)),
                "Readback texture creation failed");
        context->CopyResource(staging, backbuffer);
        D3D11_MAPPED_SUBRESOURCE pixels{};
        require(SUCCEEDED(context->Map(staging, 0, D3D11_MAP_READ, 0, &pixels)),
                "Rendered panel readback failed");
        const auto* sample =
            static_cast<const unsigned char*>(pixels.pData) + 80 * pixels.RowPitch + 40 * 4;
        const bool drawn = sample[2] < 180;
        context->Unmap(staging, 0);
        staging->Release();
        require(drawn, "Present ran but the in-game panel did not change the backbuffer");
        check_guide_visibility(chain, device, context, target, backbuffer);
        stress_game_buffer_lifetimes(chain, device, context, target);
        viewer.check_lifecycle();
        // F9 gives Deadlock its own mouse capture for hero/replay UI interaction.
        // A hidden ImGui backend used to receive every mouse-up and ReleaseCapture
        // even when Dolly did not own the click. Do not touch that capture.
        SetCapture(window);
        require(GetCapture() == window, "Could not establish game mouse capture");
        snapshot.owner = dolly::EditorOwner::GameUI;
        SendMessageW(window, WM_LBUTTONUP, 0, 0);
        require(GetCapture() == window, "Hidden overlay released the game's mouse capture");
        require(SUCCEEDED(chain->Present(0, 0)), "Hidden-overlay Present failed");
        require(GetCapture() == window, "Hidden-overlay Present changed game mouse capture");
        snapshot.owner = dolly::EditorOwner::Panel;
        SendMessageW(window, WM_LBUTTONDOWN, MK_LBUTTON, MAKELPARAM(30, 30));
        require(SUCCEEDED(chain->Present(0, 0)), "Panel Present for legacy mouse-down failed");
        require(observed_mouse_down[0], "Legacy mouse-down did not reach the panel");
        SendMessageW(window, WM_LBUTTONUP, 0, MAKELPARAM(30, 30));
        require(GetCapture() == window, "Panel input changed an existing game mouse capture");
        require(SUCCEEDED(chain->Present(0, 0)), "Panel Present after UI handoff failed");
        require(!observed_mouse_down[0], "Legacy mouse-up did not reach the panel");
        require(GetCapture() == window, "Panel Present changed game mouse capture");
        dolly::overlay_raw_mouse_button(0, true);
        require(SUCCEEDED(chain->Present(0, 0)) && observed_mouse_down[0],
                "Raw-only mouse-down did not reach the panel");
        dolly::overlay_raw_mouse_button(0, false);
        require(SUCCEEDED(chain->Present(0, 0)) && !observed_mouse_down[0],
                "Raw-only mouse-up did not reach the panel");
        require(GetCapture() == window, "Raw-only input changed game mouse capture");
        require(present_keeps_os_cursor, "Present is allowed to change the OS mouse cursor");
        ReleaseCapture();
        // Exercise the real Present capture across the editor-to-path handoff.
        // A valid session survives loss of manual input, pose readiness and focus.
        wchar_t temporary[MAX_PATH]{};
        require(GetTempPathW(MAX_PATH, temporary) != 0, "Video temporary directory unavailable");
        const auto movie = std::wstring(temporary) + L"DollyHandoff-" +
                           std::to_wstring(GetCurrentProcessId()) + L".mp4";
        require(GetFileAttributesW(movie.c_str()) == INVALID_FILE_ATTRIBUTES,
                "Handoff output already exists");
        snapshot.focused = true;
        require(dolly::video::start(movie.c_str(), 30, 2000000), "Handoff recording start failed");
        const auto saved_snapshot = snapshot;
        for (unsigned stage = 0; stage < 3; ++stage) {
            if (stage == 1) {
                snapshot.enabled = false;
                snapshot.ready = false;
            }
            if (stage == 2)
                snapshot.focused = false;
            const auto goal = dolly::video::status().frames_written + 3;
            const auto deadline = GetTickCount64() + 10000;
            while (dolly::video::status().frames_written < goal && GetTickCount64() < deadline) {
                dolly::media_worker_tick(L"Local\\DollyHandoffSmoke", true);
                const float color[] = {0.2f, 0.5f, 0.8f, 1.0f};
                context->ClearRenderTargetView(target, color);
                require(SUCCEEDED(chain->Present(0, 0)), "Handoff recording Present failed");
                const auto video = dolly::video::status();
                require(video.state == dolly::video::State::starting ||
                            video.state == dolly::video::State::recording,
                        "Camera handoff or focus change stopped the recorder");
                Sleep(10);
            }
            require(dolly::video::status().frames_written >= goal,
                    "Recording stopped producing frames during camera handoff");
        }
        dolly::media_worker_tick(nullptr, false);
        require(!dolly::media_session_active(), "Disconnected media session remained active");
        dolly::video::shutdown();
        require(dolly::video::status().state == dolly::video::State::completed,
                "Disconnect did not finalize the recording");
        DeleteFileW(movie.c_str());
        snapshot = saved_snapshot;
        context->OMSetRenderTargets(0, nullptr, nullptr);
        target->Release();
        backbuffer->Release();
        require(SUCCEEDED(chain->ResizeBuffers(1, 720, 480, DXGI_FORMAT_UNKNOWN, 0)),
                "Overlay retained a backbuffer reference across ResizeBuffers");
        require(SUCCEEDED(chain->Present(0, 0)) && available,
                "Overlay did not recover after resize");
        dolly::shutdown_overlay();
        require(!available && attached == nullptr, "Shutdown did not release editor ownership");
        require(GetWindowLongPtrW(window, GWLP_WNDPROC) == before_proc,
                "Shutdown did not restore the original window callback");
        context->Release();
        device->Release();
        chain->Release();
        DestroyWindow(window);
        UnregisterClassW(wc.lpszClassName, instance);
        std::puts(
            "DX11 WARP overlay: panel/guides, mapping lifecycle, state restoration, buffer retirement, resize and UI mouse ownership passed.");
        return 0;
    } catch (const std::exception& error) {
        dolly::shutdown_overlay();
        std::fprintf(stderr, "Overlay smoke failed: %s\n", error.what());
        return 1;
    }
}
