#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <d3d11.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mfreadwrite.h>
#include <cstdio>
#include <cwchar>
#include <cstring>
#include <string>
#include "dolly_video.hpp"

namespace {
template <class T> void release(T*& p) {
    if (p) {
        p->Release();
        p = nullptr;
    }
}
bool check(bool condition, const char* message) {
    if (!condition)
        std::fprintf(stderr, "FAIL: %s\n", message);
    return condition;
}
bool terminal(dolly::video::State state) {
    return state == dolly::video::State::completed || state == dolly::video::State::failed ||
           state == dolly::video::State::cancelled;
}
bool wait_terminal() {
    const auto until = GetTickCount64() + 15000;
    while (GetTickCount64() < until) {
        if (terminal(dolly::video::status().state))
            return true;
        Sleep(5);
    }
    return false;
}
bool inspect_mp4(const wchar_t* path, std::uint64_t expected_frames,
                 std::uint64_t expected_duration, std::uint32_t expected_fps = 30) {
    // Read the muxed H.264 samples back without requiring a video decoder.
    const auto platform = LoadLibraryExW(L"mfplat.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
    const auto readwrite =
        LoadLibraryExW(L"mfreadwrite.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!platform || !readwrite) {
        if (platform)
            FreeLibrary(platform);
        if (readwrite)
            FreeLibrary(readwrite);
        return false;
    }
    const auto address_of = [](HMODULE module, const char* name, auto& function) {
        const auto address = GetProcAddress(module, name);
        static_assert(sizeof(address) == sizeof(function), "Windows function pointer width");
        std::memcpy(&function, &address, sizeof(function));
    };
    decltype(&MFStartup) startup = nullptr;
    decltype(&MFShutdown) shutdown = nullptr;
    decltype(&MFCreateSourceReaderFromURL) create_reader = nullptr;
    address_of(platform, "MFStartup", startup);
    address_of(platform, "MFShutdown", shutdown);
    address_of(readwrite, "MFCreateSourceReaderFromURL", create_reader);
    const HRESULT com = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    const bool started = startup && shutdown && create_reader && SUCCEEDED(com) &&
                         SUCCEEDED(startup(MF_VERSION, MFSTARTUP_FULL));
    IMFSourceReader* reader = nullptr;
    bool ok = started && SUCCEEDED(create_reader(path, nullptr, &reader));
    if (ok) {
        reader->SetStreamSelection(MF_SOURCE_READER_ALL_STREAMS, FALSE);
        reader->SetStreamSelection(MF_SOURCE_READER_FIRST_VIDEO_STREAM, TRUE);
        IMFMediaType* type = nullptr;
        UINT32 width = 0, height = 0, fps_n = 0, fps_d = 0;
        ok = SUCCEEDED(reader->GetCurrentMediaType(MF_SOURCE_READER_FIRST_VIDEO_STREAM, &type)) &&
             SUCCEEDED(MFGetAttributeSize(type, MF_MT_FRAME_SIZE, &width, &height)) &&
             width == 320 && height == 240 &&
             SUCCEEDED(MFGetAttributeRatio(type, MF_MT_FRAME_RATE, &fps_n, &fps_d)) &&
             fps_n == expected_fps && fps_d == 1;
        release(type);
    }
    std::uint64_t frames = 0;
    LONGLONG last = -1, end = 0;
    while (ok && frames <= expected_frames) {
        DWORD flags = 0;
        LONGLONG timestamp = 0;
        IMFSample* sample = nullptr;
        const HRESULT hr = reader->ReadSample(MF_SOURCE_READER_FIRST_VIDEO_STREAM, 0, nullptr,
                                              &flags, &timestamp, &sample);
        if (FAILED(hr)) {
            ok = false;
            break;
        }
        if (sample) {
            LONGLONG duration = 0;
            ok =
                SUCCEEDED(sample->GetSampleDuration(&duration)) && timestamp > last && duration > 0;
            if (!frames)
                ok = ok && timestamp == 0;
            last = timestamp;
            end = timestamp + duration;
            ++frames;
            release(sample);
        }
        if (flags & MF_SOURCE_READERF_ENDOFSTREAM)
            break;
    }
    ok = ok && frames == expected_frames && end > 0 &&
         std::uint64_t(end) + 10000 >= expected_duration &&
         std::uint64_t(end) <= expected_duration + 10000;
    release(reader);
    if (started)
        shutdown();
    if (SUCCEEDED(com))
        CoUninitialize();
    FreeLibrary(readwrite);
    FreeLibrary(platform);
    return check(
        ok, "MP4 has the expected dimensions, monotonic sample timestamps and elapsed duration");
}
}

int main() {
    using namespace dolly::video;
    if (!check(!start(L"relative.mp4", 30, 10000000), "relative paths are refused") ||
        !check(!start(L"C:\\unused.mp4", 24, 10000000), "unsupported FPS is refused"))
        return 1;
    wchar_t temporary[MAX_PATH]{};
    if (!GetTempPathW(MAX_PATH, temporary))
        return 1;
    const auto path = std::wstring(temporary) + L"DollyVideoSmoke-" +
                      std::to_wstring(GetCurrentProcessId()) + L".mp4";
    // A unique temp name avoids deleting a file from any earlier test run.
    if (GetFileAttributesW(path.c_str()) != INVALID_FILE_ATTRIBUTES)
        return 1;
    HWND window = CreateWindowExW(0, L"STATIC", L"Dolly video smoke", WS_OVERLAPPEDWINDOW, 0, 0,
                                  320, 240, nullptr, nullptr, GetModuleHandleW(nullptr), nullptr);
    DXGI_SWAP_CHAIN_DESC description{};
    description.BufferDesc.Width = 320;
    description.BufferDesc.Height = 240;
    description.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    description.SampleDesc.Count = 1;
    description.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    description.BufferCount = 1;
    description.OutputWindow = window;
    description.Windowed = TRUE;
    description.SwapEffect = DXGI_SWAP_EFFECT_DISCARD;
    IDXGISwapChain* swapchain = nullptr;
    ID3D11Device* device = nullptr;
    ID3D11DeviceContext* context = nullptr;
    HRESULT hr = D3D11CreateDeviceAndSwapChain(nullptr, D3D_DRIVER_TYPE_WARP, nullptr, 0, nullptr,
                                               0, D3D11_SDK_VERSION, &description, &swapchain,
                                               &device, nullptr, &context);
    if (!check(SUCCEEDED(hr), "WARP swapchain creation"))
        return 1;
    ID3D11Texture2D* buffer = nullptr;
    ID3D11RenderTargetView* view = nullptr;
    hr = swapchain->GetBuffer(0, __uuidof(ID3D11Texture2D), reinterpret_cast<void**>(&buffer));
    if (SUCCEEDED(hr))
        hr = device->CreateRenderTargetView(buffer, nullptr, &view);
    bool ok = check(SUCCEEDED(hr), "backbuffer view") &&
              check(start(path.c_str(), 30, 2000000), "recording starts");
    const auto until = GetTickCount64() + 15000;
    std::uint64_t started_at = 0;
    while (ok && GetTickCount64() < until) {
        const float color[] = {0.8f, 0.2f, 0.1f, 1.0f};
        context->ClearRenderTargetView(view, color);
        capture(swapchain, device, context);
        swapchain->Present(0, 0);
        const auto snapshot = status();
        if (snapshot.state == State::failed) {
            std::fwprintf(stderr, L"Recorder failure 0x%08x: %ls\n", snapshot.error_code,
                          snapshot.error);
            ok = false;
            break;
        }
        if (snapshot.state == State::recording && !started_at)
            started_at = GetTickCount64();
        if (started_at && GetTickCount64() - started_at >= 1200)
            break;
        Sleep(8);
    }
    stop();
    // Deliberately provide no more Presents: finalization must still finish
    // without blocking or requiring the game to regain foreground focus.
    ok = check(wait_terminal(), "asynchronous finalization without Present") && ok;
    const auto result = status();
    ok = check(result.state == State::completed && result.frames_written >= 4,
               "real frames were written to a completed recording") &&
         ok;
    shutdown();
    reset_resources();
    if (ok)
        ok = inspect_mp4(path.c_str(), result.frames_written, result.duration_100ns);
    WIN32_FILE_ATTRIBUTE_DATA before{}, after{};
    if (ok) {
        ok = GetFileAttributesExW(path.c_str(), GetFileExInfoStandard, &before) != FALSE;
        ok = check(start(path.c_str(), 30, 2000000),
                   "existing-path request is handled asynchronously") &&
             ok;
        capture(swapchain, device, context);
        ok = check(wait_terminal() && status().state == State::failed, "existing MP4 is refused") &&
             ok;
        shutdown();
        reset_resources();
        ok = GetFileAttributesExW(path.c_str(), GetFileExInfoStandard, &after) &&
             before.nFileSizeHigh == after.nFileSizeHigh &&
             before.nFileSizeLow == after.nFileSizeLow && ok;
    }
    const auto high_path = path + L"-120.mp4";
    if (ok) {
        ok = check(GetFileAttributesW(high_path.c_str()) == INVALID_FILE_ATTRIBUTES,
                   "120 FPS output must not exist") &&
             check(start(high_path.c_str(), 120, 2000000), "120 FPS job starts");
        const auto high_deadline = GetTickCount64() + 10000;
        while (ok && status().frames_written < 6 && GetTickCount64() < high_deadline) {
            const float color[] = {0.1f, 0.6f, 0.3f, 1.0f};
            context->ClearRenderTargetView(view, color);
            capture(swapchain, device, context);
            swapchain->Present(0, 0);
            ok = check(!terminal(status().state), "120 FPS encoder remains active");
            Sleep(2);
        }
        ok = check(status().frames_written >= 6, "120 FPS encoder produces real samples") && ok;
        stop();
        ok = check(wait_terminal() && status().state == State::completed,
                   "120 FPS finalization completes") &&
             ok;
        const auto high_result = status();
        shutdown();
        reset_resources();
        if (ok)
            ok = inspect_mp4(high_path.c_str(), high_result.frames_written,
                             high_result.duration_100ns, 120);
        DeleteFileW(high_path.c_str());
    }
    const auto cancel_path = path + L"-cancel.mp4";
    if (ok) {
        ok = check(start(cancel_path.c_str(), 60, 2000000), "60 FPS cancellation job starts");
        const auto cancel_deadline = GetTickCount64() + 10000;
        while (GetTickCount64() < cancel_deadline && status().state == State::starting) {
            capture(swapchain, device, context);
            swapchain->Present(0, 0);
            Sleep(5);
        }
        ok = check(status().state == State::recording, "60 FPS encoder initializes") && ok;
        capture(swapchain, device, context);
        swapchain->Present(0, 0);
        stop(true);
        ok = check(wait_terminal() && status().state == State::cancelled,
                   "cancel finishes asynchronously") &&
             ok;
        shutdown();
        reset_resources();
        ok = check(GetFileAttributesW(cancel_path.c_str()) == INVALID_FILE_ATTRIBUTES,
                   "cancel removes only its incomplete output") &&
             ok;
    }
    release(view);
    release(buffer);
    release(context);
    release(device);
    release(swapchain);
    DestroyWindow(window);
    DeleteFileW(path.c_str());
    if (ok)
        std::puts("Windows DX11 real-time MP4 capture/finalization/no-overwrite smoke passed.");
    return ok ? 0 : 1;
}
