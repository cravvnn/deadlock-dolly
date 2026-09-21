// Deadlock Dolly native main-view bridge. Exact uploaded module fingerprints
// gate every hook. Runs only in the -dev -insecure process started by Dolly.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <shellapi.h>
#include <bcrypt.h>
#include <intrin.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <memory>
#include <string>
#include <thread>
#include <vector>
#include "MinHook.h"
#include "dolly_path.hpp"
#include "dolly_effects.hpp"
#include "dolly_protocol.hpp"
#include "dolly_replay_clock.hpp"
#include "dolly_editor.hpp"
#include "dolly_overlay.hpp"
#include "dolly_player_capture.hpp"
#include "dolly_renderer_diagnostics.hpp"
#include "dolly_visualization_runtime.hpp"
#include "dolly_media.hpp"
#include "dolly_video.hpp"
// Pulled in again (as a no-op) by dolly_compat_runtime.hpp from inside the
// anonymous namespace below. Declaring it here first keeps `#pragma once` from
// introducing a `dolly` namespace in that anonymous namespace, which would
// shadow the real global `dolly` and make dolly::... ambiguous (MSVC C2872).
#include "dolly_pattern_scan.hpp"

namespace {
using namespace dolly;
// Reviewed client camera profiles come from native/profiles via
// tools/generate_profile.py (dolly_compat_generated.hpp). Exact SHA-256 matches
// are the fast path. Each profile also carries an AOB signature (fixed bytes +
// wildcard mask) that is a fail-closed fallback: it only matches a build whose
// code is byte-identical modulo relocated addresses, and every derived symbol
// is validated before the hook is installed.
constexpr char kEngineHash[] = "887201acec33837fdb18d73c04f8e0894971d26eebafe992a28a12fada118afb";
constexpr char kUpdatedEngineHash[] =
    "301d042c7443090241d7b83244747bf8a32916f61df60aea5d8a1799f432ef8d";
constexpr char kUnlockerHash[] = "74047120e79245d479e61142a878f3311c8384a1f5f33e3f1cb8f3e87749e42a";
// Reviewed scenesystem.dll for the player layer capture. Any other build keeps
// the capture disabled instead of patching unverified producer code; re-review
// this hash in the same turn as a game update.
constexpr char kPlayerCaptureScenesystemHash[] =
    "e480a7f28ae073dd4a83833bfee8db44bfff22f097147f2f697a109b2ea2de4b";
constexpr std::uintptr_t kDemoGlobal = 0x61b618, kDemoTable = 0x535730, kEngineTable = 0x540128;
// Identical across every reviewed client build; the exact-hash path re-checks it.
constexpr unsigned char kSetupPrologue[] = {
    0x48, 0x8b, 0xc4, 0x48, 0x89, 0x58, 0x10, 0x55, 0x56, 0x57, 0x41, 0x54, 0x41, 0x55, 0x41, 0x56,
    0x41, 0x57, 0x48, 0x81, 0xec, 0xc0, 0x08, 0x00, 0x00, 0x0f, 0x29, 0x70, 0xb8, 0x4c, 0x8b, 0xf9};
HMODULE gModule = nullptr;
std::uintptr_t gClient = 0, gEngine = 0;
HANDLE gMapping = nullptr, gEditor = nullptr;
unsigned char* gMemory = nullptr;
std::atomic<bool> gHookInstalled{false};
std::atomic<bool> gDemoSeeking{false};
std::atomic<bool> gReliefAllowed{true};
std::atomic<double> gHeartbeatTime{0};
std::atomic<unsigned> gWorkerError{0};
std::atomic_flag gStatusLock = ATOMIC_FLAG_INIT;
std::atomic<std::uint64_t> gHookCalls{0};
LARGE_INTEGER gFrequency{};
using SetupFn = void(__fastcall*)(void*, std::uintptr_t);
SetupFn gOriginalSetup = nullptr;
using Factory = void*(__cdecl*)(const char*, int*);
Factory gOriginalFactory = nullptr;
INIT_ONCE gLoaderOnce = INIT_ONCE_STATIC_INIT;
INIT_ONCE gWorkerOnce = INIT_ONCE_STATIC_INIT;
namespace attach_runtime {
struct Cache;
}
struct Command {
    ControlHeader wire{};
    std::shared_ptr<const NativePath> path;
    std::shared_ptr<const NativeShot> shot;
    std::vector<std::shared_ptr<const attach_runtime::Cache>> attach;
    bool manual = false, has_manual_pose = false;
    CameraPose manual_pose{};
};
std::shared_ptr<const Command> gCommand;

static double now_seconds() noexcept {
    LARGE_INTEGER n{};
    QueryPerformanceCounter(&n);
    return gFrequency.QuadPart ? double(n.QuadPart) / double(gFrequency.QuadPart) : 0;
}
// Keep editor liveness independent of bounded-but-expensive model discovery.
// This thread only observes our mapping/process; it never touches game memory.
class HeartbeatMonitor {
    std::atomic<bool> stopped_{false};
    std::thread thread_;

public:
    HeartbeatMonitor(unsigned char* memory, HANDLE editor)
        : thread_([this, memory, editor] {
              std::uint64_t previous = 0;
              while (!stopped_.load()) {
                  if (WaitForSingleObject(editor, 0) != WAIT_TIMEOUT) {
                      gWorkerError = 30;
                      break;
                  }
                  const auto beat = static_cast<std::uint64_t>(InterlockedCompareExchange64(
                      reinterpret_cast<volatile LONG64*>(memory + 32), 0, 0));
                  if (beat != previous) {
                      previous = beat;
                      gHeartbeatTime = now_seconds();
                  }
                  Sleep(25);
              }
          }) {}
    ~HeartbeatMonitor() {
        stopped_ = true;
        thread_.join();
    }
    HeartbeatMonitor(const HeartbeatMonitor&) = delete;
    HeartbeatMonitor& operator=(const HeartbeatMonitor&) = delete;
};
static std::wstring module_path(HMODULE module) {
    std::vector<wchar_t> p(32768);
    DWORD n = GetModuleFileNameW(module, p.data(), DWORD(p.size()));
    return n && n < p.size() ? std::wstring(p.data(), n) : std::wstring();
}
static std::wstring beside_module(const wchar_t* name) {
    auto path = module_path(gModule);
    auto i = path.find_last_of(L"\\/");
    return i == std::wstring::npos ? std::wstring() : path.substr(0, i + 1) + name;
}
static bool development_flags() {
    int count = 0;
    auto args = CommandLineToArgvW(GetCommandLineW(), &count);
    if (!args)
        return false;
    bool dev = false, insecure = false;
    for (int i = 1; i < count; ++i) {
        dev |= _wcsicmp(args[i], L"-dev") == 0;
        insecure |= _wcsicmp(args[i], L"-insecure") == 0;
    }
    LocalFree(args);
    return dev && insecure;
}
static bool read_memory(std::uintptr_t from, void* to, std::size_t bytes) noexcept {
    SIZE_T read = 0;
    return from &&
           ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(from), to, bytes,
                             &read) &&
           read == bytes;
}
template <typename T> static bool read_value(std::uintptr_t address, T& value) noexcept {
    return read_memory(address, &value, sizeof(value));
}
static std::string hash_file(const std::wstring& path) {
    HANDLE file = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE,
                              nullptr, OPEN_EXISTING, FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
    if (file == INVALID_HANDLE_VALUE)
        return {};
    BCRYPT_ALG_HANDLE alg = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    unsigned char digest[32]{};
    std::string result;
    if (BCryptOpenAlgorithmProvider(&alg, BCRYPT_SHA256_ALGORITHM, nullptr, 0) >= 0 &&
        BCryptCreateHash(alg, &hash, nullptr, 0, nullptr, 0, 0) >= 0) {
        std::vector<unsigned char> block(1024 * 1024);
        DWORD n = 0;
        bool okay = true;
        for (;;) {
            if (!ReadFile(file, block.data(), DWORD(block.size()), &n, nullptr)) {
                okay = false;
                break;
            }
            if (!n)
                break;
            if (BCryptHashData(hash, block.data(), n, 0) < 0) {
                okay = false;
                break;
            }
        }
        if (okay && BCryptFinishHash(hash, digest, sizeof(digest), 0) >= 0) {
            static const char digits[] = "0123456789abcdef";
            for (auto byte : digest) {
                result += digits[byte >> 4];
                result += digits[byte & 15];
            }
        }
    }
    if (hash)
        BCryptDestroyHash(hash);
    if (alg)
        BCryptCloseAlgorithmProvider(alg, 0);
    CloseHandle(file);
    return result;
}
static void write_status(Status status, bool may_wait = false) noexcept {
    if (!gMemory)
        return;
    while (gStatusLock.test_and_set(std::memory_order_acquire)) {
        if (!may_wait)
            return;
        Sleep(1);
    }
    auto out = gMemory + kControlBytes;
    auto sequence = reinterpret_cast<volatile LONG*>(out + 8);
    LONG old = InterlockedCompareExchange(sequence, 0, 0);
    LONG even = (old & 1) ? old + 1 : old;
    InterlockedExchange(sequence, even + 1);
    std::memcpy(status.magic, kStatusMagic, 8);
    status.abi = kBridgeAbi;
    status.game_pid = GetCurrentProcessId();
    status.real_time = now_seconds();
    std::memcpy(out, &status, 8);
    std::memcpy(out + 12, reinterpret_cast<unsigned char*>(&status) + 12, sizeof(Status) - 12);
    MemoryBarrier();
    InterlockedExchange(sequence, even + 2);
    gStatusLock.clear(std::memory_order_release);
}
static void startup_status(State state, unsigned error, const char* message) noexcept {
    Status status{};
    status.state = std::uint32_t(state);
    status.error = error;
    std::snprintf(status.message, sizeof(status.message), "%s", message);
    write_status(status, true);
}
static bool read_control(ControlHeader& h, std::vector<unsigned char>& payload,
                         std::uint32_t accepted) {
    auto sequence = reinterpret_cast<volatile LONG*>(gMemory + 12);
    LONG before = InterlockedCompareExchange(sequence, 0, 0);
    if (before & 1)
        return false;
    std::memcpy(&h, gMemory, sizeof(h));
    if (h.command == accepted)
        return false;
    if (h.payload_bytes > kMaxPayloadBytes)
        return false;
    payload.resize(h.payload_bytes);
    if (h.payload_bytes)
        std::memcpy(payload.data(), gMemory + kPayloadOffset, h.payload_bytes);
    MemoryBarrier();
    LONG after = InterlockedCompareExchange(sequence, 0, 0);
    return before == after && !(after & 1) && std::memcmp(h.magic, kControlMagic, 8) == 0 &&
           h.abi == kBridgeAbi;
}
static bool read_config(std::wstring& mapping, DWORD& editor_pid) {
    auto p = beside_module(L"dolly_native.cfg");
    HANDLE file = CreateFileW(p.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                              FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE)
        return false;
    char data[1024]{};
    DWORD n = 0;
    bool okay = ReadFile(file, data, sizeof(data) - 1, &n, nullptr) != 0;
    CloseHandle(file);
    if (!okay || !n)
        return false;
    char token[65]{};
    unsigned long pid = 0;
    if (std::sscanf(data, "DOLLY_NATIVE_1\n%64s\n%lu", token, &pid) != 2 ||
        std::strlen(token) != 32 || !pid)
        return false;
    for (const char* s = token; *s; ++s)
        if (!((*s >= '0' && *s <= '9') || (*s >= 'a' && *s <= 'f')))
            return false;
    mapping = L"Local\\DeadlockDollyNative_";
    for (char c : std::string(token))
        mapping += wchar_t(c);
    editor_pid = DWORD(pid);
    return true;
}
static bool module_matches(HMODULE module, const char* expected, std::uint32_t image_size) {
    if (!module || hash_file(module_path(module)) != expected)
        return false;
    auto base = reinterpret_cast<std::uintptr_t>(module);
    IMAGE_DOS_HEADER dos{};
    IMAGE_NT_HEADERS64 nt{};
    return read_value(base, dos) && dos.e_magic == IMAGE_DOS_SIGNATURE && dos.e_lfanew > 0 &&
           dos.e_lfanew < 4096 && read_value(base + dos.e_lfanew, nt) &&
           nt.Signature == IMAGE_NT_SIGNATURE &&
           nt.FileHeader.Machine == IMAGE_FILE_MACHINE_AMD64 &&
           nt.OptionalHeader.SizeOfImage == image_size;
}
#include "dolly_compat_runtime.hpp"
#include "native_camera_view.hpp"
camera_view::Cache gCameraCache;
#include "native_effects_win.hpp"
#include "native_relief_win.hpp"
#include "dolly_attach_runtime.hpp"

// Attach preview state resolved by the worker from the published selection.
// The render callback only reads this immutable snapshot: it never walks
// entities, and authored offsets edited by attached fly stay local until the
// result block publishes them back to the editor.
struct AttachPreview {
    std::uint32_t sequence = 0;
    attach_runtime::Cache cache;
    attach_runtime::Offsets schema;
    std::array<double, 6> authored{};
    AttachPoint point = AttachPoint::eyes;
    double smoothing = 0.0;
    std::uint32_t handle = 0, entity_index = 0;
    std::uint64_t model = 0;
    std::uint64_t bone_hash = 0;
};
std::shared_ptr<const AttachPreview> gAttachPreview;

static void publish_attach_result(const AttachPreview& preview,
                                  const std::array<double, 6>& offsets) noexcept {
    if (!gMemory)
        return;
    EditorAttachResult result{};
    std::memcpy(result.magic, "DLYATR01", 8);
    result.abi = kEditorAttachResultAbi;
    result.flags = 1;
    result.reserved = preview.sequence;
    result.handle = preview.handle;
    result.entity_index = preview.entity_index;
    result.point = std::uint32_t(preview.point);
    result.model = preview.model;
    for (int index = 0; index < 6; ++index)
        result.offset[index] = offsets[index];
    result.smoothing = preview.smoothing;
    auto destination = gMemory + kEditorAttachResultOffset;
    auto sequence = reinterpret_cast<volatile LONG*>(destination + 8);
    const LONG current = InterlockedCompareExchange(sequence, 0, 0);
    const LONG odd = (current & 1) ? current + 2 : current + 1;
    InterlockedExchange(sequence, odd);
    MemoryBarrier();
    std::memcpy(destination, &result, 8);
    std::memcpy(destination + 12, reinterpret_cast<unsigned char*>(&result) + 12,
                sizeof(result) - 12);
    MemoryBarrier();
    InterlockedExchange(sequence, odd + 1);
}
CompatResolution gCompat;

struct DemoState {
    bool playing = false, paused = false, seeking = false;
    int tick = 0;
    double time = 0;
    double interval = 0;
    char name[512]{};
};
static bool read_demo(DemoState& result) noexcept {
    std::uintptr_t demo = 0, table = 0, engine_client = 0, engine_table = 0, globals = 0;
    if (!read_value(gEngine + kDemoGlobal, demo) || !read_value(demo, table) ||
        table != gEngine + kDemoTable)
        return false;
    auto playing = reinterpret_cast<bool(__fastcall*)(void*)>(gEngine + 0x2ec00);
    result.playing = playing(reinterpret_cast<void*>(demo));
    if (!result.playing)
        return true;
    if (!read_value(gClient + gCompat.engine_client, engine_client) ||
        !read_value(engine_client, engine_table) || engine_table != gEngine + kEngineTable)
        return false;
    auto active = reinterpret_cast<bool(__fastcall*)(void*)>(gEngine + 0x7bf30);
    if (!active(reinterpret_cast<void*>(engine_client))) {
        result.playing = false;
        return true;
    }
    result.paused = reinterpret_cast<bool(__fastcall*)(void*)>(gEngine + 0x368d0)(
        reinterpret_cast<void*>(demo));
    result.seeking = reinterpret_cast<bool(__fastcall*)(void*)>(gEngine + 0x2a240)(
        reinterpret_cast<void*>(demo));
    result.tick =
        reinterpret_cast<int(__fastcall*)(void*)>(gEngine + 0x36910)(reinterpret_cast<void*>(demo));
    auto name = reinterpret_cast<const char*(__fastcall*)(void*)>(gEngine + 0x7bf80)(
        reinterpret_cast<void*>(engine_client));
    if (!name)
        return false;
    // Read bounded chunks up to the current memory-region boundary; console
    // filenames are not assumed to have 512 readable bytes after the terminator.
    std::size_t used = 0;
    while (used < sizeof(result.name) - 1) {
        MEMORY_BASIC_INFORMATION region{};
        auto p = reinterpret_cast<std::uintptr_t>(name) + used;
        if (!VirtualQuery(reinterpret_cast<void*>(p), &region, sizeof(region)) ||
            region.State != MEM_COMMIT)
            return false;
        auto available =
            reinterpret_cast<std::uintptr_t>(region.BaseAddress) + region.RegionSize - p;
        auto amount = std::min<std::size_t>({available, sizeof(result.name) - 1 - used, 64});
        if (!amount || !read_memory(p, result.name + used, amount))
            return false;
        if (std::memchr(result.name + used, 0, amount))
            break;
        used += amount;
    }
    if (!std::memchr(result.name, 0, sizeof(result.name) - 1))
        return false;
    if (!read_value(gClient + gCompat.globals, globals))
        return false;
    float current = 0, interval = 0;
    if (!read_value(globals + 0x30, current) || !read_value(globals + 0x54, interval))
        return false;
    if (!std::isfinite(current) || current < 0 || !std::isfinite(interval) || interval < .001f ||
        interval > .2f)
        return false;
    result.time = current;
    if (gCompat.render_fraction) {
        float fraction = 0;
        if (!read_value(globals + gCompat.render_fraction, fraction) ||
            !replay_view_time(result.tick, fraction, interval, result.time))
            return false;
    }
    result.interval = interval;
    return true;
}
static bool same_demo(const char* expected, const char* actual) noexcept {
    auto basename = [](const char* s) {
        const char* b = s;
        for (const char* p = s; *p; ++p)
            if (*p == '/' || *p == '\\')
                b = p + 1;
        return b;
    };
    expected = basename(expected);
    actual = basename(actual);
    const auto expected_length = std::strlen(expected);
    const auto actual_length = std::strlen(actual);
    if (expected_length <= 4 || _stricmp(expected + expected_length - 4, ".dem") != 0)
        return false;
    // Preserve dots in custom recording names. Only the selected file's final
    // .dem extension may be omitted by an engine status response.
    return (actual_length == expected_length || actual_length == expected_length - 4) &&
           _strnicmp(expected, actual, actual_length) == 0;
}
static bool pose_valid(const CameraPose& p) noexcept {
    for (double v : p)
        if (!std::isfinite(v) || std::abs(v) > 1e8)
            return false;
    return p[6] >= .25 && p[6] <= 8;
}
// Called only after the game's complete SetUpView and before its matrices.
// No IPC reads, disk work, parser, sleeps or blocking request is allowed here.
static void on_view(void* self, std::uintptr_t caller) noexcept {
    static thread_local bool entered = false;
    if (entered)
        return;
    entered = true;
    struct Exit {
        bool& flag;
        ~Exit() { flag = false; }
    } exit{entered};
    ++gHookCalls;
    std::uintptr_t table = 0;
    if (caller != gClient + gCompat.caller ||
        !read_value(reinterpret_cast<std::uintptr_t>(self), table) ||
        table != gClient + gCompat.view_table)
        return;
    auto view = reinterpret_cast<std::uintptr_t>(self) + 0x10;
    static std::uint32_t seen = 0;
    static double phase = 0, play_base = 0, anchor = 0, real_anchor = 0, last_game = 0,
                  last_real = 0, largest_interval = 0;
    static int last_tick = 0;
    static std::uint64_t frames = 0;
    static bool started = false, completed = false;
    static CameraPose manual_pose{}, displayed_pose{};
    static bool displayed_valid = false;
    static unsigned fault = 0;
    static unsigned first_fault_code = 0;
    static char first_fault_message[192]{};
    static std::uint32_t previous_mode = 0;
    auto command = std::atomic_load_explicit(&gCommand, std::memory_order_acquire);
    Status status{};
    status.frame_count = ++frames;
    status.hook_calls = gHookCalls.load();
    double now = now_seconds();
    status.frame_interval_ms = last_real ? 1000 * (now - last_real) : 0;
    largest_interval = std::max(largest_interval, status.frame_interval_ms);
    status.max_frame_interval_ms = largest_interval;
    double real_delta = last_real ? std::max(0.0, now - last_real) : 0;
    last_real = now;
    float original_xyz[3]{}, original_angles[3]{}, original_fov = 0, original_aspect = 0;
    unsigned char flags = 0;
    int width = 0, height = 0;
    bool view_ok = read_memory(view + 0x4a0, original_xyz, sizeof(original_xyz)) &&
                   read_memory(view + 0x4b8, original_angles, sizeof(original_angles)) &&
                   read_value(view + 0x498, original_fov) &&
                   read_value(view + 0x4d8, original_aspect) && read_value(view + 0x555, flags) &&
                   read_value(view + 0x430, width) && read_value(view + 0x438, height);
    CameraPose original{};
    for (int i = 0; i < 3; ++i) {
        original[i] = original_xyz[i];
        original[i + 3] = original_angles[i];
    }
    original[6] = original_aspect;
    for (int i = 0; i < 7; ++i) {
        status.original_pose[i] = original[i];
        status.applied_pose[i] = original[i];
    }
    status.original_fov = status.applied_fov = original_fov;
    DemoState demo{};
    bool demo_ok = read_demo(demo);
    // Published for the worker's seek relief: the setup hook is the only place
    // that reads the game's demo state safely, once per main view.
    gDemoSeeking.store(demo.seeking, std::memory_order_relaxed);
    status.tick = demo.tick;
    status.paused = demo.paused;
    status.engine_time = demo.time;
    std::snprintf(status.demo_name, sizeof(status.demo_name), "%s", demo.name);
    status.ack_command = seen;
    status.phase = phase;
    auto finish = [&](State state, unsigned error, const char* message) {
        // Keep the first specific fault message: the per-frame fallback on the
        // next view would otherwise replace it before the editor reads it.
        if (state == State::Fault && error) {
            if (first_fault_code != error) {
                first_fault_code = error;
                std::snprintf(first_fault_message, sizeof(first_fault_message), "%s", message);
            } else if (first_fault_message[0]) {
                message = first_fault_message;
            }
        }
        // The attach branches set the hidden handle every frame; only a
        // terminal state may clear it, or per-frame status calls would wipe it
        // before the render thread's draws.
        // A completed path holds the final attached camera, so hiding stays
        // until the shot is stopped, faulted or released.
        if (state == State::Fault || state == State::Stopped || state == State::Unsupported)
            player_capture::set_hidden_handle(0, 100 + unsigned(state));
        if ((state == State::Fault || state == State::Stopped) && !gEffects.restore()) {
            state = State::Fault;
            error = 36;
            message = "Native effect restoration failed; retry Stop / restore.";
        }
        status.effect_count = gEffects.count;
        status.effect_error = gEffects.error;
        status.effect_frames = gEffects.frames;
        status.effect_phase = gEffects.phase;
        bool editor_ready = demo_ok && demo.playing && !demo.seeking && view_ok &&
                            pose_valid(original) && std::isfinite(original_fov) &&
                            original_fov > 1 && original_fov < 179 && width > 0 && height > 0 &&
                            !(flags & 2) && state != State::Fault && state != State::Unsupported;
        CameraPose shown{};
        for (int i = 0; i < 7; ++i)
            shown[i] = status.applied_pose[i];
        if (editor_ready) {
            displayed_pose = shown;
            displayed_valid = true;
        }
        editor_update_view(editor_ready, demo.paused,
                           editor_ready && state == State::Armed && command && command->manual,
                           shown, phase, demo.tick, status.applied_fov,
                           width > 0 ? std::uint32_t(width) : 0,
                           height > 0 ? std::uint32_t(height) : 0);
        status.state = std::uint32_t(state);
        status.error = error;
        status.phase = phase;
        std::snprintf(status.message, sizeof(status.message), "%s", message);
        write_status(status);
    };
    if (!command) {
        player_capture::set_hidden_handle(0, 2);
        finish(State::Probe, 0, "Native view hook ready; load a local replay to test a camera.");
        return;
    }
    // Each view republishes its own state; only a successful path evaluation
    // below turns the native path clock back on.
    video::publish_path_replay_time(false, 0);
    player_capture::publish_replay_time(-1.0);
    // Configuration and shader discovery start on the verified startup worker,
    // before a camera command can arrive. Capture still requires its marker.
    player_capture::tick();
    const auto& c = command->wire;
    if (c.command != seen) {
        // Every new command is first acknowledged by a real matching main view.
        bool was_hold = previous_mode == std::uint32_t(Mode::Hold) ||
                        previous_mode == std::uint32_t(Mode::HoldCurrent);
        seen = c.command;
        status.ack_command = seen;
        fault = 0;
        first_fault_code = 0;
        first_fault_message[0] = 0;
        if (c.mode == std::uint32_t(Mode::Manual)) {
            manual_pose = command->has_manual_pose ? command->manual_pose
                                                   : (displayed_valid ? displayed_pose : original);
            phase = gEffects.count ? gEffects.phase : 0;
            anchor = demo.time;
            started = false;
            completed = false;
            editor_reset_motion();
        } else if (c.mode == std::uint32_t(Mode::Hold)) {
            phase = c.start_phase;
            anchor = demo.time;
            started = false;
            completed = false;
        } else if (c.mode == std::uint32_t(Mode::Play)) {
            if (!was_hold) {
                phase = c.start_phase;
                anchor = demo.time;
            }
            play_base = phase;
            real_anchor = now;
            started = false;
            completed = false;
            largest_interval = 0;
        } else if (c.mode == std::uint32_t(Mode::HoldCurrent)) {
            anchor = demo.time;
            started = false;
            completed = false;
        }
        previous_mode = c.mode;
        last_game = demo.time;
        last_tick = demo.tick;
    }
    if (c.mode == std::uint32_t(Mode::Release)) {
        finish(State::Stopped, 0, "Native camera released; the game owns the view.");
        return;
    }
    if (gWorkerError.load() || now - gHeartbeatTime.load() > 2.0) {
        fault = 10;
        finish(State::Fault, fault, "Dolly control connection expired; native camera released.");
        return;
    }
    if (fault) {
        finish(State::Fault, fault,
               "Native camera stopped; restart the shot after checking diagnostics.");
        return;
    }
    if (!gCameraCache.basis || !view_ok || !pose_valid(original) || !std::isfinite(original_fov) ||
        original_fov <= 1 || original_fov >= 179 || width <= 0 || height <= 0 || (flags & 2)) {
        fault = 11;
        finish(State::Fault, fault,
               "This view or projection is unsupported; native camera released.");
        return;
    }
    if (!demo_ok || !demo.playing || demo.seeking || !same_demo(c.demo_name, demo.name)) {
        fault = 12;
        finish(State::Fault, fault,
               "Replay changed, stopped or is seeking; native camera released.");
        return;
    }
    if (command->manual) {
        auto editor = editor_snapshot();
        if (!editor.input_available) {
            fault = 19;
            finish(
                State::Fault, fault,
                "Native raw-input interception is unavailable; original game input remains available. Export diagnostics and restart the editing session.");
            return;
        }
        // The worker may read a fresh camera command just after reading an older
        // EditorConfig. DX11 can also still be attaching/recreating its first view.
        // These are pending states, not permanent camera faults. The controller
        // bounds startup waiting and cancels on timeout; no view is written here.
        if (!editor.enabled || !editor.overlay_available) {
            finish(State::Starting, 0,
                   !editor.enabled ? "Waiting for native editor configuration."
                                   : "Waiting for the DirectX 11 editor panel.");
            return;
        }
        // Attach preview: input edits the local offsets and the camera is
        // composed from the live target. Snap (free camera only) stores offsets
        // that reproduce the current pose; both publish through the result
        // block for the editor to persist on the selected key.
        EditorAttachConfig preview_config{};
        const bool preview_flag =
            editor_attach_config(preview_config) && (preview_config.flags & 4);
        auto preview = std::atomic_load(&gAttachPreview);
        static AttachSmoothing preview_smoothing;
        static CameraPose preview_offsets;
        static std::uint32_t preview_seed = 0;
        static std::array<double, 6> preview_published{};
        static std::uint32_t snap_seen = 0;
        const bool snap_requested = preview_config.snap_request != snap_seen;
        const bool manual_mode = c.mode == std::uint32_t(Mode::Manual);
        const bool preview_current = preview && preview->sequence == preview_config.sequence;
        if (preview_flag && manual_mode && (!preview_current || !preview->cache.ready)) {
            player_capture::set_hidden_handle(0, 4);
            if (!preview_current) {
                finish(State::Starting, 0, "Resolving the selected attach camera.");
            } else {
                fault = 41;
                finish(State::Fault, fault,
                       preview->cache.error ? preview->cache.error
                                            : "The selected attach camera could not be resolved.");
            }
            return;
        }
        const bool preview_active =
            preview_current && preview->cache.ready && preview_flag && manual_mode;
        if (snap_requested && !preview_active && preview_current && preview->cache.ready &&
            manual_mode) {
            snap_seen = preview_config.snap_request;
            AttachSample snap_sample{};
            const char* snap_error = nullptr;
            AttachSegment snap_segment{};
            snap_segment.point = preview->point;
            if (attach_runtime::sample(preview->schema, preview->cache, snap_segment, snap_sample,
                                       snap_error)) {
                std::array<double, 6> snapped{};
                if (attach_snap_offsets(snap_sample, manual_pose, snapped))
                    publish_attach_result(*preview, snapped);
            }
        }
        if (preview_active) {
            bool preview_dirty = false;
            if (preview_seed != preview->sequence) {
                preview_seed = preview->sequence;
                for (int index = 0; index < 6; ++index)
                    preview_offsets[index] = preview->authored[index];
                preview_offsets[6] = manual_pose[6];
                preview_smoothing.reset();
                preview_dirty = true;
            }
            editor_integrate_flight(preview_offsets, real_delta);
            AttachSample preview_sample{};
            const char* preview_error = nullptr;
            AttachSegment segment{};
            segment.point = preview->point;
            for (int index = 0; index < 6; ++index)
                segment.offset[index] = preview_offsets[index];
            if (!attach_runtime::sample(preview->schema, preview->cache, segment, preview_sample,
                                        preview_error)) {
                fault = 41;
                finish(State::Fault, fault,
                       preview_error ? preview_error
                                     : "The attach preview sample was unavailable.");
                return;
            }
            CameraPose composed = manual_pose;
            if (!resolve_attach_pose(preview_sample, segment, composed)) {
                fault = 42;
                finish(State::Fault, fault, "The attach preview pose check failed.");
                return;
            }
            preview_smoothing.apply(composed, real_delta, preview->smoothing);
            manual_pose = composed;
            player_capture::set_hidden_handle(
                preview_config.hide ? preview_sample.target.handle : 0, 3);
            for (int index = 0; index < 6; ++index)
                preview_dirty = preview_dirty ||
                                std::abs(preview_offsets[index] - preview_published[index]) > 0.001;
            if (preview_dirty) {
                for (int index = 0; index < 6; ++index)
                    preview_published[index] = preview_offsets[index];
                publish_attach_result(*preview, preview_published);
            }
        } else if (manual_mode) {
            player_capture::set_hidden_handle(0, 4);
            editor_integrate_flight(manual_pose, real_delta);
        }
        if (!pose_valid(manual_pose)) {
            fault = 16;
            finish(State::Fault, fault,
                   "Native flight produced an invalid camera; camera released.");
            return;
        }
        double fov = original_fov;
        if (c.flags & kAspect) {
            constexpr double pi = 3.14159265358979323846;
            fov = 2 * 180 / pi *
                  std::atan(std::tan(double(original_fov) * pi / 360) * manual_pose[6] /
                            original_aspect);
        }
        if (!std::isfinite(fov) || fov <= 1 || fov >= 179) {
            fault = 17;
            finish(State::Fault, fault, "Native flight aspect is unsupported; camera released.");
            return;
        }
        // Preserve the selected preview lens/effects while manually reframing.
        // The frozen effect phase stays owned until an explicit Stop/Release.
        if (!gEffects.apply(gEffects.shot, gEffects.phase)) {
            fault = 35;
            finish(State::Fault, fault,
                   "Could not maintain the selected camera effects during manual flight.");
            return;
        }
        float xyz[3], angles[3];
        for (int i = 0; i < 3; ++i) {
            xyz[i] = float(manual_pose[i]);
            angles[i] = float(std::remainder(manual_pose[i + 3], 360.0));
        }
        camera_view::apply(gCameraCache, view, xyz, angles);
        if (c.flags & kAspect) {
            float value = float(fov), aspect = float(manual_pose[6]);
            std::memcpy(reinterpret_cast<void*>(view + 0x498), &value, 4);
            std::memcpy(reinterpret_cast<void*>(view + 0x4d8), &aspect, 4);
        }
        for (int i = 0; i < 7; ++i)
            status.applied_pose[i] = manual_pose[i];
        status.applied_fov = fov;
        finish(State::Armed, 0,
               c.mode == std::uint32_t(Mode::Manual)
                   ? "Native free camera updates each rendered main view."
                   : "Native manual camera held for capture or playback handoff.");
        return;
    }
    if (!command->path || command->path->empty()) {
        fault = 13;
        finish(State::Fault, fault, "No valid native path was loaded.");
        return;
    }
    if (c.mode == std::uint32_t(Mode::Hold) || c.mode == std::uint32_t(Mode::HoldCurrent)) {
        anchor = demo.time;
        real_anchor = now;
        last_game = demo.time;
        last_tick = demo.tick;
    } else if (!completed) {
        if (c.flags & kFrozen) {
            if (!demo.paused || demo.tick != last_tick) {
                fault = 14;
                finish(State::Fault, fault, "The replay moved during frozen native playback.");
                return;
            }
            phase = play_base + (now - real_anchor) * c.speed;
        } else {
            if (!demo.paused) {
                double delta = demo.time - last_game;
                double permitted = std::max(.25, real_delta * c.speed * 3 + 2 * demo.interval);
                if (delta < -2 * demo.interval || delta > permitted || demo.tick < last_tick ||
                    demo.tick - last_tick > 128) {
                    fault = 15;
                    finish(State::Fault, fault,
                           "Replay time jumped during native playback; camera released.");
                    return;
                }
                started = true;
                phase = std::max(phase, play_base + demo.time - anchor);
            } else if (!started) {
                anchor = demo.time;
            }
            last_game = demo.time;
            last_tick = demo.tick;
        }
        if (phase >= command->path->duration()) {
            phase = command->path->duration();
            completed = true;
        }
    }
    if (c.flags & kGamePov) {
        player_capture::set_hidden_handles(0, 0, 5);
        if (c.mode == std::uint32_t(Mode::Play)) {
            video::publish_path_replay_time(true, phase);
            player_capture::publish_replay_time(phase);
        }
        finish(completed ? State::Completed
                         : (c.mode == std::uint32_t(Mode::Play) ? State::Playing : State::Armed),
               0, completed ? "POV segment finished." : "Game spectator owns the POV camera.");
        return;
    }
    CameraPose applied{};
    if (!command->path->evaluate(phase, applied) || !pose_valid(applied)) {
        fault = 16;
        finish(State::Fault, fault, "Native path returned an invalid camera.");
        return;
    }
    // Attach camera: an enabled segment replaces the evaluated pose. The worker
    // resolved the target when the command arrived; a missing target, offset
    // block or identity check faults instead of rendering a guessed camera.
    // Each source keeps its own filter through the blend and arrival. Sharing
    // one filter would jump when two keys use different offsets on one hero.
    static std::array<AttachSmoothing, AttachTrack::max_segments> attach_smoothing;
    static std::uint32_t smoothing_command = 0;
    static double smoothing_phase = 0;
    if (smoothing_command != c.command || phase < smoothing_phase) {
        for (auto& filter : attach_smoothing)
            filter.reset();
        smoothing_command = c.command;
    }
    smoothing_phase = phase;
    const AttachSegment* attach_segment = command->shot ? command->shot->attach.at(phase) : nullptr;
    const CameraPose free_pose = applied;
    std::uint32_t hidden_current = 0, hidden_arriving = 0;
    if (attach_segment && (attach_segment->flags & 1)) {
        EditorAttachConfig attach_config{};
        const auto segment_index =
            std::size_t(attach_segment - command->shot->attach.segments().data());
        const auto cache =
            segment_index < command->attach.size() ? command->attach[segment_index] : nullptr;
        if (!cache || !editor_attach_config(attach_config)) {
            fault = 40;
            finish(State::Fault, fault,
                   "Attach camera offsets are not available; reconnect the editor session.");
            return;
        }
        AttachSample attach_sample{};
        const char* attach_error = nullptr;
        if (!attach_runtime::sample(attach_runtime::offsets_from(attach_config), *cache,
                                    *attach_segment, attach_sample, attach_error)) {
            fault = 41;
            finish(State::Fault, fault,
                   attach_error ? attach_error : "The attach camera sample was unavailable.");
            return;
        }
        CameraPose resolved = applied;
        if (!resolve_attach_pose(attach_sample, *attach_segment, resolved)) {
            fault = 42;
            finish(State::Fault, fault, "The attach camera identity or pose check failed.");
            return;
        }
        attach_smoothing[segment_index].apply(resolved, real_delta, attach_segment->smoothing);
        applied = resolved;
        hidden_current = (attach_segment->flags & 2) ? attach_sample.target.handle : 0;
    }
    double blend_weight = 0;
    const auto arriving =
        command->shot ? command->shot->attach.arriving(phase, blend_weight) : nullptr;
    if (arriving) {
        CameraPose destination = free_pose;
        if (arriving->flags & 1) {
            EditorAttachConfig config{};
            const auto index = std::size_t(arriving - command->shot->attach.segments().data());
            const auto cache = index < command->attach.size() ? command->attach[index] : nullptr;
            AttachSample sample{};
            const char* error = nullptr;
            if (!cache || !editor_attach_config(config) ||
                !attach_runtime::sample(attach_runtime::offsets_from(config), *cache, *arriving,
                                        sample, error) ||
                !resolve_attach_pose(sample, *arriving, destination)) {
                fault = 42;
                finish(State::Fault, fault,
                       error ? error : "The arriving camera source could not be resolved.");
                return;
            }
            attach_smoothing[index].apply(destination, real_delta, arriving->smoothing);
            hidden_arriving = (arriving->flags & 2) ? sample.target.handle : 0;
        }
        applied = blend_attach_poses(applied, destination, blend_weight);
    }
    player_capture::set_hidden_handles(hidden_current, hidden_arriving, 5);
    double fov = original_fov;
    if (c.flags & kAspect) {
        // SetUpView has already scaled the original camera FOV by current aspect.
        // Replace that factor, preserving the game's base lens and viewport ratio.
        constexpr double pi = 3.14159265358979323846;
        fov = 2 * 180 / pi *
              std::atan(std::tan(double(original_fov) * pi / 360) * applied[6] / original_aspect);
        if (!std::isfinite(fov) || fov <= 1 || fov >= 179) {
            fault = 17;
            finish(State::Fault, fault, "Native aspect produced an unsupported field of view.");
            return;
        }
    }
    // DOF render-graph construction reads these cvars after main-view setup.
    // Apply the same phase as XYZ/angles, using the native typed setter.
    if (!gEffects.apply(command->shot, phase)) {
        fault = 35;
        finish(
            State::Fault, fault,
            "Native DOF setting was unavailable, deferred or clamped; shot stopped and originals restored.");
        return;
    }
    float xyz[3], angles[3];
    for (int i = 0; i < 3; ++i) {
        xyz[i] = float(applied[i]);
        angles[i] = float(std::remainder(applied[i + 3], 360.0));
    }
    // The verified callback owns this live main-view object at this point.
    camera_view::apply(gCameraCache, view, xyz, angles);
    if (c.flags & kAspect) {
        float value = float(fov), aspect = float(applied[6]);
        std::memcpy(reinterpret_cast<void*>(view + 0x498), &value, 4);
        std::memcpy(reinterpret_cast<void*>(view + 0x4d8), &aspect, 4);
    }
    for (int i = 0; i < 7; ++i)
        status.applied_pose[i] = applied[i];
    status.applied_fov = fov;
    if (c.mode == std::uint32_t(Mode::Play)) {
        video::publish_path_replay_time(true, phase);
        player_capture::publish_replay_time(phase);
    }
    finish(completed ? State::Completed
                     : (c.mode == std::uint32_t(Mode::Play) ? State::Playing : State::Armed),
           0,
           completed ? "Native path finished; holding the final camera for handoff."
                     : (c.mode == std::uint32_t(Mode::Play)
                            ? "Native path evaluates during each main-view setup."
                            : "Native camera held; waiting for playback or handoff."));
}
__declspec(noinline) static void __fastcall setup_hook(void* self, std::uintptr_t opaque) {
#if defined(_MSC_VER)
    auto caller = reinterpret_cast<std::uintptr_t>(_ReturnAddress());
#else
    auto caller = reinterpret_cast<std::uintptr_t>(__builtin_return_address(0));
#endif
    gOriginalSetup(self, opaque);
    on_view(self, caller);
}
static DWORD WINAPI worker(void*) {
    try {
        std::wstring mapping;
        DWORD editor_pid = 0;
        if (!read_config(mapping, editor_pid))
            return 0;
        gMapping = OpenFileMappingW(FILE_MAP_ALL_ACCESS, FALSE, mapping.c_str());
        if (!gMapping)
            return 0;
        gMemory = static_cast<unsigned char*>(
            MapViewOfFile(gMapping, FILE_MAP_ALL_ACCESS, 0, 0, kMappingBytes));
        if (!gMemory)
            return 0;
        gEditor = OpenProcess(SYNCHRONIZE, FALSE, editor_pid);
        if (!gEditor) {
            startup_status(State::Fault, 20, "Could not verify the Dolly editor process.");
            return 0;
        }
        startup_status(State::Starting, 0, "Waiting for Deadlock client and engine modules.");
        double began = now_seconds();
        HMODULE client = nullptr, engine = nullptr;
        while (now_seconds() - began < 120 && WaitForSingleObject(gEditor, 0) == WAIT_TIMEOUT) {
            client = GetModuleHandleW(L"client.dll");
            engine = GetModuleHandleW(L"engine2.dll");
            if (client && engine)
                break;
            Sleep(20);
        }
        if (!module_matches(engine, kEngineHash, 0x969000) &&
            !module_matches(engine, kUpdatedEngineHash, 0x969000)) {
            startup_status(
                State::Unsupported, 21,
                "The installed engine2.dll does not match this native build. Use Console camera and provide the updated DLL.");
            return 0;
        }
        gClient = reinterpret_cast<std::uintptr_t>(client);
        gEngine = reinterpret_cast<std::uintptr_t>(engine);
        gCompat = resolve_client_profile(client, true);
        if (!gCompat.resolved) {
            startup_status(
                State::Unsupported, 21,
                "Installed game modules do not match this native build. Use Console camera and provide updated DLLs.");
            return 0;
        }
        if (!init_cvar_interface()) {
            startup_status(
                State::Unsupported, 26,
                "The tier0 cvar interface does not match this native build. Use Console camera mode.");
            return 0;
        }
        gCameraCache = camera_view::resolve(client, gClient + gCompat.setup);
        if (!gCameraCache.basis) {
            startup_status(
                State::Unsupported, 37,
                "The native camera visibility layout differs from this Dolly build. Use Console camera mode.");
            return 0;
        }
        unsigned char prologue[sizeof(kSetupPrologue)]{};
        if (!read_memory(gClient + gCompat.setup, prologue, sizeof(prologue)) ||
            std::memcmp(prologue, kSetupPrologue, sizeof(prologue))) {
            startup_status(State::Unsupported, 22,
                           "Native view function bytes differ; no hook was installed.");
            return 0;
        }
        HMODULE pinned = nullptr;
        if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                                    GET_MODULE_HANDLE_EX_FLAG_PIN,
                                reinterpret_cast<LPCWSTR>(&setup_hook), &pinned)) {
            startup_status(State::Fault, 25,
                           "Could not keep the native callback resident; no hook was installed.");
            return 0;
        }
        if (MH_Initialize() != MH_OK ||
            MH_CreateHook(reinterpret_cast<void*>(gClient + gCompat.setup),
                          reinterpret_cast<void*>(setup_hook),
                          reinterpret_cast<void**>(&gOriginalSetup)) != MH_OK) {
            startup_status(State::Fault, 23, "Could not prepare the native view hook.");
            return 0;
        }
        // Collect shader metadata during hideout/replay loading, not only after
        // the first camera command (when character shaders may already exist).
        // Keep disk hashing off the render callback and retain the exact gate.
        const auto scene_module = GetModuleHandleW(L"scenesystem.dll");
        player_capture::configure(reinterpret_cast<std::uintptr_t>(scene_module),
                                  scene_module != nullptr && hash_file(module_path(scene_module)) ==
                                                                 kPlayerCaptureScenesystemHash);
        gHeartbeatTime = now_seconds();
        HeartbeatMonitor heartbeat_monitor(gMemory, gEditor);
        if (MH_EnableHook(reinterpret_cast<void*>(gClient + gCompat.setup)) != MH_OK) {
            startup_status(State::Fault, 24, "Could not enable the native view hook.");
            return 0;
        }
        gHookInstalled = true;
        // Hook discovery and installation happen on the worker, never in DllMain
        // or the view callback. The main camera remains usable if overlay fails.
        editor_install_input_hooks();
        install_overlay_hooks();
        // Hook and original trampoline remain resident until process exit. Losing
        // the editor only releases ownership, avoiding code-unload races in a view.
        std::shared_ptr<const NativeShot> shot;
        std::uint32_t accepted = 0;
        bool manual = false;
        std::vector<unsigned char> payload;
        HMODULE diagnostic_renderer = nullptr;
        ULONGLONG next_renderer_probe = 0;
        for (;;) {
            if (WaitForSingleObject(gEditor, 0) != WAIT_TIMEOUT) {
                gWorkerError = 30;
                seek_relief_tick(false);
                editor_worker_tick(gMemory, false);
                visualization_worker_tick(nullptr, false);
                media_worker_tick(nullptr, false);
                video::shutdown();
                break;
            }
            editor_worker_tick(gMemory, now_seconds() - gHeartbeatTime.load() < 2.0);
            visualization_worker_tick(mapping.c_str(), now_seconds() - gHeartbeatTime.load() < 2.0);
            media_worker_tick(mapping.c_str(), now_seconds() - gHeartbeatTime.load() < 2.0);
            const auto diagnostic_now = GetTickCount64();
            if (diagnostic_now >= next_renderer_probe) {
                next_renderer_probe = diagnostic_now + 1000;
                const auto module = GetModuleHandleW(L"rendersystemdx11.dll");
                if (module != diagnostic_renderer) {
                    diagnostic_renderer = module;
                    // Optional observation only. Fingerprint failures must never stop the
                    // camera worker, alter ownership, or write into the game renderer.
                    try {
                        const auto hash = module ? hash_file(module_path(module)) : std::string();
                        renderer_diagnostics_probe(reinterpret_cast<std::uintptr_t>(module),
                                                   hash.c_str());
                    } catch (...) {
                        renderer_diagnostics_probe(reinterpret_cast<std::uintptr_t>(module), "");
                    }
                }
            }
            renderer_diagnostics_tick(gMemory);
            // Apply the reversible render relief while the replay is seeking
            // (the main cause of the vertex-buffer overflow and lingering
            // effects) or the engine's buffer queue is already near capacity.
            // The editor can opt out per command with kNoSeekRelief.
            seek_relief_tick(
                gReliefAllowed.load(std::memory_order_relaxed) &&
                (gDemoSeeking.load(std::memory_order_relaxed) || overlay_renderer_pressure()));
            ControlHeader control{};
            if (read_control(control, payload, accepted)) {
                // CreateProcess can reach the proxy before the launcher receives the PID.
                if (control.game_pid == 0) {
                    Sleep(5);
                    continue;
                }
                if (control.editor_pid != editor_pid || control.game_pid != GetCurrentProcessId() ||
                    control.mode > 4 ||
                    control.flags & ~(kFrozen | kAspect | kNoSeekRelief | kGamePov) ||
                    ((control.flags & kGamePov) && ((control.flags & (kFrozen | kAspect)) ||
                                                    control.mode == std::uint32_t(Mode::Manual))) ||
                    !std::isfinite(control.start_phase) || control.start_phase < 0 ||
                    !std::isfinite(control.speed) || control.speed < .05 || control.speed > 4 ||
                    !std::memchr(control.demo_name, 0, sizeof(control.demo_name))) {
                    gWorkerError = 31;
                    Sleep(10);
                    continue;
                }
                gReliefAllowed.store(!(control.flags & kNoSeekRelief), std::memory_order_relaxed);
                auto candidate = shot;
                bool next_manual = control.mode == std::uint32_t(Mode::Manual) ||
                                   (manual && control.mode == std::uint32_t(Mode::HoldCurrent));
                CameraPose seed{};
                bool has_seed = false;
                if (control.mode == std::uint32_t(Mode::Manual)) {
                    if (control.payload_bytes) {
                        if (control.payload_bytes != sizeof(seed)) {
                            gWorkerError = 32;
                            Sleep(10);
                            continue;
                        }
                        std::memcpy(seed.data(), payload.data(), sizeof(seed));
                        if (!pose_valid(seed)) {
                            gWorkerError = 32;
                            Sleep(10);
                            continue;
                        }
                        has_seed = true;
                    }
                } else if (control.payload_bytes) {
                    auto parsed = std::make_shared<NativeShot>();
                    std::string error;
                    if (!parsed->load(payload.data(), payload.size(), error)) {
                        gWorkerError = 32;
                        Sleep(10);
                        continue;
                    }
                    candidate = parsed;
                    next_manual = false;
                }
                if (control.mode && !next_manual &&
                    (!candidate || control.start_phase > candidate->camera.duration())) {
                    gWorkerError = 33;
                    Sleep(10);
                    continue;
                }
                std::vector<std::shared_ptr<const attach_runtime::Cache>> attach_caches;
                if ((control.flags & kGamePov) && candidate &&
                    (!candidate->effects.empty() ||
                     std::any_of(candidate->attach.segments().begin(),
                                 candidate->attach.segments().end(),
                                 [](const auto& segment) { return (segment.flags & 1) != 0; }))) {
                    gWorkerError = 32;
                    Sleep(10);
                    continue;
                }
                if (candidate)
                    for (const auto& attach_segment : candidate->attach.segments()) {
                        if (!(attach_segment.flags & 1)) {
                            attach_caches.push_back(nullptr);
                            continue;
                        }
                        // A shot may cut between players or bones. Resolve each distinct
                        // source on the worker, never reuse the first source blindly.
                        std::shared_ptr<const attach_runtime::Cache> reused;
                        for (std::size_t index = 0; index < attach_caches.size(); ++index) {
                            const auto& previous = candidate->attach.segments()[index];
                            if (attach_caches[index] &&
                                previous.target.handle == attach_segment.target.handle &&
                                previous.target.entity_id == attach_segment.target.entity_id &&
                                previous.target.model == attach_segment.target.model &&
                                previous.point == attach_segment.point &&
                                previous.bone_hash == attach_segment.bone_hash) {
                                reused = attach_caches[index];
                                break;
                            }
                        }
                        if (reused) {
                            attach_caches.push_back(reused);
                            continue;
                        }
                        auto attach_cache = std::make_shared<attach_runtime::Cache>();
                        EditorAttachConfig attach_config{};
                        if (!editor_attach_config(attach_config)) {
                            attach_cache->error =
                                "Attach camera offsets are not available yet; reconnect "
                                "the editor session or re-play the shot.";
                        } else {
                            const char* attach_error = nullptr;
                            attach_runtime::resolve(reinterpret_cast<HMODULE>(gClient),
                                                    attach_runtime::offsets_from(attach_config),
                                                    attach_segment.target, attach_segment,
                                                    *attach_cache, attach_error);
                            if (!attach_cache->ready && attach_error)
                                attach_cache->error = attach_error;
                        }
                        attach_caches.push_back(attach_cache);
                    }
                auto command = std::make_shared<Command>();
                command->wire = control;
                command->shot = candidate;
                command->attach = std::move(attach_caches);
                command->manual = next_manual;
                command->has_manual_pose = has_seed;
                command->manual_pose = seed;
                manual = next_manual;
                if (candidate)
                    command->path =
                        std::shared_ptr<const NativePath>(candidate, &candidate->camera);
                shot = candidate;
                accepted = control.command;
                gWorkerError = 0;
                std::atomic_store_explicit(&gCommand, std::shared_ptr<const Command>(command),
                                           std::memory_order_release);
            }
            static double last_roster_publish = 0;
            const double roster_now = now_seconds();
            if (gMemory && roster_now - last_roster_publish >= 2.0) {
                last_roster_publish = roster_now;
                attach_runtime::publish_roster(gMemory, reinterpret_cast<HMODULE>(gClient));
            }
            // Resolve the selected attach target whenever the editor publishes
            // a different selection; used by both preview and snap.
            static std::uint32_t attach_preview_sequence = 0;
            EditorAttachConfig attach_config{};
            if (editor_attach_config(attach_config)) {
                if ((attach_config.flags & 3) == 3) {
                    if (attach_config.sequence != attach_preview_sequence) {
                        attach_preview_sequence = attach_config.sequence;
                        auto preview = std::make_shared<AttachPreview>();
                        preview->sequence = attach_config.sequence;
                        preview->schema = attach_runtime::offsets_from(attach_config);
                        for (int index = 0; index < 6; ++index)
                            preview->authored[index] = attach_config.offset[index];
                        preview->point = static_cast<AttachPoint>(attach_config.point);
                        preview->smoothing = attach_config.smoothing;
                        preview->handle = attach_config.handle;
                        preview->entity_index = attach_config.entity_id;
                        preview->model = attach_config.model;
                        preview->bone_hash = attach_config.bone_hash;
                        AttachTarget target;
                        target.handle = attach_config.handle;
                        target.entity_id = attach_config.entity_id;
                        target.model = attach_config.model;
                        const char* attach_error = nullptr;
                        AttachSegment preview_segment{};
                        preview_segment.point = preview->point;
                        preview_segment.bone_hash = attach_config.bone_hash;
                        auto previous = std::atomic_load(&gAttachPreview);
                        AttachSample check{};
                        const bool reuse =
                            previous && previous->cache.ready &&
                            previous->handle == preview->handle &&
                            previous->entity_index == preview->entity_index &&
                            previous->model == preview->model &&
                            previous->point == preview->point &&
                            previous->bone_hash == preview->bone_hash &&
                            attach_runtime::sample(preview->schema, previous->cache,
                                                   preview_segment, check, attach_error);
                        if (reuse)
                            preview->cache = previous->cache;
                        else
                            attach_runtime::resolve(reinterpret_cast<HMODULE>(gClient),
                                                    preview->schema, target, preview_segment,
                                                    preview->cache, attach_error);
                        if (!preview->cache.ready && attach_error)
                            preview->cache.error = attach_error;
                        if (!reuse)
                            attach_runtime::publish_bones(gMemory, &preview->cache);
                        std::atomic_store_explicit(&gAttachPreview,
                                                   std::shared_ptr<const AttachPreview>(preview),
                                                   std::memory_order_release);
                    }
                } else if (attach_preview_sequence) {
                    attach_preview_sequence = 0;
                    attach_runtime::publish_bones(gMemory, nullptr);
                    std::atomic_store_explicit(&gAttachPreview,
                                               std::shared_ptr<const AttachPreview>(),
                                               std::memory_order_release);
                }
            }
            Sleep(5);
        }
    } catch (...) {
        seek_relief_tick(false);
        visualization_worker_tick(nullptr, false);
        media_worker_tick(nullptr, false);
        gWorkerError = 99;
        if (!gHookInstalled)
            startup_status(
                State::Fault, 99,
                "Native bridge initialization failed; console unlocker remains available.");
    }
    return 0;
}
static BOOL CALLBACK start_worker(PINIT_ONCE, PVOID, PVOID*) {
    HANDLE thread = CreateThread(nullptr, 0, worker, nullptr, 0, nullptr);
    if (thread)
        CloseHandle(thread);
    return TRUE;
}
static BOOL CALLBACK load_unlocker(PINIT_ONCE, PVOID, PVOID*) {
    if (!development_flags())
        return TRUE;
    auto path = beside_module(L"dolly_cvar_unlocker.dll");
    if (path.empty() || hash_file(path) != kUnlockerHash)
        return TRUE;
    HMODULE module = LoadLibraryExW(path.c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (module)
        gOriginalFactory = reinterpret_cast<Factory>(GetProcAddress(module, "CreateInterface"));
    return TRUE;
}
}
extern "C" __declspec(dllexport) unsigned DollyNativeProtocolVersion() {
    return dolly::kBridgeAbi;
}
// Win64 implements these operations as intrinsics, so kernel32 need not export
// callable functions for ctypes. Expose our own stable C entry points instead.
// This shared-memory utility does not initialize the unlocker or camera hook.
extern "C" __declspec(dllexport) LONG DollyAtomicExchange32(volatile LONG* target, LONG value) {
    return InterlockedExchange(target, value);
}
extern "C" __declspec(dllexport) LONG64 DollyAtomicExchange64(volatile LONG64* target,
                                                              LONG64 value) {
    return InterlockedExchange64(target, value);
}
extern "C" __declspec(dllexport) LONG DollyAtomicCompareExchange32(volatile LONG* target,
                                                                   LONG value, LONG comparand) {
    return InterlockedCompareExchange(target, value, comparand);
}
extern "C" __declspec(dllexport) void* CreateInterface(const char* name, int* result) {
    try {
        InitOnceExecuteOnce(&gLoaderOnce, load_unlocker, nullptr, nullptr);
        if (!gOriginalFactory) {
            if (result)
                *result = 1;
            OutputDebugStringA(
                "Dolly native: development flags or verified cvar unlocker missing.\n");
            return nullptr;
        }
        void* original = gOriginalFactory(name, result);
        InitOnceExecuteOnce(&gWorkerOnce, start_worker, nullptr, nullptr);
        return original;
    } catch (...) {
        if (result)
            *result = 1;
        return nullptr;
    }
}
BOOL WINAPI DllMain(HINSTANCE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        gModule = module;
        QueryPerformanceFrequency(&gFrequency);
    }
    return TRUE;
}
