#pragma once
#include <cstdint>

namespace dolly::prop_runtime {

// EXPERIMENTAL persistence probe for real engine props.
//
// The engine's console prop-spawn commands (prop_dynamic_create /
// prop_physics_create) are server-side and no-op in a replay (proven live:
// even with sv_cheats enabled the entity count never changes). The only
// in-process path is the CLIENT entity system, so this unit calls the client's
// own create-by-classname entry directly.
//
// This is intentionally a PROBE, not a feature: it creates exactly ONE
// model-less prop_dynamic (no model binding, so the CSkeletonInstance::SetModel
// nonresident-asset fatal assert cannot fire) and reports whether the entity
// count changed and whether it survives a replay tick/seek. It is hash-gated and
// fails closed.
//
// A wrong argument to the engine factory is a hard Plat_FatalError (process
// kill). That is the accepted risk of this probe; it never writes config, never
// oversteps the -dev -insecure owned session, and touches nothing but one
// client entity.

struct Diagnostics {
    std::uint32_t state{};        // 0 unavailable, 1 ready, 2 created, 3 fault
    std::uint32_t resolved{};     // bit0 create fn row matched
    std::uint32_t before_count{};
    std::uint32_t after_count{};
    std::uint32_t entity_handle{};  // raw result pointer low bits (evidence only)
    std::uint32_t calls{};
    std::uint32_t error{};          // 0 none, 1 bad base, 2 no entity system, 3 create returned null
    std::uint32_t reserved{};
};

// Resolve the client create entry point against the running module. Returns
// false (state unavailable) when the build does not match the reviewed row.
bool initialize(std::uintptr_t client_base) noexcept;

// Clear the probe state.
void disable() noexcept;

// Queue exactly one probe on the worker. ``recount_only`` re-counts entities
// without creating (used to test survival after a tick/seek). Thread-safe; the
// request is consumed by the next tick().
void request(bool recount_only) noexcept;

// Execute a pending probe. Must be called on the game/render thread (the same
// thread that drives confetti::on_frame). Does nothing when no request is
// queued.
void tick() noexcept;

const char* status() noexcept;
Diagnostics diagnostics() noexcept;

} // namespace dolly::prop_runtime
