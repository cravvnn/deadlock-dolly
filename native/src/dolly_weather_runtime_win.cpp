#include "dolly_weather_runtime.hpp"

#include "dolly_depth_scene.hpp"
#include "dolly_env_render_dx11.hpp"
#include "dolly_map_grid.hpp"
#include "dolly_precip_dx11.hpp"
#include "dolly_seqlock_win.hpp"
#include "dolly_video.hpp"
#include "dolly_weather_state.hpp"
#include "dolly_weather_view_dx11.hpp"
#include "dolly_wind_fluid_dx11.hpp"

#include <d3d11.h>
#include <dxgi.h>
#include <wrl/client.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <future>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>

namespace dolly::weather_runtime {
using Microsoft::WRL::ComPtr;

namespace {

// ---- editor block (worker -> Present) ----
std::shared_ptr<const weather::Wire> gBlock;
// ---- hero trails (bridge worker + view thread -> Present) ----
std::shared_ptr<const weather::trails::Wire> gTrailsBlock;
// A recording of the heroes' paths, taken while the editor plays the shot through.
struct TrailRecording {
    bool active = false;
    std::uint32_t id = 0;
    std::string path;
    std::vector<weather::trails::Sample> samples;
};
std::mutex gTrailsMutex;
TrailRecording gTrailRecording;
std::atomic<bool> gTrailsRecording{false};

// ---- replay clock (view thread -> Present) ----
std::atomic<double> gReplayTime{-1.0};
std::atomic<double> gTickInterval{1.0 / 64.0};
std::atomic<bool> gPlaying{false};
std::atomic<bool> gSeeking{false};
// The heroes seen by the same view as the clock, kept with that clock's time so a frame
// never pairs one replay time with another time's positions.
struct Heroes {
    double time = -1.0;
    uint32_t count = 0;
    float origin[env::kMaxFluidBodies][3]{};
};
std::mutex gHeroesMutex;
Heroes gHeroes;
// Hero wake radius (world units): a little wider than a hero's capsule.
constexpr float kHeroWakeRadius = 56.0f;

// ---- Present-thread state ----
std::filesystem::path cache_directory();
void set_status(const char* text) noexcept;

// GPU timing for the weather pass, for evidence-based tuning. Active only while
// <cache>/profile exists (checked every 2 s). Queries are read a few frames later
// without flushing or waiting, so profiling never stalls the game.
// Development-only: while <cache>/snap exists, write the back buffer as composited by the
// weather pass (before ReShade, capture and the UI) 2 s after each new weather block, as a
// BMP beside the status log. It shows exactly what the GPU produced, unlike desktop capture.
// A test script can also ask for one frame by name: write a label (letters, digits, '-'
// and '_') into <cache>/snap-now; the next weather frame is saved as snap-<label>.bmp and
// the request file is removed, so frames can be taken at a paused replay tick on demand.
struct Snapshot {
    uint32_t block = 0;
    ULONGLONG due = 0;
    int written = 0;
    ULONGLONG polled = 0;
};
Snapshot gSnap;

bool write_snapshot(ID3D11Device* device, ID3D11DeviceContext* ctx, ID3D11Texture2D* back_buffer,
                    const std::filesystem::path& path);

void maybe_snapshot(ID3D11Device* device, ID3D11DeviceContext* ctx, ID3D11Texture2D* back_buffer,
                    uint32_t block) noexcept {
    try {
        const ULONGLONG now = GetTickCount64();
        if (now - gSnap.polled >= 250) {
            gSnap.polled = now;
            const auto dir = cache_directory();
            std::error_code ec;
            const auto request = dir / L"snap-now";
            if (!dir.empty() && std::filesystem::exists(request, ec)) {
                std::string label;
                if (FILE* f = _wfopen(request.c_str(), L"rb")) {
                    char text[64]{};
                    const size_t n = std::fread(text, 1, sizeof(text) - 1, f);
                    std::fclose(f);
                    for (size_t i = 0; i < n; ++i) {
                        const char c = text[i];
                        if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                            (c >= '0' && c <= '9') || c == '-' || c == '_')
                            label += c;
                    }
                }
                if (label.empty())
                    label = "now";
                std::filesystem::remove(request, ec);
                if (write_snapshot(device, ctx, back_buffer, dir / ("snap-" + label + ".bmp"))) {
                    // The replay time each frame was drawn at, so a script can tell a running
                    // weather clock from a frozen one.
                    if (FILE* f = _wfopen((dir / L"snaps.txt").c_str(), L"ab")) {
                        std::fprintf(f, "%s time %.4f playing %d\n", label.c_str(),
                                     gReplayTime.load(std::memory_order_relaxed),
                                     gPlaying.load(std::memory_order_relaxed) ? 1 : 0);
                        std::fclose(f);
                    }
                }
            }
        }
        if (gSnap.written >= 8)
            return;
        if (block != gSnap.block) {
            gSnap.block = block;
            gSnap.due = now + 2000;
            return;
        }
        if (!gSnap.due || now < gSnap.due)
            return;
        gSnap.due = 0;
        const auto dir = cache_directory();
        std::error_code ec;
        if (dir.empty() || !std::filesystem::exists(dir / L"snap", ec))
            return;
        char name[64];
        std::snprintf(name, sizeof(name), "snap-%d-block%u.bmp", gSnap.written, block);
        if (write_snapshot(device, ctx, back_buffer, dir / name))
            ++gSnap.written;
    } catch (...) {
    }
}

bool write_snapshot(ID3D11Device* device, ID3D11DeviceContext* ctx, ID3D11Texture2D* back_buffer,
                    const std::filesystem::path& path) {
    bool ok = false;
    try {
        D3D11_TEXTURE2D_DESC d{};
        back_buffer->GetDesc(&d);
        const bool bgra =
            d.Format == DXGI_FORMAT_B8G8R8A8_UNORM || d.Format == DXGI_FORMAT_B8G8R8A8_UNORM_SRGB;
        const bool rgba =
            d.Format == DXGI_FORMAT_R8G8B8A8_UNORM || d.Format == DXGI_FORMAT_R8G8B8A8_UNORM_SRGB;
        if (!bgra && !rgba)
            return false;
        D3D11_TEXTURE2D_DESC sd = d;
        sd.Usage = D3D11_USAGE_STAGING;
        sd.BindFlags = 0;
        sd.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
        sd.MiscFlags = 0;
        ComPtr<ID3D11Texture2D> staging;
        if (FAILED(device->CreateTexture2D(&sd, nullptr, &staging)))
            return false;
        ctx->CopyResource(staging.Get(), back_buffer);
        D3D11_MAPPED_SUBRESOURCE m{};
        if (FAILED(ctx->Map(staging.Get(), 0, D3D11_MAP_READ, 0, &m)))
            return false;
        if (FILE* f = _wfopen(path.c_str(), L"wb")) {
            const uint32_t row = d.Width * 4, image = row * d.Height;
            unsigned char header[54]{};
            header[0] = 'B';
            header[1] = 'M';
            const uint32_t file_size = 54 + image, offset = 54, info = 40;
            const int32_t w = static_cast<int32_t>(d.Width), h = -static_cast<int32_t>(d.Height);
            const uint16_t planes = 1, bits = 32;
            std::memcpy(header + 2, &file_size, 4);
            std::memcpy(header + 10, &offset, 4);
            std::memcpy(header + 14, &info, 4);
            std::memcpy(header + 18, &w, 4);
            std::memcpy(header + 22, &h, 4); // negative: rows stored top-down
            std::memcpy(header + 26, &planes, 2);
            std::memcpy(header + 28, &bits, 2);
            std::memcpy(header + 34, &image, 4);
            std::fwrite(header, 1, sizeof(header), f);
            std::vector<unsigned char> line(row);
            for (UINT y = 0; y < d.Height; ++y) {
                const auto* src =
                    static_cast<const unsigned char*>(m.pData) + std::size_t(y) * m.RowPitch;
                for (UINT x = 0; x < d.Width; ++x) {
                    line[x * 4 + 0] = src[x * 4 + (bgra ? 0 : 2)];
                    line[x * 4 + 1] = src[x * 4 + 1];
                    line[x * 4 + 2] = src[x * 4 + (bgra ? 2 : 0)];
                    line[x * 4 + 3] = 255;
                }
                std::fwrite(line.data(), 1, row, f);
            }
            std::fclose(f);
            ok = true;
        }
        ctx->Unmap(staging.Get(), 0);
    } catch (...) {
    }
    return ok;
}

class GpuProfiler {
public:
    static constexpr int kMarks = 6; // start, copies, view, fluid, atmosphere, precipitation
    void initialize(ID3D11Device* device) noexcept {
        D3D11_QUERY_DESC dj{D3D11_QUERY_TIMESTAMP_DISJOINT, 0};
        D3D11_QUERY_DESC ts{D3D11_QUERY_TIMESTAMP, 0};
        for (auto& slot : slots_) {
            if (FAILED(device->CreateQuery(&dj, &slot.disjoint)))
                return;
            for (auto& stamp : slot.stamps)
                if (FAILED(device->CreateQuery(&ts, &stamp)))
                    return;
        }
        ready_ = true;
    }
    void begin(ID3D11DeviceContext* ctx, uint32_t width, uint32_t height) noexcept {
        current_ = -1;
        if (!ready_)
            return;
        refresh_enabled();
        collect_all(ctx);
        report(width, height);
        if (!enabled_)
            return;
        const int index = static_cast<int>(next_ % slots_.size());
        if (slots_[index].pending)
            return; // the GPU has not finished that frame yet; skip timing this one
        current_ = index;
        ++next_;
        slots_[index].marks = 0;
        ctx->Begin(slots_[index].disjoint.Get());
        mark(ctx);
    }
    void mark(ID3D11DeviceContext* ctx) noexcept {
        if (current_ < 0)
            return;
        Slot& slot = slots_[current_];
        if (slot.marks < kMarks)
            ctx->End(slot.stamps[slot.marks++].Get());
    }
    void end(ID3D11DeviceContext* ctx) noexcept {
        if (current_ < 0)
            return;
        Slot& slot = slots_[current_];
        ctx->End(slot.disjoint.Get());
        slot.pending = slot.marks == kMarks;
        current_ = -1;
    }

private:
    struct Slot {
        ComPtr<ID3D11Query> disjoint;
        std::array<ComPtr<ID3D11Query>, kMarks> stamps;
        int marks = 0;
        bool pending = false;
    };
    void refresh_enabled() noexcept {
        const ULONGLONG now = GetTickCount64();
        if (now - checked_ < 2000)
            return;
        checked_ = now;
        std::error_code ec;
        const auto dir = cache_directory();
        enabled_ = !dir.empty() && std::filesystem::exists(dir / L"profile", ec);
    }
    void collect_all(ID3D11DeviceContext* ctx) noexcept {
        for (auto& slot : slots_) {
            if (!slot.pending)
                continue;
            D3D11_QUERY_DATA_TIMESTAMP_DISJOINT dj{};
            if (ctx->GetData(slot.disjoint.Get(), &dj, sizeof(dj),
                             D3D11_ASYNC_GETDATA_DONOTFLUSH) != S_OK)
                continue;
            UINT64 t[kMarks]{};
            bool all = true;
            for (int i = 0; i < kMarks && all; ++i)
                all = ctx->GetData(slot.stamps[i].Get(), &t[i], sizeof(t[i]),
                                   D3D11_ASYNC_GETDATA_DONOTFLUSH) == S_OK;
            if (!all)
                continue;
            slot.pending = false;
            if (dj.Disjoint || !dj.Frequency)
                continue;
            for (int i = 0; i + 1 < kMarks; ++i)
                sum_[i] += static_cast<double>(t[i + 1] - t[i]) * 1000.0 /
                           static_cast<double>(dj.Frequency);
            ++frames_;
        }
    }
    void report(uint32_t width, uint32_t height) noexcept {
        const ULONGLONG now = GetTickCount64();
        if (!since_)
            since_ = now;
        if (now - since_ < 3000)
            return;
        since_ = now;
        if (!frames_)
            return;
        const double n = frames_;
        char text[256];
        std::snprintf(
            text, sizeof(text),
            "weather GPU ms (avg of %u frames, %ux%u): copies %.3f, view %.3f, fluid %.3f, "
            "atmosphere %.3f, precipitation %.3f, total %.3f",
            frames_, width, height, sum_[0] / n, sum_[1] / n, sum_[2] / n, sum_[3] / n, sum_[4] / n,
            (sum_[0] + sum_[1] + sum_[2] + sum_[3] + sum_[4]) / n);
        set_status(text);
        for (double& v : sum_)
            v = 0.0;
        frames_ = 0;
    }
    std::array<Slot, 4> slots_;
    double sum_[kMarks - 1]{};
    uint32_t frames_ = 0, next_ = 0;
    int current_ = -1;
    bool ready_ = false, enabled_ = false;
    ULONGLONG checked_ = 0, since_ = 0;
};

struct Gpu {
    ID3D11Device* device = nullptr; // identity only; resources hold their own references
    env::WindFluidSolverDX11 solver;
    env::EnvRendererDX11 env;
    env::PrecipitationRendererDX11 precip;
    env::GpuViewBuilderDX11 view;
    ComPtr<ID3D11Texture2D> color;
    ComPtr<ID3D11ShaderResourceView> color_srv;
    D3D11_TEXTURE2D_DESC color_desc{};
    ComPtr<ID3D11Texture2D> depth;
    ComPtr<ID3D11ShaderResourceView> depth_srv;
    D3D11_TEXTURE2D_DESC depth_desc{};
    std::string trails_path; // the hero path file whose trails are uploaded (empty: none)
    std::string grid_map;                              // map whose grid is uploaded to the solver
    std::shared_ptr<const map_grid::MapGrid> grid_cpu; // the same grid, for CPU queries
    // The drift potential uploaded to the coating, and what it was baked from.
    const map_grid::MapGrid* drift_grid = nullptr;
    int drift_direction = -1; // wind direction, whole degrees
    int drift_strength = -1;  // wind shaping, hundredths
    bool failed = false; // shader or resource creation failed for this device
    GpuProfiler profiler;
};
std::unique_ptr<Gpu> gGpu;

// The drift potential is baked at half the grid's resolution (44 u cells on Midtown): it
// says where and how much, the shader draws every edge from depth and noise.
constexpr uint32_t kDriftCells = 512;
// Wind speed (u/s) at which the wind's shaping of the drifts is full.
constexpr float kDriftFullWind = 400.0f;

// Rebakes the drift potential when the map or the steady wind changes. It depends only on
// the grid and the shot's settings, so every replay time and export sees the same drifts.
void ensure_drift(Gpu& gpu, const weather::Settings& settings) {
    if (!gpu.grid_cpu)
        return;
    float direction = std::fmod(settings.wind_direction, 360.0f);
    if (direction < 0.0f)
        direction += 360.0f;
    const int degrees = int(std::lround(direction)) % 360;
    const int strength =
        int(std::lround(std::clamp(settings.wind_speed / kDriftFullWind, 0.0f, 1.0f) * 100.0f));
    if (gpu.drift_grid == gpu.grid_cpu.get() && gpu.drift_direction == degrees &&
        gpu.drift_strength == strength)
        return;
    gpu.drift_grid = gpu.grid_cpu.get();
    gpu.drift_direction = degrees;
    gpu.drift_strength = strength;
    constexpr float kDegToRad = 3.14159265358979f / 180.0f;
    const auto drift = map_grid::bake_drift_potential(
        *gpu.grid_cpu, std::cos(float(degrees) * kDegToRad), std::sin(float(degrees) * kDegToRad),
        float(strength) / 100.0f, kDriftCells);
    if (drift.empty() || !gpu.env.set_drift_map(kDriftCells, drift.data()))
        gpu.env.set_drift_map(0, nullptr); // even cover rather than a stale map
}


// ---- map grid bake (background thread -> Present) ----
struct Bake {
    std::string map;
    std::future<std::shared_ptr<map_grid::MapGrid>> result;
};
std::unique_ptr<Bake> gBake;
std::string gFailedMap; // a map whose bake failed; not retried until it changes
std::string gBakedFrom; // VPK stem the last successful bake used (may be the fallback)
std::mutex gBakedFromMutex;

char gStatus[512] = "weather off";
std::mutex gStatusMutex;

std::filesystem::path cache_directory();

// The status line changes rarely; each change is appended to weather-status.log in
// the map-grid cache folder so a live session can be diagnosed afterwards.
void set_status(const char* text) noexcept {
    std::lock_guard<std::mutex> lock(gStatusMutex);
    if (std::strncmp(gStatus, text, sizeof(gStatus)) == 0)
        return;
    std::snprintf(gStatus, sizeof(gStatus), "%s", text);
    try {
        const auto dir = cache_directory();
        if (dir.empty())
            return;
        std::error_code ec;
        std::filesystem::create_directories(dir, ec);
        if (FILE* f = _wfopen((dir / L"weather-status.log").c_str(), L"ab")) {
            SYSTEMTIME t{};
            GetLocalTime(&t);
            std::fprintf(f, "%02u:%02u:%02u.%03u %s\r\n", t.wHour, t.wMinute, t.wSecond,
                         t.wMilliseconds, gStatus);
            std::fclose(f);
        }
    } catch (...) {
    }
}

std::filesystem::path game_directory() {
    wchar_t exe[MAX_PATH]{};
    const DWORD n = GetModuleFileNameW(nullptr, exe, MAX_PATH);
    if (!n || n >= MAX_PATH)
        return {};
    // <game>/bin/win64/deadlock.exe
    return std::filesystem::path(exe).parent_path().parent_path().parent_path();
}

std::filesystem::path cache_directory() {
    wchar_t local[MAX_PATH]{};
    const DWORD n = GetEnvironmentVariableW(L"LOCALAPPDATA", local, MAX_PATH);
    if (!n || n >= MAX_PATH)
        return {};
    return std::filesystem::path(local) / L"DeadlockDolly" / L"map-grid";
}

std::shared_ptr<map_grid::MapGrid> bake_grid(std::string map) {
    const auto game = game_directory();
    const auto cache = cache_directory();
    if (game.empty() || cache.empty())
        throw std::runtime_error("no game or cache folder");
    const auto maps = game / L"citadel" / L"maps";
    map_grid::BakeOptions options;
    options.grid_n = 1024;
    std::string error;
    // Match replays (including Street Brawl) name their map "start" in the header;
    // start.vpk is a stub with no world physics. Matches are played on dl_midtown, so a
    // named map that is missing or has no collision falls back to it.
    for (const std::string& name : {map, std::string("dl_midtown")}) {
        const auto vpk = maps / (name + ".vpk");
        std::error_code ec;
        if (!std::filesystem::is_regular_file(vpk, ec))
            continue;
        auto grid = std::make_shared<map_grid::MapGrid>();
        std::string attempt;
        if (map_grid::load_or_bake_map_grid(vpk, cache, options, *grid, nullptr, &attempt)) {
            std::lock_guard<std::mutex> lock(gBakedFromMutex);
            gBakedFrom = name;
            return grid;
        }
        error += (error.empty() ? "" : "; ") + name + ": " + attempt;
        if (name == "dl_midtown")
            break;
    }
    throw std::runtime_error(error.empty() ? "no map VPK found" : error);
}

bool ensure_gpu(ID3D11Device* device) {
    if (gGpu && gGpu->device == device)
        return !gGpu->failed;
    gGpu = std::make_unique<Gpu>();
    gGpu->device = device;
    std::string error;
    if (!gGpu->solver.initialize(device, &error) || !gGpu->env.initialize(device, &error) ||
        !gGpu->precip.initialize(device, &error) || !gGpu->view.initialize(device, &error)) {
        gGpu->failed = true;
        char text[192];
        std::snprintf(text, sizeof(text), "weather unavailable: %.150s", error.c_str());
        set_status(text);
        return false;
    }
    gGpu->profiler.initialize(device);
    return true;
}

// The grid for `map` is uploaded once its background bake finishes.
bool ensure_grid(const std::string& map) {
    Gpu& gpu = *gGpu;
    if (gpu.grid_map == map && gpu.solver.has_grid())
        return true;
    if (map.empty() || map == gFailedMap)
        return false;
    // Never drop an unfinished future: its destructor would block this frame until the
    // old bake ends. A different map waits for the bake in flight to finish first.
    if (gBake && gBake->map != map) {
        if (gBake->result.wait_for(std::chrono::seconds(0)) != std::future_status::ready)
            return false;
        gBake.reset();
    }
    if (!gBake) {
        auto bake = std::make_unique<Bake>();
        bake->map = map;
        try {
            bake->result = std::async(std::launch::async, bake_grid, map);
        } catch (...) {
            return false;
        }
        gBake = std::move(bake);
        set_status("weather: preparing the map grid");
        return false;
    }
    if (gBake->result.wait_for(std::chrono::seconds(0)) != std::future_status::ready)
        return false;
    std::shared_ptr<map_grid::MapGrid> grid;
    std::string error;
    try {
        grid = gBake->result.get();
    } catch (const std::exception& e) {
        error = e.what();
    } catch (...) {
        error = "unknown failure";
    }
    gBake.reset();
    if (!grid || !gpu.solver.upload_map_grid(*grid, 256, &error)) {
        gFailedMap = map;
        char text[480];
        std::snprintf(text, sizeof(text), "weather: the map grid could not be prepared: %.440s",
                      error.c_str());
        set_status(text);
        return false;
    }
    gpu.grid_map = map;
    gpu.grid_cpu = grid;
    {
        std::lock_guard<std::mutex> lock(gBakedFromMutex);
        char text[160];
        std::snprintf(text, sizeof(text), "weather: map grid ready from %s.vpk (replay map '%s')",
                      gBakedFrom.c_str(), map.c_str());
        set_status(text);
    }
    return true;
}

DXGI_FORMAT typeless_depth(DXGI_FORMAT format, DXGI_FORMAT& srv_format) {
    switch (format) {
    case DXGI_FORMAT_R24G8_TYPELESS:
    case DXGI_FORMAT_D24_UNORM_S8_UINT:
    case DXGI_FORMAT_R24_UNORM_X8_TYPELESS:
        srv_format = DXGI_FORMAT_R24_UNORM_X8_TYPELESS;
        return DXGI_FORMAT_R24G8_TYPELESS;
    case DXGI_FORMAT_R32_TYPELESS:
    case DXGI_FORMAT_D32_FLOAT:
    case DXGI_FORMAT_R32_FLOAT:
        srv_format = DXGI_FORMAT_R32_FLOAT;
        return DXGI_FORMAT_R32_TYPELESS;
    default:
        srv_format = DXGI_FORMAT_UNKNOWN;
        return DXGI_FORMAT_UNKNOWN;
    }
}

// Copies the back buffer and the scene depth into textures the passes can sample.
bool copy_inputs(ID3D11Device* device, ID3D11DeviceContext* ctx, ID3D11Texture2D* back_buffer,
                 ID3D11Texture2D* scene_depth) {
    Gpu& gpu = *gGpu;
    D3D11_TEXTURE2D_DESC cd{};
    back_buffer->GetDesc(&cd);
    if (cd.SampleDesc.Count != 1 || cd.ArraySize != 1)
        return false;
    if (!gpu.color || cd.Width != gpu.color_desc.Width || cd.Height != gpu.color_desc.Height ||
        cd.Format != gpu.color_desc.Format) {
        gpu.color.Reset();
        gpu.color_srv.Reset();
        D3D11_TEXTURE2D_DESC d = cd;
        d.MipLevels = 1;
        d.Usage = D3D11_USAGE_DEFAULT;
        d.BindFlags = D3D11_BIND_SHADER_RESOURCE;
        d.CPUAccessFlags = 0;
        d.MiscFlags = 0;
        if (FAILED(device->CreateTexture2D(&d, nullptr, &gpu.color)) ||
            FAILED(device->CreateShaderResourceView(gpu.color.Get(), nullptr, &gpu.color_srv))) {
            gpu.color.Reset();
            return false;
        }
        gpu.color_desc = cd;
    }
    D3D11_TEXTURE2D_DESC dd{};
    scene_depth->GetDesc(&dd);
    DXGI_FORMAT srv_format = DXGI_FORMAT_UNKNOWN;
    const DXGI_FORMAT copy_format = typeless_depth(dd.Format, srv_format);
    if (copy_format == DXGI_FORMAT_UNKNOWN || dd.SampleDesc.Count != 1 || dd.ArraySize != 1)
        return false;
    if (!gpu.depth || dd.Width != gpu.depth_desc.Width || dd.Height != gpu.depth_desc.Height ||
        dd.Format != gpu.depth_desc.Format) {
        gpu.depth.Reset();
        gpu.depth_srv.Reset();
        D3D11_TEXTURE2D_DESC d = dd;
        d.Format = copy_format;
        d.MipLevels = 1;
        d.Usage = D3D11_USAGE_DEFAULT;
        d.BindFlags = D3D11_BIND_SHADER_RESOURCE;
        d.CPUAccessFlags = 0;
        d.MiscFlags = 0;
        D3D11_SHADER_RESOURCE_VIEW_DESC sv{};
        sv.Format = srv_format;
        sv.ViewDimension = D3D11_SRV_DIMENSION_TEXTURE2D;
        sv.Texture2D.MipLevels = 1;
        if (FAILED(device->CreateTexture2D(&d, nullptr, &gpu.depth)) ||
            FAILED(device->CreateShaderResourceView(gpu.depth.Get(), &sv, &gpu.depth_srv))) {
            gpu.depth.Reset();
            return false;
        }
        gpu.depth_desc = dd;
    }
    ctx->CopyResource(gpu.color.Get(), back_buffer);
    ctx->CopyResource(gpu.depth.Get(), scene_depth);
    return true;
}

} // namespace

// Starts, continues or saves the hero path recording as the editor's trails block asks.
void update_trail_recording() noexcept {
    try {
        const auto block = std::atomic_load(&gTrailsBlock);
        const auto controls = block ? weather::trails::decode(*block) : std::nullopt;
        TrailRecording finished;
        {
            std::lock_guard<std::mutex> lock(gTrailsMutex);
            TrailRecording& rec = gTrailRecording;
            const bool want = controls && controls->record;
            if (rec.active && (!want || controls->recording_id != rec.id ||
                               controls->record_path != rec.path)) {
                finished = std::move(rec);
                rec = TrailRecording{};
            }
            if (want && !rec.active) {
                rec.active = true;
                rec.id = controls->recording_id;
                rec.path = controls->record_path;
                rec.samples.clear();
            }
            gTrailsRecording.store(rec.active, std::memory_order_relaxed);
        }
        if (finished.active) {
            std::string error;
            char text[160];
            if (weather::trails::write_samples(finished.path, finished.samples, &error))
                std::snprintf(text, sizeof(text), "weather: saved %zu hero path samples",
                              finished.samples.size());
            else
                std::snprintf(text, sizeof(text), "weather: hero paths not saved (%s)",
                              error.c_str());
            set_status(text);
        }
    } catch (...) {
        set_status("weather: hero path recording failed");
    }
}

void poll_editor_block(const unsigned char* memory) noexcept {
    if (!memory)
        return;
    accept_seqlocked(memory + weather::trails::kWireOffset, gTrailsBlock,
                     [](const weather::trails::Wire& w) {
                         return weather::trails::decode(w).has_value();
                     });
    update_trail_recording();
    weather::Wire wire{};
    if (accept_seqlocked(
            memory + weather::kWireOffset, gBlock,
            [](const weather::Wire& w) { return weather::decode(w).has_value(); }, wire)) {
        char text[96];
        std::snprintf(text, sizeof(text), "weather: block %u received (preset %u, map '%s')",
                      wire.sequence, wire.preset, weather::map_name(wire).c_str());
        set_status(text);
    }
}

void publish_clock(double replay_time, double tick_interval, bool playing, bool seeking,
                   const float (*heroes)[3], uint32_t hero_count, const uint32_t* slots) noexcept {
    // Recording hero paths: every hero seen while the replay plays (never while seeking).
    if (gTrailsRecording.load(std::memory_order_relaxed) && playing && !seeking && heroes &&
        slots && std::isfinite(replay_time)) {
        try {
            std::lock_guard<std::mutex> lock(gTrailsMutex);
            if (gTrailRecording.active) {
                for (uint32_t i = 0; i < hero_count; ++i) {
                    const float* o = heroes[i];
                    if (!std::isfinite(o[0]) || !std::isfinite(o[1]) || !std::isfinite(o[2]))
                        continue;
                    weather::trails::Sample sample;
                    sample.time = replay_time;
                    sample.slot = slots[i];
                    std::memcpy(sample.pos, o, sizeof(sample.pos));
                    gTrailRecording.samples.push_back(sample);
                }
            }
        } catch (...) {
        }
    }
    if (std::isfinite(replay_time)) {
        Heroes next;
        next.time = replay_time;
        for (uint32_t i = 0; heroes && i < hero_count && next.count < env::kMaxFluidBodies; ++i) {
            const float* o = heroes[i];
            if (std::isfinite(o[0]) && std::isfinite(o[1]) && std::isfinite(o[2]) &&
                std::abs(o[0]) < 1e6f && std::abs(o[1]) < 1e6f && std::abs(o[2]) < 1e6f) {
                std::memcpy(next.origin[next.count++], o, sizeof(float) * 3);
            }
        }
        {
            std::lock_guard<std::mutex> lock(gHeroesMutex);
            gHeroes = next;
        }
        gReplayTime.store(replay_time, std::memory_order_relaxed);
    }
    if (std::isfinite(tick_interval) && tick_interval > 0.0 && tick_interval < 1.0)
        gTickInterval.store(tick_interval, std::memory_order_relaxed);
    gPlaying.store(playing, std::memory_order_relaxed);
    gSeeking.store(seeking, std::memory_order_relaxed);
}

LensInputs lens_inputs(const float camera[3]) noexcept {
    LensInputs out;
    try {
        const auto block = std::atomic_load(&gBlock);
        const auto settings = block ? weather::decode(*block) : std::nullopt;
        if (!settings || !settings->enabled() || !gGpu || !gGpu->grid_cpu || !camera)
            return out;
        const map_grid::MapGrid& g = *gGpu->grid_cpu;
        const weather::Resolved resolved = weather::resolve(*settings);
        out.raining = resolved.precipitation.kind == weather::Precipitation::Rain;
        out.rain_rate = out.raining ? resolved.precipitation.rate : 0.0f;
        out.wind_xy[0] = resolved.fluid.wind_x;
        out.wind_xy[1] = resolved.fluid.wind_y;
        out.under_cover = map_grid::cover_above(g, camera[0], camera[1], camera[2]);
        out.valid = true;
    } catch (...) {
        out = LensInputs{};
    }
    return out;
}

bool panel_settings(weather::Settings& out) noexcept {
    const auto block = std::atomic_load(&gBlock);
    if (!block)
        return false;
    const auto settings = weather::decode(*block);
    if (!settings)
        return false;
    out = *settings;
    return true;
}

bool wants_heroes() noexcept {
    if (gTrailsRecording.load(std::memory_order_relaxed))
        return true;
    const auto block = std::atomic_load(&gBlock);
    if (!block)
        return false;
    const auto settings = weather::decode(*block);
    // Wakes push the wind around heroes; the coating keeps heroes clean.
    return settings && settings->enabled() && (settings->hero_wakes || settings->coating > 0.0f);
}

bool wants_scene_depth() noexcept {
    const auto block = std::atomic_load(&gBlock);
    if (!block)
        return false;
    const auto settings = weather::decode(*block);
    return settings && settings->enabled();
}

namespace {
// Uploads the trails of the hero path file the editor names (once per file), or none.
void ensure_trails(Gpu& gpu) {
    const auto block = std::atomic_load(&gTrailsBlock);
    const auto controls = block ? weather::trails::decode(*block) : std::nullopt;
    const std::string want = controls && controls->show ? controls->show_path : std::string();
    if (want == gpu.trails_path)
        return;
    gpu.trails_path = want;
    if (want.empty()) {
        gpu.env.set_hero_trails(nullptr, 0);
        return;
    }
    std::vector<weather::trails::Sample> samples;
    std::string error;
    char text[160];
    if (!weather::trails::read_samples(want, samples, &error)) {
        gpu.env.set_hero_trails(nullptr, 0);
        std::snprintf(text, sizeof(text), "weather: no hero trails (%s)", error.c_str());
        set_status(text);
        return;
    }
    const auto segments = weather::trails::build_segments(std::move(samples));
    const bool ok = gpu.env.set_hero_trails(segments.data(),
                                            static_cast<uint32_t>(segments.size()));
    std::snprintf(text, sizeof(text), "weather: hero trails %s (%zu segments)",
                  ok ? "loaded" : "failed", segments.size());
    set_status(text);
}
} // namespace

void render(ID3D11Device* device, ID3D11DeviceContext* ctx, IDXGISwapChain* chain,
            ID3D11RenderTargetView* target, const depth::SceneFrame* frame) noexcept {
    try {
        const auto block = std::atomic_load(&gBlock);
        const auto settings = block ? weather::decode(*block) : std::nullopt;
        if (!settings || !settings->enabled()) {
            set_status("weather off");
            return;
        }
        if (!device || !ctx || !chain || !target)
            return;
        // Layered takes: weather belongs in Color and World, in the Effects passes only when
        // the shot includes it, and never in Players (or the depth master, which it never
        // writes).
        const auto plate = video::current_plate();
        if (plate == video::Plate::Players || plate == video::Plate::Depth ||
            ((plate == video::Plate::EffectsBlack || plate == video::Plate::EffectsWhite) &&
             !settings->include_in_effects))
            return;
        const double time = gReplayTime.load(std::memory_order_relaxed);
        if (time < 0.0) {
            set_status("weather: waiting for the replay clock");
            return;
        }
        if (!ensure_gpu(device) || !ensure_grid(weather::map_name(*block)))
            return;
        if (!frame || frame->result != depth::SceneResult::ready || !frame->texture()) {
            char text[96];
            std::snprintf(text, sizeof(text), "weather: waiting for scene depth (%s)",
                          !frame             ? "no depth sample"
                          : frame->texture() ? "result not ready"
                                             : "no texture");
            set_status(text);
            return;
        }

        ComPtr<ID3D11Texture2D> back_buffer;
        if (FAILED(chain->GetBuffer(0, __uuidof(ID3D11Texture2D),
                                    reinterpret_cast<void**>(back_buffer.GetAddressOf()))))
            return;
        GpuProfiler& profiler = gGpu->profiler;
        {
            D3D11_TEXTURE2D_DESC bd{};
            back_buffer->GetDesc(&bd);
            profiler.begin(ctx, bd.Width, bd.Height);
        }
        if (!copy_inputs(device, ctx, back_buffer.Get(), frame->texture())) {
            profiler.end(ctx);
            set_status("weather: unsupported back buffer or depth format");
            return;
        }
        profiler.mark(ctx);

        std::array<depth::ProjectionBuffer, env::GpuViewBuilderDX11::kMaxCandidates> buffers{};
        const std::size_t count = frame->calibration(buffers.data(), buffers.size());
        std::array<ID3D11Buffer*, env::GpuViewBuilderDX11::kMaxCandidates> candidates{};
        std::array<uint32_t, env::GpuViewBuilderDX11::kMaxCandidates> sizes{};
        uint32_t used = 0;
        for (std::size_t i = 0; i < count; ++i) {
            if (!buffers[i].buffer)
                continue;
            uint32_t bytes = buffers[i].bytes;
            if (!bytes) {
                D3D11_BUFFER_DESC bd{};
                buffers[i].buffer->GetDesc(&bd);
                bytes = bd.ByteWidth > buffers[i].offset ? bd.ByteWidth - buffers[i].offset : 0;
            }
            if (buffers[i].offset != 0)
                continue; // the per-view layout starts at the buffer origin
            candidates[used] = buffers[i].buffer;
            sizes[used] = bytes;
            ++used;
        }

        Gpu& gpu = *gGpu;
        env::EnvCamera camera{};
        camera.width = gpu.color_desc.Width;
        camera.height = gpu.color_desc.Height;
        camera.reversed_z = true;
        camera.gpu_view =
            gpu.view.build(ctx, candidates.data(), sizes.data(), used, frame->width, frame->height);
        profiler.mark(ctx);
        if (!camera.gpu_view) {
            profiler.end(ctx);
            return;
        }

        weather::Resolved resolved = weather::resolve(*settings);
        const double start =
            static_cast<double>(block->start_tick) * gTickInterval.load(std::memory_order_relaxed);
        // The shot start anchors the wind's history, so playback, scrubbing and every export
        // frame rate reach the same state at the same replay time.
        // Hero wakes: the heroes where the view saw them at this replay time (none when the
        // shot turns wakes off or the game's player offsets are unavailable).
        std::array<env::FluidBody, env::kMaxFluidBodies> bodies{};
        uint32_t body_count = 0;
        if (settings->hero_wakes) {
            std::lock_guard<std::mutex> lock(gHeroesMutex);
            if (gHeroes.time == time) {
                for (uint32_t i = 0; i < gHeroes.count; ++i) {
                    env::FluidBody& b = bodies[body_count++];
                    std::memcpy(b.world_pos, gHeroes.origin[i], sizeof(b.world_pos));
                    b.radius = kHeroWakeRadius;
                    b.active = 1.0f;
                }
            }
        }
        // The coating leaves the same heroes clean, wakes on or off.
        {
            std::lock_guard<std::mutex> lock(gHeroesMutex);
            if (gHeroes.time == time) {
                const uint32_t n = std::min<uint32_t>(gHeroes.count,
                                                      env::EnvRenderParams::kMaxCoatingHeroes);
                for (uint32_t i = 0; i < n; ++i)
                    std::memcpy(resolved.render.heroes[i], gHeroes.origin[i],
                                sizeof(resolved.render.heroes[i]));
                resolved.render.hero_count = n;
            }
        }
        if (resolved.render.enable_coating)
            ensure_drift(gpu, *settings);
        ensure_trails(gpu);
        static uint32_t logged_heroes = ~0u;
        if (body_count != logged_heroes) {
            logged_heroes = body_count;
            char text[64];
            std::snprintf(text, sizeof(text), "weather: hero wakes from %u heroes", body_count);
            set_status(text);
        }
        gpu.solver.step_replay_time(ctx, time, resolved.fluid, bodies.data(), body_count, nullptr,
                                    0, start);
        profiler.mark(ctx);
        resolved.render.sim_time = static_cast<float>(time);
        resolved.render.shot_start_time = static_cast<float>(start);
        resolved.render.coating_growth = weather::coating_growth(*settings, time - start);
        resolved.precipitation.time = time;
        // Fixed-step exports are not real time: march with twice the step budget.
        const auto recorder = video::status().state;
        const bool exporting =
            (recorder == video::State::starting || recorder == video::State::recording) &&
            video::wants_fixed_step();
        resolved.render.march_steps = exporting ? 128 : 64;
        resolved.render.allow_half_res = !exporting; // exports always march at full size
        // Puddles mirror the frame only in the full-colour picture; the World and Effects
        // plates keep the sky's sheen, so no plate carries another plate's content.
        resolved.render.puddle_reflections =
            plate == video::Plate::Unspecified || plate == video::Plate::Color;
        gpu.env.render(ctx, gpu.color_srv.Get(), gpu.depth_srv.Get(), target, camera,
                       resolved.render, gpu.solver, nullptr, 0);
        profiler.mark(ctx);
        gpu.precip.render(ctx, gpu.depth_srv.Get(), target, camera, resolved.precipitation,
                          gpu.solver);
        profiler.mark(ctx);
        profiler.end(ctx);
        maybe_snapshot(device, ctx, back_buffer.Get(), block->sequence);
        char text[160];
        std::snprintf(text, sizeof(text),
                      "weather on: %u view candidates of %zu, scene %ux%u, output %ux%u, map %s",
                      used, count, frame->width, frame->height, camera.width, camera.height,
                      gpu.grid_map.c_str());
        set_status(text);
    } catch (...) {
        set_status("weather: render failed");
    }
}

void release() noexcept {
    gGpu.reset();
}

const char* status() noexcept {
    static thread_local char copy[sizeof(gStatus)];
    std::lock_guard<std::mutex> lock(gStatusMutex);
    std::memcpy(copy, gStatus, sizeof(copy));
    return copy;
}

} // namespace dolly::weather_runtime
