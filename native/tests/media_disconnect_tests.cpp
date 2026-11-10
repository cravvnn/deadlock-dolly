#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <iostream>
#include <stdexcept>
#include "dolly_media.hpp"
#include "dolly_editor.hpp"
#include "dolly_video.hpp"
#include "dolly_reshade.hpp"
#include "dolly_sound_capture.hpp"
static ULONGLONG clock_now = 1000;
static ULONGLONG review_clock() { return clock_now; }
#define GetTickCount64 review_clock
#include "../src/dolly_media_win.cpp"
#undef GetTickCount64
static dolly::video::Status video_state;
static bool audio_active = true;
static unsigned audio_stops = 0, video_stops = 0, reshade_stops = 0;
namespace dolly {
namespace video {
bool start(const Options&) noexcept { return true; }
void stop(bool) noexcept { ++video_stops; video_state.state = State::completed; }
Status status() noexcept { return video_state; }
}
EditorSnapshot editor_snapshot() noexcept { return {}; }
bool reshade_initialize_async(const wchar_t*, const wchar_t*) noexcept { return true; }
void reshade_set_enabled(bool enabled) noexcept { if (!enabled) ++reshade_stops; }
bool reshade_request_overlay(bool) noexcept { return true; }
bool reshade_overlay_open() noexcept { return false; }
bool reshade_overlay_pending() noexcept { return false; }
ReShadeStatus reshade_status() noexcept { return {}; }
bool sound_capture_start(const wchar_t*) noexcept { audio_active = true; return true; }
void sound_capture_stop() noexcept { audio_active = false; ++audio_stops; }
void sound_capture_worker_tick() noexcept {}
const char* sound_capture_error() noexcept { return ""; }
bool sound_silence_ui_voices(unsigned) noexcept { return true; }
}
static void require(bool good, const char* message) { if (!good) throw std::runtime_error(message); }
static void reset(dolly::video::State state) {
    dolly::close_mapping();
    dolly::disconnected_since.store(0);
    dolly::session_seen.store(0);
    audio_active = true;
    audio_stops = video_stops = reshade_stops = 0;
    video_state = {};
    video_state.state = state;
    clock_now = 1000;
}
int main(int argc, char** argv) {
    try {
        const std::string selected = argc > 1 ? argv[1] : "all";
        if (selected == "all" || selected == "idle") {
        reset(dolly::video::State::idle);
        dolly::media_worker_tick(L"owned-disconnected-session", false);
        require(!audio_active && audio_stops == 1, "idle source capture not stopped");
        require(video_stops == 1 && reshade_stops == 1, "other cleanup skipped");
        require(!dolly::media_session_active(), "disconnected media still active");
        }
        if (selected == "all" || selected == "grace") {
        for (auto state : {dolly::video::State::starting, dolly::video::State::recording,
                           dolly::video::State::finalizing}) {
            reset(state);
            dolly::media_worker_tick(L"owned-disconnected-session", false);
            require(audio_active && !audio_stops && !video_stops && !reshade_stops,
                    "recording grace stopped media prematurely");
            clock_now = 30999;
            dolly::media_worker_tick(L"owned-disconnected-session", false);
            require(audio_active && !audio_stops && !video_stops && !reshade_stops,
                    "grace boundary stopped media prematurely");
            clock_now = 31000;
            dolly::media_worker_tick(L"owned-disconnected-session", false);
            require(!audio_active && audio_stops == 1 && video_stops == 1 && reshade_stops == 1,
                    "grace expiry skipped source capture cleanup");
            require(!dolly::media_session_active(), "expired session still active");
        }
        }
        if (selected == "all" || selected == "unnamed") {
        reset(dolly::video::State::idle);
        dolly::media_worker_tick(nullptr, false);
        require(!audio_active && audio_stops == 1, "unnamed shutdown changed");
        }
        std::cout << "PASS actual media owner: idle/3 active grace states/expiry/unnamed shutdown\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
