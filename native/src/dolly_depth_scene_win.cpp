#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <d3d11_1.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <unordered_map>
#include <vector>
#include "dolly_depth_scene.hpp"

namespace dolly::depth {
namespace {
template <class T> struct Com {
    T* p = nullptr;
    ~Com() { reset(); }
    Com() = default;
    Com(const Com&) = delete;
    Com& operator=(const Com&) = delete;
    void reset(T* value = nullptr) noexcept {
        if (p)
            p->Release();
        p = value;
    }
};
constexpr std::size_t kBindings = 14, kMaxEvents = 64, kMaxContexts = 64;
const GUID kCommandData = {
    0x421d0f73, 0xbd87, 0x4eda, {0xa1, 0x79, 0x55, 0x0c, 0x94, 0x4c, 0x1b, 0x4e}};
const GUID kCommandType = {
    0x2c1e2256, 0x1747, 0x49e6, {0x87, 0x05, 0x5b, 0x2a, 0x6c, 0x0d, 0xae, 0xa9}};
std::atomic<std::uint64_t> next_epoch{0};
}

struct SceneResources {
    Com<ID3D11Texture2D> depth;
    std::array<Com<ID3D11Buffer>, kBindings> buffers;
    std::array<std::uint32_t, kBindings> sizes{};
    std::size_t count = 0;
};
ID3D11Texture2D* SceneFrame::texture() const noexcept {
    return resources ? resources->depth.p : nullptr;
}
std::size_t SceneFrame::calibration(ProjectionBuffer* output, std::size_t capacity) const noexcept {
    if (!resources || !output || capacity < resources->count)
        return 0;
    for (std::size_t i = 0; i < resources->count; ++i)
        output[i] = {resources->buffers[i].p, 0, resources->sizes[i]};
    return resources->count;
}

namespace {
struct Event {
    bool clear = false;
    std::shared_ptr<SceneResources> source;
    bool incompatible_view = false;
};
struct CommandData {
    std::uint64_t epoch = 0;
    std::vector<Event> events;
};
struct CommandInterface : IUnknown {
    virtual const CommandData& data() const noexcept = 0;
};
class CommandHolder final : public CommandInterface {
    std::atomic<ULONG> refs{1};
    CommandData value;

public:
    explicit CommandHolder(CommandData data) : value(std::move(data)) {}
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID id, void** out) override {
        if (!out)
            return E_POINTER;
        *out = nullptr;
        if (id != __uuidof(IUnknown) && id != kCommandType)
            return E_NOINTERFACE;
        *out = static_cast<CommandInterface*>(this);
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs; }
    ULONG STDMETHODCALLTYPE Release() override {
        const auto left = --refs;
        if (!left)
            delete this;
        return left;
    }
    const CommandData& data() const noexcept override { return value; }
};
}

struct SceneTracker::Impl {
    Com<ID3D11Device> device;
    std::uint32_t width, height;
    std::uint64_t epoch = ++next_epoch;
    std::mutex mutex;
    std::atomic<bool> failed{false};
    struct Context {
        Com<ID3D11DeviceContext> owner;
        std::vector<Event> events;
    };
    std::unordered_map<ID3D11DeviceContext*, Context> contexts;
    SceneFrame frame;
    Com<ID3D11Texture2D> first_source;

    Impl(ID3D11Device* value, std::uint32_t w, std::uint32_t h) : width(w), height(h) {
        device.p = value;
        if (value)
            value->AddRef();
        if (!value || !w || !h || w > 16384 || h > 16384)
            failed = true;
    }
    bool owns(ID3D11DeviceContext* context) const noexcept {
        if (!context || failed)
            return false;
        Com<ID3D11Device> owner;
        context->GetDevice(&owner.p);
        return owner.p == device.p;
    }
    bool source(ID3D11DepthStencilView* view, Com<ID3D11Texture2D>& result) const noexcept {
        if (!view)
            return false;
        Com<ID3D11Resource> resource;
        view->GetResource(&resource.p);
        if (!resource.p || FAILED(resource.p->QueryInterface(__uuidof(ID3D11Texture2D),
                                                             reinterpret_cast<void**>(&result.p))))
            return false;
        D3D11_TEXTURE2D_DESC desc{};
        result.p->GetDesc(&desc);
        D3D11_DEPTH_STENCIL_VIEW_DESC dsv{};
        view->GetDesc(&dsv);
        if (desc.Width != width || desc.Height != height || desc.ArraySize != 1 ||
            desc.MipLevels != 1 || desc.SampleDesc.Count != 1 ||
            dsv.ViewDimension != D3D11_DSV_DIMENSION_TEXTURE2D || dsv.Texture2D.MipSlice != 0 ||
            (dsv.Flags & D3D11_DSV_READ_ONLY_DEPTH) != 0 ||
            (dsv.Format != DXGI_FORMAT_D24_UNORM_S8_UINT && dsv.Format != DXGI_FORMAT_D32_FLOAT))
            return false;
        // This is the scene scratch target identified in both reviewed game
        // captures, not a RenderDoc resource ID or a largest-texture guess.
        char expected[128]{}, name[128]{};
        std::snprintf(expected, sizeof(expected), "scratchrendertarget_1118301577_%ux%u_17_1.vtex",
                      width, height);
        UINT size = sizeof(name) - 1;
        if (FAILED(result.p->GetPrivateData(WKPDID_D3DDebugObjectName, &size, name)) ||
            size >= sizeof(name))
            return false;
        return std::strcmp(name, expected) == 0;
    }
    Context* record(ID3D11DeviceContext* context) {
        auto found = contexts.find(context);
        if (found != contexts.end())
            return &found->second;
        if (contexts.size() >= kMaxContexts) {
            failed = true;
            return nullptr;
        }
        auto& entry = contexts.try_emplace(context).first->second;
        entry.owner.p = context;
        context->AddRef();
        return &entry;
    }
    void apply(const Event& event) {
        if (frame.result == SceneResult::ambiguous ||
            frame.result == SceneResult::untracked_commands ||
            frame.result == SceneResult::incompatible_view)
            return;
        if (event.incompatible_view) {
            frame = {SceneResult::incompatible_view, {}};
            return;
        }
        if (event.clear) {
            if (frame.resources && frame.resources->depth.p == event.source->depth.p) {
                frame.resources.reset();
                frame.result = SceneResult::cleared;
            }
            return;
        }
        if (first_source.p && first_source.p != event.source->depth.p) {
            frame = {SceneResult::ambiguous, {}};
            return;
        }
        if (!first_source.p) {
            first_source.p = event.source->depth.p;
            first_source.p->AddRef();
        }
        frame = {SceneResult::ready, event.source};
    }
    bool snapshot(ID3D11DeviceContext* context, SceneResources& target) {
        std::array<ID3D11Buffer*, kBindings> bindings{};
        std::array<UINT, kBindings> first{}, counts{};
        Com<ID3D11DeviceContext1> extended;
        if (SUCCEEDED(context->QueryInterface(__uuidof(ID3D11DeviceContext1),
                                              reinterpret_cast<void**>(&extended.p)))) {
            extended.p->VSGetConstantBuffers1(0, kBindings, bindings.data(), first.data(),
                                              counts.data());
        } else {
            context->VSGetConstantBuffers(0, kBindings, bindings.data());
        }
        bool good = true;
        target.count = 0;
        for (std::size_t slot = 0; slot < kBindings; ++slot) {
            Com<ID3D11Buffer> bound;
            bound.p = bindings[slot];
            if (!bound.p)
                continue;
            D3D11_BUFFER_DESC desc{};
            bound.p->GetDesc(&desc);
            const auto offset = std::uint64_t(first[slot]) * 16;
            if (offset > desc.ByteWidth)
                continue;
            auto bytes = std::min<std::uint64_t>(640, desc.ByteWidth - offset);
            if (extended.p)
                bytes = std::min<std::uint64_t>(bytes, std::uint64_t(counts[slot]) * 16);
            if (bytes < 464)
                continue;
            const auto index = target.count++;
            if (!target.buffers[index].p || target.sizes[index] != bytes) {
                target.buffers[index].reset();
                D3D11_BUFFER_DESC copy{};
                copy.ByteWidth = static_cast<UINT>(bytes);
                copy.Usage = D3D11_USAGE_DEFAULT;
                if (FAILED(device.p->CreateBuffer(&copy, nullptr, &target.buffers[index].p))) {
                    good = false;
                    continue;
                }
                target.sizes[index] = static_cast<UINT>(bytes);
            }
            D3D11_BOX range{static_cast<UINT>(offset),         0, 0,
                            static_cast<UINT>(offset + bytes), 1, 1};
            context->CopySubresourceRegion(target.buffers[index].p, 0, 0, 0, 0, bound.p, 0, &range);
        }
        return good && target.count > 0;
    }
};

SceneTracker::SceneTracker(ID3D11Device* device, std::uint32_t width, std::uint32_t height)
    : impl(std::make_unique<Impl>(device, width, height)) {}
SceneTracker::~SceneTracker() = default;

void SceneTracker::draw(ID3D11DeviceContext* context) noexcept {
    if (!impl->owns(context))
        return;
    try {
        Com<ID3D11DepthStencilView> dsv;
        context->OMGetRenderTargets(0, nullptr, &dsv.p);
        Com<ID3D11Texture2D> texture;
        if (!impl->source(dsv.p, texture))
            return;
        Com<ID3D11DepthStencilState> state;
        UINT stencil = 0;
        context->OMGetDepthStencilState(&state.p, &stencil);
        D3D11_DEPTH_STENCIL_DESC desc{};
        // A null state has enabled writes and the forward-Z LESS comparison.
        desc.DepthEnable = true;
        desc.DepthWriteMask = D3D11_DEPTH_WRITE_MASK_ALL;
        desc.DepthFunc = D3D11_COMPARISON_LESS;
        if (state.p)
            state.p->GetDesc(&desc);
        if (!desc.DepthEnable || desc.DepthWriteMask != D3D11_DEPTH_WRITE_MASK_ALL)
            return;
        UINT count = 1;
        D3D11_VIEWPORT viewport{};
        context->RSGetViewports(&count, &viewport);
        const bool supported = count == 1 && viewport.TopLeftX == 0 && viewport.TopLeftY == 0 &&
                               viewport.Width == impl->width && viewport.Height == impl->height &&
                               viewport.MinDepth == 0 && viewport.MaxDepth == 1 &&
                               (desc.DepthFunc == D3D11_COMPARISON_GREATER_EQUAL ||
                                desc.DepthFunc == D3D11_COMPARISON_GREATER);
        std::lock_guard<std::mutex> lock(impl->mutex);
        auto* record = impl->record(context);
        if (!record)
            return;
        std::shared_ptr<SceneResources> snapshot;
        if (supported && !record->events.empty() && !record->events.back().clear &&
            !record->events.back().incompatible_view &&
            record->events.back().source->depth.p == texture.p) {
            snapshot = record->events.back().source;
        } else {
            if (record->events.size() >= kMaxEvents) {
                impl->failed = true;
                return;
            }
            snapshot = std::make_shared<SceneResources>();
            snapshot->depth.p = texture.p;
            texture.p->AddRef();
            record->events.push_back({false, snapshot, !supported});
        }
        if (supported && !impl->snapshot(context, *snapshot)) {
            impl->failed = true;
            return;
        }
        if (context->GetType() == D3D11_DEVICE_CONTEXT_IMMEDIATE)
            impl->apply(record->events.back());
    } catch (...) {
        impl->failed = true;
    }
}
void SceneTracker::clear(ID3D11DeviceContext* context, ID3D11DepthStencilView* depth,
                         unsigned flags) noexcept {
    if (!impl->owns(context) || !(flags & D3D11_CLEAR_DEPTH))
        return;
    try {
        Com<ID3D11Texture2D> source;
        if (!impl->source(depth, source))
            return;
        std::lock_guard<std::mutex> lock(impl->mutex);
        auto* record = impl->record(context);
        if (!record)
            return;
        if (record->events.size() >= kMaxEvents) {
            impl->failed = true;
            return;
        }
        auto snapshot = std::make_shared<SceneResources>();
        snapshot->depth.p = source.p;
        source.p->AddRef();
        record->events.push_back({true, snapshot});
        if (context->GetType() == D3D11_DEVICE_CONTEXT_IMMEDIATE)
            impl->apply(record->events.back());
    } catch (...) {
        impl->failed = true;
    }
}
void SceneTracker::finish(ID3D11DeviceContext* context, ID3D11CommandList* list) noexcept {
    if (!impl->owns(context) || !list)
        return;
    try {
        std::lock_guard<std::mutex> lock(impl->mutex);
        CommandData data;
        data.epoch = impl->epoch;
        auto found = impl->contexts.find(context);
        if (found != impl->contexts.end()) {
            data.events = std::move(found->second.events);
            impl->contexts.erase(found);
        }
        auto* holder = new CommandHolder(std::move(data));
        const auto result = list->SetPrivateDataInterface(kCommandData, holder);
        holder->Release();
        if (FAILED(result))
            impl->failed = true;
    } catch (...) {
        impl->failed = true;
    }
}
void SceneTracker::execute(ID3D11DeviceContext* context, ID3D11CommandList* list) noexcept {
    if (!impl->owns(context) || !list)
        return;
    try {
        Com<IUnknown> holder;
        UINT size = sizeof(holder.p);
        Com<CommandInterface> commands;
        const bool known = SUCCEEDED(list->GetPrivateData(kCommandData, &size, &holder.p)) &&
                           size == sizeof(holder.p) && holder.p &&
                           SUCCEEDED(holder.p->QueryInterface(
                               kCommandType, reinterpret_cast<void**>(&commands.p))) &&
                           commands.p->data().epoch == impl->epoch;
        std::lock_guard<std::mutex> lock(impl->mutex);
        if (!known) {
            impl->frame = {SceneResult::untracked_commands, {}};
            return;
        }
        for (const auto& event : commands.p->data().events)
            impl->apply(event);
    } catch (...) {
        impl->failed = true;
    }
}
SceneFrame SceneTracker::consume(ID3D11DeviceContext* immediate) noexcept {
    if (!impl->owns(immediate) || immediate->GetType() != D3D11_DEVICE_CONTEXT_IMMEDIATE)
        return {SceneResult::failed, {}};
    try {
        std::lock_guard<std::mutex> lock(impl->mutex);
        SceneFrame result = std::move(impl->frame);
        impl->frame = {};
        impl->first_source.reset();
        impl->contexts.erase(immediate);
        if (impl->failed)
            return {SceneResult::failed, {}};
        return result;
    } catch (...) {
        impl->failed = true;
        return {SceneResult::failed, {}};
    }
}
}
