#include "dolly_depth.hpp"
#include <array>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
void require(bool condition, const char* message) {
    if (!condition)
        throw std::runtime_error(message);
}
void close_to(float actual, double expected, const char* message) {
    require(std::abs(actual - expected) <= std::abs(expected) * 1e-6, message);
}
std::uint32_t read32(const std::string& data, std::size_t at) {
    require(at + 4 <= data.size(), "Truncated EXR integer");
    std::uint32_t value = 0;
    for (unsigned i = 0; i < 4; ++i)
        value |= std::uint32_t(static_cast<unsigned char>(data[at + i])) << (8 * i);
    return value;
}
}

int main(int argc, char** argv) {
    try {
        using namespace dolly::depth;
        const Projection captured{{0, -1, 0.1428571492433548, 0}, 0, 1};
        require(captured.valid(), "Captured reverse projection rejected");
        float z = 0;
        require(captured.linearize(1, z), "Near clip conversion failed");
        close_to(z, 7, "Captured near clip differs from 7 game units");
        require(captured.linearize(.001, z), "Distant depth conversion failed");
        close_to(z, 7000, "Distant depth was clipped or normalized");
        require(captured.linearize(0, z) && std::isinf(z) && z > 0,
                "Infinite far plane must remain positive infinity");
        // Finite forward-Z projection with near=2, far=200.
        const Projection forward{{0, -400, -198, 200}, 0, 1};
        require(forward.linearize(0, z), "Forward near clip rejected");
        close_to(z, 2, "Forward near plane incorrect");
        require(forward.linearize(1, z), "Forward far clip rejected");
        close_to(z, 200, "Forward far plane incorrect");
        auto viewport = captured;
        viewport.viewport_min = .25;
        viewport.viewport_max = .75;
        require(viewport.linearize(.5, z), "Viewport depth normalization failed");
        close_to(z, 14, "Viewport depth range ignored");
        require(!viewport.linearize(.2, z), "Out-of-viewport depth accepted");
        require(!captured.linearize(std::nan(""), z), "NaN depth accepted");
        require(!captured.linearize(std::numeric_limits<double>::infinity(), z),
                "Infinite device depth accepted");
        require(!Projection{}.valid(), "Empty calibration accepted");
        require(!Projection{{0, -1, 1, -.5}, 0, 1}.valid(),
                "Projection with an interior pole accepted");
        require(!Projection{{0, 1, 1, 0}, 0, 1}.valid(), "Negative scene distance accepted");
        require(!Projection{{0, -1, 1, 0}, 1, 0}.valid(), "Reversed viewport range accepted");

        std::array<unsigned char, 640> per_view{};
        const auto put = [&per_view](unsigned offset, float value) {
            std::uint32_t bits = 0;
            std::memcpy(&bits, &value, 4);
            for (unsigned i = 0; i < 4; ++i)
                per_view[offset + i] = static_cast<unsigned char>(bits >> (i * 8));
        };
        for (unsigned offset : {128u, 148u, 168u, 188u, 192u, 212u, 372u, 436u})
            put(offset, 1);
        put(236, 7);
        put(248, -1);
        put(260, -1);
        put(264, 1.0f / 7);
        put(328, 17);
        put(332, 3);
        put(336, 1.0f / 17);
        put(340, 1.0f / 3);
        put(376, 7);
        put(380, std::numeric_limits<float>::infinity());
        put(456, -1);
        Projection parsed;
        require(read_per_view_projection(per_view.data(), per_view.size(), 17, 3, parsed),
                "Valid per-view layout rejected");
        require(!read_per_view_projection(per_view.data(), per_view.size(), 16, 3, parsed),
                "Projection for a different viewport accepted");
        const auto saved_per_view = per_view;
        for (unsigned offset : {128u, 140u, 188u, 224u, 236u, 264u, 328u, 336u, 376u, 436u, 456u}) {
            per_view = saved_per_view;
            put(offset, 123);
            require(!read_per_view_projection(per_view.data(), per_view.size(), 17, 3, parsed),
                    "Corrupt or unrelated per-view buffer accepted");
        }

        // D24 little-endian words. Stencil is deliberately nonzero and each
        // row has four poison padding bytes that must never become a pixel.
        const std::array<unsigned char, 24> packed = {255, 255, 255, 2,  0,  0,  128, 255,
                                                      71,  72,  73,  74, 0,  0,  0,   7,
                                                      0,   0,   64,  2,  71, 72, 73,  74};
        std::array<float, 4> decoded{};
        require(convert(packed.data(), packed.size(), 12, 2, 2, Format::d24s8, captured,
                        decoded.data(), decoded.size()),
                "Padded D24 decode failed");
        close_to(decoded[0], 7, "Stencil contaminated depth");
        close_to(decoded[1], 14, "First row order changed");
        require(std::isinf(decoded[2]), "Far plane lost in D24 decode");
        close_to(decoded[3], 28, "Second row padding sampled");
        require(!convert(packed.data(), 19, 12, 2, 2, Format::d24s8, captured, decoded.data(), 4),
                "Short source read accepted");
        require(!convert(packed.data(), packed.size(), 7, 2, 2, Format::d24s8, captured,
                         decoded.data(), 4),
                "Short pitch accepted");
        require(!convert(packed.data(), packed.size(), 12, 2, 2, Format::d24s8, captured,
                         decoded.data(), 3),
                "Short output accepted");
        require(!convert(packed.data(), packed.size(), std::numeric_limits<std::size_t>::max(), 2,
                         2, Format::d24s8, captured, decoded.data(), 4),
                "Overflowing row span accepted");
        const std::array<unsigned char, 8> d32 = {0, 0, 128, 63, 0, 0, 0, 63};
        require(convert(d32.data(), 8, 8, 2, 1, Format::d32_float, captured, decoded.data(), 4),
                "D32 float decode failed");
        close_to(decoded[1], 14, "D32 bits were interpreted as UNORM");
        const std::array<unsigned char, 4> nan32 = {0, 0, 192, 127};
        require(!convert(nan32.data(), 4, 4, 1, 1, Format::d32_float, captured, decoded.data(), 4),
                "NaN D32 sample accepted");

        const std::array<float, 6> master = {
            7, 14, 70000.125f, 1e20f, std::numeric_limits<float>::infinity(), 28};
        std::array<unsigned char, 6> gray{};
        require(preview(master.data(), master.size(), 7, 21, gray.data()), "Preview failed");
        require(gray[0] == 255 && gray[1] == 128 && gray[2] == 0 && gray[4] == 0,
                "Preview range mapping incorrect");
        require(master[2] == 70000.125f && master[3] == 1e20f,
                "Preview altered the lossless master");
        require(!preview(master.data(), master.size(), 21, 7, gray.data()),
                "Invalid preview range accepted");

        Frame frame{3, 2, 9007199254740993ULL, 123.125, captured};
        std::ostringstream output(std::ios::out | std::ios::binary);
        require(write_exr(output, frame, master.data(), master.size()), "EXR write failed");
        const auto bytes = output.str();
        require(read32(bytes, 0) == 20000630 && read32(bytes, 4) == 2, "Invalid EXR magic/version");
        std::size_t at = 8;
        bool channel = false, sample = false;
        while (bytes.at(at)) {
            const auto end = bytes.find('\0', at);
            const auto name = bytes.substr(at, end - at);
            at = bytes.find('\0', end + 1) + 1;
            const auto size = read32(bytes, at);
            at += 4;
            if (name == "channels") {
                channel = true;
                require(bytes.substr(at, 2) == std::string("Z", 2) && read32(bytes, at + 2) == 2,
                        "Depth is not a 32-bit float Z channel");
            }
            if (name == "dollySample") {
                sample = true;
                require(bytes.substr(at, size) == "9007199254740993", "Sample ID lost precision");
            }
            at += size;
        }
        require(channel && sample, "Depth metadata missing");
        ++at;
        for (unsigned y = 0; y < 2; ++y) {
            const auto offset = std::uint64_t(read32(bytes, at + y * 8)) |
                                (std::uint64_t(read32(bytes, at + y * 8 + 4)) << 32);
            require(read32(bytes, offset) == y && read32(bytes, offset + 4) == 12,
                    "EXR scanline table points to wrong data");
            for (unsigned x = 0; x < 3; ++x) {
                const auto bits = read32(bytes, offset + 8 + x * 4);
                std::uint32_t expected = 0;
                std::memcpy(&expected, &master[y * 3 + x], 4);
                require(bits == expected, "EXR changed a float sample");
            }
        }
        std::ostringstream invalid;
        require(!write_exr(invalid, frame, master.data(), 5) && invalid.str().empty(),
                "Invalid frame wrote a partial header");
        std::ostringstream failed;
        failed.setstate(std::ios::badbit);
        require(!write_exr(failed, frame, master.data(), master.size()), "Stream failure ignored");
        if (argc == 2) {
            std::ofstream fixture(argv[1], std::ios::out | std::ios::binary);
            require(write_exr(fixture, frame, master.data(), master.size()),
                    "Fixture write failed");
        }
        if (argc == 3 && std::string(argv[1]) == "--per-view") {
            std::ifstream capture(argv[2], std::ios::binary);
            const std::vector<char> raw((std::istreambuf_iterator<char>(capture)), {});
            require(read_per_view_projection(raw.data(), raw.size(), 2560, 1440, parsed),
                    "Captured per-view buffer rejected");
            require(parsed.linearize(.5, z), "Captured calibration could not convert depth");
            close_to(z, 14, "Captured calibration changed");
        }
        std::puts("Depth projection, row pitch, preview and float EXR tests passed.");
        return 0;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "%s\n", error.what());
        return 1;
    }
}
