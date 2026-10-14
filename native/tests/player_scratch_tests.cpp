#include <windows.h>
#include "dolly_player_scratch.hpp"
#include "dolly_player_sequence_writer.hpp"
#include <filesystem>
#include <iostream>
#include <chrono>

#define CHECK(x) do { if (!(x)) { std::cerr << "Failed line " << __LINE__ << '\n'; return 1; } } while (0)
int main() {
    namespace fs = std::filesystem;
    using namespace dolly::player_capture;
    const std::string request(32, 'a');
    auto root = fs::temp_directory_path() / ("dolly-scratch-test-" + std::to_string(GetCurrentProcessId()));
    CHECK(!fs::exists(root));
    CHECK(fs::create_directory(root));
    CHECK(fs::weakly_canonical(root).parent_path() == fs::weakly_canonical(fs::temp_directory_path()));
    auto folder = root / fs::path(L"Unicode-\u03b1 space") / ("dolly-players-" + request);
    fs::create_directories(folder);
    struct Cleanup { fs::path root; ~Cleanup(){ std::error_code error; fs::remove_all(root,error); } } cleanup{root};
    std::ofstream(folder / ".dolly-players-owner") << request;
    auto utf8 = folder.u8string();
    std::string hex;
    for (unsigned char c : utf8) { hex += "0123456789abcdef"[c>>4]; hex += "0123456789abcdef"[c&15]; }
    std::wstring path;
    CHECK(scratch_directory(hex,request,path));
    CHECK(!scratch_directory(hex,std::string(32,'b'),path));
    CHECK(!scratch_directory("00",request,path));
    CHECK(!scratch_directory("ff",request,path));
    CHECK(!scratch_directory(hex+"zz",request,path));
    auto bundle = folder / "dolly_owner_color_frame.bin";
    SequenceWriter writer;
    writer.synchronous_for_tests(true);
    CHECK(writer.open(bundle.wstring(),2,true));
    std::uint64_t header[8]={1,2,3,4,5,6,7,8};
    SequenceWriter::Buffer pixels(8,42);
    CHECK(writer.push(header,std::move(pixels)));
    CHECK(writer.finish());
    CHECK(fs::file_size(bundle)==72);
    CHECK(!scratch_directory(hex,request,path));
    // Reproduce a slow destination with a held worker and a full bounded queue.
    // A failed handoff must not empty the caller's completed readback: the next
    // render poll can still inspect it while capture failure is being drained.
    for (bool exclusive : {false, true}) {
        SequenceWriter slow;
        slow.hold_worker_for_tests(true);
        auto output = root / (exclusive ? "slow-exclusive.bin" : "slow-stream.bin");
        CHECK(slow.open(output.wstring(), 2, exclusive));
        SequenceWriter::Buffer first(8, 17);
        CHECK(slow.push(header, std::move(first)));
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
        while (!slow.in_flight() && std::chrono::steady_clock::now() < deadline)
            std::this_thread::yield();
        CHECK(slow.in_flight() == 1);
        for (unsigned n = 0; n < 2; ++n) {
            SequenceWriter::Buffer queued(8, 29);
            CHECK(slow.push(header, std::move(queued)));
        }
        SequenceWriter::Buffer rejected(8, 107);
        const auto* retained = rejected.data();
        CHECK(!slow.push(header, std::move(rejected)));
        CHECK(rejected.size() == 8 && rejected.data() == retained);
        CHECK(rejected == SequenceWriter::Buffer(8, 107));
        CHECK(slow.refused() == 1);
        CHECK(slow.finish()); // Releases the held worker and drains accepted frames.
        CHECK(slow.written() == 3 && fs::file_size(output) == 3 * 72);
        CHECK(!slow.push(header, std::move(rejected)));
        CHECK(rejected == SequenceWriter::Buffer(8, 107));
    }
    SequenceWriter collision;
    CHECK(!collision.open(bundle.wstring(),2,true));
    CHECK(fs::file_size(bundle)==72);
    fs::remove(bundle);
    CHECK(scratch_directory(hex,request,path));
    std::ofstream(folder / ".dolly-players-owner",std::ios::app) << 'x';
    CHECK(!scratch_directory(hex,request,path));
    std::cout << "Players scratch ownership and exclusive writer passed\n";
}
