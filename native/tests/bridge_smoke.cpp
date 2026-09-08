// Exercise the actual view callback against synthetic Windows memory. No game
// module, hook installation, injection or private game data is used by this test.
// Including the implementation keeps this gate on the production callback, not
// a second model of its state machine that could pass while the DLL is broken.
#include "../src/bridge_win.cpp"

#include <iostream>
#include <stdexcept>

namespace smoke {

void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

void close_to(double actual, double expected, const char* message, double tolerance = 1e-4) {
    require(std::isfinite(actual) && std::abs(actual - expected) <= tolerance, message);
}

struct Allocation {
    void* pointer;
    explicit Allocation(std::size_t bytes)
        : pointer(VirtualAlloc(nullptr, bytes, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE)) {
        require(pointer != nullptr, "Could not allocate synthetic native test memory");
    }
    ~Allocation() { if (pointer) VirtualFree(pointer, 0, MEM_RELEASE); }
    Allocation(const Allocation&) = delete;
    Allocation& operator=(const Allocation&) = delete;
    std::uintptr_t address() const { return reinterpret_cast<std::uintptr_t>(pointer); }
};

template<typename T> void put(std::uintptr_t address, const T& value) {
    std::memcpy(reinterpret_cast<void*>(address), &value, sizeof(value));
}

bool playing = true, paused = true, seeking = false, active = true;
int tick = 4096;
char demo_name[512] = "replays/native-smoke.dem";
bool __fastcall is_playing(void*) { return playing; }
bool __fastcall is_paused(void*) { return paused; }
bool __fastcall is_seeking(void*) { return seeking; }
bool __fastcall is_active(void*) { return active; }
int __fastcall current_tick(void*) { return tick; }
const char* __fastcall current_name(void*) { return demo_name; }

template<typename Function>
void thunk(std::uintptr_t address, Function function) {
    // mov rax, imm64; jmp rax. The synthetic module keeps the production
    // helper addresses unchanged while redirecting calls to harmless fixtures.
    unsigned char bytes[12] = {0x48, 0xb8, 0, 0, 0, 0, 0, 0, 0, 0, 0xff, 0xe0};
    const auto target = reinterpret_cast<std::uintptr_t>(function);
    std::memcpy(bytes + 2, &target, sizeof(target));
    DWORD previous = 0;
    require(VirtualProtect(reinterpret_cast<void*>(address), sizeof(bytes), PAGE_READWRITE, &previous) != 0,
            "Could not make a synthetic helper writable");
    std::memcpy(reinterpret_cast<void*>(address), bytes, sizeof(bytes));
    require(VirtualProtect(reinterpret_cast<void*>(address), sizeof(bytes), PAGE_EXECUTE_READ, &previous) != 0,
            "Could not make a synthetic helper executable");
    require(FlushInstructionCache(GetCurrentProcess(), reinterpret_cast<void*>(address), sizeof(bytes)) != 0,
            "Could not flush synthetic helper instructions");
}

void u32(std::vector<unsigned char>& bytes, std::uint32_t value) {
    for (unsigned i = 0; i < 4; ++i) bytes.push_back(static_cast<unsigned char>(value >> (8 * i)));
}

void number(std::vector<unsigned char>& bytes, double value) {
    std::uint64_t bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));
    for (unsigned i = 0; i < 8; ++i) bytes.push_back(static_cast<unsigned char>(bits >> (8 * i)));
}

const CameraPose first = {100, 200, 300, -15, 175, 0, 16.0 / 9};
const CameraPose last = {120, 240, 310, -25, 205, 20, 4.0 / 3};

std::shared_ptr<const NativePath> make_path() {
    const char magic[8] = {'D', 'L', 'Y', 'P', 'A', 'T', 'H', 0};
    std::vector<unsigned char> bytes(magic, magic + 8);
    u32(bytes, 1); u32(bytes, 1); u32(bytes, 7); u32(bytes, 0);
    number(bytes, 1); number(bytes, 0); number(bytes, 1);
    for (double value : first) number(bytes, value);
    for (double value : last) number(bytes, value);
    number(bytes, 0); number(bytes, 1);
    for (std::size_t i = 0; i < first.size(); ++i) {
        u32(bytes, 1); u32(bytes, 0);
        number(bytes, first[i]); number(bytes, last[i]);
        number(bytes, 0); number(bytes, 0);
    }
    auto path = std::make_shared<NativePath>();
    std::string error;
    require(path->load(bytes.data(), bytes.size(), error), "Synthetic path must parse through NativePath.load");
    return path;
}

struct Fixture {
    Allocation client{63733760}, engine{0x969000}, view{0x2000};
    Allocation demo{4096}, engine_client{4096}, globals{4096};
    std::vector<unsigned char> mapping = std::vector<unsigned char>(kMappingBytes);
    std::shared_ptr<const NativePath> path = make_path();
    std::uint32_t next_command = 0;
    const float original_fov = 90, original_aspect = 16.0f / 9;

    Fixture() {
        require(QueryPerformanceFrequency(&gFrequency) != 0, "QPC frequency unavailable");
        gClient = client.address(); gEngine = engine.address(); gMemory = mapping.data();
        put(gClient + kEngineClient, engine_client.address());
        put(gClient + kGlobals, globals.address());
        put(gEngine + kDemoGlobal, demo.address());
        put(demo.address(), gEngine + kDemoTable);
        put(engine_client.address(), gEngine + kEngineTable);
        thunk(gEngine + 0x2ec00, is_playing);
        thunk(gEngine + 0x7bf30, is_active);
        thunk(gEngine + 0x368d0, is_paused);
        thunk(gEngine + 0x2a240, is_seeking);
        thunk(gEngine + 0x36910, current_tick);
        thunk(gEngine + 0x7bf80, current_name);
        put(globals.address() + 0x54, 1.0f / 64);
        clock(64, 4096);
        gWorkerError = 0;
        gHeartbeatTime = now_seconds();
        original_view();
    }

    ~Fixture() {
        std::atomic_store(&gCommand, std::shared_ptr<const Command>());
        gMemory = nullptr; gClient = 0; gEngine = 0;
    }

    void clock(float time, int at_tick) {
        put(globals.address() + 0x30, time);
        tick = at_tick;
    }

    void original_view(unsigned char flags = 0) {
        put(view.address(), gClient + kViewTable);
        const auto camera = view.address() + 0x10;
        const float xyz[3] = {10, 20, 30}, angles[3] = {1, 2, 3};
        std::memcpy(reinterpret_cast<void*>(camera + 0x4a0), xyz, sizeof(xyz));
        std::memcpy(reinterpret_cast<void*>(camera + 0x4b8), angles, sizeof(angles));
        put(camera + 0x498, original_fov); put(camera + 0x4d8, original_aspect);
        put(camera + 0x430, 1280); put(camera + 0x438, 720); put(camera + 0x555, flags);
    }

    std::uint32_t command(Mode mode, double phase = 0, std::uint32_t flags = kAspect) {
        auto value = std::make_shared<Command>();
        value->wire.command = ++next_command;
        value->wire.mode = static_cast<std::uint32_t>(mode);
        value->wire.start_phase = phase; value->wire.speed = .1;
        value->wire.flags = flags; value->path = path;
        std::snprintf(value->wire.demo_name, sizeof(value->wire.demo_name), "native-smoke.dem");
        std::atomic_store(&gCommand, std::shared_ptr<const Command>(value));
        return next_command;
    }

    Status status() const {
        Status result{};
        std::memcpy(&result, mapping.data() + kControlBytes, sizeof(result));
        require(std::memcmp(result.magic, kStatusMagic, 8) == 0 && result.abi == kBridgeAbi,
                "Actual callback did not publish valid bridge status");
        require(!(result.sequence & 1), "Published status must have a complete even sequence");
        return result;
    }

    Status frame(unsigned char projection_flags = 0, bool renew_lease = true) {
        original_view(projection_flags);
        if (renew_lease) gHeartbeatTime = now_seconds();
        on_view(view.pointer, gClient + kCaller);
        return status();
    }

    void unchanged() const {
        const auto camera = view.address() + 0x10;
        float xyz[3]{}, angles[3]{}, fov = 0, aspect = 0;
        require(read_memory(camera + 0x4a0, xyz, sizeof(xyz)) &&
                read_memory(camera + 0x4b8, angles, sizeof(angles)) &&
                read_value(camera + 0x498, fov) && read_value(camera + 0x4d8, aspect),
                "Cannot read synthetic view");
        for (int i = 0; i < 3; ++i) {
            close_to(xyz[i], double((i + 1) * 10), "Released camera overwrote original position");
            close_to(angles[i], double(i + 1), "Released camera overwrote original angles");
        }
        close_to(fov, original_fov, "Released camera overwrote FOV");
        close_to(aspect, original_aspect, "Released camera overwrote aspect");
    }

    void applied(double phase, bool aspect_enabled = true) const {
        CameraPose expected{};
        require(path->evaluate(phase, expected), "Cannot evaluate expected camera");
        const auto camera = view.address() + 0x10;
        float xyz[3]{}, angles[3]{}, fov = 0, aspect = 0;
        require(read_memory(camera + 0x4a0, xyz, sizeof(xyz)) &&
                read_memory(camera + 0x4b8, angles, sizeof(angles)) &&
                read_value(camera + 0x498, fov) && read_value(camera + 0x4d8, aspect),
                "Cannot read applied synthetic view");
        for (int i = 0; i < 3; ++i) {
            close_to(xyz[i], expected[i], "Native callback did not write path XYZ");
            close_to(angles[i], std::remainder(expected[i + 3], 360.0),
                     "Native callback did not write wrapped path rotation");
        }
        constexpr double pi = 3.14159265358979323846;
        const double expected_fov = aspect_enabled
            ? 360 / pi * std::atan(std::tan(original_fov * pi / 360) * expected[6] / original_aspect)
            : original_fov;
        close_to(fov, expected_fov, "Aspect/FOV transform differs from actual view lens");
        close_to(aspect, aspect_enabled ? expected[6] : original_aspect,
                 "Native aspect flag was not respected");
    }

    void fresh_hold(double phase = 0) {
        playing = true; paused = true; seeking = false; active = true;
        std::snprintf(demo_name, sizeof(demo_name), "replays/native-smoke.dem");
        clock(64, 4096); gWorkerError = 0;
        command(Mode::Hold, phase);
        const auto value = frame();
        require(value.state == static_cast<unsigned>(State::Armed) && !value.error,
                "New HOLD must clear previous fault and arm on an actual view");
        applied(phase);
    }
};

void run() {
    Fixture f;
    auto status = f.frame();
    require(status.state == static_cast<unsigned>(State::Probe), "No command must leave the hook in probe mode");
    f.unchanged();

    // Calling another view/caller must neither take camera ownership nor
    // acknowledge a command that never reached the verified main-view site.
    f.command(Mode::Hold, .25);
    f.original_view();
    on_view(f.view.pointer, gClient + kCaller + 1);
    require(f.status().frame_count == status.frame_count, "Unverified caller updated native status");
    f.unchanged();
    status = f.frame();
    require(status.ack_command == f.next_command && status.state == static_cast<unsigned>(State::Armed),
            "HOLD must acknowledge from the actual verified callback");
    f.applied(.25);

    const auto play_id = f.command(Mode::Play, 0);
    status = f.frame();
    require(status.ack_command == play_id && status.paused && !status.error,
            "PLAY must acknowledge while waiting for demo_resume");
    close_to(status.phase, .25, "PLAY while paused must retain the held camera");
    f.applied(.25);

    // Eight interpolated engine times inside one tick must produce eight
    // distinct view positions; this catches a return to tick-only stepping.
    paused = false;
    double previous_x = status.applied_pose[0];
    for (int frame = 1; frame <= 8; ++frame) {
        f.clock(64 + frame / 1024.0f, 4096);
        status = f.frame();
        close_to(status.phase, .25 + frame / 1024.0, "Main-view engine time was not evaluated continuously");
        require(status.applied_pose[0] > previous_x && !status.error, "Subtick camera position repeated or went backwards");
        f.applied(status.phase);
        previous_x = status.applied_pose[0];
    }

    const auto held_phase = status.phase;
    f.command(Mode::HoldCurrent, 0);
    status = f.frame();
    close_to(status.phase, held_phase, "HoldCurrent changed the displayed phase");
    f.clock(64.0625f, 4100); status = f.frame();
    close_to(status.phase, held_phase, "HoldCurrent followed a moving replay clock");
    f.command(Mode::Play, 0); status = f.frame();
    close_to(status.phase, held_phase, "Resuming a held camera jumped to command start_phase");
    f.clock(64.125f, 4104); status = f.frame();
    close_to(status.phase, held_phase + .0625, "Resume did not advance from held phase");
    f.applied(status.phase);

    f.fresh_hold(.5);
    f.applied(.5); // Non-native aspect must alter FOV as well as view aspect.
    f.command(Mode::Hold, .5, 0); f.frame(); f.applied(.5, false);

    f.fresh_hold();
    f.command(Mode::Play); f.frame(); paused = false;
    for (int quarter = 1; quarter <= 4; ++quarter) {
        f.clock(64 + quarter / 4.0f, 4096 + quarter * 16);
        status = f.frame();
        require(!status.error, "Ordinary native progression faulted before endpoint");
    }
    require(status.state == static_cast<unsigned>(State::Completed), "Exact endpoint was not marked completed");
    close_to(status.phase, 1, "Final phase is not exact", 0);
    f.applied(1);
    f.clock(65.125f, 4168); status = f.frame();
    close_to(status.phase, 1, "Completed camera did not keep final pose", 0); f.applied(1);

    f.fresh_hold();
    std::snprintf(demo_name, sizeof(demo_name), "another-demo.dem");
    status = f.frame();
    require(status.state == static_cast<unsigned>(State::Fault) && status.error == 12,
            "Changing demos must fault before writing the camera");
    f.unchanged();
    f.command(Mode::Release); status = f.frame();
    require(status.state == static_cast<unsigned>(State::Stopped) && !status.error,
            "Release after a fault must acknowledge and relinquish camera ownership");
    f.unchanged();

    f.fresh_hold(); gHeartbeatTime = now_seconds() - 3;
    status = f.frame(0, false);
    require(status.error == 10, "Expired editor lease must release the view"); f.unchanged();
    f.command(Mode::Release); status = f.frame(0, false);
    require(status.state == static_cast<unsigned>(State::Stopped), "Expired lease prevented explicit release");
    f.unchanged();

    f.fresh_hold(); f.command(Mode::Play); f.frame(); paused = false;
    f.clock(70, 4480); status = f.frame();
    require(status.error == 15, "A large replay time jump must fault before evaluating a distant camera");
    f.unchanged();

    f.fresh_hold(); status = f.frame(2);
    require(status.error == 11, "Alternate projection must be rejected"); f.unchanged();
    f.fresh_hold(); seeking = true; status = f.frame();
    require(status.error == 12, "Seeking replay must release native camera ownership"); f.unchanged();

    f.fresh_hold(.25); f.command(Mode::Play, 0, kFrozen | kAspect);
    status = f.frame(); const double frozen_start = status.phase;
    Sleep(10); status = f.frame();
    require(status.phase > frozen_start && status.paused && !status.error,
            "Frozen native playback must advance from real time while replay remains paused");
    f.applied(status.phase);
    f.clock(64, 4097); status = f.frame();
    require(status.error == 14, "Moving frozen replay must release the native camera"); f.unchanged();

    std::cout << "Actual native callback smoke tests passed (synthetic Windows memory; no game runtime claim)\n";
}

} // namespace smoke

int main() {
    try { smoke::run(); return 0; }
    catch (const std::exception& error) {
        std::cerr << "Native callback smoke test failed: " << error.what() << '\n';
        return 1;
    }
}
