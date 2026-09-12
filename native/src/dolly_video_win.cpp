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
#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstring>
#include <cwchar>
#include <memory>
#include <mutex>
#include <string>
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
    Encoder encoder = Encoder::media_foundation;
    Codec codec = Codec::auto_select;
    std::uint32_t quality = 0, preset = 0;
    bool fixed_step = false;
    std::wstring ffmpeg;
    // Published once by the render callback before configured=true.
    std::uint32_t width = 0, height = 0;
    bool rgba = false;
    std::atomic<bool> configured{false}, ready{false}, stopping{false}, cancel{false}, done{false};
    std::atomic<unsigned> producer_active{0};
    std::atomic<State> state{State::starting};
    std::atomic<HRESULT> error{S_OK};
    std::atomic<const wchar_t*> error_text{L""};
    // Backing store for a dynamically composed error (FFmpeg stderr). Filled
    // once before error_text is published; never mutated afterwards.
    wchar_t error_buffer[256]{};
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
// ---- FFmpeg external-encoder backend -------------------------------------
// The capture path above is unchanged: this backend only replaces the sink
// that consumes owned CPU frames, so a slow encoder can never stall rendering.
void fail_text(Session& s, HRESULT hr, const std::wstring& message) noexcept {
    HRESULT empty = S_OK;
    if (s.error.compare_exchange_strong(empty, FAILED(hr) ? hr : E_FAIL)) {
        const auto count = std::min<std::size_t>(message.size(), 255);
        std::wmemcpy(s.error_buffer, message.c_str(), count);
        s.error_buffer[count] = 0;
        s.error_text.store(s.error_buffer, std::memory_order_release);
    }
    request_stop(s, false);
}
const wchar_t* codec_token(Codec codec) noexcept {
    switch (codec) {
    case Codec::h264_nvenc:
        return L"h264_nvenc";
    case Codec::hevc_nvenc:
        return L"hevc_nvenc";
    case Codec::h264_mf:
        return L"h264_mf";
    case Codec::libx264:
        return L"libx264";
    case Codec::libx265:
        return L"libx265";
    case Codec::h264_qsv:
        return L"h264_qsv";
    case Codec::hevc_qsv:
        return L"hevc_qsv";
    case Codec::h264_amf:
        return L"h264_amf";
    case Codec::hevc_amf:
        return L"hevc_amf";
    case Codec::lossless:
        return L"ffv1";
    default:
        return L"h264_nvenc";
    }
}
std::uint32_t default_quality(Codec codec) noexcept {
    switch (codec) {
    case Codec::h264_mf:
    case Codec::lossless:
        return 0; // bitrate / lossless: no constant-quality value
    default:
        return 20;
    }
}
std::wstring preset_token(Codec codec, std::uint32_t index) {
    if (!index)
        index = 5;
    switch (codec) {
    case Codec::h264_nvenc:
    case Codec::hevc_nvenc:
        return L"p" + std::to_wstring(std::min<std::uint32_t>(index, 7));
    case Codec::libx264:
    case Codec::libx265: {
        static const wchar_t* names[] = {L"ultrafast", L"superfast", L"veryfast", L"faster",
                                         L"fast",      L"medium",    L"slow"};
        return names[std::min<std::uint32_t>(index, 7) - 1];
    }
    default:
        return L"";
    }
}
std::wstring quote_arg(const std::wstring& value) {
    std::wstring out;
    out.reserve(value.size() + 2);
    out.push_back(L'"');
    for (wchar_t c : value) {
        if (c == L'"')
            out.push_back(L'\\');
        out.push_back(c);
    }
    out.push_back(L'"');
    return out;
}
std::wstring build_ffmpeg_command(const Session& s) {
    const bool mp4 =
        s.path.size() >= 4 && _wcsicmp(s.path.c_str() + s.path.size() - 4, L".mp4") == 0;
    const Codec codec = s.codec == Codec::auto_select ? Codec::h264_nvenc : s.codec;
    std::wstring cmd = quote_arg(s.ffmpeg);
    cmd += L" -hide_banner -loglevel error -nostdin -n";
    cmd += L" -f rawvideo -pixel_format ";
    cmd += s.rgba ? L"rgba" : L"bgra";
    cmd += L" -video_size " + std::to_wstring(s.width) + L"x" + std::to_wstring(s.height);
    cmd += L" -framerate " + std::to_wstring(s.fps);
    cmd += L" -i pipe:0 -an -c:v ";
    cmd += codec_token(codec);
    const auto preset = preset_token(codec, s.preset);
    if (!preset.empty())
        cmd += L" -preset " + preset;
    const auto quality = s.quality ? s.quality : default_quality(codec);
    switch (codec) {
    case Codec::h264_nvenc:
    case Codec::hevc_nvenc:
        cmd += L" -rc vbr -cq " + std::to_wstring(quality) + L" -b:v 0";
        break;
    case Codec::libx264:
    case Codec::libx265:
        cmd += L" -crf " + std::to_wstring(quality) + L" -b:v 0";
        break;
    case Codec::h264_qsv:
    case Codec::hevc_qsv:
        cmd += L" -global_quality " + std::to_wstring(quality);
        break;
    case Codec::h264_amf:
    case Codec::hevc_amf:
        cmd +=
            L" -rc cqp -qp_i " + std::to_wstring(quality) + L" -qp_p " + std::to_wstring(quality);
        break;
    case Codec::lossless:
        cmd += L" -pix_fmt bgra";
        break;
    default:
        cmd += L" -b:v " + std::to_wstring(s.bitrate);
        break;
    }
    if (codec != Codec::lossless)
        cmd += L" -pix_fmt yuv420p";
    if (mp4)
        cmd += L" -movflags +faststart";
    cmd += L" ";
    cmd += quote_arg(s.path);
    return cmd;
}
HRESULT write_all(HANDLE pipe, const void* data, std::size_t bytes) noexcept {
    const auto* cursor = static_cast<const unsigned char*>(data);
    while (bytes) {
        DWORD written = 0;
        const DWORD chunk = static_cast<DWORD>(std::min<std::size_t>(bytes, 1u << 24));
        if (!WriteFile(pipe, cursor, chunk, &written, nullptr) || !written)
            return HRESULT_FROM_WIN32(GetLastError() ? GetLastError() : ERROR_BROKEN_PIPE);
        cursor += written;
        bytes -= written;
    }
    return S_OK;
}
std::wstring read_log_tail(const std::wstring& path) {
    HANDLE file = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                              nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE)
        return L"";
    LARGE_INTEGER size{};
    std::string data;
    if (GetFileSizeEx(file, &size) && size.QuadPart > 0 && size.QuadPart < (1 << 20)) {
        data.resize(static_cast<std::size_t>(size.QuadPart));
        DWORD read = 0;
        ReadFile(file, data.data(), static_cast<DWORD>(data.size()), &read, nullptr);
        data.resize(read);
    }
    CloseHandle(file);
    if (data.size() > 600)
        data = data.substr(data.size() - 600);
    if (data.empty())
        return L"";
    const int wide =
        MultiByteToWideChar(CP_UTF8, 0, data.data(), static_cast<int>(data.size()), nullptr, 0);
    std::wstring out(static_cast<std::size_t>(std::max(0, wide)), L'\0');
    if (wide > 0)
        MultiByteToWideChar(CP_UTF8, 0, data.data(), static_cast<int>(data.size()), out.data(),
                            wide);
    for (auto& c : out)
        if (c == L'\n' || c == L'\r')
            c = L' ';
    return out;
}
struct Child {
    HANDLE process = nullptr;
    HANDLE input = nullptr;
    std::wstring log;
    void close_input() noexcept {
        if (input) {
            CloseHandle(input);
            input = nullptr;
        }
    }
    void close_process() noexcept {
        if (process) {
            CloseHandle(process);
            process = nullptr;
        }
    }
};
bool spawn_ffmpeg(Session& s, Child& child, std::wstring& error) {
    const bool existed = GetFileAttributesW(s.path.c_str()) != INVALID_FILE_ATTRIBUTES;
    if (existed) {
        error = L"The output file already exists. Choose a new filename.";
        return false;
    }
    SECURITY_ATTRIBUTES sa{};
    sa.nLength = sizeof(sa);
    sa.bInheritHandle = TRUE;
    HANDLE read_end = nullptr, write_end = nullptr;
    if (!CreatePipe(&read_end, &write_end, &sa, 0)) {
        error = L"Could not create the FFmpeg input pipe.";
        return false;
    }
    SetHandleInformation(write_end, HANDLE_FLAG_INHERIT, 0);
    HANDLE nul = CreateFileW(L"NUL", GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, &sa,
                             OPEN_EXISTING, 0, nullptr);
    wchar_t temp[MAX_PATH]{};
    GetTempPathW(MAX_PATH, temp);
    child.log = std::wstring(temp) + L"DollyFFmpeg-" + std::to_wstring(GetCurrentProcessId()) +
                L"-" + std::to_wstring(GetTickCount64()) + L".log";
    HANDLE log = CreateFileW(child.log.c_str(), GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                             &sa, CREATE_ALWAYS, FILE_ATTRIBUTE_TEMPORARY, nullptr);
    if (!nul || nul == INVALID_HANDLE_VALUE || !log || log == INVALID_HANDLE_VALUE) {
        if (nul && nul != INVALID_HANDLE_VALUE)
            CloseHandle(nul);
        if (log && log != INVALID_HANDLE_VALUE)
            CloseHandle(log);
        CloseHandle(read_end);
        CloseHandle(write_end);
        error = L"Could not prepare the FFmpeg output handles.";
        return false;
    }
    STARTUPINFOW si{};
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdInput = read_end;
    si.hStdOutput = nul;
    si.hStdError = log;
    PROCESS_INFORMATION pi{};
    auto command = build_ffmpeg_command(s);
    std::vector<wchar_t> mutable_command(command.begin(), command.end());
    mutable_command.push_back(0);
    const BOOL started = CreateProcessW(s.ffmpeg.c_str(), mutable_command.data(), nullptr, nullptr,
                                        TRUE, CREATE_NO_WINDOW, nullptr, nullptr, &si, &pi);
    CloseHandle(read_end);
    CloseHandle(nul);
    CloseHandle(log);
    if (!started) {
        CloseHandle(write_end);
        error = L"FFmpeg could not start. Check the selected ffmpeg.exe path and codec support.";
        return false;
    }
    CloseHandle(pi.hThread);
    child.process = pi.hProcess;
    child.input = write_end;
    return true;
}
void encode_ffmpeg(std::shared_ptr<Session> s) noexcept {
    bool created = false, finalized = false, wrote_any = false;
    std::uint64_t frames = 0;
    Child child;
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
            std::wstring error;
            if (s->ffmpeg.empty() || !spawn_ffmpeg(*s, child, error)) {
                fail_text(*s, HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND),
                          error.empty() ? std::wstring(L"FFmpeg is unavailable.") : error);
            } else {
                created = true;
                for (auto& frame : s->cpu)
                    frame.pixels.resize(std::size_t(s->width) * s->height * 4);
                s->state.store(State::recording, std::memory_order_release);
                s->ready.store(true, std::memory_order_release);
            }
        }
        std::uint64_t stop_deadline = 0;
        while (child.process && SUCCEEDED(s->error.load()) && !s->cancel.load()) {
            apply_deferred_stop(*s);
            const bool stopping = s->stopping.load(std::memory_order_acquire);
            if (stopping && !stop_deadline) {
                s->state.store(State::finalizing, std::memory_order_release);
                stop_deadline = GetTickCount64() + 250;
            }
            const auto read = s->consumed.load(std::memory_order_relaxed);
            if (read != s->produced.load(std::memory_order_acquire)) {
                const auto& frame = s->cpu[read % kSlots];
                const HRESULT hr = write_all(child.input, frame.pixels.data(),
                                             std::size_t(s->width) * s->height * 4);
                s->consumed.store(read + 1, std::memory_order_release);
                if (FAILED(hr)) {
                    fail(*s, hr,
                         L"FFmpeg stopped accepting frames. Check the encoder and output path.");
                    break;
                }
                wrote_any = true;
                ++frames;
                s->written.fetch_add(1, std::memory_order_relaxed);
                s->duration.store(frames * 10000000ull / std::max<std::uint32_t>(1, s->fps),
                                  std::memory_order_release);
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
        s->dropped.fetch_add(s->gpu_pending.exchange(0), std::memory_order_relaxed);
        while (child.process && !s->cancel.load() && SUCCEEDED(s->error.load()) &&
               s->consumed.load() != s->produced.load(std::memory_order_acquire)) {
            const auto read = s->consumed.load(std::memory_order_relaxed);
            const auto& frame = s->cpu[read % kSlots];
            const HRESULT hr =
                write_all(child.input, frame.pixels.data(), std::size_t(s->width) * s->height * 4);
            s->consumed.store(read + 1, std::memory_order_release);
            if (FAILED(hr)) {
                fail(*s, hr, L"FFmpeg stopped accepting frames while finishing.");
                break;
            }
            wrote_any = true;
            ++frames;
            s->written.fetch_add(1, std::memory_order_relaxed);
        }
        child.close_input();
        if (child.process && !s->cancel.load() && SUCCEEDED(s->error.load())) {
            if (!wrote_any) {
                fail(*s, E_FAIL, L"Recording ended before any game frame was captured.");
            } else {
                const DWORD wait = WaitForSingleObject(child.process, 120000);
                DWORD code = 1;
                GetExitCodeProcess(child.process, &code);
                if (wait != WAIT_OBJECT_0 || code != 0) {
                    const auto tail = read_log_tail(child.log);
                    fail_text(
                        *s, E_FAIL,
                        tail.empty()
                            ? std::wstring(
                                  L"FFmpeg could not encode the recording. This build may lack the selected encoder.")
                            : tail);
                } else {
                    finalized = true;
                }
            }
        }
    } catch (...) {
        fail(*s, E_OUTOFMEMORY,
             L"Video recording ran out of memory or the encoder could not continue.");
    }
    s->ready.store(false, std::memory_order_release);
    while (s->producer_active.load(std::memory_order_acquire))
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    if (child.process && (s->cancel.load() || FAILED(s->error.load()))) {
        TerminateProcess(child.process, 1);
        WaitForSingleObject(child.process, 5000);
    }
    child.close_input();
    child.close_process();
    if (s->cancel.load() || FAILED(s->error.load()))
        s->dropped.fetch_add(s->produced.load() - s->consumed.load(), std::memory_order_relaxed);
    if (!child.log.empty())
        DeleteFileW(child.log.c_str());
    if (created && (!finalized || s->cancel.load()))
        DeleteFileW(s->path.c_str());
    s->state.store(FAILED(s->error.load())          ? State::failed
                   : (s->cancel.load() || !created) ? State::cancelled
                                                    : State::completed,
                   std::memory_order_release);
    s->done.store(true, std::memory_order_release);
}

void encode(std::shared_ptr<Session> s) noexcept {
    if (s->encoder == Encoder::ffmpeg) {
        encode_ffmpeg(s);
        return;
    }
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

namespace {
bool absolute_path(const wchar_t* path, std::size_t length) noexcept {
    const bool drive = length > 3 && path[1] == L':' && (path[2] == L'\\' || path[2] == L'/');
    const bool unc = length > 3 && path[0] == L'\\' && path[1] == L'\\';
    return drive || unc;
}
}
bool start(const Options& options) noexcept {
    try {
        const wchar_t* path = options.path;
        if (!path || !*path || std::wcslen(path) > 1023 ||
            (options.fps != 30 && options.fps != 60 && options.fps != 120 && options.fps != 300 &&
             options.fps != 600) ||
            options.bitrate < 1000000 || options.bitrate > 100000000)
            return false;
        // Refuse relative paths and URLs. The picker passes an absolute local
        // or UNC destination. FFmpeg lossless output uses Matroska (.mkv).
        const auto length = std::wcslen(path);
        const bool mp4 = length >= 4 && _wcsicmp(path + length - 4, L".mp4") == 0;
        const bool mkv = length >= 4 && _wcsicmp(path + length - 4, L".mkv") == 0;
        if (!absolute_path(path, length) || length < 4 || (!mp4 && !mkv))
            return false;
        if (options.encoder == Encoder::ffmpeg) {
            const wchar_t* ffmpeg = options.ffmpeg;
            if (!ffmpeg || !*ffmpeg)
                return false;
            const auto ffmpeg_length = std::wcslen(ffmpeg);
            if (ffmpeg_length > 32767 || !absolute_path(ffmpeg, ffmpeg_length) || ffmpeg_length < 4)
                return false;
            if (options.codec == Codec::lossless && !mkv)
                return false;
        }
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
        next->fps = options.fps;
        next->bitrate = options.bitrate;
        next->encoder = options.encoder;
        next->codec = options.codec;
        next->quality = options.quality;
        next->preset = options.preset;
        next->fixed_step = options.fixed_step;
        if (options.ffmpeg)
            next->ffmpeg = options.ffmpeg;
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
bool start(const wchar_t* path, std::uint32_t fps, std::uint32_t bitrate) noexcept {
    Options options;
    options.path = path;
    options.fps = fps;
    options.bitrate = bitrate;
    options.encoder = Encoder::media_foundation;
    options.codec = Codec::auto_select;
    return start(options);
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
    if (s.fixed_step) {
        // One captured frame per Present. The frame index, not wall-clock
        // time, defines the timestamp, so the export is deterministic.
        pts = std::uint64_t(gpu.submitted) * 10000000ull / std::max<std::uint32_t>(1, s.fps);
        std::uint64_t zero = 0;
        s.first_qpc.compare_exchange_strong(zero, now, std::memory_order_release);
    } else {
        if (!gpu.cadence.sample(now, pts, missed))
            return;
        s.first_qpc.store(gpu.cadence.start, std::memory_order_release);
    }
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
