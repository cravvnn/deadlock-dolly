#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <algorithm>
#include <atomic>
#include <cstring>
#include <cwchar>
#include <string>
#include "dolly_media.hpp"
#include "dolly_editor.hpp"
#include "dolly_video.hpp"
#include "dolly_reshade.hpp"

namespace dolly {
namespace {
std::atomic<ULONGLONG> session_seen{0};
HANDLE mapping = nullptr;
unsigned char* memory = nullptr;
std::wstring mapping_name;
ULONGLONG next_open = 0, next_status = 0;
std::uint32_t accepted = 0, publication = 0, command_error = 0;
wchar_t command_message[384]{};

std::uint32_t sequence(std::size_t offset) noexcept {
    return static_cast<std::uint32_t>(
        InterlockedCompareExchange(reinterpret_cast<volatile LONG*>(memory + offset), 0, 0));
}
template <std::size_t N> void copy_text(char16_t (&out)[N], const wchar_t* text) noexcept {
    static_assert(sizeof(wchar_t) == 2, "Windows UTF-16 paths");
    if (!text)
        return;
    const auto count = std::min<std::size_t>(wcsnlen(text, N), N - 1);
    std::memcpy(out, text, count * 2);
    out[count] = 0;
}
void reject(const wchar_t* message) noexcept {
    command_error = 1;
    wcsncpy(command_message, message, 383);
    command_message[383] = 0;
}
void close_mapping() noexcept {
    if (memory)
        UnmapViewOfFile(memory);
    if (mapping)
        CloseHandle(mapping);
    memory = nullptr;
    mapping = nullptr;
    mapping_name.clear();
    accepted = publication = command_error = 0;
    command_message[0] = 0;
    next_open = next_status = 0;
}
bool terminated(const char16_t* text) noexcept {
    for (unsigned i = 0; i < 1024; ++i)
        if (text[i] == 0)
            return true;
    return false;
}
void consume() noexcept {
    const auto before = sequence(8);
    if (!before || (before & 1) || before == accepted)
        return;
    MediaCommand command{};
    std::memcpy(&command, memory, sizeof(command));
    MemoryBarrier();
    if (sequence(8) != before || command.sequence != before)
        return;
    accepted = before;
    command_error = 0;
    command_message[0] = 0;
    if (std::memcmp(command.magic, "DLYMED01", 8) || command.abi != kMediaAbi || command.reserved ||
        !terminated(command.path) || !terminated(command.config_path)) {
        reject(L"Media protocol differs from this Dolly build.");
        return;
    }
    const auto path = reinterpret_cast<const wchar_t*>(command.path);
    const auto config = reinterpret_cast<const wchar_t*>(command.config_path);
    switch (command.command) {
    case 1: {
        const auto editor = editor_snapshot();
        if (!editor.enabled || !editor.ready) {
            reject(L"Open a local replay and connect the native editor before recording.");
            break;
        }
        if (!video::start(path, command.fps, command.bitrate)) {
            const auto state = video::status();
            reject(state.error[0] ? state.error
                                  : L"A recording is already active or its settings are invalid.");
        }
        break;
    }
    case 2:
        video::stop(false);
        break;
    case 3:
        video::stop(true);
        break;
    case 4:
        if (!reshade_initialize_async(path, config))
            reject(L"ReShade could not load. Check the ReShade status for details.");
        break;
    case 5:
        reshade_set_enabled(false);
        break;
    case 6:
        if (!reshade_request_overlay(!(reshade_overlay_open() || reshade_overlay_pending())))
            reject(L"Load a compatible ReShade runtime before opening its menu.");
        break;
    default:
        reject(L"Unknown native media command.");
        break;
    }
}
void publish() noexcept {
    const auto video_state = video::status();
    const auto reshade = reshade_status();
    MediaStatus status{};
    std::memcpy(status.magic, "DLYMDS01", 8);
    publication += 2;
    if (!publication)
        publication = 2;
    status.sequence = publication;
    status.abi = kMediaAbi;
    status.ack = accepted;
    status.command_error = command_error;
    status.video_state = static_cast<std::uint32_t>(video_state.state);
    status.fps = video_state.fps;
    status.width = video_state.width;
    status.height = video_state.height;
    status.frames_written = video_state.frames_written;
    status.frames_dropped = video_state.frames_dropped;
    status.duration_100ns = video_state.duration_100ns;
    status.video_error = video_state.error_code;
    status.reshade_state = reshade.failed
                               ? 3
                               : (reshade.loading || (reshade.enabled && !reshade.available)
                                      ? 1
                                      : (reshade.available && reshade.enabled ? 2 : 0));
    status.reshade_open = reshade.overlay_open;
    copy_text(status.video_message, video_state.error);
    wchar_t message[384]{};
    MultiByteToWideChar(CP_UTF8, 0, reshade.message, -1, message, 383);
    copy_text(status.reshade_message, message);
    copy_text(status.command_message, command_message);
    auto* slot = reinterpret_cast<volatile LONG*>(memory + kMediaStatusOffset + 8);
    InterlockedExchange(slot, static_cast<LONG>(publication - 1));
    std::memcpy(memory + kMediaStatusOffset, &status, 8);
    std::memcpy(memory + kMediaStatusOffset + 12,
                reinterpret_cast<const unsigned char*>(&status) + 12, sizeof(status) - 12);
    InterlockedExchange(slot, static_cast<LONG>(publication));
}
}

bool media_session_active() noexcept {
    const auto seen = session_seen.load(std::memory_order_acquire);
    return seen && GetTickCount64() - seen < 2000;
}

void media_worker_tick(const wchar_t* session_name, bool connected) noexcept {
    try {
        if (!connected || !session_name || !*session_name || wcsnlen(session_name, 256) == 256) {
            session_seen.store(0, std::memory_order_release);
            video::stop(false);
            reshade_set_enabled(false);
            close_mapping();
            return;
        }
        const auto now = GetTickCount64();
        session_seen.store(now, std::memory_order_release);
        if (!memory) {
            if (now < next_open)
                return;
            next_open = now + 100;
            mapping_name = std::wstring(session_name) + L".media";
            mapping = OpenFileMappingW(FILE_MAP_ALL_ACCESS, FALSE, mapping_name.c_str());
            if (!mapping)
                return;
            memory = static_cast<unsigned char*>(
                MapViewOfFile(mapping, FILE_MAP_ALL_ACCESS, 0, 0, kMediaMappingBytes));
            if (!memory) {
                CloseHandle(mapping);
                mapping = nullptr;
                return;
            }
        }
        const auto previous = accepted;
        consume();
        if (previous != accepted || now >= next_status) {
            next_status = now + 50;
            publish();
        }
    } catch (...) {
        session_seen.store(0, std::memory_order_release);
        video::stop(false);
        reshade_set_enabled(false);
        close_mapping();
    }
}
}
