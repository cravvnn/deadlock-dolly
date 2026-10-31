#include "dolly_runtime_generated.hpp"
#include <cstddef>
#include <cstdint>
#include <cstring>
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <cassert>
namespace {
#include "dolly_follow_anchor_generated.hpp"
}

int main() {
    using namespace dolly::reviewed;
    // Old correction profiles must never acquire the newer pawn member simply
    // because the current Python observer monitor was updated.
    static_assert(FollowTarget::OBSERVER_SERVICES_OFFSET == 0xe98);
    static_assert(sizeof(kFollowAnchorProfiles) / sizeof(*kFollowAnchorProfiles) == 11);
    unsigned historical = 0, reviewed_current = 0;
    for (const auto& profile : kFollowAnchorProfiles) {
        // Keep this reviewed correction independent of future camera-only reviews.
        if (!std::strcmp(profile.hash,
                         "8da25ddcd11c1146eeb9770bca24fb25c594c7abdb28947035a44ec8f2945843")) {
            ++reviewed_current;
            assert(profile.observer_services_offset == 0xe98);
            assert(profile.local_player_table == 0x276d3d8);
        } else if (!std::strcmp(profile.hash,
                         "d61348c564ce931e969996af18dbdec8544643810f28f3d330fb94b33231c460") ||
            !std::strcmp(profile.hash,
                         "8dff04c139d20b78d8d2c7a3039b691cfc221b8460438ec6fc6107c315741a5c") ||
            !std::strcmp(profile.hash,
                         "a7d0d9e88d11f09a36294569662718394cfa36d99286774ecf8c9fd6ea4f589e") ||
            !std::strcmp(profile.hash,
                         "96d84cb1120e05cb030a279801ea0e65b544ac93db1e6ce7022cefb65ee51843") ||
            !std::strcmp(profile.hash,
                         "ca6aee3b46fc63e84c89e34fbf9c51f3127abceaec17a1bc6aa9e9d1ff482b53") ||
            !std::strcmp(profile.hash,
                         "3a333e279f2fa4ad6270a3406f8039ce349f20ef787bae2e73c784de768c6817") ||
            !std::strcmp(profile.hash,
                         "96ee2a334a93a6a0f40ed56c0cedf441b882681b9605deeb46d83d62d4a3e4b9")) {
            ++reviewed_current;
            assert(profile.observer_services_offset == 0xe98);
            const bool harrow_build = !std::strcmp(profile.hash,
                "a7d0d9e88d11f09a36294569662718394cfa36d99286774ecf8c9fd6ea4f589e") || !std::strcmp(profile.hash,
                "96d84cb1120e05cb030a279801ea0e65b544ac93db1e6ce7022cefb65ee51843") || !std::strcmp(profile.hash,
                "ca6aee3b46fc63e84c89e34fbf9c51f3127abceaec17a1bc6aa9e9d1ff482b53") || !std::strcmp(profile.hash,
                "3a333e279f2fa4ad6270a3406f8039ce349f20ef787bae2e73c784de768c6817") || !std::strcmp(profile.hash,
                "96ee2a334a93a6a0f40ed56c0cedf441b882681b9605deeb46d83d62d4a3e4b9");
            assert(profile.local_player_table == (harrow_build ? 0x276c3d8 : 0x2748d20));
        } else {
            ++historical;
            assert(profile.observer_services_offset == 0xe40);
            assert(profile.local_player_table == 0);
        }
        bool composition = false, blend = false;
        for (std::size_t i = 0; i < profile.count; ++i) {
            const auto& span = profile.spans[i];
            assert(span.rva + span.size <= profile.image_size);
            composition |= span.rva == profile.composition;
            blend |= span.rva == profile.blend;
        }
        assert(composition && blend);
    }
    assert(historical == 3 && reviewed_current == 8);
    // A network vector contains metadata between its float components. Verify
    // the shared decoder indices against a fixed layout fixture.
    unsigned char raw[36]{};
    const float expected[] = {12.f, -34.f, 56.f};
    for (unsigned i = 0; i < 3; ++i)
        std::memcpy(raw + 16 + i * 8, expected + i, 4);
    for (unsigned i = 0; i < 3; ++i) {
        float observed = 0;
        std::memcpy(&observed, raw + kViewOffsetComponents[i], 4);
        assert(observed == expected[i]);
    }
    static_assert(static_cast<unsigned>(AttachFieldIndex::scene_node) == 0);
    static_assert(static_cast<unsigned>(AttachFieldIndex::eye_angles) == 5);
    static_assert(static_cast<unsigned>(AttachFieldIndex::scene_sibling) == 7);
}
