#include "dolly_depth_scene.hpp"
#include <d3d11.h>
#include <MinHook.h>
#include <array>
#include <atomic>
#include <mutex>
#include <vector>

namespace dolly::depth {
namespace {
std::shared_ptr<SceneTracker> active;
std::mutex install_mutex;
// Public ID3D11DeviceContext ABI, shared by Context1/2/3/4. Both immediate
// and deferred implementations are installed; their entry points may differ.
constexpr std::array<unsigned, 10> slots{12, 13, 20, 21, 38, 39, 40, 53, 58, 114};
std::array<std::array<void*, 10>, 2> originals{}, targets{};
bool installed = false;
void observe_draw(ID3D11DeviceContext* context) noexcept {
    if (auto tracker = std::atomic_load(&active))
        tracker->draw(context);
}
template <unsigned I>
void STDMETHODCALLTYPE indexed(ID3D11DeviceContext* c, UINT n, UINT start, INT base) {
    using Fn = void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, UINT, UINT, INT);
    reinterpret_cast<Fn>(originals[I][0])(c, n, start, base);
    if (n)
        observe_draw(c);
}
template <unsigned I> void STDMETHODCALLTYPE draw(ID3D11DeviceContext* c, UINT n, UINT start) {
    using Fn = void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, UINT, UINT);
    reinterpret_cast<Fn>(originals[I][1])(c, n, start);
    if (n)
        observe_draw(c);
}
template <unsigned I>
void STDMETHODCALLTYPE indexed_instanced(ID3D11DeviceContext* c, UINT n, UINT instances, UINT start,
                                         INT base, UINT first) {
    using Fn = void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, UINT, UINT, UINT, INT, UINT);
    reinterpret_cast<Fn>(originals[I][2])(c, n, instances, start, base, first);
    if (n && instances)
        observe_draw(c);
}
template <unsigned I>
void STDMETHODCALLTYPE instanced(ID3D11DeviceContext* c, UINT n, UINT instances, UINT start,
                                 UINT first) {
    using Fn = void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, UINT, UINT, UINT, UINT);
    reinterpret_cast<Fn>(originals[I][3])(c, n, instances, start, first);
    if (n && instances)
        observe_draw(c);
}
template <unsigned I> void STDMETHODCALLTYPE automatic(ID3D11DeviceContext* c) {
    using Fn = void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*);
    reinterpret_cast<Fn>(originals[I][4])(c);
    observe_draw(c);
}
template <unsigned I, unsigned M>
void STDMETHODCALLTYPE indirect(ID3D11DeviceContext* c, ID3D11Buffer* args, UINT offset) {
    using Fn = void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, ID3D11Buffer*, UINT);
    reinterpret_cast<Fn>(originals[I][M])(c, args, offset);
    observe_draw(c);
}
template <unsigned I>
void STDMETHODCALLTYPE clear(ID3D11DeviceContext* c, ID3D11DepthStencilView* dsv, UINT flags,
                             FLOAT depth, UINT8 stencil) {
    using Fn =
        void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, ID3D11DepthStencilView*, UINT, FLOAT, UINT8);
    reinterpret_cast<Fn>(originals[I][7])(c, dsv, flags, depth, stencil);
    if (auto tracker = std::atomic_load(&active))
        tracker->clear(c, dsv, flags);
}
template <unsigned I>
void STDMETHODCALLTYPE execute(ID3D11DeviceContext* c, ID3D11CommandList* list, BOOL restore) {
    using Fn = void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, ID3D11CommandList*, BOOL);
    reinterpret_cast<Fn>(originals[I][8])(c, list, restore);
    if (auto tracker = std::atomic_load(&active))
        tracker->execute(c, list);
}
template <unsigned I>
HRESULT STDMETHODCALLTYPE finish(ID3D11DeviceContext* c, BOOL restore, ID3D11CommandList** list) {
    using Fn = HRESULT(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, BOOL, ID3D11CommandList**);
    const auto result = reinterpret_cast<Fn>(originals[I][9])(c, restore, list);
    if (SUCCEEDED(result) && list && *list)
        if (auto tracker = std::atomic_load(&active))
            tracker->finish(c, *list);
    return result;
}
template <unsigned I> std::array<void*, 10> detours() {
    return {
        reinterpret_cast<void*>(&indexed<I>),           reinterpret_cast<void*>(&draw<I>),
        reinterpret_cast<void*>(&indexed_instanced<I>), reinterpret_cast<void*>(&instanced<I>),
        reinterpret_cast<void*>(&automatic<I>),         reinterpret_cast<void*>(&indirect<I, 5>),
        reinterpret_cast<void*>(&indirect<I, 6>),       reinterpret_cast<void*>(&clear<I>),
        reinterpret_cast<void*>(&execute<I>),           reinterpret_cast<void*>(&finish<I>)};
}
}
void set_scene_tracker(std::shared_ptr<SceneTracker> tracker) noexcept {
    std::atomic_store(&active, std::move(tracker));
}
bool install_scene_hooks(ID3D11Device* device, ID3D11DeviceContext* immediate) noexcept {
    if (!device || !immediate || immediate->GetType() != D3D11_DEVICE_CONTEXT_IMMEDIATE)
        return false;
    ID3D11Device* owner = nullptr;
    immediate->GetDevice(&owner);
    const bool owned = owner == device;
    if (owner)
        owner->Release();
    if (!owned)
        return false;
    ID3D11DeviceContext* deferred = nullptr;
    if (FAILED(device->CreateDeferredContext(0, &deferred)))
        return false;
    std::array<std::array<void*, 10>, 2> found{};
    auto** a = *reinterpret_cast<void***>(immediate);
    auto** b = *reinterpret_cast<void***>(deferred);
    for (unsigned m = 0; m < slots.size(); ++m) {
        found[0][m] = a[slots[m]];
        found[1][m] = b[slots[m]];
    }
    deferred->Release();
    try {
        std::lock_guard<std::mutex> lock(install_mutex);
        if (installed)
            return found == targets;
        const auto init = MH_Initialize();
        if (init != MH_OK && init != MH_ERROR_ALREADY_INITIALIZED)
            return false;
        const std::array<std::array<void*, 10>, 2> callbacks{detours<0>(), detours<1>()};
        std::vector<void*> created;
        created.reserve(20);
        const auto rollback = [&created]() {
            for (auto* target : created) {
                MH_DisableHook(target);
                MH_RemoveHook(target);
            }
        };
        for (unsigned i = 0; i < 2; ++i) {
            for (unsigned m = 0; m < slots.size(); ++m) {
                if (i && found[i][m] == found[0][m]) {
                    originals[i][m] = originals[0][m];
                    continue;
                }
                if (MH_CreateHook(found[i][m], callbacks[i][m], &originals[i][m]) != MH_OK) {
                    rollback();
                    return false;
                }
                created.push_back(found[i][m]);
            }
        }
        // Enable only this module's hooks. Do not affect the overlay or any
        // other MinHook client sharing the process.
        for (auto* target : created) {
            if (MH_EnableHook(target) != MH_OK) {
                rollback();
                return false;
            }
        }
        targets = found;
        installed = true;
        return true;
    } catch (...) {
        return false;
    }
}
}
