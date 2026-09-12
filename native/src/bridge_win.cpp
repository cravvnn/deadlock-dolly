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
#include <vector>
#include "MinHook.h"
#include "dolly_path.hpp"
#include "dolly_effects.hpp"
#include "dolly_protocol.hpp"
#include "dolly_editor.hpp"
#include "dolly_overlay.hpp"
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
constexpr char kUnlockerHash[] = "e86f270b1dedc81fd54a230f0080eee568a4f2bd39e1f41080dcf71d833267ba";
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
struct Command {
    ControlHeader wire{};
    std::shared_ptr<const NativePath> path;
    std::shared_ptr<const NativeShot> shot;
    bool manual = false, has_manual_pose = false;
    CameraPose manual_pose{};
};
std::shared_ptr<const Command> gCommand;

static double now_seconds() noexcept {
    LARGE_INTEGER n{};
    QueryPerformanceCounter(&n);
    return gFrequency.QuadPart ? double(n.QuadPart) / double(gFrequency.QuadPart) : 0;
}
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
        finish(State::Probe, 0, "Native view hook ready; load a local replay to test a camera.");
        return;
    }
    const auto& c = command->wire;
    if (c.command != seen) {
        // Every new command is first acknowledged by a real matching main view.
        bool was_hold = previous_mode == std::uint32_t(Mode::Hold) ||
                        previous_mode == std::uint32_t(Mode::HoldCurrent);
        seen = c.command;
        status.ack_command = seen;
        fault = 0;
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
        if (c.mode == std::uint32_t(Mode::Manual))
            editor_integrate_flight(manual_pose, real_delta);
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
                   ? "Native paused flight updates each rendered main view."
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
    CameraPose applied{};
    if (!command->path->evaluate(phase, applied) || !pose_valid(applied)) {
        fault = 16;
        finish(State::Fault, fault, "Native path returned an invalid camera.");
        return;
    }
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
        gHeartbeatTime = now_seconds();
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
        std::uint64_t heartbeat = 0;
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
            auto hb = static_cast<std::uint64_t>(InterlockedCompareExchange64(
                reinterpret_cast<volatile LONG64*>(gMemory + 32), 0, 0));
            if (hb != heartbeat) {
                heartbeat = hb;
                gHeartbeatTime = now_seconds();
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
                    control.mode > 4 || control.flags & ~(kFrozen | kAspect | kNoSeekRelief) ||
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
                auto command = std::make_shared<Command>();
                command->wire = control;
                command->shot = candidate;
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
