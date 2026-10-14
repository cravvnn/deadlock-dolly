// Included inside bridge_win.cpp's anonymous namespace. The reviewed game's
// post-process controller still evaluates selected-player damage/death events
// in Free Cam. Filter their render weights, not their queues or scene grading.
// Event records and scene grading are untouched; F9/POV resumes original weights.
#pragma once

constexpr char kGameplayEffectsClientHash[] =
    "cb831d124403ee2f3afa54981e69d8acf71251ba1129d0733757251f74a157c2";
constexpr std::uintptr_t kGameplayEffectsController = 0x2df4cf0;
constexpr std::uintptr_t kGameplayEffectWeight = 0x7ed690;
constexpr std::uintptr_t kGameplayEffectWeightCaller = 0x7f734d;
constexpr unsigned char kGameplayEffectWeightBytes[] = {0x48, 0x89, 0x5c, 0x24, 0x08, 0x57, 0x48,
                                                        0x83, 0xec, 0x60, 0x48, 0x8b, 0xd9, 0x33};
constexpr unsigned char kGameplayEffectCallBytes[] = {0xe8, 0x43, 0x63, 0xff, 0xff};
using GameplayEffectWeightFn = float(__fastcall*)(void*);
GameplayEffectWeightFn gOriginalGameplayEffectWeight = nullptr;
std::atomic<bool> gGameplayCameraActive{false};

// The reviewed render-graph option controls only screen-space particles,
// including the selected hero's damage/low-health borders. World particles
// have a separate pass. Disable drawing, keeping simulation alive for F9/POV.
constexpr std::uintptr_t kGameplayRenderOption = 0x576fd0;
constexpr std::uintptr_t kGameplayScreenParticlesCaller = 0x5a7304;
constexpr unsigned char kGameplayRenderOptionBytes[] = {
    0x66, 0x0f, 0x6e, 0xca, 0x4c, 0x8d, 0x1d, 0xa5, 0x45, 0x9f, 0x02, 0x66, 0x0f, 0x70, 0xc9, 0x00};
constexpr unsigned char kGameplayScreenParticlesCallBytes[] = {0xe8, 0xcc, 0xfc, 0xfc, 0xff};
using GameplayRenderOptionFn = int(__fastcall*)(void*, std::uint32_t, int);
GameplayRenderOptionFn gOriginalGameplayRenderOption = nullptr;

struct GameplayEffectsProfile {
    const char* hash;
    std::size_t image_size;
    std::uintptr_t controller, weight, weight_caller, option, screen_caller;
    std::size_t row_size;
    std::array<unsigned char, 14> weight_bytes;
    std::array<unsigned char, 5> weight_call;
    std::array<unsigned char, 16> option_bytes;
    std::array<unsigned char, 5> screen_call;
};
constexpr GameplayEffectsProfile kGameplayEffectsProfiles[] = {
    {kGameplayEffectsClientHash,
     0x3cd0000,
     kGameplayEffectsController,
     kGameplayEffectWeight,
     kGameplayEffectWeightCaller,
     kGameplayRenderOption,
     kGameplayScreenParticlesCaller,
     32,
     {0x48, 0x89, 0x5c, 0x24, 0x08, 0x57, 0x48, 0x83, 0xec, 0x60, 0x48, 0x8b, 0xd9, 0x33},
     {0xe8, 0x43, 0x63, 0xff, 0xff},
     {0x66, 0x0f, 0x6e, 0xca, 0x4c, 0x8d, 0x1d, 0xa5, 0x45, 0x9f, 0x02, 0x66, 0x0f, 0x70, 0xc9,
      0x00},
     {0xe8, 0xcc, 0xfc, 0xfc, 0xff}},
    // Build 6712: event timing gained a word; the render loop uses 36-byte
    // rows, still six vectors at +0x20 with 0x28-byte vector spacing.
    {"bc0dae383a2cd65dc1616515cdffa6c947fd057e5590edf0f5a01bd953ec19c9",
     0x40f4000,
     0x3162370,
     0x85e9f0,
     0x871305,
     0x5b50a0,
     0x61568c,
     36,
     {0x48, 0x89, 0x5c, 0x24, 0x08, 0x57, 0x48, 0x83, 0xec, 0x40, 0x48, 0x8b, 0xd9, 0x33},
     {0xe8, 0xeb, 0xd6, 0xfe, 0xff},
     {0x66, 0x0f, 0x6e, 0xca, 0x4c, 0x8d, 0x1d, 0x85, 0xe6, 0xcc, 0x02, 0x66, 0x0f, 0x70, 0xc9,
      0x00},
     {0xe8, 0x14, 0xfa, 0xf9, 0xff}},
    // Version 6722: normalized full-function comparison retains 36-byte rows.
    {"44a50bc28e7a49f52e725b95a62a7046fcbc99cc9107beae4b448cccbd52dbbc",
     0x40f5000,
     0x3162370,
     0x85ea80,
     0x871395,
     0x5b50a0,
     0x61571c,
     36,
     {0x48, 0x89, 0x5c, 0x24, 0x08, 0x57, 0x48, 0x83, 0xec, 0x40, 0x48, 0x8b, 0xd9, 0x33},
     {0xe8, 0xeb, 0xd6, 0xfe, 0xff},
     {0x66, 0x0f, 0x6e, 0xca, 0x4c, 0x8d, 0x1d, 0x85, 0xe6, 0xcc, 0x02, 0x66, 0x0f, 0x70, 0xc9,
      0x00},
     {0xe8, 0x84, 0xf9, 0xf9, 0xff}},
    // Version6723: separately reviewed exact code and relocated callers.
    {"14a1f187b4dbe0801c805cfb4050dff495601867b1f0c7a21889d47c633a4bf3",
     0x40f5000,
     0x3162370,
     0x85eaf0,
     0x871405,
     0x5b50c0,
     0x61578c,
     36,
     {0x48, 0x89, 0x5c, 0x24, 0x08, 0x57, 0x48, 0x83, 0xec, 0x40, 0x48, 0x8b, 0xd9, 0x33},
     {0xe8, 0xeb, 0xd6, 0xfe, 0xff},
     {0x66, 0x0f, 0x6e, 0xca, 0x4c, 0x8d, 0x1d, 0x65, 0xe6, 0xcc, 0x02, 0x66, 0x0f, 0x70, 0xc9,
      0x00},
     {0xe8, 0x34, 0xf9, 0xf9, 0xff}},
    {"07f65ab6f862517ef6b1049679f78342d572acc589cba54e9851d61380b19e6e",
     0x40f7000,
     0x3162370,
     0x860f70,
     0x873885,
     0x5b5090,
     0x616334,
     36,
     {0x48, 0x89, 0x5c, 0x24, 0x08, 0x57, 0x48, 0x83, 0xec, 0x40, 0x48, 0x8b, 0xd9, 0x33},
     {0xe8, 0xeb, 0xd6, 0xfe, 0xff},
     {0x66, 0x0f, 0x6e, 0xca, 0x4c, 0x8d, 0x1d, 0x65, 0x21, 0xcd, 0x02, 0x66, 0x0f, 0x70, 0xc9,
      0x00},
     {0xe8, 0x5c, 0xed, 0xf9, 0xff}},
    // Build 6731: controller and render loop relocated together; the six
    // event vectors and 36-byte rows are unchanged.
    {"cb244664a4b42057b02788bc95be489140fbba587266fdb40c5faf7d4d0784e3",
     0x40fa000,
     0x3168610,
     0x861a60,
     0x874375,
     0x5b5a90,
     0x616da9,
     36,
     {0x48, 0x89, 0x5c, 0x24, 0x08, 0x57, 0x48, 0x83, 0xec, 0x40, 0x48, 0x8b, 0xd9, 0x33},
     {0xe8, 0xeb, 0xd6, 0xfe, 0xff},
     {0x66, 0x0f, 0x6e, 0xca, 0x4c, 0x8d, 0x1d, 0xe5, 0x48, 0xcd, 0x02, 0x66, 0x0f, 0x70, 0xc9,
      0x00},
     {0xe8, 0xe7, 0xec, 0xf9, 0xff}},
    // Build 6739: controller and render loop relocated together; the six
    // event vectors and 36-byte rows are unchanged.
    {"9979035a0157de0243019c27ad36e3f7ac89b4bb2cfc769dca867f084fcce613",
     0x4150000,
     0x31a15a0,
     0x8629a0,
     0x875c05,
     0x5b80e0,
     0x6193f9,
     36,
     {0x48, 0x89, 0x5c, 0x24, 0x08, 0x57, 0x48, 0x83, 0xec, 0x40, 0x48, 0x8b, 0xd9, 0x33},
     {0xe8, 0x9b, 0xcd, 0xfe, 0xff},
     {0x66, 0x0f, 0x6e, 0xca, 0x4c, 0x8d, 0x1d, 0xb5, 0xde, 0xd0, 0x02, 0x66, 0x0f, 0x70, 0xc9,
      0x00},
     {0xe8, 0xe7, 0xec, 0xf9, 0xff}},
    // Build 6742: the October 2 hotfix is a pure relocation of 6739; the
    // controller, render loop and reviewed bytes are unchanged.
    {"255395880ac91d37c8b71124906f4f48737159a8cf4c9d1732035d1e27927657",
     0x4150000,
     0x31a15a0,
     0x8629a0,
     0x875c05,
     0x5b80e0,
     0x6193f9,
     36,
     {0x48, 0x89, 0x5c, 0x24, 0x08, 0x57, 0x48, 0x83, 0xec, 0x40, 0x48, 0x8b, 0xd9, 0x33},
     {0xe8, 0x9b, 0xcd, 0xfe, 0xff},
     {0x66, 0x0f, 0x6e, 0xca, 0x4c, 0x8d, 0x1d, 0xb5, 0xde, 0xd0, 0x02, 0x66, 0x0f, 0x70, 0xc9,
      0x00},
     {0xe8, 0xe7, 0xec, 0xf9, 0xff}},
    // Build 6745: pure relocation of the 6742 controller/render loop. The
    // entry is an explicit literal so it cannot follow the generated
    // "current client" hash onto a different build.
    {"fbb80c06626074474b35add70ebeadb561199d6cecffaf1f907dd8bbef3620c6",
     0x4150000,
     0x31a15a0,
     0x8629a0,
     0x875c05,
     0x5b80e0,
     0x6193f9,
     36,
     {0x48, 0x89, 0x5c, 0x24, 0x08, 0x57, 0x48, 0x83, 0xec, 0x40, 0x48, 0x8b, 0xd9, 0x33},
     {0xe8, 0x9b, 0xcd, 0xfe, 0xff},
     {0x66, 0x0f, 0x6e, 0xca, 0x4c, 0x8d, 0x1d, 0xb5, 0xde, 0xd0, 0x02, 0x66, 0x0f, 0x70, 0xc9,
      0x00},
     {0xe8, 0xe7, 0xec, 0xf9, 0xff}},
    // Build 6746: the controller and render loop are unchanged; only the
    // option RIP displacement relocated (0x2d0deb5 -> 0x2d0df35). Verified
    // masked-equal with identical instruction lengths (resolve_effects_6746).
    {"f66a0fdfe60029cca711dff32644941303396c2d9d16774a6cb2570c28520b10",
     0x4150000,
     0x31a15a0,
     0x8629a0,
     0x875c05,
     0x5b80e0,
     0x6193f9,
     36,
     {0x48, 0x89, 0x5c, 0x24, 0x08, 0x57, 0x48, 0x83, 0xec, 0x40, 0x48, 0x8b, 0xd9, 0x33},
     {0xe8, 0x9b, 0xcd, 0xfe, 0xff},
     {0x66, 0x0f, 0x6e, 0xca, 0x4c, 0x8d, 0x1d, 0x35, 0xdf, 0xd0, 0x02, 0x66, 0x0f, 0x70, 0xc9,
      0x00},
     {0xe8, 0xe7, 0xec, 0xf9, 0xff}},
    // Build 6753: weight/option/controller and the render loop are pure
    // relocations with paired instruction streams; the render loop gained two
    // instructions, so the screen call site was aligned by sequence diff
    // (resolve_effects_6753 + resolve_screen_caller_6753).
    {"678aec94adb44623e335ee7ec76c08f4477a0ea88a85cea5cb70abace1bacaa8",
     0x415b000,
     0x31aa6c0,
     0x864d80,
     0x877fe5,
     0x5ba0b0,
     0x61b409,
     36,
     {0x48, 0x89, 0x5c, 0x24, 0x08, 0x57, 0x48, 0x83, 0xec, 0x40, 0x48, 0x8b, 0xd9, 0x33},
     {0xe8, 0x9b, 0xcd, 0xfe, 0xff},
     {0x66, 0x0f, 0x6e, 0xca, 0x4c, 0x8d, 0x1d, 0xa5, 0x53, 0xd1, 0x02, 0x66, 0x0f, 0x70, 0xc9,
      0x00},
     {0xe8, 0xa7, 0xec, 0xf9, 0xff}},
    // Build 6779: named controller/factory and full weight, render-update,
    // diagnostic and option bodies reviewed. Screen token 0x66ddb8aa still
    // feeds frame+0x140; its zero branch skips the screen-space render setup.
    {"7602e238c7ce10b2759a3cbf9ad4f219257dd5598e1f69cfa20b46a1785c09db",
     0x4283000,
     0x327d620,
     0x86a260,
     0x87daa5,
     0x5bf7e0,
     0x62050d,
     36,
     {0x48, 0x89, 0x5c, 0x24, 0x08, 0x57, 0x48, 0x83, 0xec, 0x40, 0x48, 0x8b, 0xd9, 0x33},
     {0xe8, 0xbb, 0xc7, 0xfe, 0xff},
     {0x66, 0x0f, 0x6e, 0xca, 0x4c, 0x8d, 0x1d, 0x95, 0xc2, 0xde, 0x02, 0x66, 0x0f, 0x70, 0xc9, 0x00},
     {0xe8, 0xd3, 0xf2, 0xf9, 0xff}},
    // Build 6782: named controller/factory, complete weight and screen
    // render bodies reviewed; world particle simulation remains unchanged.
    {"b7deb2d934614b2b9a4e3edf6cf7bfb954b9f1436aadb0bf07b24f07bb7d5515",
     0x4283000,
     0x327d620,
     0x86a270,
     0x87dab5,
     0x5bf7e0,
     0x62050d,
     36,
     {0x48, 0x89, 0x5c, 0x24, 0x08, 0x57, 0x48, 0x83, 0xec, 0x40, 0x48, 0x8b, 0xd9, 0x33},
     {0xe8, 0xbb, 0xc7, 0xfe, 0xff},
     {0x66, 0x0f, 0x6e, 0xca, 0x4c, 0x8d, 0x1d, 0x15, 0xc2, 0xde, 0x02, 0x66, 0x0f, 0x70, 0xc9, 0x00},
     {0xe8, 0xd3, 0xf2, 0xf9, 0xff}},
};
const GameplayEffectsProfile* gGameplayEffectsProfile = &kGameplayEffectsProfiles[0];

static bool gameplay_camera_owns_view(const Command* command, bool active, bool healthy) noexcept {
    return active && healthy && command && !(command->wire.flags & kGamePov) &&
           command->wire.mode != std::uint32_t(Mode::Release);
}

static bool gameplay_camera_active() noexcept {
    const auto command = std::atomic_load_explicit(&gCommand, std::memory_order_acquire);
    return gameplay_camera_owns_view(command.get(), gGameplayCameraActive.load(),
                                     !gWorkerError.load() &&
                                         now_seconds() - gHeartbeatTime.load() < 2.0);
}

static int gameplay_render_option(void* options, std::uint32_t key, int fallback,
                                  std::uintptr_t caller) noexcept {
    if (caller == gClient + gGameplayEffectsProfile->screen_caller && gameplay_camera_active())
        return 0;
    return gOriginalGameplayRenderOption(options, key, fallback);
}

__declspec(noinline) static int __fastcall gameplay_render_option_hook(void* options,
                                                                       std::uint32_t key,
                                                                       int fallback) {
#if defined(_MSC_VER)
    const auto caller = reinterpret_cast<std::uintptr_t>(_ReturnAddress());
#else
    const auto caller = reinterpret_cast<std::uintptr_t>(__builtin_return_address(0));
#endif
    return gameplay_render_option(options, key, fallback, caller);
}

static bool gameplay_damage_or_death(std::uintptr_t controller, std::uintptr_t row) noexcept {
    // The controller's Damaged and Killed vectors are the first two of six.
    // The exact build selects the event row size. Other game-state effects and calls
    // outside the controller's render update always keep the game's result.
    for (unsigned i = 0; i < 2; ++i) {
        const auto vector = controller + 0x20 + i * 0x28;
        std::int32_t count = 0;
        std::uintptr_t first = 0;
        if (!read_value(vector, count) || count <= 0 || count > 4096 ||
            !read_value(vector + 8, first) || first < 0x10000 || row < first)
            continue;
        const auto offset = row - first;
        const auto row_size = gGameplayEffectsProfile->row_size;
        if (offset < std::uintptr_t(count) * row_size && offset % row_size == 0)
            return true;
    }
    return false;
}

static float gameplay_effect_weight(void* effect, std::uintptr_t caller) noexcept {
    if (caller == gClient + gGameplayEffectsProfile->weight_caller && gameplay_camera_active() &&
        gameplay_damage_or_death(gClient + gGameplayEffectsProfile->controller,
                                 reinterpret_cast<std::uintptr_t>(effect)))
        return 0.0f;
    return gOriginalGameplayEffectWeight(effect);
}

__declspec(noinline) static float __fastcall gameplay_effect_weight_hook(void* effect) {
#if defined(_MSC_VER)
    const auto caller = reinterpret_cast<std::uintptr_t>(_ReturnAddress());
#else
    const auto caller = reinterpret_cast<std::uintptr_t>(__builtin_return_address(0));
#endif
    return gameplay_effect_weight(effect, caller);
}

static CapabilityResult install_gameplay_effect_filter(HMODULE client) {
    // Optional for older reviewed camera profiles. Never use these addresses
    // for a signature-only camera match or a different client fingerprint.
    const GameplayEffectsProfile* selected = nullptr;
    for (const auto& profile : kGameplayEffectsProfiles) {
        if (module_matches(client, profile.hash, profile.image_size)) {
            selected = &profile;
            break;
        }
    }
    if (!selected)
        return {CapabilityState::NotRequired, CapabilityReason::None};
    unsigned char weight[sizeof(kGameplayEffectWeightBytes)]{};
    unsigned char caller[sizeof(kGameplayEffectCallBytes)]{};
    unsigned char option[sizeof(kGameplayRenderOptionBytes)]{};
    unsigned char screen_call[sizeof(kGameplayScreenParticlesCallBytes)]{};
    if (!read_memory(gClient + selected->weight, weight, sizeof(weight)) ||
        std::memcmp(weight, selected->weight_bytes.data(), sizeof(weight)) ||
        !read_memory(gClient + selected->weight_caller - sizeof(caller), caller, sizeof(caller)) ||
        std::memcmp(caller, selected->weight_call.data(), sizeof(caller)) ||
        !read_memory(gClient + selected->option, option, sizeof(option)) ||
        std::memcmp(option, selected->option_bytes.data(), sizeof(option)) ||
        !read_memory(gClient + selected->screen_caller - sizeof(screen_call), screen_call,
                     sizeof(screen_call)) ||
        std::memcmp(screen_call, selected->screen_call.data(), sizeof(screen_call)))
        return {CapabilityState::Unavailable, CapabilityReason::SignatureMismatch};
    gGameplayEffectsProfile = selected;
    auto target = reinterpret_cast<void*>(gClient + selected->weight);
    const bool installed =
        MH_CreateHook(target, reinterpret_cast<void*>(gameplay_effect_weight_hook),
                      reinterpret_cast<void**>(&gOriginalGameplayEffectWeight)) == MH_OK &&
        MH_EnableHook(target) == MH_OK &&
        MH_CreateHook(reinterpret_cast<void*>(gClient + selected->option),
                      reinterpret_cast<void*>(gameplay_render_option_hook),
                      reinterpret_cast<void**>(&gOriginalGameplayRenderOption)) == MH_OK &&
        MH_EnableHook(reinterpret_cast<void*>(gClient + selected->option)) == MH_OK;
    return installed
               ? CapabilityResult{CapabilityState::Ready, CapabilityReason::None}
               : CapabilityResult{CapabilityState::Unavailable, CapabilityReason::HookInstallation};
}
