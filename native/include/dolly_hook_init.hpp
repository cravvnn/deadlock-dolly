#pragma once
#include "MinHook.h"
namespace dolly {
// Optional startup instrumentation may initialize our shared MinHook instance
// before the camera. Already initialized is success, not a startup fault.
inline bool initialize_hook_library() noexcept {
    const auto status=MH_Initialize();
    return status==MH_OK || status==MH_ERROR_ALREADY_INITIALIZED;
}
}
