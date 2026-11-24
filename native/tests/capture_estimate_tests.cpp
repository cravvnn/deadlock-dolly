// Export drawer estimate and progress text. The cases mirror SHARED_FRAMES,
// SHARED_DURATIONS and SHARED_LINES in tests/test_capture_estimate.py so the
// in-game drawer and the desktop show the same words for the same take.
#include "dolly_capture_estimate.hpp"
#include <cstdio>
#include <cstdlib>
#include <limits>
#include <string>

using namespace dolly::estimate;

static void require(bool condition, const char* what) {
    if (!condition) {
        std::fprintf(stderr, "capture estimate check failed: %s\n", what);
        std::exit(1);
    }
}
static void same(const std::string& actual, const char* expected) {
    if (actual != expected) {
        std::fprintf(stderr, "capture estimate text: got \"%s\", want \"%s\"\n", actual.c_str(),
                     expected);
        std::exit(1);
    }
}

int main() {
    // SHARED_FRAMES
    require(take_frames(6.55, 600, .25, true) == 15720, "600 FPS at 0.25x");
    require(take_frames(6.55, 600, .25, false) == 3930, "real time ignores speed");
    require(take_frames(6.55, 600, 1, true) == 3930, "600 FPS at 1x");
    require(take_frames(10, 60, 1, true) == 600, "POV 10 s at 60 FPS");
    require(take_frames(1.0 / 3.0, 30, 1, true) == 10, "no off-by-one from rounding");
    require(take_frames(0, 60, 1, true) == 0, "unknown length");
    require(take_frames(5, 0, 1, true) == 0, "no FPS");
    require(take_frames(5, 60, 0, true) == 0, "no speed");
    require(take_frames(std::numeric_limits<double>::quiet_NaN(), 60, 1, true) == 0, "NaN");
    // SHARED_DURATIONS
    const struct {
        double seconds;
        const char* text;
    } durations[]{{0, "5 s"},       {3, "5 s"},      {42, "40 s"},     {42.5, "45 s"},
                  {90, "2 min"},    {54, "55 s"},    {55, "1 min"},    {1441, "24 min"},
                  {3570, "1 h"},    {3600, "1 h"},   {3900, "1 h 5 min"}};
    for (const auto& c : durations)
        same(format_duration(c.seconds), c.text);
    // SHARED_LINES (the middle dot is UTF-8 U+00B7, as in the Python strings)
    same(estimate_line(15720, 26.2, 0), "15,720 frames \xC2\xB7 26.2 s of video");
    same(estimate_line(15720, 26.2, 11.0),
         "15,720 frames \xC2\xB7 26.2 s of video \xC2\xB7 about 24 min to record");
    same(estimate_line(600, 10, 120.0),
         "600 frames \xC2\xB7 10.0 s of video \xC2\xB7 about 5 s to record");
    same(estimate_line(15720, 26.2, 11.0, true), "15,720 frames per pass \xC2\xB7 26.2 s of video");
    same(progress_line(1810, 15720, 1264.5), "1,810 / 15,720 frames \xC2\xB7 about 21 min left");
    same(progress_line(1810, 15720, -1), "1,810 / 15,720 frames \xC2\xB7 estimating time left");
    same(progress_line(15725, 15720, 0), "15,725 / 15,725 frames \xC2\xB7 almost done");
    same(progress_line(15000, 15720, 4.9), "15,000 / 15,720 frames \xC2\xB7 almost done");
    same(progress_line(15000, 15720, 5), "15,000 / 15,720 frames \xC2\xB7 about 5 s left");
    same(progress_line(1810, 0, -1), "1,810 frames");
    // Rates need enough of the take; elapsed reads two QPC values.
    require(capture_rate(29, 100) == 0 && capture_rate(1000, 4.9) == 0, "rate threshold");
    require(capture_rate(55, 5) == 11, "rate");
    require(seconds_left(10, 100, 0) < 0 && seconds_left(10, 100, 9) == 10 &&
                seconds_left(200, 100, 9) == 0,
            "seconds left");
    require(elapsed(0, 50, 10) == 0 && elapsed(10, 50, 0) == 0 && elapsed(50, 10, 10) == 0,
            "elapsed unknown");
    require(elapsed(20000000, 125000000, 10000000) == 10.5, "elapsed");
    require(rate_kind(false, true) == 0 && rate_kind(true, false) == 1 && rate_kind(true, true) == 2,
            "rate kinds");
    std::puts("Capture estimate and progress text passed.");
    return 0;
}
