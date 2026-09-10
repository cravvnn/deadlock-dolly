#include "dolly_video_math.hpp"
#include <array>
#include <cstdlib>
#include <cstdio>

void require(bool value) {
    if (!value) {
        std::fputs("Video math regression failed.\n", stderr);
        std::exit(1);
    }
}

int main() {
    using namespace dolly::video;
    Cadence cadence;
    cadence.frequency = 10000000;
    cadence.fps = 60;
    std::uint64_t pts = 99, missed = 99;
    require(cadence.sample(50000000, pts, missed) && pts == 0 && missed == 0);
    require(!cadence.sample(50050000, pts, missed));
    require(cadence.sample(50170000, pts, missed) && pts == 170000 && missed == 0);
    require(cadence.sample(51000000, pts, missed) && pts == 1000000 && missed == 4);
    require(!cadence.sample(49999999, pts, missed));
    require(!cadence.sample(51000000, pts, missed));
    require(clock_units(3600ULL * 3579545, 3579545, 10000000) == 36000000000ULL);
    require(clock_units(1, 0, 10000000) == 0);
    Cadence ntsc_clock;
    ntsc_clock.frequency = 3579545;
    ntsc_clock.fps = 30;
    require(ntsc_clock.sample(0, pts, missed));
    require(ntsc_clock.sample(3579545, pts, missed) && pts == 10000000 && missed == 29);
    // At 120 Hz the rational timestamp clock must not drift over one hour.
    // A 60 Hz game cannot supply 120 distinct frames: account for skipped slots.
    Cadence high_rate;
    high_rate.frequency = 12000000;
    high_rate.fps = 120;
    require(high_rate.sample(0, pts, missed));
    require(high_rate.sample(100000, pts, missed) && pts == 83333 && missed == 0);
    require(!high_rate.sample(100001, pts, missed));
    require(high_rate.sample(300000, pts, missed) && pts == 250000 && missed == 1);
    require(high_rate.sample(3600ULL * high_rate.frequency, pts, missed) && pts == 36000000000ULL &&
            high_rate.last_slot == 432000);
    // RGB ordering and row stride are independent of Windows' RGB DIB
    // conventions. Top is red, bottom blue; padding must never be sampled.
    const std::array<std::uint8_t, 24> rgba = {255, 0, 0,   255, 255, 0, 0,   255, 99, 99, 99, 99,
                                               0,   0, 255, 255, 0,   0, 255, 255, 99, 99, 99, 99};
    std::array<std::uint8_t, 6> nv12{};
    rgb_to_nv12(rgba.data(), 12, nv12.data(), 2, 2, true);
    require(nv12[0] == 63 && nv12[1] == 63);
    require(nv12[2] == 32 && nv12[3] == 32);
    const std::array<std::uint8_t, 16> bgra_red = {0, 0, 255, 255, 0, 0, 255, 255,
                                                   0, 0, 255, 255, 0, 0, 255, 255};
    rgb_to_nv12(bgra_red.data(), 8, nv12.data(), 2, 2, false);
    require(nv12[0] == 63 && nv12[4] == 102 && nv12[5] == 240);
    std::array<std::uint8_t, 16> solid{};
    rgb_to_nv12(solid.data(), 8, nv12.data(), 2, 2, true);
    require(nv12[0] == 16 && nv12[4] == 128 && nv12[5] == 128);
    solid.fill(255);
    rgb_to_nv12(solid.data(), 8, nv12.data(), 2, 2, true);
    require(nv12[0] == 235 && nv12[4] == 128 && nv12[5] == 128);
    for (unsigned i = 0; i < 4; ++i) {
        solid[i * 4] = 0;
        solid[i * 4 + 1] = 255;
        solid[i * 4 + 2] = 0;
    }
    rgb_to_nv12(solid.data(), 8, nv12.data(), 2, 2, true);
    require(nv12[0] == 173 && nv12[4] == 42 && nv12[5] == 26);
    std::puts("Video cadence, timestamps, color conversion and row orientation passed.");
}
