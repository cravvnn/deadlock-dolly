// Windows CI exercises the real DX11 overlay on WARP with synthetic editor
// state. No Deadlock modules, private addresses, injection, or console needed.
#include "dolly_overlay.hpp"
#include "dolly_editor.hpp"
#include "dolly_bone_picker.hpp"
#include "dolly_object_picker.hpp"
#include "dolly_renderer_diagnostics.hpp"
#include "dolly_visualization_runtime.hpp"
#include "dolly_reshade.hpp"
#include "dolly_media.hpp"
#include "dolly_video.hpp"
#include "MinHook.h"
#include "imgui.h"
#include "imgui_internal.h"
#include <d3d11.h>
#include <dxgi.h>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <memory>
#include <stdexcept>
#include <vector>
#include <string>
#include <fstream>

namespace {
bool available = false;
HWND attached = nullptr;
dolly::EditorSnapshot snapshot;
bool observed_mouse_down[5]{};
bool present_keeps_os_cursor = false;
struct ObservedAction {
    dolly::EditorAction action;
    double value, revision, time;
};
std::vector<ObservedAction> observed_actions;
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
static ImGuiContext* screenshot_context = nullptr;
static bool framebuffer_scale_probe = false;
namespace dolly {
bool framing_grid_enabled_stub = false;
EditorSnapshot editor_snapshot() noexcept {
    return snapshot;
}
bool editor_framing_grid_enabled() noexcept {
    return framing_grid_enabled_stub;
}
bool editor_camera_list(EditorCameraList& out) noexcept {
    out = {};
    std::memcpy(out.magic, "DLYCAMS1", 8);
    out.abi = 1;
    out.revision = 7;
    out.total = snapshot.camera_count;
    out.count = std::min<std::uint32_t>(out.total, kEditorCameraListCount);
    out.flags = 3;
    for (std::uint32_t i = 0; i < out.count; ++i)
        out.rows[i] = {double(i) * 3, 16.0 / 9, double(i) * 4, i % 2, 0};
    return true;
}
EditorBinding editor_binding_snapshot(EditorAction action) noexcept {
    EditorBinding binding{};
    // A representative default so the key-cap hints render in the smoke test.
    if (action == EditorAction::Capture) {
        binding.vk = 'K';
        binding.modifiers = 3;
    } else if (action == EditorAction::PlayPath) {
        binding.vk = 0x74; // F5
    } else if (action == EditorAction::PlayPause) {
        binding.vk = 'P';
    } else if (action == EditorAction::Stop) {
        binding.vk = 0x08; // Backspace
    } else if (action == EditorAction::Flight) {
        binding.vk = 0x79; // F10
    } else if (action == EditorAction::GameUI) {
        binding.vk = 0x78; // F9
    }
    return binding;
}
bool editor_roster_snapshot(EditorRoster& out) noexcept {
    out = EditorRoster{};
    if (!snapshot.attach_available)
        return false;
    out.count = 1;
    std::snprintf(out.players[0].model_path, sizeof(out.players[0].model_path),
                  "models/heroes_staging/astro/astro.vmdl");
    out.players[0].handle = 19464268;
    out.players[0].entity_index = 76;
    return true;
}
bool editor_enqueue(EditorAction action, double value, const CameraPose* pose) noexcept {
    try {
        observed_actions.push_back({action, value, pose ? (*pose)[0] : 0,
                                    pose ? (*pose)[1] : 0});
    } catch (...) {
        return false;
    }
    return true;
}
bool editor_bones_snapshot(EditorBones& out) noexcept {
    out = EditorBones{};
    out.count = out.total = 3;
    std::snprintf(out.names[0], 64, "head");
    std::snprintf(out.names[1], 64, "hand_R");
    std::snprintf(out.names[2], 64, "weapon_bone_R");
    return snapshot.attach_available;
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
        screenshot_context = ImGui::GetCurrentContext();
        const auto& io = ImGui::GetIO();
        for (int i = 0; i < 5; ++i)
            observed_mouse_down[i] = io.MouseDown[i];
        present_keeps_os_cursor = (io.ConfigFlags & ImGuiConfigFlags_NoMouseCursorChange) != 0;
        if (framebuffer_scale_probe && ImGui::GetCurrentContext()->WithinFrameScope) {
            // Use client-coordinate geometry and a tight clip near the bottom
            // right. A smaller swapchain must scale BOTH viewport and scissors.
            auto* draw = ImGui::GetForegroundDrawList();
            const ImVec2 low(io.DisplaySize.x * .88f, io.DisplaySize.y * .88f);
            const ImVec2 high(io.DisplaySize.x * .98f, io.DisplaySize.y * .98f);
            draw->PushClipRect(low, ImVec2(io.DisplaySize.x * .93f, high.y));
            draw->AddRectFilled(low, high, IM_COL32(255, 0, 255, 255));
            draw->PopClipRect();
        }
    }
}
void editor_attach_window(HWND value) noexcept {
    attached = value;
}
bool editor_window_message(HWND, UINT, WPARAM, LPARAM, LRESULT&) noexcept {
    return false;
}
}
int main(int argc, char** argv) {
    try {
        const bool lens_screenshot = argc == 3 && std::strcmp(argv[1], "--screenshot-lens") == 0;
        const bool export_screenshot =
            argc == 3 && std::strcmp(argv[1], "--screenshot-export") == 0;
        const bool object_screenshot =
            argc == 3 && std::strcmp(argv[1], "--screenshot-object") == 0;
        const bool screenshot = lens_screenshot || export_screenshot || object_screenshot ||
                                (argc == 3 && std::strcmp(argv[1], "--screenshot") == 0);
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
                            32, 800, screenshot ? 1100 : 600, nullptr, nullptr, instance, nullptr);
        require(window != nullptr, "Could not create synthetic window");
        ShowWindow(window, SW_SHOWNOACTIVATE);
        UpdateWindow(window);
        const auto before_proc = GetWindowLongPtrW(window, GWLP_WNDPROC);
        DXGI_SWAP_CHAIN_DESC desc{};
        desc.BufferCount = 1;
        desc.BufferDesc.Width = 800;
        desc.BufferDesc.Height = screenshot ? 1100 : 600;
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
        for (const auto& profile : dolly::kRendererDiagnosticLayouts) {
            require(dolly::renderer_diagnostic_layout(profile.hash, profile.image_size,
                                                      profile.timestamp) == &profile,
                    "Reviewed renderer diagnostic layout did not resolve");
            require(!dolly::renderer_diagnostic_layout(profile.hash, profile.image_size + 1,
                                                       profile.timestamp) &&
                        !dolly::renderer_diagnostic_layout(profile.hash, profile.image_size,
                                                           profile.timestamp + 1) &&
                        !dolly::renderer_diagnostic_layout("unknown", profile.image_size,
                                                           profile.timestamp),
                    "Renderer diagnostic layout accepted changed identity metadata");
        }
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
        if (screenshot) {
            snapshot.dof_available = true;
            snapshot.dof = {1, 1, -100, 0, 180, 1490, -100, 0, 180, 2000, .5};
            snapshot.attach_available = true;
            snapshot.attach_selected = true;
            snapshot.attach_keys = 2;
            snapshot.shot_keys = 2;
            snapshot.attach_target_index = 0;
            snapshot.attach_point = 0;
            snapshot.attach_offsets[2] = 6.0;
            snapshot.attach_smoothing = .15;
            snapshot.roster_count = 1;
        }
        if (object_screenshot) {
            // Render the Object Picker with a few placed objects so the whole
            // right-side panel (library, placed list, place/snap/tool/drop rows)
            // can be reviewed visually for clipping.
            snapshot.object_picker = true;
            snapshot.object_distance = 600.0;
            snapshot.object_count = 3;
            snapshot.object_selected = 1;
            for (std::uint32_t i = 0; i < snapshot.object_count; ++i) {
                snapshot.object_items[i] = {};
                snapshot.object_items[i].shape = i + 3;  // crate, barrel, pillar
                snapshot.object_items[i].scale = 1.0f;
                snapshot.object_items[i].position[0] = 400.0f + 200.0f * i;
                snapshot.object_items[i].position[1] = 0.0f;
                snapshot.object_items[i].position[2] = 0.0f;
            }
        }
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
            if (screenshot && i == 0 && !object_screenshot) {
                const char* page =
                    lens_screenshot ? "LOOK" : (export_screenshot ? "EXPORT" : "CAMERA");
                require(screenshot_context != nullptr, "Overlay ImGui context missing");
                auto& bars = screenshot_context->TabBars;
                bool found = false;
                for (int bar_index = 0; bar_index < bars.GetMapSize(); ++bar_index) {
                    auto* bar = bars.GetByIndex(bar_index);
                    if (!bar)
                        continue;
                    for (auto& tab : bar->Tabs) {
                        if (std::strcmp(ImGui::TabBarGetTabName(bar, &tab), page) == 0) {
                            bar->NextSelectedTabId = tab.ID;
                            found = true;
                        }
                    }
                }
                require(found, "Requested editor screenshot tab missing");
            }
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
        if (screenshot) {
            // Optional no-game visual review of the real rendered panel.
            std::ofstream output(argv[2], std::ios::binary);
            output << "P6\n" << texture.Width << " " << texture.Height << "\n255\n";
            for (unsigned y = 0; y < texture.Height; ++y) {
                const auto row = static_cast<const char*>(pixels.pData) + y * pixels.RowPitch;
                for (unsigned x = 0; x < texture.Width; ++x)
                    output.write(row + x * 4, 3);
            }
            output.close();
            require(bool(output), "Could not save the panel screenshot");
            context->Unmap(staging, 0);
            staging->Release();
            return 0;
        }
        context->Unmap(staging, 0);
        staging->Release();
        require(drawn, "Present ran but the in-game panel did not change the backbuffer");
        // Exercise rendered UI shortcuts with local ImGui events. This is not
        // OS-wide input and does not launch or attach to Deadlock.
        {
            ImGui::SetCurrentContext(screenshot_context);
            auto* panel = ImGui::FindWindowByName("DEADLOCK DOLLY");
            require(panel != nullptr, "Camera panel missing");
            ImGui::FocusWindow(panel);
            auto render = [&] {
                require(SUCCEEDED(chain->Present(0, 0)), "Camera UI Present failed");
            };
            render();
            auto shortcut = [&](ImGuiKey key, bool shift, dolly::EditorAction expected) {
                observed_actions.clear();
                auto& io = ImGui::GetIO();
                io.AddKeyEvent(ImGuiMod_Ctrl, true);
                io.AddKeyEvent(ImGuiMod_Shift, shift);
                io.AddKeyEvent(key, true);
                render();
                render();
                require(observed_actions.size() == 1 && observed_actions[0].action == expected &&
                            observed_actions[0].revision == 7,
                        "Rendered history shortcut did not enqueue one revision-bound action");
                io.AddKeyEvent(key, false);
                io.AddKeyEvent(ImGuiMod_Ctrl, false);
                io.AddKeyEvent(ImGuiMod_Shift, false);
                render();
            };
            shortcut(ImGuiKey_Z, false, dolly::EditorAction::UndoShot);
            shortcut(ImGuiKey_Y, false, dolly::EditorAction::RedoShot);
            shortcut(ImGuiKey_Z, true, dolly::EditorAction::RedoShot);
            std::puts("Rendered history shortcuts: Ctrl+Z, Ctrl+Y, Ctrl+Shift+Z passed.");
            // Draggable camera ticks: Alt+drag pins the playhead and commits
            // exactly one revision-bound SetCameraTime on release; a plain drag
            // scrubs the slider instead of grabbing the tick.
            {
                const auto timeline = dolly::visualization_snapshot();
                require(timeline && timeline->cameras().size() == 2,
                        "Synthetic timeline missing for the tick drag test");
                const auto before = dolly::overlay_diagnostics();
                require(before.timeline_x1 > before.timeline_x0 && before.timeline_y > 0,
                        "Shot timeline rect was not recorded");
                const auto& camera = timeline->cameras()[0];
                const float span = std::max(float(snapshot.duration), .001f);
                const float tick_x =
                    before.timeline_x0 +
                    (before.timeline_x1 - before.timeline_x0) *
                        std::clamp(float(camera.time) / span, 0.0f, 1.0f);
                const float target_x = std::min(before.timeline_x1 - 2.0f, tick_x + 40);
                observed_actions.clear();
                auto& io = ImGui::GetIO();
                auto drag = [&](bool down, float x) {
                    io.AddMousePosEvent(x, before.timeline_y);
                    io.AddMouseButtonEvent(0, down);
                    render();
                };
                // A plain drag must scrub without moving a tick.
                drag(false, tick_x);
                drag(true, tick_x);
                drag(true, target_x);
                drag(false, target_x);
                require(observed_actions.empty() &&
                            dolly::overlay_diagnostics().timeline_drags ==
                                before.timeline_drags,
                        "Plain timeline drag must scrub without moving a tick");
                // Alt+drag moves the tick and commits once on release.
                io.AddKeyEvent(ImGuiMod_Alt, true);
                drag(false, tick_x);
                drag(true, tick_x);
                drag(true, target_x);
                drag(false, target_x);
                io.AddKeyEvent(ImGuiMod_Alt, false);
                render();
                const double expected_time =
                    double(span) * double((target_x - before.timeline_x0) /
                                          (before.timeline_x1 - before.timeline_x0));
                require(observed_actions.size() == 1 &&
                            observed_actions[0].action == dolly::EditorAction::SetCameraTime &&
                            observed_actions[0].value == camera.index &&
                            observed_actions[0].revision == 7 &&
                            std::abs(observed_actions[0].time - expected_time) < 1e-3,
                        "Alt tick drag did not enqueue one revision-bound SetCameraTime");
                require(dolly::overlay_diagnostics().timeline_drags ==
                            before.timeline_drags + 1,
                        "Alt tick drag was not counted as committed");
                std::puts("Rendered timeline: plain drag scrubs; Alt+drag commits SetCameraTime passed.");
            }
            // The Shot timeline wheel-zooms its visible window and right-drag
            // pans it; wheeling out restores the full duration.
            {
                const auto before_zoom = dolly::overlay_diagnostics();
                const float full = before_zoom.timeline_view_end - before_zoom.timeline_view_start;
                require(full > 0, "Shot timeline view window was not recorded");
                const float mid_x = (before_zoom.timeline_x0 + before_zoom.timeline_x1) * .5f;
                auto& io = ImGui::GetIO();
                io.AddMousePosEvent(mid_x, before_zoom.timeline_y);
                io.AddMouseWheelEvent(0.0f, 1.0f);
                render();
                render();
                const auto zoomed = dolly::overlay_diagnostics();
                const float span = zoomed.timeline_view_end - zoomed.timeline_view_start;
                require(span < full, "Wheel over the Shot timeline did not zoom in");
                require(zoomed.timeline_view_start >= -1e-3f &&
                            zoomed.timeline_view_end <= float(snapshot.duration) + 1e-3f,
                        "Zoomed Shot timeline left the shot duration");
                // Right-drag pans the zoomed window (dragging right moves the
                // visible window earlier in the shot).
                io.AddMousePosEvent(mid_x, zoomed.timeline_y);
                io.AddMouseButtonEvent(1, true);
                render();
                io.AddMousePosEvent(mid_x + 40.0f, zoomed.timeline_y);
                render();
                io.AddMouseButtonEvent(1, false);
                render();
                require(dolly::overlay_diagnostics().timeline_view_start <
                            zoomed.timeline_view_start,
                        "Right-drag did not pan the zoomed Shot timeline");
                // Wheel out restores the full duration.
                for (int i = 0; i < 40; ++i) {
                    io.AddMousePosEvent(mid_x, zoomed.timeline_y);
                    io.AddMouseWheelEvent(0.0f, -1.0f);
                    render();
                }
                const auto restored = dolly::overlay_diagnostics();
                require(std::abs((restored.timeline_view_end - restored.timeline_view_start) - full) < 1e-2f,
                        "Wheel out did not restore the full Shot timeline");
                std::puts("Rendered timeline: wheel zoom, right-drag pan and full-range restore passed.");
            }
            // Framing grid: fixed thirds and centre cross while the paused
            // editor owns the view; independent of the path-guides toggle.
            {
                const auto before = dolly::overlay_diagnostics();
                dolly::framing_grid_enabled_stub = true;
                render();
                render();
                require(dolly::overlay_diagnostics().grid_frames == before.grid_frames + 2,
                        "Framing grid did not draw while the paused editor owned the view");
                dolly::framing_grid_enabled_stub = false;
                render();
                require(dolly::overlay_diagnostics().grid_frames == before.grid_frames + 2,
                        "Framing grid kept drawing after being disabled");
                std::puts("Rendered framing grid: enabled/disabled draw counts passed.");
            }
            ImGuiTable* cameras = nullptr;
            auto& tables = screenshot_context->Tables;
            for (int index = 0; index < tables.GetMapSize(); ++index) {
                auto* table = tables.GetByIndex(index);
                if (table && table->ColumnsCount == 4 && table->ColumnsNames.Buf.Size &&
                    std::strcmp(table->ColumnsNames.Buf.Data, "Camera") == 0)
                    cameras = table;
            }
            require(cameras != nullptr, "Rendered camera chooser table missing");
            // At the smoke test's 600-pixel height, the chooser is below the
            // content fold. Scroll its real containing editor before clicking.
            for (auto* parent = cameras->OuterWindow; parent; parent = parent->ParentWindow) {
                if (parent->ScrollMax.y > 0) {
                    ImGui::SetScrollY(parent, 180);
                    break;
                }
            }
            render();
            render();
            const float x = cameras->Columns[0].MinX + 20;
            const float y = (cameras->RowPosY1 + cameras->RowPosY2) / 2;
            auto mouse = [&](bool down) {
                ImGui::GetIO().AddMousePosEvent(x, y);
                ImGui::GetIO().AddMouseButtonEvent(0, down);
                render();
            };
            observed_actions.clear();
            mouse(false);
            mouse(true);
            mouse(false);
            if (observed_actions.size() != 1 ||
                observed_actions[0].action != dolly::EditorAction::SelectCamera ||
                observed_actions[0].value != 1) {
                std::fprintf(stderr, "Chooser click at %.1f %.1f: %zu actions\n", x, y,
                             observed_actions.size());
                for (const auto& action : observed_actions)
                    std::fprintf(stderr, "Action %u value %.1f revision %.1f\n",
                                 unsigned(action.action), action.value, action.revision);
            }
            require(observed_actions.size() == 1 &&
                        observed_actions[0].action == dolly::EditorAction::SelectCamera &&
                        observed_actions[0].value == 1 && observed_actions[0].revision == 7,
                    "A camera row click must select only, with its current revision");
            observed_actions.clear();
            mouse(true);
            mouse(false);
            require(observed_actions.size() == 1 &&
                        observed_actions[0].action == dolly::EditorAction::ViewCamera &&
                        observed_actions[0].value == 1 && observed_actions[0].revision == 7,
                    "A camera row double-click must request the selected view");
            std::puts("Rendered camera chooser: click selects, double-click views passed.");
        }
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
        // Exercise the real transparent picker canvas, not merely the pure
        // selection helpers. The original implementation drew circles but
        // provided no ImGui surface that could own a scene click.
        {
            auto catalog = std::make_shared<dolly::PickerCatalog>();
            catalog->sequence = 42;
            catalog->total = 4;
            catalog->portrait.width = catalog->portrait.height = 2;
            catalog->portrait.bgra = {11, 22, 33, 255, 11, 22, 33, 255,
                                      11, 22, 33, 255, 11, 22, 33, 255};
            dolly::PickerSample sample{};
            sample.count = 4;
            const char* names[] = {"head", "pelvis", "hand_R", "ankle_L"};
            for (unsigned i = 0; i < 4; ++i) {
                dolly::PickerBone bone{};
                bone.source_index = i;
                std::strcpy(bone.name, names[i]);
                catalog->bones.push_back(bone);
                sample.transforms[i][2] = 90 - 25 * float(i);
            }
            sample.transforms[2][1] = -35;
            dolly::picker_publish(catalog);
            auto pose = snapshot.pose;
            snapshot.bone_picker = true;
            auto render_picker = [&] {
                dolly::picker_camera(
                    catalog->sequence, 10, true, true, pose, 90, {},
                    [](void* p, dolly::PickerSample& out, const char*&) noexcept {
                        out = *static_cast<dolly::PickerSample*>(p);
                        return true;
                    },
                    &sample);
                require(SUCCEEDED(chain->Present(0, 0)), "Picker Present failed");
            };
            render_picker();
            render_picker();
            // The UI borrows the renderer's portrait; validate the actual GPU
            // pixels and request-change clearing, not an implementation mock.
            auto portrait_view = [&]() -> ID3D11ShaderResourceView* {
                auto* picker = ImGui::FindWindowByName("Bone Picker");
                require(picker != nullptr, "Picker panel missing");
                for (const auto& command : picker->DrawList->CmdBuffer)
                    if (command.GetTexID() && command.GetTexID() != ImGui::GetIO().Fonts->TexID)
                        return reinterpret_cast<ID3D11ShaderResourceView*>(command.GetTexID());
                return nullptr;
            };
            auto check_portrait = [&](unsigned char expected_blue) {
                auto* view = portrait_view();
                require(view != nullptr, "Portrait image missing from picker draw data");
                ID3D11Resource* resource = nullptr;
                view->GetResource(&resource);
                ID3D11Texture2D* image = nullptr;
                require(SUCCEEDED(resource->QueryInterface(__uuidof(ID3D11Texture2D),
                                                           reinterpret_cast<void**>(&image))),
                        "Portrait is not a texture");
                resource->Release();
                D3D11_TEXTURE2D_DESC description{};
                image->GetDesc(&description);
                require(description.Width == 2 && description.Height == 2 &&
                            description.Format == DXGI_FORMAT_B8G8R8A8_UNORM,
                        "Portrait texture dimensions/format changed");
                description.Usage = D3D11_USAGE_STAGING;
                description.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
                description.BindFlags = description.MiscFlags = 0;
                ID3D11Texture2D* readback = nullptr;
                require(SUCCEEDED(device->CreateTexture2D(&description, nullptr, &readback)),
                        "Portrait readback creation failed");
                context->CopyResource(readback, image);
                D3D11_MAPPED_SUBRESOURCE pixels{};
                require(SUCCEEDED(context->Map(readback, 0, D3D11_MAP_READ, 0, &pixels)),
                        "Portrait readback failed");
                const auto* pixel = static_cast<const unsigned char*>(pixels.pData);
                const bool matches = pixel[0] == expected_blue && pixel[1] == 22 &&
                                     pixel[2] == 33 && pixel[3] == 255;
                context->Unmap(readback, 0);
                readback->Release();
                image->Release();
                require(matches, "Picker displayed stale or altered portrait pixels");
            };
            check_portrait(11);
            dolly::PickerFrame frame{};
            require(dolly::picker_snapshot(frame) && frame.ready, "Synthetic picker not ready");
            ImGui::SetCurrentContext(screenshot_context);
            const auto size = ImGui::GetIO().DisplaySize;
            dolly::VisualizationView view{frame.view, frame.fov, size.x, size.y, 1};
            std::array<double, 3> point{};
            dolly::VisualizationPoint screen{};
            require(dolly::picker_position(frame.sample, catalog->bones[2], point) &&
                        dolly::project_visualization_point(view, point, screen),
                    "Synthetic hand not visible");
            auto mouse = [&](bool down) {
                ImGui::SetCurrentContext(screenshot_context);
                ImGui::GetIO().AddMousePosEvent(float(screen.x), float(screen.y));
                ImGui::GetIO().AddMouseButtonEvent(0, down);
                render_picker();
            };
            mouse(false);
            mouse(true);
            mouse(false);
            require(dolly::picker_snapshot(frame) && frame.selected == 2,
                    "Clicking the visible hand marker did not select its source bone");
            const auto before_orbit = frame.view;
            dolly::overlay_raw_mouse_button(2, true);
            render_picker();
            ImGui::GetIO().AddMousePosEvent(float(screen.x) + 100, float(screen.y) + 20);
            render_picker();
            render_picker();
            require(dolly::picker_snapshot(frame) && frame.view != before_orbit &&
                        frame.selected == 2,
                    "Middle drag must orbit the real canvas without selecting a new bone");
            dolly::overlay_raw_mouse_button(2, false);
            render_picker();
            render_picker();
            require(dolly::picker_snapshot(frame), "Read released orbit");
            const auto stopped_view = frame.view;
            ImGui::GetIO().AddMousePosEvent(float(screen.x) + 150, float(screen.y) + 25);
            render_picker();
            render_picker();
            require(dolly::picker_snapshot(frame) && frame.view == stopped_view,
                    "Mouse motion after release cannot orbit");
            view.pose = frame.view;
            require(dolly::project_visualization_point(view, point, screen),
                    "Hand visible after orbit");
            require(dolly::picker_select(42, 0),
                    "Select different bone before checking rotated hand");
            mouse(false);
            mouse(true);
            mouse(false);
            require(dolly::picker_snapshot(frame) && frame.selected == 2,
                    "Rotated marker hit target must follow the applied camera immediately");
            const auto before_panel_drag = frame.view;
            ImGui::GetIO().AddMousePosEvent(size.x - 100, 100);
            render_picker();
            dolly::overlay_raw_mouse_button(2, true);
            render_picker();
            ImGui::GetIO().AddMousePosEvent(float(screen.x), float(screen.y));
            render_picker();
            render_picker();
            require(dolly::picker_snapshot(frame) && frame.view == before_panel_drag,
                    "Middle drag starting on the panel must not become a scene orbit");
            dolly::overlay_raw_mouse_button(2, false);
            render_picker();
            render_picker();
            dolly::overlay_raw_mouse_button(2, true);
            render_picker();
            ImGui::GetIO().AddFocusEvent(false);
            render_picker();
            ImGui::GetIO().AddFocusEvent(true);
            ImGui::GetIO().AddMousePosEvent(float(screen.x) + 50, float(screen.y));
            render_picker();
            render_picker();
            require(dolly::picker_snapshot(frame) && frame.view == before_panel_drag,
                    "Losing focus must cancel a held middle drag");
            dolly::picker_camera(42, 10, false, true, pose, 90, {}, nullptr, nullptr);
            // A new request without artwork must not retain the old hero image.
            catalog = std::make_shared<dolly::PickerCatalog>(*catalog);
            catalog->sequence = 43;
            catalog->portrait = {};
            dolly::picker_publish(catalog);
            render_picker();
            render_picker();
            require(portrait_view() == nullptr, "New empty portrait retained the previous image");
            catalog = std::make_shared<dolly::PickerCatalog>(*catalog);
            dolly::picker_camera(43, 10, false, true, pose, 90, {}, nullptr, nullptr);
            catalog->sequence = 44;
            catalog->portrait.width = catalog->portrait.height = 2;
            catalog->portrait.bgra = {99, 22, 33, 255, 99, 22, 33, 255,
                                      99, 22, 33, 255, 99, 22, 33, 255};
            dolly::picker_publish(catalog);
            render_picker();
            render_picker();
            check_portrait(99);
            std::puts("Picker portrait: GPU pixels and request-change clearing/replacement passed.");
            dolly::picker_camera(44, 10, false, true, pose, 90, {}, nullptr, nullptr);
            snapshot.bone_picker = false;
            require(SUCCEEDED(chain->Present(0, 0)), "Picker cleanup Present failed");
        }
        // The Object Picker is its own right-side mode. Render it with one placed
        // proxy object and require its window plus projected wireframe line(s).
        {
            snapshot.object_picker = true;
            snapshot.ready = true;
            snapshot.horizontal_fov = 90;
            snapshot.view_width = 2560;
            snapshot.view_height = 1440;
            snapshot.object_count = 1;
            snapshot.object_selected = 0;
            snapshot.object_distance = 600.0;
            snapshot.object_items[0] = {};
            snapshot.object_items[0].shape = 0;      // marker (box)
            // Place the box straight ahead of the camera using the same math the
            // picker uses, so it is guaranteed on-screen for this frame.
            dolly::VisualizationView place_view{snapshot.pose, 90,
                                                double(snapshot.view_width),
                                                double(snapshot.view_height), 1};
            std::array<double, 3> centre{};
            require(dolly::object_place_screen(place_view, snapshot.view_width * 0.5,
                                               snapshot.view_height * 0.5, 600.0, centre),
                    "Object placement failed in smoke test");
            snapshot.object_items[0].position[0] = float(centre[0]);
            snapshot.object_items[0].position[1] = float(centre[1]);
            snapshot.object_items[0].position[2] = float(centre[2]);
            snapshot.object_items[0].scale = 40.0f;
            auto render_objects = [&] {
                require(SUCCEEDED(chain->Present(0, 0)), "Object Picker Present failed");
            };
            render_objects();
            render_objects();
            auto* window = ImGui::FindWindowByName("##object-picker");
            require(window != nullptr, "Object Picker window missing");
            auto* canvas = ImGui::FindWindowByName("##object-canvas");
            require(canvas != nullptr, "Object Picker scene canvas missing");
            // Proxy wireframes draw on the background draw list; count line
            // elements across the whole frame's draw data (post-Render).
            std::size_t lines = 0;
            const auto* data = ImGui::GetDrawData();
            for (int list = 0; list < data->CmdListsCount; ++list)
                for (const auto& command : data->CmdLists[list]->CmdBuffer)
                    lines += command.ElemCount;
            require(lines >= 2, "Object Picker drew no proxy wireframe lines");
            std::puts("Object Picker: right-side window and proxy wireframe render passed.");
            // Move gizmo math is exercised in object_picker_tests; here confirm
            // the panel still renders with a selection ring and the tool row.
            snapshot.object_selected = 0;
            render_objects();
            require(ImGui::FindWindowByName("##object-picker") != nullptr,
                    "Object Picker window missing with selection");
            snapshot.object_picker = false;
            snapshot.object_count = 0;
            snapshot.object_selected = -1;
            require(SUCCEEDED(chain->Present(0, 0)), "Object Picker cleanup Present failed");
        }
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
        framebuffer_scale_probe = true;
        require(SUCCEEDED(chain->GetBuffer(0, __uuidof(ID3D11Texture2D),
                                           reinterpret_cast<void**>(&backbuffer))),
                "Resized backbuffer unavailable");
        require(SUCCEEDED(device->CreateRenderTargetView(backbuffer, nullptr, &target)),
                "Resized render target unavailable");
        const float black[] = {0, 0, 0, 1};
        context->ClearRenderTargetView(target, black);
        require(SUCCEEDED(chain->Present(0, 0)), "Scaled panel Present failed");
        backbuffer->GetDesc(&texture);
        texture.Usage = D3D11_USAGE_STAGING;
        texture.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
        texture.BindFlags = texture.MiscFlags = 0;
        staging = nullptr;
        require(SUCCEEDED(device->CreateTexture2D(&texture, nullptr, &staging)),
                "Scaled panel readback texture failed");
        context->CopyResource(staging, backbuffer);
        require(SUCCEEDED(context->Map(staging, 0, D3D11_MAP_READ, 0, &pixels)),
                "Scaled panel readback failed");
        const auto* inside = static_cast<const unsigned char*>(pixels.pData) +
                             UINT(texture.Height * .94f) * pixels.RowPitch +
                             UINT(texture.Width * .90f) * 4;
        const auto* clipped = static_cast<const unsigned char*>(pixels.pData) +
                              UINT(texture.Height * .94f) * pixels.RowPitch +
                              UINT(texture.Width * .96f) * 4;
        const bool fitted = inside[0] > 240 && inside[1] < 8 && inside[2] > 240;
        const bool scissors = clipped[0] < 8 && clipped[1] < 8 && clipped[2] < 8;
        context->Unmap(staging, 0);
        staging->Release();
        target->Release();
        backbuffer->Release();
        framebuffer_scale_probe = false;
        require(fitted, "Client-sized panel was clipped by a smaller render buffer");
        require(scissors, "Panel clip rectangles did not scale with the render buffer");
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
