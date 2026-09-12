#include "dolly_depth_sequence.hpp"
#include <windows.h>
#include <filesystem>
#include <fstream>
#include <string>
#include <cstring>
#include <stdexcept>
#include <cstdio>

namespace {
void require(bool value, const char* message) {
    if (!value)
        throw std::runtime_error(message);
}
std::string contents(const std::filesystem::path& path) {
    std::ifstream file(path, std::ios::binary);
    return {std::istreambuf_iterator<char>(file), {}};
}
}
int main() {
    using namespace dolly::depth;
    namespace fs = std::filesystem;
    try {
        const auto root = fs::temp_directory_path() /
                          (L"DollyDepthSequence-" + std::to_wstring(GetCurrentProcessId()));
        require(fs::create_directory(root), "Test directory already exists");
        RawFrame raw;
        raw.frame = {2, 2, 0, 1.25, {{0, -1, 1.0 / 7, 0}, 0, 1}};
        raw.format = Format::d32_float;
        raw.pixels.resize(16);
        const float values[]{1, .5f, .25f, 0};
        std::memcpy(raw.pixels.data(), values, sizeof(values));
        const auto normal = root / "good.mp4";
        {
            Sequence sequence;
            require(sequence.begin(normal.c_str()) && sequence.write(raw, 0), "First EXR failed");
            raw.frame.sample = 9; // Nonconsecutive input samples still use color-frame file order.
            require(sequence.write(raw, 1234567) && sequence.finish(2),
                    "Sequence finalization failed");
            require(!fs::exists(normal.wstring() + L".depth/00000001.exr.part"),
                    "Partial EXR remained");
        }
        const fs::path directory = normal.wstring() + L".depth";
        const auto first = contents(directory / "00000000.exr");
        require(first.size() > 200 && first.find("dollyCapturePTS100ns") != std::string::npos,
                "EXR capture timestamp missing");
        require(contents(directory / "manifest.json").find("\"frames\": 2") != std::string::npos,
                "Manifest frame count missing");
        {
            Sequence existing;
            require(!existing.begin(normal.c_str()), "Existing sequence accepted");
        }
        require(contents(directory / "00000000.exr") == first, "Existing EXR changed");
        const auto abandoned = root / "abandoned.mp4";
        {
            Sequence sequence;
            require(sequence.begin(abandoned.c_str()) && sequence.write(raw, 0),
                    "Abandoned fixture failed");
        }
        require(!fs::exists(abandoned.wstring() + L".depth"),
                "Unfinished output survived destructor");
        const auto bad = root / "mismatch.mp4";
        {
            Sequence sequence;
            require(sequence.begin(bad.c_str()) && sequence.write(raw, 0),
                    "Mismatch fixture failed");
            require(!sequence.finish(2), "Mismatched color count accepted");
        }
        require(!fs::exists(bad.wstring() + L".depth"), "Failed output not discarded");
        const auto conflict = root / "conflict.mp4";
        const fs::path conflict_dir = conflict.wstring() + L".depth";
        {
            Sequence sequence;
            require(sequence.begin(conflict.c_str()), "Conflict fixture failed");
            {
                std::ofstream foreign(conflict_dir / "00000000.exr");
                foreign << "foreign";
            }
            require(!sequence.write(raw, 0), "Existing frame overwritten");
        }
        require(contents(conflict_dir / "00000000.exr") == "foreign", "Foreign frame deleted");
        require(!fs::exists(conflict_dir / "00000000.exr.part"),
                "Failed publish left owned partial file");
        const auto duplicate = root / "duplicate.mp4";
        {
            Sequence sequence;
            require(sequence.begin(duplicate.c_str()) && sequence.write(raw, 0),
                    "Duplicate fixture failed");
            require(!sequence.write(raw, 333333), "Duplicate scene sample accepted");
        }
        // Delete only the specific files and empty directories this test owns.
        fs::remove(directory / "00000000.exr");
        fs::remove(directory / "00000001.exr");
        fs::remove(directory / "manifest.json");
        fs::remove(directory);
        fs::remove(conflict_dir / "00000000.exr");
        fs::remove(conflict_dir);
        fs::remove(root);
        std::puts(
            "Depth sequence publication, frame identity, collision preservation and cancellation passed.");
        return 0;
    } catch (const std::exception& e) {
        std::fprintf(stderr, "%s\n", e.what());
        return 1;
    }
}
