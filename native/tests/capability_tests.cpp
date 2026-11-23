#include "dolly_capabilities.hpp"
#include "dolly_optional_hooks.hpp"
#include "../src/dolly_seqlock_win.hpp"
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <cassert>
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <memory>

// Model a hook engine that can fail at each installation boundary, including
// after enabling the first callback. Never permit removal of exposed code.
struct Hooks {
    unsigned fail = 0, step = 0;
    std::array<bool, 2> created{}, queued{}, enabled{}, exposed{}, removed{};
    bool next() { return ++step != fail; }
    bool create(unsigned i) { return created[i] = next(); }
    bool queue_enable(unsigned i) {
        assert(created[i]);
        return queued[i] = next();
    }
    void queue_disable(unsigned i) { queued[i] = false; }
    void remove(unsigned i) {
        assert(!exposed[i]);
        created[i] = queued[i] = false;
        removed[i] = true;
    }
    void disable(unsigned i) {
        // MinHook returns MH_ERROR_DISABLED without changing queueEnable for
        // an already-disabled hook. Preserve that behavior in the failure case.
        if (enabled[i])
            enabled[i] = queued[i] = false;
    }
    bool apply() {
        enabled[0] = exposed[0] = true;
        if (!next())
            return false;
        enabled[1] = exposed[1] = true;
        return true;
    }
};

int main() {
    using namespace dolly;
    for (unsigned failure = 0; failure <= 5; ++failure) {
        Hooks hooks;
        hooks.fail = failure;
        const bool installed = install_optional_pair(hooks);
        assert(installed == (failure == 0));
        if (installed) {
            assert(hooks.enabled[0] && hooks.enabled[1]);
        } else {
            assert(!hooks.enabled[0] && !hooks.enabled[1]);
            if (hooks.queued[0] || hooks.queued[1]) {
                std::fputs("Rejected hook remains queued for a later activation\n", stderr);
                return 1;
            }
            if (failure == 5) {
                assert(hooks.created[0] && hooks.created[1]);
                assert(!hooks.removed[0] && !hooks.removed[1]);
            } else {
                assert(!hooks.created[0] && !hooks.created[1]);
            }
        }
        CapabilitySnapshot report;
        report.at(Capability::CameraCore) = {CapabilityState::Ready, CapabilityReason::None};
        report.at(Capability::CameraEffects) = {CapabilityState::Ready, CapabilityReason::None};
        report.at(Capability::FollowAnchor) =
            installed ? CapabilityResult{CapabilityState::Ready, CapabilityReason::None}
                      : CapabilityResult{CapabilityState::Unavailable,
                                         CapabilityReason::HookInstallation};
        assert(report.camera_available());
        assert(report.follow_available() == installed);
        report.at(Capability::CameraEffects) = {CapabilityState::Unavailable,
                                                CapabilityReason::SignatureMismatch};
        assert(!report.camera_available() && !report.follow_available());
    }
    CapabilitySnapshot legacy_profile;
    legacy_profile.at(Capability::CameraCore) = {CapabilityState::Ready, CapabilityReason::None};
    legacy_profile.at(Capability::CameraEffects) = {CapabilityState::NotRequired,
                                                    CapabilityReason::None};
    legacy_profile.at(Capability::FollowAnchor) = {CapabilityState::NotRequired,
                                                   CapabilityReason::None};
    assert(legacy_profile.follow_available());
    legacy_profile.at(Capability::CameraCore) = {};
    assert(!legacy_profile.camera_available() && !legacy_profile.follow_available());
    // The shared seqlock writer used for capabilities and diagnostics: magic
    // and payload copied, the sequence left odd-free and advanced by two.
    struct Block {
        char magic[8];
        std::uint32_t sequence, abi;
        double payload[3];
    };
    alignas(8) unsigned char shared[sizeof(Block)]{};
    Block block{{'D', 'L', 'Y', 'T', 'E', 'S', 'T', '1'}, 99, 7, {1.5, -2, 3}};
    const auto sequence_at = [&] {
        std::uint32_t value = 0;
        std::memcpy(&value, shared + 8, 4);
        return value;
    };
    publish_seqlocked(shared, block);
    Block read{};
    std::memcpy(&read, shared, sizeof(read));
    assert(sequence_at() == 2 && std::memcmp(read.magic, block.magic, 8) == 0 && read.abi == 7 &&
           read.payload[0] == 1.5 && read.payload[1] == -2 && read.payload[2] == 3);
    const std::uint32_t torn = 5; // a writer that died mid-publish
    std::memcpy(shared + 8, &torn, 4);
    block.payload[2] = 4;
    publish_seqlocked(shared, block);
    std::memcpy(&read, shared, sizeof(read));
    assert(sequence_at() == 8 && read.payload[2] == 4);
    // The matching reader: accepts a new even sequence once, carries the
    // observed sequence, and skips odd (mid-write) or invalid blocks.
    std::shared_ptr<const Block> slot;
    const auto valid = [](const Block& value) { return value.abi == 7; };
    Block accepted{};
    assert(accept_seqlocked(shared, slot, valid, accepted) && slot && slot->sequence == 8 &&
           accepted.sequence == 8 && accepted.payload[2] == 4);
    assert(!accept_seqlocked(shared, slot, valid)); // unchanged sequence
    const std::uint32_t writing = 9;
    std::memcpy(shared + 8, &writing, 4);
    assert(!accept_seqlocked(shared, slot, valid) && slot->sequence == 8);
    block.abi = 8;
    publish_seqlocked(shared, block);
    assert(!accept_seqlocked(shared, slot, valid) && slot->sequence == 8 && slot->abi == 7);
    block.abi = 7;
    block.payload[0] = 6;
    publish_seqlocked(shared, block);
    assert(accept_seqlocked(shared, slot, valid) && slot->sequence == 14 && slot->payload[0] == 6);
}
