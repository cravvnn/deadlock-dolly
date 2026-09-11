// Exercise the actual view callback against synthetic Windows memory. No game
// module, hook installation, injection or private game data is used by this test.
// Including the implementation keeps this gate on the production callback, not
// a second model of its state machine that could pass while the DLL is broken.
#include "../src/bridge_win.cpp"
#include "../src/dolly_editor_win.cpp"

#include <iostream>
#include <limits>
#include <stdexcept>

namespace smoke {

void require(bool condition, const char* message) {
    if (!condition)
        throw std::runtime_error(message);
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
    ~Allocation() {
        if (pointer)
            VirtualFree(pointer, 0, MEM_RELEASE);
    }
    Allocation(const Allocation&) = delete;
    Allocation& operator=(const Allocation&) = delete;
    std::uintptr_t address() const { return reinterpret_cast<std::uintptr_t>(pointer); }
};

template <typename T> void put(std::uintptr_t address, const T& value) {
    std::memcpy(reinterpret_cast<void*>(address), &value, sizeof(value));
}

bool playing = true, paused = true, seeking = false, active = true;
int tick = 4096;
char demo_name[512] = "replays/native-smoke.dem";
bool __fastcall is_playing(void*) {
    return playing;
}
bool __fastcall is_paused(void*) {
    return paused;
}
bool __fastcall is_seeking(void*) {
    return seeking;
}
bool __fastcall is_active(void*) {
    return active;
}
int __fastcall current_tick(void*) {
    return tick;
}
const char* __fastcall current_name(void*) {
    return demo_name;
}

template <typename Function> void thunk(std::uintptr_t address, Function function) {
    // mov rax, imm64; jmp rax. The synthetic module keeps the production
    // helper addresses unchanged while redirecting calls to harmless fixtures.
    unsigned char bytes[12] = {0x48, 0xb8, 0, 0, 0, 0, 0, 0, 0, 0, 0xff, 0xe0};
    const auto target = reinterpret_cast<std::uintptr_t>(function);
    std::memcpy(bytes + 2, &target, sizeof(target));
    DWORD previous = 0;
    require(VirtualProtect(reinterpret_cast<void*>(address), sizeof(bytes), PAGE_READWRITE,
                           &previous) != 0,
            "Could not make a synthetic helper writable");
    std::memcpy(reinterpret_cast<void*>(address), bytes, sizeof(bytes));
    require(VirtualProtect(reinterpret_cast<void*>(address), sizeof(bytes), PAGE_EXECUTE_READ,
                           &previous) != 0,
            "Could not make a synthetic helper executable");
    require(FlushInstructionCache(GetCurrentProcess(), reinterpret_cast<void*>(address),
                                  sizeof(bytes)) != 0,
            "Could not flush synthetic helper instructions");
}

void u32(std::vector<unsigned char>& bytes, std::uint32_t value) {
    for (unsigned i = 0; i < 4; ++i)
        bytes.push_back(static_cast<unsigned char>(value >> (8 * i)));
}

void number(std::vector<unsigned char>& bytes, double value) {
    std::uint64_t bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));
    for (unsigned i = 0; i < 8; ++i)
        bytes.push_back(static_cast<unsigned char>(bits >> (8 * i)));
}

const CameraPose first = {100, 200, 300, -15, 175, 0, 16.0 / 9};
const CameraPose last = {120, 240, 310, -25, 205, 20, 4.0 / 3};

std::shared_ptr<const NativePath> make_path() {
    const char magic[8] = {'D', 'L', 'Y', 'P', 'A', 'T', 'H', 0};
    std::vector<unsigned char> bytes(magic, magic + 8);
    u32(bytes, 1);
    u32(bytes, 1);
    u32(bytes, 7);
    u32(bytes, 0);
    number(bytes, 1);
    number(bytes, 0);
    number(bytes, 1);
    for (double value : first)
        number(bytes, value);
    for (double value : last)
        number(bytes, value);
    number(bytes, 0);
    number(bytes, 1);
    for (std::size_t i = 0; i < first.size(); ++i) {
        u32(bytes, 1);
        u32(bytes, 0);
        number(bytes, first[i]);
        number(bytes, last[i]);
        number(bytes, 0);
        number(bytes, 0);
    }
    auto path = std::make_shared<NativePath>();
    std::string error;
    require(path->load(bytes.data(), bytes.size(), error),
            "Synthetic path must parse through NativePath.load");
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
        gClient = client.address();
        gEngine = engine.address();
        gMemory = mapping.data();
        // Synthetic profile: the September 11 reviewed layout. The fixture
        // allocates a full-size client image, so the real offsets fit.
        gCompat = CompatResolution{};
        gCompat.resolved = true;
        gCompat.exact = true;
        gCompat.setup = 0x16bd550;
        gCompat.caller = 0x16b6ce4;
        gCompat.view_table = 0x2349418;
        gCompat.globals = 0x2f091f0;
        gCompat.engine_client = 0x37f67c0;
        put(gClient + gCompat.engine_client, engine_client.address());
        put(gClient + gCompat.globals, globals.address());
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
        gMemory = nullptr;
        gClient = 0;
        gEngine = 0;
    }

    void clock(float time, int at_tick) {
        put(globals.address() + 0x30, time);
        tick = at_tick;
    }

    void original_view(unsigned char flags = 0) {
        put(view.address(), gClient + gCompat.view_table);
        const auto camera = view.address() + 0x10;
        const float xyz[3] = {10, 20, 30}, angles[3] = {1, 2, 3};
        std::memcpy(reinterpret_cast<void*>(camera + 0x4a0), xyz, sizeof(xyz));
        std::memcpy(reinterpret_cast<void*>(camera + 0x4b8), angles, sizeof(angles));
        put(camera + 0x498, original_fov);
        put(camera + 0x4d8, original_aspect);
        put(camera + 0x430, 1280);
        put(camera + 0x438, 720);
        put(camera + 0x555, flags);
    }

    std::uint32_t command(Mode mode, double phase = 0, std::uint32_t flags = kAspect) {
        auto value = std::make_shared<Command>();
        value->wire.command = ++next_command;
        value->wire.mode = static_cast<std::uint32_t>(mode);
        value->wire.start_phase = phase;
        value->wire.speed = .1;
        value->wire.flags = flags;
        value->path = path;
        std::snprintf(value->wire.demo_name, sizeof(value->wire.demo_name), "native-smoke.dem");
        std::atomic_store(&gCommand, std::shared_ptr<const Command>(value));
        return next_command;
    }

    void enable_synthetic_editor() {
        // Only this synthetic test translation unit changes internal flags.
        // No production entry point bypasses input/overlay installation.
        EditorConfig config{};
        std::memcpy(config.magic, "DLYEDIT1", 8);
        config.sequence = 2;
        config.abi = kEditorAbi;
        config.enabled = 1;
        config.owner = std::uint32_t(EditorOwner::Flight);
        config.owner_sequence = 1;
        config.speed = 400;
        config.sensitivity = .08;
        std::memcpy(mapping.data() + kEditorConfigOffset, &config, sizeof(config));
        dolly::gInput = true;
        editor_overlay_available(true);
        editor_worker_tick(mapping.data(), true);
    }

    void manual(Mode mode = Mode::Manual, const CameraPose* seed = nullptr) {
        command(mode);
        auto value = std::make_shared<Command>(*std::atomic_load(&gCommand));
        value->manual = true;
        value->path.reset();
        value->has_manual_pose = seed != nullptr;
        if (seed)
            value->manual_pose = *seed;
        std::atomic_store(&gCommand, std::shared_ptr<const Command>(value));
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
        if (renew_lease)
            gHeartbeatTime = now_seconds();
        on_view(view.pointer, gClient + gCompat.caller);
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
        const double expected_fov =
            aspect_enabled
                ? 360 / pi *
                      std::atan(std::tan(original_fov * pi / 360) * expected[6] / original_aspect)
                : original_fov;
        close_to(fov, expected_fov, "Aspect/FOV transform differs from actual view lens");
        close_to(aspect, aspect_enabled ? expected[6] : original_aspect,
                 "Native aspect flag was not respected");
    }

    void fresh_hold(double phase = 0) {
        playing = true;
        paused = true;
        seeking = false;
        active = true;
        std::snprintf(demo_name, sizeof(demo_name), "replays/native-smoke.dem");
        clock(64, 4096);
        gWorkerError = 0;
        command(Mode::Hold, phase);
        const auto value = frame();
        require(value.state == static_cast<unsigned>(State::Armed) && !value.error,
                "New HOLD must clear previous fault and arm on an actual view");
        applied(phase);
    }
};

void replay_identity() {
    require(same_demo("practice.session.01.dem", "replays/practice.session.01.dem"),
            "Full custom recording name was rejected");
    require(same_demo("practice.session.01.dem", "replays/practice.session.01"),
            "Optional final .dem suffix removed part of the custom recording name");
    require(same_demo("C:\\selected\\Haze aim.01.dem", "replays/HAZE AIM.01.DEM"),
            "Replay basename case or path separators changed identity");
    for (const char* other : {"", "practice.session", "practice.session.01.info",
                              "practice.session.01.dem.info", "practice.session.02.dem"})
        require(!same_demo("practice.session.01.dem", other),
                "Native replay guard accepted an incomplete or different recording name");
    require(!same_demo("practice.session.01", "practice.session.01"),
            "Selected replay must identify a .dem file");
}

void atomic_exports() {
    static_assert(sizeof(LONG) == 4 && sizeof(LONG64) == 8,
                  "Atomic export widths must match the bridge wire format");
    alignas(8) volatile LONG word = 7;
    const LONG high32 = std::numeric_limits<LONG>::min();
    require(DollyAtomicExchange32(&word, high32) == 7 && word == high32,
            "32-bit atomic exchange lost its return value or sign bit");
    require(DollyAtomicCompareExchange32(&word, 9, 7) == high32 && word == high32,
            "Mismatched atomic compare-exchange changed memory or truncated the sign bit");
    require(DollyAtomicCompareExchange32(&word, -1, high32) == high32 && word == -1,
            "Matched atomic compare-exchange did not preserve signed high bits");
    require(DollyAtomicCompareExchange32(&word, 0, 0) == -1 && word == -1,
            "Atomic sequence read did not return the complete 32-bit value");
    require(DollyAtomicExchange32(&word, 0) == -1 && word == 0,
            "32-bit atomic exchange must return the previous value");

    alignas(8) volatile LONG64 heartbeat = 0;
    const LONG64 high64 = std::numeric_limits<LONG64>::min();
    const LONG64 wide64 = 0x1234567887654321LL;
    require(DollyAtomicExchange64(&heartbeat, high64) == 0 && heartbeat == high64,
            "64-bit atomic exchange lost the sign bit");
    require(DollyAtomicExchange64(&heartbeat, wide64) == high64 && heartbeat == wide64,
            "64-bit atomic exchange truncated a heartbeat or its previous value");
    require(DollyAtomicExchange64(&heartbeat, -1) == wide64 && heartbeat == -1,
            "64-bit atomic exchange did not preserve all high bits");
    require(DollyAtomicExchange64(&heartbeat, 0) == -1 && heartbeat == 0,
            "64-bit atomic exchange must return the previous signed value");
}

// Test the production typed effect binding and lifecycle with synthetic convars.
Allocation* effect_data = nullptr;
bool reject_effect_write = false;
unsigned vector_effect_writes = 0;
void* __fastcall find_effect(void*, std::uint64_t* out, const char* name, int) {
    *out = 0xffffffff;
    for (unsigned i = 0; i < kEffects.size(); ++i)
        if (!std::strcmp(name, kEffects[i].name))
            *out = i;
    return out;
}
std::uintptr_t __fastcall get_effect(void*, std::uint64_t id) {
    return id < kEffects.size() ? effect_data->address() + id * 0x100 : 0;
}
void __fastcall set_effect(CvarRef* ref, int slot, const void* value, void* current,
                           void* context) {
    require(slot == 0 && context == nullptr, "Incorrect verified setter call arguments");
    require(current == reinterpret_cast<void*>(ref->data + 0x58),
            "Setter used an incorrect value slot");
    if (reject_effect_write)
        return;
    if (kEffects[ref->id].components == 4)
        ++vector_effect_writes;
    std::memcpy(current, value, kEffects[ref->id].type == 0 ? 1 : 4 * kEffects[ref->id].components);
}
void effect_checks(Fixture& f) {
    Allocation values{4096};
    effect_data = &values;
    gCvar = 1;
    gFindCvar = find_effect;
    gGetCvarData = get_effect;
    gSetCvar = set_effect;
    for (unsigned i = 0; i < kEffects.size(); ++i) {
        auto at = values.address() + i * 0x100;
        put(at, reinterpret_cast<std::uintptr_t>(kEffects[i].name));
        put(at + 0x28, static_cast<std::uint16_t>(kEffects[i].type));
        put(at + 0x30, std::uint64_t(8));
        if (kEffects[i].type == 7)
            put(at + 0x58, 600.0f);
    }
    auto shot = std::make_shared<NativeShot>();
    shot->camera = *f.path;
    EffectTrack focus;
    focus.id = 1;
    focus.first = 100;
    focus.last = 900;
    focus.last_time = 1;
    focus.segments.push_back({0, 1, 1, 1, 100, 900, 0, 0});
    EffectTrack enabled;
    enabled.id = 0;
    enabled.first = 0;
    enabled.last = 1;
    enabled.last_time = .5;
    enabled.segments.push_back({0, .5, 0, 1, 0, 1, 0, 0});
    EffectTrack mode = enabled;
    mode.id = 4;
    mode.last = 2;
    mode.segments[0].right = 2;
    shot->effects = {focus, enabled, mode};
    auto command = [&](Mode mode) {
        f.command(mode);
        auto c = std::make_shared<Command>(*std::atomic_load(&gCommand));
        c->shot = shot;
        std::atomic_store(&gCommand, std::shared_ptr<const Command>(c));
    };
    f.fresh_hold();
    command(Mode::Hold);
    auto status = f.frame();
    require(status.effect_count == 3 && !status.error, "Native effects did not arm");
    double value = 0;
    require(gEffects.bindings[0].read(value) && value == 100,
            "First effect not applied before resume");
    command(Mode::Play);
    f.frame();
    paused = false;
    for (int n = 1; n <= 8; ++n) {
        f.clock(64 + n / 1024.0f, 4096);
        status = f.frame();
        require(status.effect_phase == status.phase && !status.effect_error,
                "Effect and camera phases differ");
        require(gEffects.bindings[0].read(value), "Effect readback failed");
        close_to(value, 100 + 800 * status.phase,
                 "Effect stepped instead of following fractional view time");
    }
    for (int n = 1; n <= 4; ++n) {
        f.clock(64 + n / 8.0f, 4096 + n * 8);
        status = f.frame();
        require(!status.error, "DOF progression failed");
    }
    require(gEffects.bindings[1].read(value) && value == 1, "Boolean DOF step did not apply");
    require(gEffects.bindings[2].read(value) && value == 2, "Integer DOF mode did not apply");
    command(Mode::HoldCurrent);
    status = f.frame();
    double held = status.effect_phase;
    f.clock(64.125f, 4104);
    status = f.frame();
    close_to(status.effect_phase, held, "Held effect advanced");
    // Manual reframing must retain the currently selected effect phase.
    paused = true;
    f.manual();
    status = f.frame();
    require(!status.error && status.effect_count == 3, "Entering manual flight lost selected DOF");
    close_to(status.effect_phase, held, "Manual flight changed the selected effect phase");
    command(Mode::Release);
    status = f.frame();
    float restored = 0;
    read_value(values.address() + 0x100 + 0x58, restored);
    require(status.effect_count == 0 && restored == 600, "Stop did not restore original effect");
    // Force a setter/readback mismatch: stop before camera write, then restore.
    f.fresh_hold();
    command(Mode::Hold);
    reject_effect_write = true;
    status = f.frame();
    require(status.error == 35, "A refused effect setter was reported as synchronized");
    f.unchanged();
    reject_effect_write = false;
    command(Mode::Release);
    f.frame();
    // Lease expiry must restore settings without an editor console round trip.
    f.fresh_hold();
    command(Mode::Hold);
    f.frame();
    gHeartbeatTime = now_seconds() - 3;
    status = f.frame(0, false);
    read_value(values.address() + 0x100 + 0x58, restored);
    require(status.error == 10 && restored == 600 && status.effect_count == 0,
            "Editor loss did not restore effects");
    f.command(Mode::Release);
    f.frame();
    // Invalid binding is refused before any shot cvar changes.
    put(values.address() + 0x100 + 0x28, std::uint16_t(3));
    f.fresh_hold();
    command(Mode::Hold);
    status = f.frame();
    require(status.error == 35 && status.effect_count == 0, "Wrong native type was accepted");
    f.unchanged();
    put(values.address() + 0x100 + 0x28, std::uint16_t(7));
    f.command(Mode::Release);
    f.frame();
    // Failed restoration must not acknowledge a successful release.
    f.fresh_hold();
    command(Mode::Hold);
    f.frame();
    reject_effect_write = true;
    command(Mode::Release);
    status = f.frame();
    require(status.error == 36 && status.effect_count == 3, "Failed restore was acknowledged");
    reject_effect_write = false;
    status = f.frame();
    require(status.state == static_cast<unsigned>(State::Stopped) && status.effect_count == 0,
            "Restore retry did not clear ownership");
    // Explicit restore overrides survive native completion/release.
    shot->effects[0].restore_override = true;
    shot->effects[0].restore = 123;
    f.fresh_hold();
    command(Mode::Hold);
    f.frame();
    command(Mode::Release);
    status = f.frame();
    read_value(values.address() + 0x100 + 0x58, restored);
    require(restored == 123 && !status.error, "Restore override was lost");
    // Four authored lanes must become one typed Vector4 write and restore.
    auto vector_shot = std::make_shared<NativeShot>();
    vector_shot->camera = *f.path;
    const std::array<float, 4> original{{-100, 0, 180, 2000}};
    put(values.address() + 8 * 0x100 + 0x58, original);
    for (unsigned component = 0; component < 4; ++component) {
        EffectTrack lane;
        lane.id = 8;
        lane.component = component;
        lane.last_time = 1;
        lane.first = 10 + component;
        lane.last = 20 + component;
        lane.segments.push_back({0, 1, 1, 1, lane.first, lane.last, 0, 0});
        vector_shot->effects.push_back(lane);
    }
    gExtendedCvarSupported = false;
    require(!gEffects.apply(vector_shot, 0) && gEffects.count == 0,
            "Unverified Vector4 profile was accepted");
    gExtendedCvarSupported = true;
    vector_effect_writes = 0;
    require(gEffects.apply(vector_shot, .5) && gEffects.count == 1 && vector_effect_writes == 1,
            "Vector was not written once as a complete value");
    EffectValue vector_value{};
    require(gEffects.bindings[0].read(vector_value), "Vector readback failed");
    for (unsigned component = 0; component < 4; ++component)
        require(vector_value[component] == 15 + component, "Vector lane order changed");
    require(gEffects.apply(vector_shot, .5) && vector_effect_writes == 1,
            "Unchanged vector was rewritten");
    require(gEffects.restore() && vector_effect_writes == 2,
            "Vector original was not restored in one call");
    std::array<float, 4> restored_vector{};
    read_value(values.address() + 8 * 0x100 + 0x58, restored_vector);
    require(restored_vector == original, "Vector restore lost a component");
    gExtendedCvarSupported = false;
    gCvar = 0;
    gFindCvar = nullptr;
    gGetCvarData = nullptr;
    gSetCvar = nullptr;
    effect_data = nullptr;
}

std::uint64_t cursor_clip_calls = 0;
BOOL WINAPI observe_cursor_clip(const RECT*) {
    ++cursor_clip_calls;
    return TRUE;
}

void editor_cursor_startup_checks() {
    const auto saved_window = dolly::gWindow.load();
    const auto saved_clip = dolly::gClipCursor;
    const auto saved_owner = dolly::gOwner.load();
    const auto saved_view = editor_snapshot();
    dolly::gWindow = nullptr;
    dolly::gFocused = false;
    dolly::gClipCursor = observe_cursor_clip;
    editor_update_view(false, true, false, saved_view.pose);
    editor_set_owner(EditorOwner::Flight);
    editor_worker_tick(nullptr, true);
    const auto initial_calls = cursor_clip_calls;

    // First view readiness is a later callback than the owner's Flight request.
    // The callback may only publish a refresh; the worker owns OS cursor calls.
    dolly::gKeys['W'] = true;
    dolly::gBlocked['W'] = false;
    dolly::gMouseX = 17;
    dolly::gMouseY = -9;
    editor_update_view(true, true, true, saved_view.pose);
    require(cursor_clip_calls == initial_calls,
            "View readiness performed an OS cursor operation in the render callback");
    editor_worker_tick(nullptr, true);
    require(cursor_clip_calls == initial_calls + 1,
            "First ready view did not reconcile Flight cursor ownership");
    require(dolly::key_down('W') && dolly::gMouseX == 17 && dolly::gMouseY == -9,
            "Readiness reconciliation reset active camera movement");
    editor_worker_tick(nullptr, true);
    editor_update_view(true, true, true, saved_view.pose);
    editor_worker_tick(nullptr, true);
    require(cursor_clip_calls == initial_calls + 1,
            "Unchanged ready views repeatedly changed OS cursor ownership");
    editor_update_view(false, true, false, saved_view.pose);
    editor_worker_tick(nullptr, true);
    require(cursor_clip_calls == initial_calls + 2,
            "Losing view readiness did not release cursor ownership");

    dolly::gKeys['W'] = false;
    dolly::gMouseX = 0;
    dolly::gMouseY = 0;
    dolly::gClipCursor = saved_clip;
    dolly::gWindow = saved_window;
    editor_set_owner(saved_owner);
    editor_update_view(saved_view.ready, saved_view.paused, saved_view.manual_active,
                       saved_view.pose, saved_view.phase, saved_view.tick,
                       saved_view.horizontal_fov, saved_view.view_width, saved_view.view_height);
}

void editor_input_checks() {
    // Feed the real key-transition handler after its callers' focus checks.
    // No foreground window or OS input injection is needed for this fixture.
    auto saved = std::atomic_load(&dolly::gConfig);
    const auto saved_view = editor_snapshot();
    auto configured = std::make_shared<EditorConfig>(*saved);
    configured->playback_flags = 0;
    configured->bindings[9] = {VK_F8, 0};
    configured->bindings[10] = {VK_F9, 0};
    std::atomic_store(&dolly::gConfig, std::shared_ptr<const EditorConfig>(configured));
    dolly::gWindow = nullptr;
    dolly::gTextInput = false;
    for (unsigned i = 0; i < 256; ++i) {
        dolly::gKeys[i] = false;
        dolly::gBlocked[i] = false;
        dolly::gFocusBlocked[i] = false;
    }
    dolly::gAcknowledged = dolly::gLastEvent;
    editor_update_view(true, true, true, saved_view.pose);
    editor_set_owner(EditorOwner::Flight);
    auto event_count = [] { return dolly::gLastEvent; };
    auto console_event = [](double value) {
        const auto& event = dolly::gEvents[(dolly::gLastEvent - 1) % kEditorEventCount];
        require(event.action == std::uint32_t(EditorAction::Console) && event.value == value,
                "Console shortcut queued the wrong requested visibility");
    };

    dolly::key_event(VK_F8, true);
    require(dolly::gOwner == EditorOwner::Panel, "F8 did not open the panel");
    // Raw + legacy copies and held-key repeat must not toggle the panel back.
    dolly::key_event(VK_F8, true);
    dolly::key_event('X', true);
    dolly::key_event('X', false);
    dolly::unblock_released(); // OS key polling cannot erase a recorded shortcut edge.
    dolly::key_event(VK_F8, true);
    require(dolly::gOwner == EditorOwner::Panel, "Repeated/duplicate F8 flashed the panel");
    dolly::key_event(VK_F8, false);
    dolly::key_event(VK_F8, false);
    dolly::key_event(VK_F8, true);
    require(dolly::gOwner == EditorOwner::Flight, "A new F8 press did not close the panel");
    dolly::key_event(VK_F8, false);

    // After a path endpoint or Stop, F8 must actually re-arm the manual
    // camera. Changing only the input owner leaves every movement key dead.
    editor_update_view(true, true, false, saved_view.pose);
    editor_set_owner(EditorOwner::Panel);
    const auto before_return = event_count();
    dolly::key_event(VK_F8, true);
    require(dolly::gOwner == EditorOwner::Panel && event_count() == before_return + 1,
            "F8 claimed flight input before re-arming a held camera");
    require(dolly::gEvents[(dolly::gLastEvent - 1) % kEditorEventCount].action ==
                std::uint32_t(EditorAction::Flight),
            "F8 did not request the acknowledged camera flight handoff");
    dolly::key_event(VK_F8, true);
    require(event_count() == before_return + 1, "Held F8 repeatedly requested flight");
    editor_update_view(true, true, true, saved_view.pose);
    editor_set_owner(EditorOwner::Flight); // controller/render acknowledgement
    dolly::key_event(VK_F8, true);
    require(dolly::gOwner == EditorOwner::Flight && event_count() == before_return + 1,
            "Flight acknowledgement reopened the panel for the same F8 press");
    dolly::key_event(VK_F8, false);

    // F8 remains a panel visibility control during a running shot; it must
    // never send a manual-flight command that stops/replaces that shot.
    configured = std::make_shared<EditorConfig>(*configured);
    configured->playback_flags = 1;
    std::atomic_store(&dolly::gConfig, std::shared_ptr<const EditorConfig>(configured));
    editor_update_view(true, true, false, saved_view.pose); // includes frozen previews
    editor_set_owner(EditorOwner::Panel);
    const auto during_shot = event_count();
    dolly::key_event(VK_F8, true);
    require(dolly::gOwner == EditorOwner::Flight && event_count() == during_shot,
            "F8 interrupted a playing shot with a manual-flight request");
    dolly::key_event(VK_F8, false);
    editor_update_view(false, true, false, saved_view.pose);
    editor_set_owner(EditorOwner::Panel);
    dolly::key_event(VK_F8, true);
    require(dolly::gOwner == EditorOwner::Flight && event_count() == during_shot,
            "F8 interrupted a known playing shot when its view was temporarily unavailable");
    dolly::key_event(VK_F8, false);
    configured = std::make_shared<EditorConfig>(*configured);
    configured->playback_flags = 2;
    std::atomic_store(&dolly::gConfig, std::shared_ptr<const EditorConfig>(configured));
    editor_set_owner(EditorOwner::Panel);
    dolly::key_event(VK_F8, true);
    require(dolly::gOwner == EditorOwner::Panel && event_count() == during_shot,
            "F8 changed camera ownership during an unfinished operation");
    dolly::key_event(VK_F8, false);
    configured = std::make_shared<EditorConfig>(*configured);
    configured->playback_flags = 0;
    std::atomic_store(&dolly::gConfig, std::shared_ptr<const EditorConfig>(configured));
    editor_update_view(false, true, false, saved_view.pose);
    editor_set_owner(EditorOwner::Panel);
    dolly::key_event(VK_F8, true);
    require(dolly::gOwner == EditorOwner::Panel && event_count() == during_shot,
            "F8 hid recovery controls before a usable camera view was available");
    dolly::key_event(VK_F8, false);
    editor_update_view(true, true, true, saved_view.pose);
    editor_set_owner(EditorOwner::Flight);

    const auto before_console = event_count();
    dolly::key_event(VK_F7, true);
    require(dolly::gOwner == EditorOwner::Console && event_count() == before_console + 1,
            "F7 did not request the console once");
    console_event(1);
    dolly::key_event(VK_F7, true);
    dolly::key_event('X', true);
    dolly::key_event('X', false);
    dolly::unblock_released();
    dolly::key_event(VK_F7, true);
    require(event_count() == before_console + 1, "Held F7 queued duplicate console toggles");
    dolly::key_event(VK_F7, false);
    dolly::key_event(VK_F7, false);
    dolly::key_event(VK_F7, true);
    require(event_count() == before_console + 2 && dolly::gOwner == EditorOwner::Console,
            "Console input was released before hideconsole confirmation");
    console_event(0);
    editor_set_owner(EditorOwner::Panel); // asynchronous hideconsole confirmation
    dolly::key_event(VK_F7, true);
    require(event_count() == before_console + 2 && dolly::gOwner == EditorOwner::Panel,
            "Controller confirmation converted a held F7 into another toggle");
    dolly::key_event(VK_F7, false);

    editor_set_owner(EditorOwner::Console);
    dolly::key_event(VK_F8, true);
    {
        const auto& event = dolly::gEvents[(dolly::gLastEvent - 1) % kEditorEventCount];
        require(event.action == std::uint32_t(EditorAction::Panel) && event.value == 1,
                "F8 from console must return to Dolly rather than the underlying game UI");
    }
    const auto close_requested = event_count();
    require(dolly::gOwner == EditorOwner::Console, "F8 bypassed console close confirmation");
    dolly::key_event(VK_F8, true);
    editor_set_owner(EditorOwner::Panel);
    dolly::key_event(VK_F8, true);
    require(event_count() == close_requested && dolly::gOwner == EditorOwner::Panel,
            "F8 close confirmation flashed the panel");
    dolly::key_event(VK_F8, false);

    // F9 returns input only after the controller has hidden the game's replay
    // HUD and the native view has acknowledged flight. Alt-tab preserves owner.
    editor_set_owner(EditorOwner::Flight);
    dolly::key_event(VK_F9, true);
    dolly::key_event(VK_F9, false);
    require(dolly::gOwner == EditorOwner::GameUI, "F9 did not suspend editor input");
    auto unfocused = editor_snapshot();
    require(!unfocused.focused && unfocused.owner == EditorOwner::GameUI,
            "Alt-tab erased game UI ownership from native status");
    dolly::key_event(VK_F9, true);
    dolly::key_event(VK_F9, false);
    require(dolly::gOwner == EditorOwner::GameUI, "F9 released input before flight confirmation");
    const auto& return_event = dolly::gEvents[(dolly::gLastEvent - 1) % kEditorEventCount];
    require(return_event.action == std::uint32_t(EditorAction::GameUI) && return_event.value == 0,
            "F9 did not request explicit game UI close");
    editor_set_owner(EditorOwner::Flight);
    const auto after_game_ui = event_count();

    // A configurable text key must remain available for typing in the console.
    configured = std::make_shared<EditorConfig>(*configured);
    configured->bindings[9] = {'H', 0};
    std::atomic_store(&dolly::gConfig, std::shared_ptr<const EditorConfig>(configured));
    editor_set_owner(EditorOwner::Console);
    dolly::key_event('H', true);
    dolly::key_event('H', false);
    require(event_count() == after_game_ui, "Custom text binding stole console typing");
    dolly::key_event(VK_ESCAPE, true);
    console_event(0);
    const auto escape_requested = event_count();
    dolly::key_event(VK_ESCAPE, true);
    require(event_count() == escape_requested, "Held Escape queued duplicate close requests");
    dolly::key_event(VK_ESCAPE, false);

    editor_set_owner(EditorOwner::Flight);
    dolly::key_event('W', true);
    require(dolly::key_down('W'), "Fresh flight movement was not recorded");
    editor_set_owner(EditorOwner::Flight);
    require(dolly::key_down('W'), "Reasserting the same owner interrupted movement");
    editor_set_owner(EditorOwner::Panel);
    editor_set_owner(EditorOwner::Flight);
    dolly::key_event('W', true);
    require(!dolly::key_down('W'), "Held movement leaked across an ownership transition");
    dolly::key_event('W', false);
    dolly::key_event('W', true);
    require(dolly::key_down('W'), "Movement did not resume after release and a fresh press");
    dolly::key_event('W', false);
    std::atomic_store(&dolly::gConfig, saved);
    editor_update_view(saved_view.ready, saved_view.paused, saved_view.manual_active,
                       saved_view.pose, saved_view.phase, saved_view.tick,
                       saved_view.horizontal_fov, saved_view.view_width, saved_view.view_height);
    dolly::gAcknowledged = dolly::gLastEvent;
}

void run() {
    atomic_exports();
    replay_identity();
    Fixture f;
    auto status = f.frame();
    require(status.state == static_cast<unsigned>(State::Probe),
            "No command must leave the hook in probe mode");
    f.unchanged();

    // Calling another view/caller must neither take camera ownership nor
    // acknowledge a command that never reached the verified main-view site.
    f.command(Mode::Hold, .25);
    f.original_view();
    on_view(f.view.pointer, gClient + gCompat.caller + 1);
    require(f.status().frame_count == status.frame_count,
            "Unverified caller updated native status");
    f.unchanged();
    status = f.frame();
    require(status.ack_command == f.next_command &&
                status.state == static_cast<unsigned>(State::Armed),
            "HOLD must acknowledge from the actual verified callback");
    f.applied(.25);

    std::snprintf(demo_name, sizeof(demo_name), "replays/native-smoke");
    f.command(Mode::Hold, .25);
    status = f.frame();
    require(status.state == static_cast<unsigned>(State::Armed) && !status.error,
            "The actual native view rejected the selected replay without its .dem suffix");
    f.applied(.25);
    std::snprintf(demo_name, sizeof(demo_name), "replays/native-smoke.dem");

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
        close_to(status.phase, .25 + frame / 1024.0,
                 "Main-view engine time was not evaluated continuously");
        require(status.applied_pose[0] > previous_x && !status.error,
                "Subtick camera position repeated or went backwards");
        f.applied(status.phase);
        previous_x = status.applied_pose[0];
    }

    const auto held_phase = status.phase;
    f.command(Mode::HoldCurrent, 0);
    status = f.frame();
    close_to(status.phase, held_phase, "HoldCurrent changed the displayed phase");
    f.clock(64.0625f, 4100);
    status = f.frame();
    close_to(status.phase, held_phase, "HoldCurrent followed a moving replay clock");
    f.command(Mode::Play, 0);
    status = f.frame();
    close_to(status.phase, held_phase, "Resuming a held camera jumped to command start_phase");
    f.clock(64.125f, 4104);
    status = f.frame();
    close_to(status.phase, held_phase + .0625, "Resume did not advance from held phase");
    f.applied(status.phase);

    f.fresh_hold(.5);
    f.applied(.5); // Non-native aspect must alter FOV as well as view aspect.
    f.command(Mode::Hold, .5, 0);
    f.frame();
    f.applied(.5, false);

    f.fresh_hold();
    f.command(Mode::Play);
    f.frame();
    paused = false;
    for (int quarter = 1; quarter <= 4; ++quarter) {
        f.clock(64 + quarter / 4.0f, 4096 + quarter * 16);
        status = f.frame();
        require(!status.error, "Ordinary native progression faulted before endpoint");
    }
    require(status.state == static_cast<unsigned>(State::Completed),
            "Exact endpoint was not marked completed");
    close_to(status.phase, 1, "Final phase is not exact", 0);
    f.applied(1);
    f.clock(65.125f, 4168);
    status = f.frame();
    close_to(status.phase, 1, "Completed camera did not keep final pose", 0);
    f.applied(1);

    f.fresh_hold();
    std::snprintf(demo_name, sizeof(demo_name), "another-demo.dem");
    status = f.frame();
    require(status.state == static_cast<unsigned>(State::Fault) && status.error == 12,
            "Changing demos must fault before writing the camera");
    f.unchanged();
    f.command(Mode::Release);
    status = f.frame();
    require(status.state == static_cast<unsigned>(State::Stopped) && !status.error,
            "Release after a fault must acknowledge and relinquish camera ownership");
    f.unchanged();

    f.fresh_hold();
    gHeartbeatTime = now_seconds() - 3;
    status = f.frame(0, false);
    require(status.error == 10, "Expired editor lease must release the view");
    f.unchanged();
    f.command(Mode::Release);
    status = f.frame(0, false);
    require(status.state == static_cast<unsigned>(State::Stopped),
            "Expired lease prevented explicit release");
    f.unchanged();

    f.fresh_hold();
    f.command(Mode::Play);
    f.frame();
    paused = false;
    f.clock(70, 4480);
    status = f.frame();
    require(status.error == 15,
            "A large replay time jump must fault before evaluating a distant camera");
    f.unchanged();

    f.fresh_hold();
    status = f.frame(2);
    require(status.error == 11, "Alternate projection must be rejected");
    f.unchanged();
    f.fresh_hold();
    seeking = true;
    status = f.frame();
    require(status.error == 12, "Seeking replay must release native camera ownership");
    f.unchanged();

    f.fresh_hold(.25);
    f.command(Mode::Play, 0, kFrozen | kAspect);
    status = f.frame();
    const double frozen_start = status.phase;
    Sleep(10);
    status = f.frame();
    require(status.phase > frozen_start && status.paused && !status.error,
            "Frozen native playback must advance from real time while replay remains paused");
    f.applied(status.phase);
    f.clock(64, 4097);
    status = f.frame();
    require(status.error == 14, "Moving frozen replay must release the native camera");
    f.unchanged();

    f.enable_synthetic_editor();
    // Configuration is published separately from camera commands. A view can
    // observe Manual just before the worker consumes the new enabled config.
    auto enabled_config = std::atomic_load(&dolly::gConfig);
    auto pending_config = std::make_shared<EditorConfig>(*enabled_config);
    pending_config->enabled = 0;
    std::atomic_store(&dolly::gConfig, std::shared_ptr<const EditorConfig>(pending_config));
    f.manual(Mode::Manual, &first);
    status = f.frame();
    require(status.state == static_cast<unsigned>(State::Starting) && !status.error &&
                !editor_snapshot().manual_active,
            "A pending editor config latched a camera fault or armed input");
    f.unchanged();
    std::atomic_store(&dolly::gConfig, enabled_config);
    status = f.frame();
    require(status.state == static_cast<unsigned>(State::Armed) && !status.error,
            "The same manual command did not recover after configuration arrived");
    for (int i = 0; i < 7; ++i)
        close_to(status.applied_pose[i], first[i], "Pending configuration lost the manual seed");
    editor_overlay_available(false);
    f.manual(Mode::Manual, &last);
    status = f.frame();
    require(status.state == static_cast<unsigned>(State::Starting) && !status.error &&
                !editor_snapshot().manual_active,
            "Pending DX11 initialization latched a camera fault or armed input");
    f.unchanged();
    editor_overlay_available(true);
    status = f.frame();
    require(status.state == static_cast<unsigned>(State::Armed) && !status.error,
            "Manual flight did not recover when the DX11 panel became available");
    for (int i = 0; i < 7; ++i)
        close_to(status.applied_pose[i], last[i], "Pending DX11 setup lost the manual seed");
    f.fresh_hold(.5);
    auto selected = f.status();
    f.manual();
    status = f.frame();
    require(!status.error && status.state == static_cast<unsigned>(State::Armed),
            "Manual flight without a path did not arm");
    for (int i = 0; i < 7; ++i)
        close_to(status.applied_pose[i], selected.applied_pose[i],
                 "Manual camera jumped away from the displayed preview");
    f.manual(Mode::HoldCurrent);
    status = f.frame();
    for (int i = 0; i < 7; ++i)
        close_to(status.applied_pose[i], selected.applied_pose[i],
                 "HoldCurrent lost the manual camera");
    paused = false;
    f.clock(65, 4160);
    status = f.frame();
    require(!status.error, "Resuming replay while holding a manual camera faulted");
    for (int i = 0; i < 7; ++i)
        close_to(status.applied_pose[i], selected.applied_pose[i],
                 "Resuming replay moved the held manual view");
    paused = true;
    CameraPose seed = {500, 600, 12, -30, 120, 8, 1.5};
    f.manual(Mode::Manual, &seed);
    status = f.frame();
    for (int i = 0; i < 7; ++i)
        close_to(status.applied_pose[i], seed[i], "Explicit manual seed was ignored");
    // Ring never overwrites unacknowledged actions or inserts sequence gaps.
    for (unsigned i = 0; i < kEditorEventCount; ++i)
        require(editor_enqueue(EditorAction::Capture), "Editor event ring filled early");
    require(!editor_enqueue(EditorAction::Capture), "Full event ring overwrote a capture");
    editor_worker_tick(f.mapping.data(), true);
    EditorStatus events{};
    std::memcpy(&events, f.mapping.data() + kEditorStatusOffset, sizeof(events));
    EditorInputDiagnostics input{};
    std::memcpy(&input, f.mapping.data() + kEditorInputDiagnosticsOffset, sizeof(input));
    require(std::memcmp(input.magic, "DLYINP01", 8) == 0 && input.abi == 1 && input.sequence &&
                !(input.sequence & 1),
            "Optional input diagnostics were not published as a committed snapshot");
    require(input.raw_mouse_packets == dolly::gRawMousePackets.load() &&
                input.consumed_motion_frames == dolly::gConsumedMotionFrames.load() &&
                input.cursor_syncs == dolly::gCursorSyncs.load(),
            "Input diagnostic counters differ from the native input path");
    require(events.last_event == kEditorEventCount && events.dropped_events == 1,
            "Overflow changed editor event numbering");
    for (const auto& event : events.events) {
        require(event.tick == tick && event.paused, "Capture lost its replay-time snapshot");
        for (int i = 0; i < 7; ++i)
            close_to(event.pose[i], seed[i], "Capture did not contain the displayed camera");
    }
    EditorConfig config{};
    std::memcpy(&config, f.mapping.data() + kEditorConfigOffset, sizeof(config));
    config.sequence += 2;
    config.ack_event = kEditorEventCount;
    config.sensitivity = 10;
    std::memcpy(f.mapping.data() + kEditorConfigOffset, &config, sizeof(config));
    // Force the producer lock busy exactly when a fresh config/ack is read.
    require(!dolly::gEventLock.test_and_set(std::memory_order_acquire),
            "Synthetic event lock was already occupied");
    editor_worker_tick(f.mapping.data(), true);
    require(dolly::gAcknowledged == 0, "Worker mutated the ring without its lock");
    dolly::gEventLock.clear(std::memory_order_release);
    editor_worker_tick(f.mapping.data(), true); // same config sequence: retry ack
    require(dolly::gAcknowledged == kEditorEventCount,
            "Contended acknowledgement was lost after accepting config");
    close_to(editor_snapshot().sensitivity, 10,
             "Native sensitivity limit differs from Python settings");
    require(editor_enqueue(EditorAction::Capture), "Acknowledged ring space was not reusable");
    // Playback settings use appended event IDs and the existing shared
    // configuration; they must never alter the live camera clock.
    config.sequence += 2;
    config.playback_speed = .1;
    config.playback_rate = 120;
    std::memcpy(f.mapping.data() + kEditorConfigOffset, &config, sizeof(config));
    editor_worker_tick(f.mapping.data(), true);
    close_to(editor_snapshot().playback_speed, .1,
             "In-game playback speed differs from the desktop setting");
    require(editor_snapshot().playback_rate == 120,
            "In-game update rate differs from the desktop setting");
    const auto before_settings = dolly::gLastEvent;
    require(!editor_enqueue(EditorAction::SetPlaybackSpeed, 0) &&
                !editor_enqueue(EditorAction::SetPlaybackSpeed, 4.1),
            "Invalid playback speed entered the event queue");
    require(!editor_enqueue(EditorAction::SetPlaybackRate, 90),
            "Invalid playback rate entered the event queue");
    require(editor_enqueue(EditorAction::SetPlaybackSpeed, .25) &&
                editor_enqueue(EditorAction::SetPlaybackRate, 60),
            "Playback controls could not queue valid choices");
    require(dolly::gLastEvent == before_settings + 2,
            "Playback control validation changed event numbering");
    const auto& speed_event = dolly::gEvents[before_settings % kEditorEventCount];
    const auto& rate_event = dolly::gEvents[(before_settings + 1) % kEditorEventCount];
    require(speed_event.action == 29 && speed_event.value == .25 && rate_event.action == 30 &&
                rate_event.value == 60,
            "Playback action IDs or values differ from Python");
    config.sequence += 2;
    config.playback_flags = 1;
    std::memcpy(f.mapping.data() + kEditorConfigOffset, &config, sizeof(config));
    editor_worker_tick(f.mapping.data(), true);
    require(!editor_enqueue(EditorAction::SetPlaybackSpeed, 1) &&
                !editor_enqueue(EditorAction::SetPlaybackRate, 30),
            "Playback controls changed settings during a playing shot");
    config.sequence += 2;
    config.playback_flags = 0;
    std::memcpy(f.mapping.data() + kEditorConfigOffset, &config, sizeof(config));
    editor_worker_tick(f.mapping.data(), true);
    f.manual();
    dolly::gInput = false;
    status = f.frame();
    require(status.error == 19, "Manual flight bypassed missing input hooks");
    f.unchanged();
    dolly::gInput = true;
    f.manual();
    seeking = true;
    status = f.frame();
    require(status.error == 12, "Manual flight ignored a replay seek");
    f.unchanged();
    seeking = false;
    f.manual();
    gHeartbeatTime = now_seconds() - 3;
    status = f.frame(0, false);
    require(status.error == 10, "Manual flight retained camera after editor loss");
    f.unchanged();
    f.command(Mode::Release);
    f.frame();
    effect_checks(f);
    editor_cursor_startup_checks();
    editor_input_checks();

    std::cout
        << "Actual native callback smoke tests passed (synthetic Windows memory; no game runtime claim)\n";
}

} // namespace smoke

int main() {
    try {
        smoke::run();
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Native callback smoke test failed: " << error.what() << '\n';
        return 1;
    }
}
