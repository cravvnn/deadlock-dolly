#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <d3d11.h>
#include <dxgi.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mfreadwrite.h>
#include <mferror.h>
#include <codecapi.h>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstring>
#include <cwchar>
#include <memory>
#include <mutex>
#include <thread>
#include <vector>
#include "dolly_video.hpp"
#include "dolly_video_math.hpp"

namespace dolly::video {
namespace {
constexpr unsigned kSlots = 3;
constexpr std::uint64_t kMemoryLimit = 256ULL * 1024 * 1024;
template <class T> struct Com {
    T* p = nullptr;
    ~Com() { reset(); }
    Com() = default;
    Com(const Com&) = delete;
    Com& operator=(const Com&) = delete;
    T** put() {
        reset();
        return &p;
    }
    void reset() {
        if (p) {
            p->Release();
            p = nullptr;
        }
    }
    T* operator->() const { return p; }
};
template <class T> bool load_function(HMODULE module, const char* name, T& result) noexcept {
    const FARPROC address = GetProcAddress(module, name);
    static_assert(sizeof(address) == sizeof(result), "Windows function pointer width");
    std::memcpy(&result, &address, sizeof(result));
    return result != nullptr;
}
struct MediaApi {
    HMODULE platform = nullptr, readwrite = nullptr;
    decltype(&::MFStartup) MFStartup = nullptr;
    decltype(&::MFShutdown) MFShutdown = nullptr;
    decltype(&::MFCreateFile) MFCreateFile = nullptr;
    decltype(&::MFCreateAttributes) MFCreateAttributes = nullptr;
    decltype(&::MFCreateMediaType) MFCreateMediaType = nullptr;
    decltype(&::MFCreateMemoryBuffer) MFCreateMemoryBuffer = nullptr;
    decltype(&::MFCreateSample) MFCreateSample = nullptr;
    decltype(&::MFCreateSinkWriterFromURL) MFCreateSinkWriterFromURL = nullptr;
    bool load() noexcept {
        platform = LoadLibraryExW(L"mfplat.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
        readwrite = LoadLibraryExW(L"mfreadwrite.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
        if (!platform || !readwrite)
            return false;
        return load_function(platform, "MFStartup", MFStartup) &&
               load_function(platform, "MFShutdown", MFShutdown) &&
               load_function(platform, "MFCreateFile", MFCreateFile) &&
               load_function(platform, "MFCreateAttributes", MFCreateAttributes) &&
               load_function(platform, "MFCreateMediaType", MFCreateMediaType) &&
               load_function(platform, "MFCreateMemoryBuffer", MFCreateMemoryBuffer) &&
               load_function(platform, "MFCreateSample", MFCreateSample) &&
               load_function(readwrite, "MFCreateSinkWriterFromURL", MFCreateSinkWriterFromURL);
    }
    void unload() noexcept {
        if (readwrite) {
            FreeLibrary(readwrite);
            readwrite = nullptr;
        }
        if (platform) {
            FreeLibrary(platform);
            platform = nullptr;
        }
    }
    ~MediaApi() { unload(); }
};
struct CpuFrame {
    std::vector<std::uint8_t> pixels;
    std::uint64_t pts = 0;
};
struct Session {
    std::wstring path;
    std::uint32_t fps = 30, bitrate = 20000000;
    // Published once by the render callback before configured=true.
    std::uint32_t width = 0, height = 0;
    bool rgba = false;
    std::atomic<bool> configured{false}, ready{false}, stopping{false}, cancel{false}, done{false};
    std::atomic<unsigned> producer_active{0};
    std::atomic<State> state{State::starting};
    std::atomic<HRESULT> error{S_OK};
    std::atomic<const wchar_t*> error_text{L""};
    std::atomic<std::uint64_t> written{0}, dropped{0}, duration{0};
    // The render callback is the sole producer, the encoder the sole consumer.
    CpuFrame cpu[kSlots];
    std::atomic<std::uint64_t> produced{0}, consumed{0}, gpu_pending{0};
    std::atomic<std::uint64_t> first_qpc{0}, stop_qpc{0};
    std::uint64_t frequency = 1;
    std::mutex wait_mutex;
    std::condition_variable wake;
};
struct GpuSlot {
    Com<ID3D11Texture2D> staging;
    std::uint64_t pts = 0;
};
struct RenderResources {
    // These objects are touched only under the caller's Present/Resize lock.
    std::shared_ptr<Session> owner;
    IDXGISwapChain* swapchain = nullptr; // Identity only; never dereferenced later.
    ID3D11Device* device = nullptr;
    GpuSlot slots[kSlots];
    Com<ID3D11Texture2D> resolved;
    D3D11_TEXTURE2D_DESC description{};
    std::uint64_t submitted = 0, drained = 0;
    Cadence cadence;
    void clear() noexcept {
        for (auto& slot : slots)
            slot.staging.reset();
        resolved.reset();
        if (owner)
            owner->gpu_pending.store(0, std::memory_order_release);
        owner.reset();
        swapchain = nullptr;
        device = nullptr;
        description = {};
        submitted = drained = 0;
        cadence = {};
    }
};
std::mutex control_mutex;
std::shared_ptr<Session> current;
// The native DLL stays resident until process exit. A process-lifetime thread
// holder avoids std::thread's terminating destructor during abrupt ExitProcess;
// normal session cleanup still explicitly joins through shutdown().
std::thread* encoder_thread = nullptr;
std::atomic<unsigned> deferred_stop{0};
std::atomic<std::uint64_t> deferred_stop_qpc{0};
struct CachedStatus {
    std::atomic<State> state{State::idle};
    std::atomic<std::uint32_t> width{0}, height{0}, fps{0}, error_code{0};
    std::atomic<std::uint64_t> written{0}, dropped{0}, duration{0};
    std::atomic<const wchar_t*> error{L""};
} cached;
RenderResources gpu;

std::uint64_t now_qpc() noexcept {
    LARGE_INTEGER result{};
    QueryPerformanceCounter(&result);
    return std::uint64_t(result.QuadPart);
}
void request_stop(Session& s, bool cancel) noexcept {
    if (cancel)
        s.cancel.store(true, std::memory_order_release);
    std::uint64_t unset = 0;
    s.stop_qpc.compare_exchange_strong(unset, now_qpc());
    s.stopping.store(true, std::memory_order_release);
    s.wake.notify_one();
}
void apply_deferred_stop(Session& s) noexcept {
    const auto request = deferred_stop.exchange(0, std::memory_order_acq_rel);
    if (!request)
        return;
    auto timestamp = deferred_stop_qpc.exchange(0);
    if (!timestamp)
        timestamp = now_qpc();
    std::uint64_t unset = 0;
    s.stop_qpc.compare_exchange_strong(unset, timestamp);
    request_stop(s, request == 2);
}
void fail(Session& s, HRESULT hr, const wchar_t* message) noexcept {
    HRESULT empty = S_OK;
    if (s.error.compare_exchange_strong(empty, FAILED(hr) ? hr : E_FAIL))
        s.error_text.store(message, std::memory_order_release);
    request_stop(s, false);
}
bool active(State state) noexcept {
    return state == State::starting || state == State::recording || state == State::finalizing;
}

HRESULT media_type(IMFMediaType* type, const GUID& subtype, const Session& s) {
    HRESULT hr = type->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video);
    if (SUCCEEDED(hr))
        hr = type->SetGUID(MF_MT_SUBTYPE, subtype);
    if (SUCCEEDED(hr))
        hr = type->SetUINT32(MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive);
    if (SUCCEEDED(hr))
        hr = MFSetAttributeSize(type, MF_MT_FRAME_SIZE, s.width, s.height);
    if (SUCCEEDED(hr))
        hr = MFSetAttributeRatio(type, MF_MT_FRAME_RATE, s.fps, 1);
    if (SUCCEEDED(hr))
        hr = MFSetAttributeRatio(type, MF_MT_PIXEL_ASPECT_RATIO, 1, 1);
    if (SUCCEEDED(hr))
        hr = type->SetUINT32(MF_MT_YUV_MATRIX, MFVideoTransferMatrix_BT709);
    if (SUCCEEDED(hr))
        hr = type->SetUINT32(MF_MT_VIDEO_NOMINAL_RANGE, MFNominalRange_16_235);
    if (SUCCEEDED(hr))
        hr = type->SetUINT32(MF_MT_VIDEO_PRIMARIES, MFVideoPrimaries_BT709);
    if (SUCCEEDED(hr))
        hr = type->SetUINT32(MF_MT_TRANSFER_FUNCTION, MFVideoTransFunc_709);
    return hr;
}

HRESULT begin_writer(MediaApi& api, Session& s, Com<IMFByteStream>& file,
                     Com<IMFSinkWriter>& writer, DWORD& stream, bool& created) {
    // This operation atomically refuses existing files, including races after
    // a file picker/launcher existence check. It never truncates another file.
    HRESULT hr = api.MFCreateFile(MF_ACCESSMODE_WRITE, MF_OPENMODE_FAIL_IF_EXIST, MF_FILEFLAGS_NONE,
                                  s.path.c_str(), file.put());
    if (FAILED(hr))
        return hr;
    created = true;
    Com<IMFAttributes> attributes;
    hr = api.MFCreateAttributes(attributes.put(), 2);
    if (SUCCEEDED(hr))
        hr = attributes->SetGUID(MF_TRANSCODE_CONTAINERTYPE, MFTranscodeContainerType_MPEG4);
    if (SUCCEEDED(hr))
        hr = attributes->SetUINT32(MF_READWRITE_ENABLE_HARDWARE_TRANSFORMS, TRUE);
    if (SUCCEEDED(hr))
        hr = api.MFCreateSinkWriterFromURL(nullptr, file.p, attributes.p, writer.put());
    Com<IMFMediaType> output, input;
    if (SUCCEEDED(hr))
        hr = api.MFCreateMediaType(output.put());
    if (SUCCEEDED(hr))
        hr = media_type(output.p, MFVideoFormat_H264, s);
    if (SUCCEEDED(hr))
        hr = output->SetUINT32(MF_MT_AVG_BITRATE, s.bitrate);
    if (SUCCEEDED(hr))
        hr = output->SetUINT32(MF_MT_MPEG2_PROFILE, eAVEncH264VProfile_Base);
    if (SUCCEEDED(hr))
        hr = writer->AddStream(output.p, &stream);
    if (SUCCEEDED(hr))
        hr = api.MFCreateMediaType(input.put());
    if (SUCCEEDED(hr))
        hr = media_type(input.p, MFVideoFormat_NV12, s);
    if (SUCCEEDED(hr))
        hr = input->SetUINT32(MF_MT_DEFAULT_STRIDE, s.width);
    if (SUCCEEDED(hr))
        hr = writer->SetInputMediaType(stream, input.p, nullptr);
    // The sink writer's default throttling stays enabled: any encoder
    // backpressure waits here on this worker, never on the render thread.
    if (SUCCEEDED(hr))
        hr = writer->BeginWriting();
    return hr;
}

HRESULT make_sample(MediaApi& api, const Session& s, const CpuFrame& frame,
                    Com<IMFSample>& sample) {
    const DWORD bytes = s.width * s.height * 3 / 2;
    Com<IMFMediaBuffer> buffer;
    HRESULT hr = api.MFCreateMemoryBuffer(bytes, buffer.put());
    BYTE* destination = nullptr;
    if (SUCCEEDED(hr))
        hr = buffer->Lock(&destination, nullptr, nullptr);
    if (SUCCEEDED(hr)) {
        rgb_to_nv12(frame.pixels.data(), std::size_t(s.width) * 4, destination, s.width, s.height,
                    s.rgba);
        hr = buffer->Unlock();
    }
    if (SUCCEEDED(hr))
        hr = buffer->SetCurrentLength(bytes);
    if (SUCCEEDED(hr))
        hr = api.MFCreateSample(sample.put());
    if (SUCCEEDED(hr))
        hr = sample->AddBuffer(buffer.p);
    if (SUCCEEDED(hr))
        hr = sample->SetSampleTime(LONGLONG(frame.pts));
    return hr;
}
HRESULT write_sample(Session& s, IMFSinkWriter* writer, DWORD stream, IMFSample* sample,
                     std::uint64_t start_pts, std::uint64_t end_pts) {
    const auto duration = std::max<std::uint64_t>(1, end_pts > start_pts ? end_pts - start_pts : 1);
    HRESULT hr = sample->SetSampleDuration(LONGLONG(duration));
    if (SUCCEEDED(hr))
        hr = writer->WriteSample(stream, sample);
    if (SUCCEEDED(hr)) {
        s.written.fetch_add(1, std::memory_order_relaxed);
        s.duration.store(start_pts + duration, std::memory_order_release);
    }
    return hr;
}
HRESULT consume_frame(MediaApi& api, Session& s, IMFSinkWriter* writer, DWORD stream,
                      Com<IMFSample>& previous, std::uint64_t& previous_pts) {
    const auto read = s.consumed.load(std::memory_order_relaxed);
    auto& frame = s.cpu[read % kSlots];
    Com<IMFSample> next;
    HRESULT hr = make_sample(api, s, frame, next);
    const auto pts = frame.pts;
    s.consumed.store(read + 1, std::memory_order_release);
    if (SUCCEEDED(hr) && previous.p)
        hr = write_sample(s, writer, stream, previous.p, previous_pts, pts);
    if (FAILED(hr))
        return hr;
    previous.reset();
    previous.p = next.p;
    next.p = nullptr;
    previous_pts = pts;
    return S_OK;
}
void encode(std::shared_ptr<Session> s) noexcept {
    bool com_started = false, mf_started = false, created = false, finalized = false;
    MediaApi api;
    Com<IMFByteStream> file;
    Com<IMFSinkWriter> writer;
    Com<IMFSample> previous;
    DWORD stream = 0;
    std::uint64_t previous_pts = 0;
    try {
        const auto deadline = GetTickCount64() + 10000;
        while (!s->configured.load(std::memory_order_acquire) &&
               !s->stopping.load(std::memory_order_acquire)) {
            std::unique_lock<std::mutex> lock(s->wait_mutex);
            s->wake.wait_for(lock, std::chrono::milliseconds(20));
            apply_deferred_stop(*s);
            if (GetTickCount64() > deadline)
                fail(*s, HRESULT_FROM_WIN32(ERROR_TIMEOUT),
                     L"No DirectX 11 game frame was available for recording.");
        }
        if (!s->stopping.load()) {
            HRESULT hr = api.load() ? S_OK : HRESULT_FROM_WIN32(ERROR_MOD_NOT_FOUND);
            if (SUCCEEDED(hr))
                hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
            com_started = SUCCEEDED(hr);
            if (SUCCEEDED(hr)) {
                hr = api.MFStartup(MF_VERSION, MFSTARTUP_FULL);
                mf_started = SUCCEEDED(hr);
            }
            if (SUCCEEDED(hr))
                hr = begin_writer(api, *s, file, writer, stream, created);
            if (FAILED(hr)) {
                fail(
                    *s, hr,
                    L"MP4 encoder could not start. Check the output path, Windows Media Feature Pack and H.264 encoder support.");
            } else {
                for (auto& frame : s->cpu)
                    frame.pixels.resize(std::size_t(s->width) * s->height * 4);
                s->state.store(State::recording, std::memory_order_release);
                s->ready.store(true, std::memory_order_release);
            }
        }
        std::uint64_t stop_deadline = 0;
        while (writer.p && SUCCEEDED(s->error.load()) && !s->cancel.load()) {
            apply_deferred_stop(*s);
            const bool stopping = s->stopping.load(std::memory_order_acquire);
            if (stopping && !stop_deadline) {
                s->state.store(State::finalizing, std::memory_order_release);
                stop_deadline = GetTickCount64() + 250;
            }
            const auto read = s->consumed.load(std::memory_order_relaxed);
            if (read != s->produced.load(std::memory_order_acquire)) {
                const HRESULT hr = consume_frame(api, *s, writer.p, stream, previous, previous_pts);
                if (FAILED(hr)) {
                    fail(*s, hr,
                         L"Video encoding failed. Check available disk space and encoder support.");
                    break;
                }
                continue;
            }
            if (stopping && (!s->gpu_pending.load(std::memory_order_acquire) ||
                             GetTickCount64() >= stop_deadline))
                break;
            std::unique_lock<std::mutex> lock(s->wait_mutex);
            s->wake.wait_for(lock, std::chrono::milliseconds(5));
        }
        s->ready.store(false, std::memory_order_release);
        while (s->producer_active.load(std::memory_order_acquire))
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        // No Present after alt-tab/shutdown is required to finish the file.
        // Pending GPU copies are counted as lost, then released at the next
        // serialized render/reset boundary, never by the encoder thread.
        s->dropped.fetch_add(s->gpu_pending.exchange(0), std::memory_order_relaxed);
        // A Present may have published one last CPU frame while the worker
        // observed the stop deadline. With the producer now quiescent, drain
        // the bounded CPU queue before finalizing instead of silently losing it.
        while (writer.p && !s->cancel.load() && SUCCEEDED(s->error.load()) &&
               s->consumed.load() != s->produced.load(std::memory_order_acquire)) {
            const HRESULT hr = consume_frame(api, *s, writer.p, stream, previous, previous_pts);
            if (FAILED(hr))
                fail(*s, hr, L"Video encoding failed while finishing the last frames.");
        }
        if (writer.p && !s->cancel.load() && SUCCEEDED(s->error.load()) && previous.p) {
            const auto first = s->first_qpc.load(std::memory_order_acquire);
            const auto last = s->stop_qpc.load(std::memory_order_acquire);
            auto end = last > first ? clock_units(last - first, s->frequency, 10000000) : 0;
            end = std::max(end, previous_pts + 1);
            HRESULT hr = write_sample(*s, writer.p, stream, previous.p, previous_pts, end);
            if (SUCCEEDED(hr))
                hr = writer->Finalize();
            finalized = SUCCEEDED(hr);
            if (FAILED(hr))
                fail(*s, hr, L"The MP4 file could not be finalized. Check available disk space.");
        } else if (writer.p && !s->cancel.load() && SUCCEEDED(s->error.load())) {
            fail(*s, E_FAIL, L"Recording ended before any game frame was captured.");
        }
    } catch (...) {
        fail(*s, E_OUTOFMEMORY,
             L"Video recording ran out of memory or the encoder could not continue.");
    }
    s->ready.store(false, std::memory_order_release);
    while (s->producer_active.load(std::memory_order_acquire))
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    if (s->cancel.load() || FAILED(s->error.load()))
        s->dropped.fetch_add(s->produced.load() - s->consumed.load() + (previous.p ? 1 : 0));
    previous.reset();
    writer.reset();
    if (file.p)
        file->Close();
    file.reset();
    if (mf_started)
        api.MFShutdown();
    if (com_started)
        CoUninitialize();
    api.unload();
    // Delete only an output atomically created by this recording, never an
    // existing destination rejected by MF_OPENMODE_FAIL_IF_EXIST.
    if (created && (!finalized || s->cancel.load()))
        DeleteFileW(s->path.c_str());
    s->state.store(FAILED(s->error.load())          ? State::failed
                   : (s->cancel.load() || !created) ? State::cancelled
                                                    : State::completed,
                   std::memory_order_release);
    s->done.store(true, std::memory_order_release);
}

bool initialize_resources(Session& s, IDXGISwapChain* swapchain, ID3D11Device* device,
                          ID3D11Texture2D* backbuffer, const D3D11_TEXTURE2D_DESC& desc) noexcept {
    const bool rgba =
        desc.Format == DXGI_FORMAT_R8G8B8A8_UNORM || desc.Format == DXGI_FORMAT_R8G8B8A8_UNORM_SRGB;
    const bool bgra =
        desc.Format == DXGI_FORMAT_B8G8R8A8_UNORM || desc.Format == DXGI_FORMAT_B8G8R8A8_UNORM_SRGB;
    const auto bytes = std::uint64_t(desc.Width) * desc.Height * 4;
    // Three GPU textures, three CPU frames, one optional resolve texture and
    // two NV12 samples: a fixed bound independent of recording length.
    if ((!rgba && !bgra) || !desc.Width || !desc.Height || (desc.Width & 1) || (desc.Height & 1) ||
        desc.Width > 3840 || desc.Height > 2160 || bytes * 8 > kMemoryLimit ||
        desc.ArraySize != 1 || desc.MipLevels != 1) {
        fail(s, E_INVALIDARG,
             L"Recording requires an even-sized SDR BGRA/RGBA backbuffer, up to 3840 x 2160.");
        return false;
    }
    auto staging_desc = desc;
    staging_desc.SampleDesc = {1, 0};
    staging_desc.Usage = D3D11_USAGE_STAGING;
    staging_desc.BindFlags = 0;
    staging_desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    staging_desc.MiscFlags = 0;
    HRESULT hr = S_OK;
    for (auto& slot : gpu.slots) {
        hr = device->CreateTexture2D(&staging_desc, nullptr, slot.staging.put());
        if (FAILED(hr))
            break;
    }
    if (SUCCEEDED(hr) && desc.SampleDesc.Count > 1) {
        UINT support = 0;
        hr = device->CheckFormatSupport(desc.Format, &support);
        if (SUCCEEDED(hr) && !(support & D3D11_FORMAT_SUPPORT_MULTISAMPLE_RESOLVE))
            hr = E_NOTIMPL;
        auto resolved_desc = staging_desc;
        resolved_desc.Usage = D3D11_USAGE_DEFAULT;
        resolved_desc.CPUAccessFlags = 0;
        if (SUCCEEDED(hr))
            hr = device->CreateTexture2D(&resolved_desc, nullptr, gpu.resolved.put());
    }
    if (FAILED(hr)) {
        fail(s, hr, L"DirectX 11 could not allocate the bounded video readback buffers.");
        return false;
    }
    (void)backbuffer;
    gpu.swapchain = swapchain;
    gpu.device = device;
    gpu.description = desc;
    gpu.cadence.frequency = s.frequency;
    gpu.cadence.fps = s.fps;
    s.width = desc.Width;
    s.height = desc.Height;
    s.rgba = rgba;
    s.configured.store(true, std::memory_order_release);
    s.wake.notify_one();
    return true;
}
}

bool start(const wchar_t* path, std::uint32_t fps, std::uint32_t bitrate) noexcept {
    try {
        if (!path || !*path || std::wcslen(path) > 1023 || (fps != 30 && fps != 60 && fps != 120) ||
            bitrate < 1000000 || bitrate > 100000000)
            return false;
        // Refuse relative paths and URLs. The picker passes an absolute local
        // or UNC destination, and the Media Foundation worker reserves it.
        const auto length = std::wcslen(path);
        const bool drive = length > 3 && path[1] == L':' && (path[2] == L'\\' || path[2] == L'/');
        const bool unc = length > 3 && path[0] == L'\\' && path[1] == L'\\';
        if ((!drive && !unc) || length < 4 || _wcsicmp(path + length - 4, L".mp4") != 0)
            return false;
        std::lock_guard<std::mutex> lock(control_mutex);
        if (current && !current->done.load(std::memory_order_acquire))
            return false;
        if (!encoder_thread)
            encoder_thread = new std::thread;
        if (encoder_thread->joinable())
            encoder_thread->join();
        deferred_stop.store(0);
        deferred_stop_qpc.store(0);
        auto next = std::make_shared<Session>();
        next->path = path;
        next->fps = fps;
        next->bitrate = bitrate;
        LARGE_INTEGER frequency{};
        if (!QueryPerformanceFrequency(&frequency) || frequency.QuadPart <= 0)
            return false;
        next->frequency = std::uint64_t(frequency.QuadPart);
        *encoder_thread = std::thread(encode, next);
        current = std::move(next);
        return true;
    } catch (...) {
        return false;
    }
}
void stop(bool cancel) noexcept {
    std::unique_lock<std::mutex> lock(control_mutex, std::try_to_lock);
    if (lock.owns_lock()) {
        if (current && !current->done.load(std::memory_order_acquire))
            request_stop(*current, cancel);
        return;
    }
    std::uint64_t unset = 0;
    deferred_stop_qpc.compare_exchange_strong(unset, now_qpc());
    unsigned request = deferred_stop.load(std::memory_order_relaxed);
    const unsigned wanted = cancel ? 2 : 1;
    while (request < wanted && !deferred_stop.compare_exchange_weak(request, wanted)) {
    }
}
Status status() noexcept {
    std::unique_lock<std::mutex> lock(control_mutex, std::try_to_lock);
    Status result;
    if (!lock.owns_lock()) {
        result.state = cached.state.load(std::memory_order_acquire);
        result.width = cached.width.load();
        result.height = cached.height.load();
        result.fps = cached.fps.load();
        result.error_code = cached.error_code.load();
        result.frames_written = cached.written.load();
        result.frames_dropped = cached.dropped.load();
        result.duration_100ns = cached.duration.load();
        std::wcsncpy(result.error, cached.error.load(), 255);
        return result;
    }
    if (!current)
        return result;
    const auto& s = *current;
    result.state = s.state.load(std::memory_order_acquire);
    if (s.configured.load(std::memory_order_acquire)) {
        result.width = s.width;
        result.height = s.height;
    }
    result.fps = s.fps;
    result.error_code = std::uint32_t(s.error.load());
    result.frames_written = s.written.load();
    result.frames_dropped = s.dropped.load();
    result.duration_100ns = s.duration.load();
    const auto* message = s.error_text.load(std::memory_order_acquire);
    std::wcsncpy(result.error, message, 255);
    cached.width.store(result.width);
    cached.height.store(result.height);
    cached.fps.store(result.fps);
    cached.error_code.store(result.error_code);
    cached.written.store(result.frames_written);
    cached.dropped.store(result.frames_dropped);
    cached.duration.store(result.duration_100ns);
    cached.error.store(message);
    cached.state.store(result.state, std::memory_order_release);
    return result;
}
void capture(IDXGISwapChain* swapchain, ID3D11Device* device,
             ID3D11DeviceContext* context) noexcept {
    if (!swapchain || !device || !context)
        return;
    std::shared_ptr<Session> session;
    {
        std::unique_lock<std::mutex> lock(control_mutex, std::try_to_lock);
        if (!lock.owns_lock())
            return;
        session = current;
    }
    if (!session || session->done.load(std::memory_order_acquire)) {
        gpu.clear();
        return;
    }
    auto& s = *session;
    apply_deferred_stop(s);
    if (gpu.owner != session) {
        if (s.stopping.load(std::memory_order_acquire))
            return;
        gpu.clear();
        gpu.owner = session;
    }
    if (FAILED(s.error.load()) || s.cancel.load())
        return;
    Com<ID3D11Texture2D> backbuffer;
    HRESULT hr = swapchain->GetBuffer(0, __uuidof(ID3D11Texture2D),
                                      reinterpret_cast<void**>(backbuffer.put()));
    if (FAILED(hr)) {
        fail(s, hr, L"The game's DirectX 11 backbuffer became unavailable.");
        return;
    }
    D3D11_TEXTURE2D_DESC desc{};
    backbuffer->GetDesc(&desc);
    if (!s.configured.load(std::memory_order_acquire)) {
        if (s.stopping.load())
            return;
        initialize_resources(s, swapchain, device, backbuffer.p, desc);
        return;
    }
    if (gpu.swapchain != swapchain || gpu.device != device || desc.Width != gpu.description.Width ||
        desc.Height != gpu.description.Height || desc.Format != gpu.description.Format ||
        desc.SampleDesc.Count != gpu.description.SampleDesc.Count) {
        fail(s, E_FAIL,
             L"The game resolution, swapchain or color format changed during recording.");
        return;
    }
    struct ProducerScope {
        Session& session;
        explicit ProducerScope(Session& value) : session(value) {
            session.producer_active.fetch_add(1, std::memory_order_acq_rel);
        }
        ~ProducerScope() { session.producer_active.fetch_sub(1, std::memory_order_release); }
    } producer(s);
    if (!s.ready.load(std::memory_order_acquire))
        return;

    // Map only already-submitted copies, never the copy issued this Present.
    // DO_NOT_WAIT makes a busy GPU a skipped opportunity, not a render stall.
    for (unsigned copies = 0; copies < 1 && gpu.drained != gpu.submitted; ++copies) {
        const auto write = s.produced.load(std::memory_order_relaxed);
        if (write - s.consumed.load(std::memory_order_acquire) >= kSlots)
            break;
        auto& slot = gpu.slots[gpu.drained % kSlots];
        D3D11_MAPPED_SUBRESOURCE mapped{};
        hr = context->Map(slot.staging.p, 0, D3D11_MAP_READ, D3D11_MAP_FLAG_DO_NOT_WAIT, &mapped);
        if (hr == DXGI_ERROR_WAS_STILL_DRAWING)
            break;
        if (FAILED(hr)) {
            fail(s, hr, L"DirectX 11 video readback failed; recording stopped.");
            return;
        }
        auto& frame = s.cpu[write % kSlots];
        const auto row_bytes = std::size_t(s.width) * 4;
        if (mapped.RowPitch < row_bytes || !mapped.pData) {
            context->Unmap(slot.staging.p, 0);
            fail(s, E_FAIL, L"DirectX 11 returned an invalid video row stride.");
            return;
        }
        for (std::uint32_t row = 0; row < s.height; ++row)
            std::memcpy(frame.pixels.data() + row * row_bytes,
                        static_cast<const std::uint8_t*>(mapped.pData) +
                            std::size_t(row) * mapped.RowPitch,
                        row_bytes);
        context->Unmap(slot.staging.p, 0);
        frame.pts = slot.pts;
        s.produced.store(write + 1, std::memory_order_release);
        ++gpu.drained;
        s.gpu_pending.store(gpu.submitted - gpu.drained, std::memory_order_release);
        s.wake.notify_one();
    }
    if (s.stopping.load(std::memory_order_acquire))
        return;
    const auto now = now_qpc();
    std::uint64_t pts = 0, missed = 0;
    if (!gpu.cadence.sample(now, pts, missed))
        return;
    s.first_qpc.store(gpu.cadence.start, std::memory_order_release);
    s.dropped.fetch_add(missed, std::memory_order_relaxed);
    if (gpu.submitted - gpu.drained >= kSlots) {
        s.dropped.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    auto& slot = gpu.slots[gpu.submitted % kSlots];
    if (gpu.resolved.p) {
        context->ResolveSubresource(gpu.resolved.p, 0, backbuffer.p, 0, desc.Format);
        context->CopyResource(slot.staging.p, gpu.resolved.p);
    } else {
        context->CopyResource(slot.staging.p, backbuffer.p);
    }
    slot.pts = pts;
    ++gpu.submitted;
    s.gpu_pending.store(gpu.submitted - gpu.drained, std::memory_order_release);
}
void reset_resources() noexcept {
    if (gpu.owner && !gpu.owner->done.load(std::memory_order_acquire) &&
        active(gpu.owner->state.load())) {
        // Resize is a normal user operation: keep any completed frames as a
        // playable MP4 instead of deleting an otherwise valid recording.
        request_stop(*gpu.owner, false);
        gpu.owner->dropped.fetch_add(gpu.owner->gpu_pending.exchange(0));
    }
    gpu.clear();
}
void shutdown() noexcept {
    std::thread worker;
    {
        std::lock_guard<std::mutex> lock(control_mutex);
        if (current && !current->done.load())
            request_stop(*current, false);
        if (encoder_thread)
            worker = std::move(*encoder_thread);
    }
    if (worker.joinable())
        worker.join();
}
}
