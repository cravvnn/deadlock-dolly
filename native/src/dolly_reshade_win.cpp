#include "dolly_reshade.hpp"
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <d3d11.h>
#include <dxgi.h>
#include <array>
#include <atomic>
#include <cstdio>
#include <cwchar>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include "../vendor/reshade/reshade_events.hpp"

extern "C" BOOL WINAPI K32EnumProcessModules(HANDLE process, HMODULE* modules, DWORD size,
                                             DWORD* needed);

namespace dolly {
namespace {
constexpr std::uint32_t kReShadeApi = 20;
using Runtime = reshade::api::effect_runtime;
using CreateRuntime = bool (*)(reshade::api::device_api, void*, void*, void*, const char*,
                               Runtime**);
using DestroyRuntime = void (*)(Runtime*);
using PresentRuntime = void (*)(Runtime*);
using RegisterAddon = bool (*)(void*, std::uint32_t);
using RegisterEvent = void (*)(void*, reshade::addon_event, void*);

struct Backend {
    HMODULE module = nullptr, addon = nullptr;
    CreateRuntime create = nullptr;
    DestroyRuntime destroy = nullptr;
    PresentRuntime present = nullptr;
    RegisterEvent register_event = nullptr;
    std::wstring library_path, config_path;
    std::string config_utf8;
};
std::shared_ptr<const Backend> backend;
std::mutex initialize_mutex, message_mutex;
std::mutex load_request_mutex;
std::wstring loading_library, loading_config;
char status_message[192] = "ReShade is not configured.";
std::atomic<bool> configured{false}, active{false}, available{false}, opened{false};
std::atomic<bool> wanted{false}, loading{false}, failed{false};
// 0 means no pending request, 1 close, 2 open. Input/worker threads never call
// into the effect runtime or wait for the render thread.
std::atomic<unsigned> requested{0};
std::atomic<std::uint64_t> frames{0}, missing_clean{0};

// Runtime state is accessed exclusively under the caller's render mutex.
Runtime* runtime = nullptr;
IDXGISwapChain* runtime_chain = nullptr;
bool creation_failed = false, applying_request = false;
bool clean_delivered = false, updating = false;
ReShadeCleanFrame frame_callback = nullptr;
void* frame_user = nullptr;
ID3D11Device* frame_device = nullptr;
ID3D11DeviceContext* frame_context = nullptr;

constexpr unsigned kInputCapacity = 512;
std::array<ReShadeInputEvent, kInputCapacity> input_events{};
std::atomic<unsigned> input_write{0}, input_read{0};
std::array<bool, 256> forwarded_down{};

void message(const char* text) noexcept {
    std::lock_guard<std::mutex> lock(message_mutex);
    std::snprintf(status_message, sizeof(status_message), "%s", text);
}

bool absolute_path(const wchar_t* path) noexcept {
    if (!path || !*path)
        return false;
    return (path[0] && path[1] == L':' && (path[2] == L'\\' || path[2] == L'/')) ||
           (path[0] == L'\\' && path[1] == L'\\');
}

std::string utf8(const wchar_t* path) {
    const int count =
        WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, path, -1, nullptr, 0, nullptr, nullptr);
    if (count < 2)
        return {};
    std::string result(static_cast<std::size_t>(count), '\0');
    if (!WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, path, -1, result.data(), count, nullptr,
                             nullptr))
        return {};
    result.pop_back();
    return result;
}

bool another_runtime_loaded() noexcept {
    std::array<HMODULE, 2048> modules{};
    DWORD bytes = 0;
    if (!K32EnumProcessModules(GetCurrentProcess(), modules.data(), sizeof(modules), &bytes) ||
        bytes > sizeof(modules))
        return true;
    for (DWORD index = 0; index < bytes / sizeof(HMODULE); ++index)
        if (GetProcAddress(modules[index], "ReShadeCreateEffectRuntime") ||
            GetProcAddress(modules[index], "ReShadeRegisterAddon"))
            return true;
    return false;
}

bool publish_existing_raw_registrations() noexcept {
    // ReShade was loaded after the game registered input. Its documented input
    // implementation ignores WM_INPUT buttons unless it observed NOLEGACY at
    // registration time. Republish only the existing mouse/keyboard entries,
    // preserving every flag and target, to let its hook learn those settings.
    std::array<RAWINPUTDEVICE, 128> devices{};
    UINT count = static_cast<UINT>(devices.size());
    const UINT found = GetRegisteredRawInputDevices(devices.data(), &count, sizeof(RAWINPUTDEVICE));
    if (found == UINT(-1) || found > devices.size())
        return false;
    std::array<RAWINPUTDEVICE, 128> replay{};
    UINT replay_count = 0;
    for (UINT index = 0; index < found; ++index)
        if (devices[index].usUsagePage == 1 &&
            (devices[index].usUsage == 2 || devices[index].usUsage == 6))
            replay[replay_count++] = devices[index];
    return replay_count == 0 ||
           RegisterRawInputDevices(replay.data(), replay_count, sizeof(RAWINPUTDEVICE));
}

void push_input(unsigned vk, bool down, unsigned modifiers) noexcept {
    const unsigned write = input_write.load(std::memory_order_relaxed);
    const unsigned next = (write + 1) % kInputCapacity;
    if (next == input_read.load(std::memory_order_acquire)) {
        // A stalled controller cannot make rendering wait. Keep the observed
        // down state unchanged so its release will be retried next frame.
        return;
    }
    input_events[write] = {static_cast<std::uint16_t>(vk), static_cast<std::uint16_t>(modifiers),
                           down};
    forwarded_down[vk] = down;
    input_write.store(next, std::memory_order_release);
}

void release_input() noexcept {
    for (unsigned vk = 1; vk < forwarded_down.size(); ++vk)
        if (forwarded_down[vk])
            push_input(vk, false, 0);
}

bool mouse_key(unsigned vk, unsigned& button) noexcept {
    switch (vk) {
    case VK_LBUTTON:
        button = 0;
        return true;
    case VK_RBUTTON:
        button = 1;
        return true;
    case VK_MBUTTON:
        button = 2;
        return true;
    case VK_XBUTTON1:
        button = 3;
        return true;
    case VK_XBUTTON2:
        button = 4;
        return true;
    default:
        return false;
    }
}

void observe_input(Runtime* source) noexcept {
    const bool menu = opened.load(std::memory_order_acquire);
    const unsigned modifiers = (source->is_key_down(VK_CONTROL) ? 1u : 0u) |
                               (source->is_key_down(VK_MENU) ? 2u : 0u) |
                               (source->is_key_down(VK_SHIFT) ? 4u : 0u);
    for (unsigned vk = 1; vk < forwarded_down.size(); ++vk) {
        unsigned button = 0;
        const bool mouse = mouse_key(vk, button);
        const bool down = mouse ? source->is_mouse_button_down(button) : source->is_key_down(vk);
        const bool pressed =
            mouse ? source->is_mouse_button_pressed(button) : source->is_key_pressed(vk);
        if (menu && pressed && !forwarded_down[vk])
            push_input(vk, true, modifiers);
        else if (!down && forwarded_down[vk])
            push_input(vk, false, modifiers);
    }
}

bool on_open_overlay(Runtime* source, bool open, reshade::api::input_source) noexcept {
    if (source != runtime)
        return false;
    if (applying_request)
        return false;
    // ReShade's notification is BEFORE a state change, and another add-on can
    // veto it. Defer external Home/close-button requests through open_overlay's
    // returned result instead of treating that notification as confirmation.
    unsigned expected = 0;
    requested.compare_exchange_strong(expected, open ? 2u : 1u, std::memory_order_acq_rel);
    return true;
}

void on_overlay(Runtime* source) noexcept {
    if (source != runtime || !updating)
        return;
    observe_input(source);
    if (opened.load(std::memory_order_acquire))
        source->block_input_next_frame();
    if (!clean_delivered) {
        clean_delivered = true;
        if (frame_callback)
            frame_callback(runtime_chain, frame_device, frame_context, frame_user);
    }
}

void clear_frame() noexcept {
    updating = false;
    frame_callback = nullptr;
    frame_user = nullptr;
    frame_device = nullptr;
    frame_context = nullptr;
}
}

static bool initialize_impl(const wchar_t* library_path, const wchar_t* config_path) noexcept {
    std::lock_guard<std::mutex> lock(initialize_mutex);
    try {
        configured.store(true, std::memory_order_release);
        if (!absolute_path(library_path) || !absolute_path(config_path)) {
            message("ReShade needs an absolute runtime DLL path and a separate Dolly config path.");
            return false;
        }
        if (auto existing = std::atomic_load(&backend)) {
            if (existing->library_path != library_path || existing->config_path != config_path) {
                message(
                    "Restart the editing session to change the ReShade runtime or configuration.");
                return false;
            }
            active.store(true, std::memory_order_release);
            return true;
        }
        wchar_t disabled[8]{};
        if (GetEnvironmentVariableW(L"RESHADE_DISABLE_GRAPHICS_HOOK", disabled,
                                    static_cast<DWORD>(std::size(disabled))) != 1 ||
            disabled[0] != L'1') {
            message("Start a new Dolly session with its optional ReShade setting enabled.");
            return false;
        }
        if (another_runtime_loaded()) {
            message(
                "Another ReShade runtime is already loaded. Dolly's managed runtime was not started.");
            return false;
        }
        const auto file_attributes = GetFileAttributesW(library_path);
        if (file_attributes == INVALID_FILE_ATTRIBUTES ||
            (file_attributes & FILE_ATTRIBUTE_DIRECTORY)) {
            message("The selected ReShade runtime DLL does not exist.");
            return false;
        }
        auto candidate = std::make_shared<Backend>();
        candidate->library_path = library_path;
        candidate->config_path = config_path;
        candidate->config_utf8 = utf8(config_path);
        if (candidate->config_utf8.empty()) {
            message("The ReShade configuration path could not be converted to UTF-8.");
            return false;
        }
        candidate->module = LoadLibraryExW(
            library_path, nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
        if (!candidate->module) {
            message(
                "ReShade could not load. Select the official 64-bit runtime with full add-on support.");
            return false;
        }
        // ReShade can install input hooks during LoadLibrary. Even a rejected
        // runtime stays loaded until process exit to avoid unloading live hooks.
        candidate->create = reinterpret_cast<CreateRuntime>(
            GetProcAddress(candidate->module, "ReShadeCreateEffectRuntime"));
        candidate->destroy = reinterpret_cast<DestroyRuntime>(
            GetProcAddress(candidate->module, "ReShadeDestroyEffectRuntime"));
        candidate->present = reinterpret_cast<PresentRuntime>(
            GetProcAddress(candidate->module, "ReShadeUpdateAndPresentEffectRuntime"));
        candidate->register_event = reinterpret_cast<RegisterEvent>(
            GetProcAddress(candidate->module, "ReShadeRegisterEventForAddon"));
        const auto register_addon = reinterpret_cast<RegisterAddon>(
            GetProcAddress(candidate->module, "ReShadeRegisterAddon"));
        if (!candidate->create || !candidate->destroy || !candidate->present ||
            !candidate->register_event || !register_addon ||
            !GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                                    GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                                reinterpret_cast<LPCWSTR>(&reshade_initialize),
                                &candidate->addon) ||
            !register_addon(candidate->addon, kReShadeApi)) {
            message(
                "Unsupported ReShade runtime. API 20 and full add-on support are required; restart after changing it.");
            return false;
        }
        if (!publish_existing_raw_registrations()) {
            message(
                "ReShade could not synchronize existing mouse input. Restart the editing session.");
            return false;
        }
        candidate->register_event(candidate->addon, reshade::addon_event::reshade_overlay,
                                  reinterpret_cast<void*>(&on_overlay));
        candidate->register_event(candidate->addon, reshade::addon_event::reshade_open_overlay,
                                  reinterpret_cast<void*>(&on_open_overlay));
        std::atomic_store(&backend, std::shared_ptr<const Backend>(candidate));
        active.store(true, std::memory_order_release);
        message("ReShade loaded; waiting for the DirectX 11 game view.");
        return true;
    } catch (...) {
        message("ReShade initialization failed; Dolly's camera remains available.");
        return false;
    }
}

bool reshade_initialize(const wchar_t* library_path, const wchar_t* config_path) noexcept {
    wanted.store(true, std::memory_order_release);
    failed.store(false, std::memory_order_release);
    const bool result = initialize_impl(library_path, config_path);
    if (!result)
        failed.store(true, std::memory_order_release);
    return result;
}

bool reshade_initialize_async(const wchar_t* library_path, const wchar_t* config_path) noexcept {
    try {
        if (!absolute_path(library_path) || !absolute_path(config_path)) {
            configured.store(true, std::memory_order_release);
            failed.store(true, std::memory_order_release);
            message("ReShade needs an absolute runtime DLL path and a separate Dolly config path.");
            return false;
        }
        std::lock_guard<std::mutex> lock(load_request_mutex);
        if (loading.load(std::memory_order_acquire)) {
            const bool same = loading_library == library_path && loading_config == config_path;
            if (same)
                wanted.store(true, std::memory_order_release);
            return same;
        }
        if (auto existing = std::atomic_load(&backend)) {
            if (existing->library_path != library_path || existing->config_path != config_path) {
                message(
                    "Restart the editing session to change the ReShade runtime or configuration.");
                return false;
            }
            if (failed.load(std::memory_order_acquire)) {
                message("Restart the editing session after a ReShade runtime error.");
                return false;
            }
            wanted.store(true, std::memory_order_release);
            active.store(true, std::memory_order_release);
            return true;
        }
        loading_library = library_path;
        loading_config = config_path;
        configured.store(true, std::memory_order_release);
        wanted.store(true, std::memory_order_release);
        failed.store(false, std::memory_order_release);
        loading.store(true, std::memory_order_release);
        message("Loading ReShade.");
        // The game-side bridge remains resident for the entire game process.
        // Loading is separate from the camera worker's heartbeat/command loop.
        std::thread([dll = loading_library, ini = loading_config] {
            const bool result = initialize_impl(dll.c_str(), ini.c_str());
            // Publication allows Present to initialize the runtime immediately.
            // Do not clear a failure reported there after successful DLL load.
            if (!result)
                failed.store(true, std::memory_order_release);
            loading.store(false, std::memory_order_release);
        }).detach();
        return true;
    } catch (...) {
        loading.store(false, std::memory_order_release);
        failed.store(true, std::memory_order_release);
        wanted.store(false, std::memory_order_release);
        message("ReShade's loader thread could not start.");
        return false;
    }
}

void reshade_set_enabled(bool enabled) noexcept {
    wanted.store(enabled, std::memory_order_release);
    active.store(enabled && !failed.load(std::memory_order_acquire) &&
                     bool(std::atomic_load(&backend)),
                 std::memory_order_release);
    if (!enabled) {
        const bool has_menu = opened.load() || available.load() || requested.load() == 2;
        requested.store(has_menu ? 1u : 0u, std::memory_order_release);
    }
}

bool reshade_enabled() noexcept {
    return wanted.load(std::memory_order_acquire) && active.load(std::memory_order_acquire);
}

ReShadeStatus reshade_status() noexcept {
    ReShadeStatus result;
    result.configured = configured.load();
    result.loaded = bool(std::atomic_load(&backend));
    result.available = available.load();
    result.overlay_open = opened.load();
    result.overlay_pending = requested.load() != 0;
    result.enabled = reshade_enabled();
    result.loading = loading.load();
    result.failed = failed.load();
    result.rendered_frames = frames.load();
    result.missing_clean_frames = missing_clean.load();
    std::lock_guard<std::mutex> lock(message_mutex);
    std::snprintf(result.message, sizeof(result.message), "%s", status_message);
    return result;
}

bool reshade_request_overlay(bool open) noexcept {
    if (open && !reshade_available())
        return false;
    if (!open && !opened.load() && !requested.load())
        return true;
    requested.store(open ? 2u : 1u, std::memory_order_release);
    return true;
}
bool reshade_overlay_open() noexcept {
    return opened.load(std::memory_order_acquire);
}
bool reshade_overlay_pending() noexcept {
    return requested.load(std::memory_order_acquire) != 0;
}
bool reshade_available() noexcept {
    return reshade_enabled() && available.load(std::memory_order_acquire);
}

bool reshade_pop_input(ReShadeInputEvent& event) noexcept {
    const unsigned read = input_read.load(std::memory_order_relaxed);
    if (read == input_write.load(std::memory_order_acquire))
        return false;
    event = input_events[read];
    input_read.store((read + 1) % kInputCapacity, std::memory_order_release);
    return true;
}

bool reshade_render(IDXGISwapChain* chain, ID3D11Device* device, ID3D11DeviceContext* context,
                    ReShadeCleanFrame clean_frame, void* user_data) noexcept {
    const auto api = std::atomic_load(&backend);
    if (!api || !reshade_enabled()) {
        if (runtime)
            reshade_release_device();
        else {
            opened.store(false, std::memory_order_release);
            requested.store(0, std::memory_order_release);
            available.store(false, std::memory_order_release);
        }
        return false;
    }
    if (!chain || !device || !context || creation_failed)
        return false;
    if (runtime && chain != runtime_chain)
        return false;
    bool handled = false;
    try {
        if (!runtime) {
            DXGI_SWAP_CHAIN_DESC desc{};
            const bool described = SUCCEEDED(chain->GetDesc(&desc));
            const bool sdr = desc.BufferDesc.Format == DXGI_FORMAT_R8G8B8A8_UNORM ||
                             desc.BufferDesc.Format == DXGI_FORMAT_R8G8B8A8_UNORM_SRGB ||
                             desc.BufferDesc.Format == DXGI_FORMAT_B8G8R8A8_UNORM ||
                             desc.BufferDesc.Format == DXGI_FORMAT_B8G8R8A8_UNORM_SRGB;
            // ReShade renders MSAA/conversion formats to a separate resolved
            // texture, then copies it to the swapchain AFTER drawing its UI.
            // Our pre-UI callback can capture the actual post-effect backbuffer
            // only on these direct, single-sample SDR surfaces.
            if (!described || desc.SampleDesc.Count != 1 || !sdr) {
                creation_failed = true;
                failed.store(true, std::memory_order_release);
                active.store(false, std::memory_order_release);
                message(
                    "ReShade requires a single-sample SDR RGBA/BGRA game backbuffer. Disable MSAA/HDR and restart the editing session.");
                return false;
            }
            Runtime* created = nullptr;
            if (!api->create(reshade::api::device_api::d3d11, device, context, chain,
                             api->config_utf8.c_str(), &created) ||
                !created) {
                creation_failed = true;
                failed.store(true, std::memory_order_release);
                message("ReShade could not create a runtime for this DirectX 11 view.");
                return false;
            }
            runtime = created;
            runtime_chain = chain;
            opened.store(false);
            applying_request = true;
            runtime->open_overlay(false, reshade::api::input_source::none);
            applying_request = false;
            available.store(true, std::memory_order_release);
            message("ReShade is ready.");
        }
        const unsigned request = requested.load(std::memory_order_acquire);
        if (request) {
            applying_request = true;
            const bool accepted =
                runtime->open_overlay(request == 2, reshade::api::input_source::keyboard);
            applying_request = false;
            if (accepted)
                opened.store(request == 2, std::memory_order_release);
            else
                message("ReShade declined the menu request.");
            unsigned expected = request;
            requested.compare_exchange_strong(expected, 0, std::memory_order_acq_rel);
        }
        frame_callback = clean_frame;
        frame_user = user_data;
        frame_device = device;
        frame_context = context;
        clean_delivered = false;
        updating = true;
        handled = true;
        api->present(runtime);
        ++frames;
        if (!clean_delivered) {
            ++missing_clean;
            failed.store(true, std::memory_order_release);
            active.store(false, std::memory_order_release);
            available.store(false, std::memory_order_release);
            message(
                "ReShade stopped: its clean-frame callback is unavailable. Select the full add-on runtime and restart.");
        }
        clear_frame();
        return true;
    } catch (...) {
        applying_request = false;
        clear_frame();
        active.store(false, std::memory_order_release);
        failed.store(true, std::memory_order_release);
        available.store(false, std::memory_order_release);
        opened.store(false, std::memory_order_release);
        requested.store(0, std::memory_order_release);
        release_input();
        message("ReShade stopped after a runtime error; Dolly's camera remains available.");
        return handled;
    }
}

void reshade_release_device() noexcept {
    available.store(false, std::memory_order_release);
    opened.store(false, std::memory_order_release);
    requested.store(0, std::memory_order_release);
    clear_frame();
    release_input();
    Runtime* previous = runtime;
    runtime = nullptr;
    runtime_chain = nullptr;
    creation_failed = false;
    if (previous) {
        const auto api = std::atomic_load(&backend);
        if (api) {
            try {
                api->destroy(previous);
            } catch (...) {
                active.store(false, std::memory_order_release);
                failed.store(true, std::memory_order_release);
                message("ReShade could not release its runtime; restart the editing session.");
            }
        }
    }
}
}
