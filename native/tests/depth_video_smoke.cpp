#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <d3d11.h>
#include <dxgi.h>
#include "dolly_depth_scene.hpp"
#include "dolly_video.hpp"
#include <array>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <string>
#include <limits>
#include <stdexcept>
#include <cstdio>

namespace {
void require(bool value, const char* message) {
    if (!value)
        throw std::runtime_error(message);
}
template <class T> struct Com {
    T* p = nullptr;
    ~Com() {
        if (p)
            p->Release();
    }
};
using namespace dolly;
struct Test {
    HWND window = nullptr;
    Com<IDXGISwapChain> chain;
    Com<ID3D11Device> device;
    Com<ID3D11DeviceContext> context;
    Com<ID3D11Texture2D> color, depth;
    Com<ID3D11RenderTargetView> rtv;
    Com<ID3D11DepthStencilView> dsv;
    Com<ID3D11DepthStencilState> state;
    Com<ID3D11Buffer> cb;
    std::unique_ptr<depth::SceneTracker> tracker;
    Test() {
        window = CreateWindowExW(0, L"STATIC", L"Dolly paired depth test", WS_OVERLAPPEDWINDOW, 0,
                                 0, 320, 240, nullptr, nullptr, GetModuleHandleW(nullptr), nullptr);
        require(window != nullptr, "Create test window failed");
        DXGI_SWAP_CHAIN_DESC sc{};
        sc.BufferDesc.Width = 320;
        sc.BufferDesc.Height = 240;
        sc.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
        sc.SampleDesc.Count = 1;
        sc.BufferCount = 1;
        sc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
        sc.OutputWindow = window;
        sc.Windowed = TRUE;
        require(SUCCEEDED(D3D11CreateDeviceAndSwapChain(nullptr, D3D_DRIVER_TYPE_WARP, nullptr, 0,
                                                        nullptr, 0, D3D11_SDK_VERSION, &sc,
                                                        &chain.p, &device.p, nullptr, &context.p)),
                "Create test swapchain failed");
        require(SUCCEEDED(chain.p->GetBuffer(0, __uuidof(ID3D11Texture2D),
                                             reinterpret_cast<void**>(&color.p))),
                "Get color failed");
        require(SUCCEEDED(device.p->CreateRenderTargetView(color.p, nullptr, &rtv.p)),
                "Create color view failed");
        D3D11_TEXTURE2D_DESC td{};
        td.Width = 320;
        td.Height = 240;
        td.MipLevels = td.ArraySize = td.SampleDesc.Count = 1;
        td.Format = DXGI_FORMAT_R32_TYPELESS;
        td.BindFlags = D3D11_BIND_DEPTH_STENCIL;
        require(SUCCEEDED(device.p->CreateTexture2D(&td, nullptr, &depth.p)),
                "Create depth failed");
        const char* name = "scratchrendertarget_1118301577_320x240_17_1.vtex";
        require(SUCCEEDED(depth.p->SetPrivateData(WKPDID_D3DDebugObjectName,
                                                  static_cast<UINT>(std::strlen(name)), name)),
                "Name depth failed");
        D3D11_DEPTH_STENCIL_VIEW_DESC vd{};
        vd.Format = DXGI_FORMAT_D32_FLOAT;
        vd.ViewDimension = D3D11_DSV_DIMENSION_TEXTURE2D;
        require(SUCCEEDED(device.p->CreateDepthStencilView(depth.p, &vd, &dsv.p)),
                "Create depth view failed");
        D3D11_DEPTH_STENCIL_DESC ds{};
        ds.DepthEnable = true;
        ds.DepthWriteMask = D3D11_DEPTH_WRITE_MASK_ALL;
        ds.DepthFunc = D3D11_COMPARISON_GREATER_EQUAL;
        require(SUCCEEDED(device.p->CreateDepthStencilState(&ds, &state.p)), "Create state failed");
        D3D11_BUFFER_DESC bd{};
        bd.ByteWidth = 640;
        bd.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
        require(SUCCEEDED(device.p->CreateBuffer(&bd, nullptr, &cb.p)),
                "Create calibration failed");
        tracker = std::make_unique<depth::SceneTracker>(device.p, 320, 240);
    }
    ~Test() {
        video::stop(true);
        video::shutdown();
        video::reset_resources();
        if (window)
            DestroyWindow(window);
    }
    void present(unsigned id, bool missing = false) {
        std::array<unsigned char, 640> bytes{};
        const auto put = [&bytes](unsigned offset, float value) {
            std::memcpy(bytes.data() + offset, &value, 4);
        };
        for (unsigned offset : {128u, 148u, 168u, 188u, 192u, 212u, 372u, 436u})
            put(offset, 1);
        const float near_clip = float(id + 7);
        put(236, near_clip);
        put(248, -1);
        put(260, -1);
        put(264, 1 / near_clip);
        put(328, 320);
        put(332, 240);
        put(336, 1.0f / 320);
        put(340, 1.0f / 240);
        put(376, near_clip);
        put(380, std::numeric_limits<float>::infinity());
        put(456, -1);
        context.p->UpdateSubresource(cb.p, 0, nullptr, bytes.data(), 0, 0);
        context.p->VSSetConstantBuffers(id % 2, 1, &cb.p);
        context.p->OMSetDepthStencilState(state.p, 0);
        context.p->OMSetRenderTargets(1, &rtv.p, dsv.p);
        const D3D11_VIEWPORT viewport{0, 0, 320, 240, 0, 1};
        context.p->RSSetViewports(1, &viewport);
        const float rgba[]{float(id) / 255, 32.0f / 255, 64.0f / 255, 1};
        context.p->ClearRenderTargetView(rtv.p, rgba);
        context.p->ClearDepthStencilView(dsv.p, D3D11_CLEAR_DEPTH, .5f, 0);
        // This test supplies a calibrated GPU fixture directly to the observer;
        // depth_scene_smoke separately verifies the real draw detours.
        tracker->draw(context.p);
        auto scene = tracker->consume(context.p);
        video::capture(chain.p, device.p, context.p, missing ? nullptr : &scene, double(id) / 10);
        chain.p->Present(0, 0);
    }
    void wait() {
        const auto end = GetTickCount64() + 15000;
        while (GetTickCount64() < end) {
            const auto s = video::status();
            if (s.state == video::State::completed || s.state == video::State::failed ||
                s.state == video::State::cancelled)
                return;
            Sleep(5);
        }
        throw std::runtime_error("Recording did not finish without Present");
    }
};
}
int wmain(int argc, wchar_t** argv) {
    namespace fs = std::filesystem;
    try {
        require(argc == 3, "Usage: depth_video_smoke ffmpeg.exe existing-output-parent");
        const fs::path root =
            fs::path(argv[2]) / (L"depth-video-" + std::to_wstring(GetCurrentProcessId()));
        require(fs::create_directory(root), "Could not create unique output directory");
        Test t;
        for (unsigned mode = 0; mode < 6; ++mode) {
            const auto path = root / (std::to_wstring(mode) + (mode == 5 ? L".mp4" : L".mkv"));
            video::Options options;
            options.path = path.c_str();
            options.ffmpeg = argv[1];
            options.encoder = video::Encoder::ffmpeg;
            options.codec = video::Codec::lossless;
            if (mode == 5) {
                options.encoder = video::Encoder::media_foundation;
                options.codec = video::Codec::h264_mf;
            }
            options.fps = 30;
            options.bitrate = 2000000;
            options.fixed_step = mode != 1;
            options.depth = true;
            require(video::start(options), "Paired recording start failed");
            const auto end = GetTickCount64() + 15000;
            bool enough = false;
            for (unsigned frame = 0; GetTickCount64() < end; ++frame) {
                t.present(frame % 100 + 1);
                const auto status = video::status();
                if (status.state == video::State::failed) {
                    std::fwprintf(stderr, L"%ls\n", status.error);
                    throw std::runtime_error("Paired recording failed");
                }
                if (status.frames_written >= 12) {
                    enough = true;
                    break;
                }
                Sleep(8);
            }
            require(enough, "Paired recording did not produce frames");
            if (mode == 3)
                t.present(77, true);
            else if (mode == 4)
                video::reset_resources();
            else
                video::stop(mode == 2);
            t.wait();
            const auto status = video::status();
            const auto expected = mode == 2   ? video::State::cancelled
                                  : mode == 3 ? video::State::failed
                                              : video::State::completed;
            require(status.state == expected, "Unexpected paired recorder final state");
            video::shutdown();
            video::reset_resources();
            if (expected == video::State::completed) {
                require(fs::exists(path) && fs::exists(path.wstring() + L".depth/manifest.json"),
                        "Completed pair missing output");
                std::ofstream report(root / (std::to_wstring(mode) + L".status"));
                report << status.frames_written << " " << status.frames_dropped << "\n";
            } else
                require(!fs::exists(path) && !fs::exists(path.wstring() + L".depth"),
                        "Failed/cancelled pair retained output");
        }
        const auto collision = root / L"collision.mkv";
        const fs::path collision_dir = collision.wstring() + L".depth";
        require(fs::create_directory(collision_dir), "Create collision fixture failed");
        {
            std::ofstream foreign(collision_dir / "keep.txt");
            foreign << "keep";
        }
        video::Options options;
        options.path = collision.c_str();
        options.ffmpeg = argv[1];
        options.encoder = video::Encoder::ffmpeg;
        options.codec = video::Codec::lossless;
        options.depth = true;
        require(video::start(options), "Collision recording request failed");
        t.present(1);
        t.wait();
        require(video::status().state == video::State::failed, "Existing depth folder accepted");
        video::shutdown();
        video::reset_resources();
        require(!fs::exists(collision) && fs::exists(collision_dir / "keep.txt"),
                "Collision cleanup changed foreign output");
        std::wprintf(
            L"Paired depth/video recordings, cancellation, missing-source failure and reset passed.\n%ls\n",
            root.c_str());
        return 0;
    } catch (const std::exception& e) {
        std::fprintf(stderr, "%s\n", e.what());
        return 1;
    }
}
