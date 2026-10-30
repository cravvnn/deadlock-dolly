#include "dolly_runtime_generated.hpp"
#include "dolly_minimap_guard.hpp"
#include <array>
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

// Executed by the real reviewed-contract native target.
static void minimap_guard_cases() {
    using namespace dolly;
    std::array<unsigned char, 0x100> icon{};
    const std::uintptr_t expected = 0x123456;
    std::uintptr_t table = expected;
    bool readable = true, playing = true;
    unsigned replay_reads = 0;
    const auto self = reinterpret_cast<std::uintptr_t>(&table);
    const auto item = reinterpret_cast<std::uintptr_t>(icon.data());
    const MinimapIconLayout layout{expected, 4, 128};
    const auto read = [&](std::uintptr_t address, auto& value) noexcept {
        if (!readable)
            return false;
        std::memcpy(&value, reinterpret_cast<const void*>(address), sizeof(value));
        return true;
    };
    const auto replay = [&]() noexcept {
        ++replay_reads;
        return playing;
    };
    const auto skip = [&](bool caller = true) {
        return incomplete_minimap_icon(caller, layout, self, item, read, replay);
    };
    for (std::uint32_t type : {1u, 45u, 88u, 92u, 121u}) {
        std::memcpy(icon.data() + 4, &type, sizeof(type));
        std::uintptr_t range = 0;
        std::memcpy(icon.data() + 128, &range, sizeof(range));
        const auto before = icon;
        const bool affected = type == 45 || type == 88 || type == 92;
        replay_reads = 0;
        assert(skip() == affected);
        assert(replay_reads == unsigned(affected));
        assert(icon == before); // The guard never edits game/HUD objects.
        assert(!skip(false));
        playing = false;
        assert(!skip());
        playing = true;
        table = expected + 8;
        assert(!skip());
        table = expected;
        readable = false;
        assert(!skip());
        readable = true;
        range = 0xabcdef;
        std::memcpy(icon.data() + 128, &range, sizeof(range));
        replay_reads = 0;
        assert(!skip());
        assert(replay_reads == 0);
    }
    assert(!incomplete_minimap_icon(true, layout, 0, item, read, replay));
    assert(!incomplete_minimap_icon(true, layout, self, 0, read, replay));
}
int main() {
    minimap_guard_cases();
    using namespace dolly::reviewed;
    // Old correction profiles must never acquire the newer pawn member simply
    // because the current Python observer monitor was updated.
    static_assert(FollowTarget::OBSERVER_SERVICES_OFFSET == 0xe98);
    static_assert(sizeof(kFollowAnchorProfiles) / sizeof(*kFollowAnchorProfiles) == 16);
    unsigned historical = 0, reviewed_current = 0;
    for (const auto& profile : kFollowAnchorProfiles) {
        // Keep this reviewed correction independent of future camera-only reviews.
        if (!std::strcmp(profile.hash,
                         "9fd12e81f4587f0f42a99cb8668eafb3fa25f15dadfd49d7d502003aa3d01160")) {
            ++reviewed_current;
            assert(profile.observer_services_offset == 0xe98);
            assert(profile.local_player_table == 0x279b310);
        } else if (!std::strcmp(profile.hash,
                         "459e8a0f4ee93a8c30324c8229b8846b39b3e6647afd60c660e0743e23cc4773")) {
            ++reviewed_current;
            assert(profile.observer_services_offset == 0xe98);
            assert(profile.local_player_table == 0x279b310);
        } else if (!std::strcmp(profile.hash,
                         "27a04887068b431f57e93421beeb47b1bcf80d50817bbb226b72c12edcfecf49")) {
            ++reviewed_current;
            assert(profile.observer_services_offset == 0xe98);
            assert(profile.local_player_table == 0x279b310);
        } else if (!std::strcmp(profile.hash,
                         "87b822441e3f3edadf096f2f46dd485778ab8c7b7678d814d0d4e67332b37c67")) {
            ++reviewed_current;
            assert(profile.observer_services_offset == 0xe98);
            assert(profile.local_player_table == 0x279b310);
        } else if (!std::strcmp(profile.hash,
                         "576b3c06dec3cab3675521ffcc8931bf5f140145ccf848427957a0a13ef99b16")) {
            ++reviewed_current;
            assert(profile.observer_services_offset == 0xe98);
            assert(profile.local_player_table == 0x279a180);
        } else if (!std::strcmp(profile.hash,
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
    assert(historical == 3 && reviewed_current == 13);
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
