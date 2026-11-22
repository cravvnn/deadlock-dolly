// An FFmpeg that stops reading its input must not wedge the recorder: the
// encoder thread blocks in WriteFile on the full pipe, and cancel has to end
// the child so the write returns, the take reaches a terminal state and a new
// take can start. This executable is its own stand-in FFmpeg: started with
// FFmpeg arguments it sleeps without ever reading stdin.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <d3d11.h>
#include <dxgi.h>
#include <cstdio>
#include <cstring>
#include <string>
#include "dolly_video.hpp"

namespace {
bool check(bool condition, const char* message) {
    if (!condition)
        std::fprintf(stderr, "FAIL: %s\n", message);
    return condition;
}
bool terminal(dolly::video::State state) {
    using dolly::video::State;
    return state == State::completed || state == State::failed || state == State::cancelled;
}
} // namespace

int main(int argc, char** argv) {
    if (argc > 1 && std::strcmp(argv[1], "-hide_banner") == 0) {
        Sleep(60000);
        return 0;
    }
    using namespace dolly::video;
    wchar_t self[MAX_PATH]{}, temporary[MAX_PATH]{};
    if (!GetModuleFileNameW(nullptr, self, MAX_PATH) || !GetTempPathW(MAX_PATH, temporary))
        return 1;
    const auto output = std::wstring(temporary) + L"DollyStalledEncoder-" +
                        std::to_wstring(GetCurrentProcessId()) + L".mkv";
    HWND window = CreateWindowExW(0, L"STATIC", L"Dolly stalled encoder", WS_OVERLAPPEDWINDOW, 0,
                                  0, 320, 240, nullptr, nullptr, GetModuleHandleW(nullptr), nullptr);
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

    Options options;
    options.path = output.c_str();
    options.fps = 30;
    options.encoder = Encoder::ffmpeg;
    options.codec = Codec::lossless;
    options.ffmpeg = self;
    bool ok = check(start(options), "FFmpeg recording starts");
    // A 320x240 frame is far larger than the pipe buffer, so the first write
    // blocks on the stand-in that never reads.
    const auto until = GetTickCount64() + 10000;
    std::uint64_t recording_at = 0;
    while (ok && GetTickCount64() < until) {
        capture(swapchain, device, context);
        swapchain->Present(0, 0);
        const auto snapshot = status();
        if (terminal(snapshot.state)) {
            ok = check(false, "recording ended before cancel");
            break;
        }
        if (snapshot.state == State::recording && !recording_at)
            recording_at = GetTickCount64();
        if (recording_at && GetTickCount64() - recording_at >= 500)
            break;
        Sleep(8);
    }
    ok = ok && check(recording_at != 0, "recording reached the encoder");
    stop(true);
    const auto cancel_until = GetTickCount64() + 10000;
    while (!terminal(status().state) && GetTickCount64() < cancel_until) {
        capture(swapchain, device, context);
        Sleep(5);
    }
    const auto result = status();
    ok = check(result.state == State::cancelled, "cancel ends a write blocked on a stalled FFmpeg") &&
         ok;
    ok = check(GetFileAttributesW(output.c_str()) == INVALID_FILE_ATTRIBUTES,
               "the cancelled take leaves no output") && ok;
    if (terminal(result.state)) {
        shutdown();
        reset_resources();
    }
    context->Release();
    device->Release();
    swapchain->Release();
    DestroyWindow(window);
    if (ok)
        std::puts("Cancel ends a recording whose FFmpeg stopped reading.");
    std::fflush(stdout);
    // A wedged encoder thread would hang a normal exit; report and leave.
    ExitProcess(ok ? 0 : 1);
}
