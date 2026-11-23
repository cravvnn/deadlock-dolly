// Included inside bridge scope; worker-owned status, never engine memory writes.
#pragma once
CapabilitySnapshot gCapabilities;

static void publish_native_capabilities() noexcept {
    if (!gMemory)
        return;
    auto value = gCapabilities;
    value.game_pid = GetCurrentProcessId();
    publish_seqlocked(gMemory + kCapabilitiesOffset, value);
}
