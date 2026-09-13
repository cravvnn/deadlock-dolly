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
        const fs::path directory = root / "good_depth";
        {
            Sequence sequence;
            require(sequence.begin(directory.c_str(), true, true) && sequence.write(raw, 0),
                    "First EXR failed");
            require(sequence.exr() && sequence.mov() && sequence.linear_count() == 4,
                    "Depth sequence accessors failed");
            raw.frame.sample = 9; // Nonconsecutive input samples still use color-frame file order.
            require(sequence.write(raw, 1234567) && sequence.finish(2),
                    "Sequence finalization failed");
            require(!fs::exists(directory / "exr" / "00000001.exr.part"), "Partial EXR remained");
        }
        const auto first = contents(directory / "exr" / "00000000.exr");
        require(first.size() > 200 && first.find("dollyCapturePTS100ns") != std::string::npos,
                "EXR capture timestamp missing");
        const auto manifest = contents(directory / "manifest.json");
        require(manifest.find("\"frames\": 2") != std::string::npos,
                "Manifest frame count missing");
        require(manifest.find("depth.mov") != std::string::npos && manifest.find("8192") != std::string::npos,
                "Depth master mapping missing");
        require(contents(directory / "preview_1x1.raw").size() == 2,
                "Normalized preview stream missing or mis-sized");
        {
            Sequence existing;
            require(!existing.begin(directory.c_str(), true, true), "Existing sequence accepted");
        }
        require(contents(directory / "exr" / "00000000.exr") == first, "Existing EXR changed");
        // MOV-only mode writes no EXR tree but keeps preview and manifest.
        const fs::path mov_only = root / "mov_only_depth";
        {
            Sequence sequence;
            require(sequence.begin(mov_only.c_str(), false, true) && sequence.write(raw, 0) &&
                        sequence.finish(1),
                    "MOV-only sequence failed");
        }
        require(!fs::exists(mov_only / "exr"), "MOV-only sequence created EXRs");
        require(contents(mov_only / "manifest.json").find("\"exr\": null") != std::string::npos,
                "MOV-only manifest claimed EXRs");
        const fs::path abandoned = root / "abandoned_depth";
        {
            Sequence sequence;
            require(sequence.begin(abandoned.c_str(), true, true) && sequence.write(raw, 0),
                    "Abandoned fixture failed");
        }
        require(!fs::exists(abandoned), "Unfinished output survived destructor");
        const fs::path bad = root / "mismatch_depth";
        {
            Sequence sequence;
            require(sequence.begin(bad.c_str(), true, false) && sequence.write(raw, 0),
                    "Mismatch fixture failed");
            require(!sequence.finish(2), "Mismatched color count accepted");
        }
        require(!fs::exists(bad), "Failed output not discarded");
        const fs::path conflict = root / "conflict_depth";
        {
            Sequence sequence;
            require(sequence.begin(conflict.c_str(), true, true), "Conflict fixture failed");
            {
                std::ofstream foreign(conflict / "exr" / "00000000.exr");
                foreign << "foreign";
            }
            require(!sequence.write(raw, 0), "Existing frame overwritten");
        }
        require(contents(conflict / "exr" / "00000000.exr") == "foreign", "Foreign frame deleted");
        require(!fs::exists(conflict / "exr" / "00000000.exr.part"),
                "Failed publish left owned partial file");
        const fs::path duplicate = root / "duplicate_depth";
        {
            Sequence sequence;
            require(sequence.begin(duplicate.c_str(), true, false) && sequence.write(raw, 0),
                    "Duplicate fixture failed");
            require(!sequence.write(raw, 333333), "Duplicate scene sample accepted");
        }
        // Delete only the specific trees this test owns.
        fs::remove_all(directory);
        fs::remove_all(mov_only);
        fs::remove_all(conflict);
        fs::remove_all(duplicate);
        fs::remove(root);
        std::puts(
            "Depth sequence publication, mov/exr modes, frame identity, collision preservation and cancellation passed.");
        return 0;
    } catch (const std::exception& e) {
        std::fprintf(stderr, "%s\n", e.what());
        return 1;
    }
}
