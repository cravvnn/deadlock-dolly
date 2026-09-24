// Private opt-in observer with bounded GPU snapshots and single-draw matte diagnostics.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <d3d11.h>
#include <d3dcompiler.h>
#include <MinHook.h>
#include <array>
#include <atomic>
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <string>
#include <sstream>
#include "dolly_player_capture.hpp"
#include "dolly_player_producers.hpp"
#include "dolly_capture_timing.hpp"
#include "dolly_hide.hpp"
#include "dolly_player_snapshot.hpp"
#include "dolly_player_matte_capture.hpp"
#include "dolly_player_material_matte.hpp"
#include "dolly_player_vertex_guard.hpp"
#include "dolly_player_sequence_writer.hpp"

namespace dolly::player_capture {
namespace {
constexpr unsigned Capacity = 65536;
struct Event {
    std::uint64_t w[136]{};
};
static_assert(sizeof(Event) == 1088);
std::array<Event, Capacity> events;
CaptureEventSlots<Capacity> eventSlots;
std::array<std::atomic<bool>, Capacity> completed{};
constexpr std::uint64_t Closed = std::uint64_t(1) << 63;
std::atomic<unsigned> used{0}, dropped{0};
std::atomic<std::uint64_t> admission{Closed};
std::atomic<bool> armed{false}, samplingDone{false}, captureActive{false};
std::atomic<double> authoredReplayTime{-1.0};
ULONGLONG drainStarted = 0;
std::uintptr_t sceneBase = 0;
Producer original = nullptr;
std::atomic<PoseObserver> poseObserver{nullptr};
bool configured = false, hashesOk = false, attempted = false, captured = false,
     producerInstalled = false;
std::atomic<bool> wantDrawHooks{false};
std::atomic<int> drawHooks{0};
// Body hiding: while a handle is set, player-owned draws matching it are
// skipped. The producer branch fills the owner table only while hiding and no
// capture is running, so the capture path is untouched.
constexpr unsigned HideSlots = 8192;
std::atomic<std::uint32_t> hiddenHandle{0};
std::atomic<std::uint32_t> hiddenOther{0};
std::atomic<bool> hidingActive{false};
std::atomic<unsigned> hiddenDrawsSeen{0}, hiddenDrawsSkipped{0}, hiddenSetCalls{0},
    hiddenClearCalls{0}, hiddenProbeCalls{0};
std::atomic<std::uint64_t> hiddenLastSetFrame{0}, hiddenLastClearFrame{0};
std::atomic<unsigned> hiddenLastSetSite{0}, hiddenLastClearSite{0};
std::atomic<unsigned> hiddenSiteCounts[16]{};
std::array<std::atomic<std::uint64_t>, HideSlots> hideOwners{};
std::atomic<unsigned> hideAuditRemaining{0}, hideAuditDraws{0}, hideAuditOtherSlot{0};
std::atomic<unsigned> hideAuditWindow{0};
std::atomic<unsigned> hideAuditSlots[32]{};
std::atomic<bool> producerHideRequested{false};
// Private COM metadata is lifetime-bound to the layout, so pointer reuse cannot
// inherit a stale declaration. No input/render state is changed.
const GUID LayoutKey = {
    0x7c6ea90b, 0xc290, 0x4f0c, {0xa7, 0x76, 0x9b, 0xc9, 0x2e, 0x51, 0xd4, 0x06}};
struct LayoutInfo {
    std::uint64_t w[8]{};
};
std::atomic<bool> layoutRequested{false};
using CreateLayout = HRESULT(STDMETHODCALLTYPE*)(ID3D11Device*, const D3D11_INPUT_ELEMENT_DESC*,
                                                 UINT, const void*, SIZE_T, ID3D11InputLayout**);
CreateLayout originalLayout = nullptr;
const GUID ShaderKey = {
    0xb1a1d260, 0xa73c, 0x46ae, {0x82, 0x49, 0x4e, 0x4a, 0x27, 0xfa, 0x61, 0xd4}};
const GUID VertexBytesKey = {
    0x369a0c21, 0xef10, 0x4a0c, {0xac, 0xa5, 0x92, 0x79, 0x37, 0x21, 0xab, 0x08}};
struct ShaderInfo {
    std::uint64_t w[8]{};
};
using CreateShader = HRESULT(STDMETHODCALLTYPE*)(ID3D11Device*, const void*, SIZE_T,
                                                 ID3D11ClassLinkage*, ID3D11VertexShader**);
CreateShader originalShader = nullptr;
HRESULT STDMETHODCALLTYPE shader_hook(ID3D11Device* device, const void* bytes, SIZE_T length,
                                      ID3D11ClassLinkage* linkage, ID3D11VertexShader** output);

ID3D11Device* layoutDevice = nullptr; // retained for the bounded process lifetime
const GUID PixelBytesKey = {
    0x4fa14e83, 0xd077, 0x4773, {0x9d, 0x31, 0x31, 0x63, 0xd1, 0x5f, 0x17, 0x82}};
using CreatePixel = HRESULT(STDMETHODCALLTYPE*)(ID3D11Device*, const void*, SIZE_T,
                                                ID3D11ClassLinkage*, ID3D11PixelShader**);
CreatePixel originalPixel = nullptr;
HRESULT STDMETHODCALLTYPE pixel_hook(ID3D11Device* device, const void* bytes, SIZE_T length,
                                     ID3D11ClassLinkage* linkage, ID3D11PixelShader** output) {
    const auto hr = originalPixel(device, bytes, length, linkage, output);
    if (device == layoutDevice && SUCCEEDED(hr) && output && *output && bytes && length >= 36 &&
        length <= 1024 * 1024 && !linkage)
        (*output)->SetPrivateData(PixelBytesKey, static_cast<UINT>(length), bytes);
    return hr;
}

HRESULT STDMETHODCALLTYPE layout_hook(ID3D11Device* device, const D3D11_INPUT_ELEMENT_DESC* desc,
                                      UINT count, const void* bytes, SIZE_T length,
                                      ID3D11InputLayout** output) {
    const auto hr = originalLayout(device, desc, count, bytes, length, output);
    if (device != layoutDevice || FAILED(hr) || !output || !*output || !desc || count > 32)
        return hr;
    LayoutInfo info{};
    info.w[0] = 1;
    for (UINT i = 0; i < count; ++i)
        if (desc[i].SemanticName && _stricmp(desc[i].SemanticName, "TEXCOORD") == 0 &&
            desc[i].SemanticIndex == 13) {
            ++info.w[1];
            info.w[2] = desc[i].InputSlot;
            info.w[3] = desc[i].Format;
            info.w[4] = desc[i].AlignedByteOffset;
            info.w[5] = desc[i].InputSlotClass;
            info.w[6] = desc[i].InstanceDataStepRate;
        }
    ID3D11ShaderReflection* reflection = nullptr;
    if (SUCCEEDED(D3DReflect(bytes, length, __uuidof(ID3D11ShaderReflection),
                             reinterpret_cast<void**>(&reflection)))) {
        D3D11_SHADER_DESC shader{};
        if (SUCCEEDED(reflection->GetDesc(&shader)))
            for (UINT i = 0; i < shader.InputParameters; ++i) {
                D3D11_SIGNATURE_PARAMETER_DESC param{};
                if (FAILED(reflection->GetInputParameterDesc(i, &param)))
                    continue;
                if (param.SemanticName && _stricmp(param.SemanticName, "TEXCOORD") == 0 &&
                    param.SemanticIndex == 13)
                    info.w[7] |= param.Mask;
            }
        reflection->Release();
    }
    // Failure leaves the layout unknown. Never affect the original HRESULT/output.
    (*output)->SetPrivateData(LayoutKey, sizeof(info), &info);
    return hr;
}

HRESULT STDMETHODCALLTYPE shader_hook(ID3D11Device* device, const void* bytes, SIZE_T length,
                                      ID3D11ClassLinkage* linkage, ID3D11VertexShader** output) {
    const auto hr = originalShader(device, bytes, length, linkage, output);
    if (device != layoutDevice || FAILED(hr) || !output || !*output)
        return hr;
    if (!linkage && bytes && length >= 36 && length <= 1024 * 1024)
        (*output)->SetPrivateData(VertexBytesKey, static_cast<UINT>(length), bytes);
    ShaderInfo info{};
    info.w[0] = 1;
    ID3D11ShaderReflection* reflection = nullptr;
    const auto reflected = D3DReflect(bytes, length, __uuidof(ID3D11ShaderReflection),
                                      reinterpret_cast<void**>(&reflection));
    info.w[1] = static_cast<std::uint32_t>(reflected);
    if (SUCCEEDED(reflected)) {
        D3D11_SHADER_DESC desc{};
        if (SUCCEEDED(reflection->GetDesc(&desc))) {
            for (UINT i = 0; i < desc.InputParameters; ++i) {
                D3D11_SIGNATURE_PARAMETER_DESC param{};
                if (SUCCEEDED(reflection->GetInputParameterDesc(i, &param)) && param.SemanticName &&
                    _stricmp(param.SemanticName, "TEXCOORD") == 0 && param.SemanticIndex == 13) {
                    ++info.w[2];
                    info.w[3] = param.Register;
                    info.w[4] = param.ReadWriteMask;
                }
            }
            for (UINT i = 0; i < desc.BoundResources; ++i) {
                D3D11_SHADER_INPUT_BIND_DESC binding{};
                if (SUCCEEDED(reflection->GetResourceBindingDesc(i, &binding)) &&
                    binding.BindPoint == 1 && binding.Type == D3D_SIT_STRUCTURED) {
                    info.w[5] = 1;
                    info.w[6] = binding.Type;
                    info.w[7] = binding.BindCount;
                }
            }
        }
        reflection->Release();
    }
    (*output)->SetPrivateData(ShaderKey, sizeof(info), &info);
    return hr;
}
std::wstring directory;
ULONGLONG started = 0;
std::uint32_t startFrame = 0;
std::atomic<std::uint64_t> presentEpoch{0};
std::uint64_t startPresent = 0;
std::array<std::uint32_t, 24> allowedHandles{};
unsigned allowedCount = 0;
std::string lastMarker, captureRequest;
unsigned captureFps = 60;
double captureSpeed = 1.0;
struct GpuSlot {
    GpuSnapshot snapshot;
    bool used = false, pending = false, mismatch = false;
    std::uint32_t owner = 0;
    std::uint64_t startEvent = 0, drawEvent = 0;
    ULONGLONG polled = 0;
};
// Ownership staging is 36 bytes/draw. Bound each of the three frame slots to
// 64 original draws (6912 staging bytes total); image-buffer count is unchanged.
constexpr unsigned AggregateDrawLimit = 64;
constexpr unsigned SequenceRing = 3;
constexpr unsigned SequenceMaxCap = 2000000;
std::array<GpuSlot, SequenceRing * AggregateDrawLimit> gpuSlots;
struct MatteSlot {
    MatteCapture capture;
    bool attempted = false;
    std::uint64_t draw = 0, event = 0;
    ULONGLONG polled = 0;
};
std::array<MatteSlot, SequenceRing * AggregateDrawLimit> mattes;
bool matteMode = false, meshMode = false, materialMode = false, occlusionMode = false,
     guardMode = false, colorMode = false, aggregateMode = false;
std::uint64_t meshFrame = 0, meshProducerFrame = 0;
bool meshFrameSet = false;
struct ColorFrame {
    MatteCapture capture;
    CaptureImageClock clock;
    bool prepared = false, sealed = false, truncated = false;
    unsigned draws = 0;
    std::uint64_t first = 0, last = 0, epoch = 0;
    std::uint64_t header[8]{};
    ULONGLONG polled = 0;
};
std::array<ColorFrame, SequenceRing> colorFrames;
unsigned colorIndex = 0;
bool sequenceMode = false, sequenceV2 = false, sequenceV3 = false;
double lastSealedReplayTime = -1.0;
unsigned sequenceCap = 2, framesSealed = 0, framesWritten = 0, framesDropped = 0;
bool playersOnly = false;
std::uint32_t ownerFieldOffset = 0;
std::uint32_t sceneNodeFieldOffset = 0;
std::atomic<bool> sequenceStall{false};
bool sequenceCadenceLost = false;
ULONGLONG firstSeal = 0, lastSeal = 0;
// Kept for the life of the process: a static writer would join its worker
// inside DLL detach. The explicit finish in tick() still closes the file.
SequenceWriter* sequenceWriterInstance = nullptr;
SequenceWriter& sequence_writer() noexcept {
    if (!sequenceWriterInstance)
        sequenceWriterInstance = new SequenceWriter();
    return *sequenceWriterInstance;
}
bool sequenceWriterReady = false;
struct FrameMeta {
    double replay_time = -1.0;
    std::uint64_t epoch = 0, draws = 0, first = 0, last = 0, wall_ms = 0, delta_ms = 0;
};
std::vector<FrameMeta> frameMeta;
void release_frame_slots(unsigned ring) noexcept {
    const unsigned first = ring * AggregateDrawLimit;
    for (unsigned i = 0; i < AggregateDrawLimit; ++i) {
        auto& gpu = gpuSlots[first + i];
        if (sequenceV3 && gpu.used) {
            if (gpu.startEvent)
                eventSlots.release(static_cast<unsigned>(gpu.startEvent - 1));
            if (gpu.drawEvent)
                eventSlots.release(static_cast<unsigned>(gpu.drawEvent - 1));
        }
        gpu.snapshot.abandon();
        gpu.used = false;
        gpu.pending = false;
        gpu.mismatch = false;
        gpu.owner = 0;
        gpu.startEvent = gpu.drawEvent = 0;
        auto& matte = mattes[first + i];
        matte.attempted = false;
        matte.draw = matte.event = 0;
    }
}
bool any_frame_pending() noexcept {
    for (const auto& slot : colorFrames)
        if (slot.capture.pending)
            return true;
    return false;
}
bool any_snapshot_pending() noexcept {
    for (const auto& slot : gpuSlots)
        if (slot.pending)
            return true;
    return false;
}
bool ring_snapshots_pending(unsigned ring) noexcept {
    const unsigned first = ring * AggregateDrawLimit;
    for (unsigned i = 0; i < AggregateDrawLimit; ++i)
        if (gpuSlots[first + i].pending)
            return true;
    return false;
}
thread_local std::uint64_t pendingMatteDraw = 0;
thread_local unsigned pendingMatteSlot = 0;
unsigned dbg[24]{};
std::uint64_t lastDrawEpoch = 0;
// Pairing and coverage audit counters (printed with the stall report).
unsigned blendOver = 0, blendDarken = 0, blendOther = 0, blendSkip = 0;
// Pairing validation from the GPU snapshot (identity + record at the draw).
unsigned recordValid = 0, recordInvalid = 0, validateStall = 0;
unsigned publishCalls = 0, publishValid = 0;
double maxAuthored = -1.0, lastAuthored = -1.0;
ULONGLONG lastAuthoredTick = 0, lastAdvancedTick = 0;
std::atomic<std::uintptr_t> firstPresentDevice{0}, lastPresentDevice{0};
std::atomic<unsigned> presentDeviceChanges{0};
std::atomic<unsigned long long> drawHookCalls{0}, producerCalls{0}, producerKnownCalls{0};
ULONGLONG lastTimeline = 0;

constexpr unsigned char Prologue[] = {
    0x48, 0x8b, 0xc4, 0x4c, 0x89, 0x48, 0x20, 0x48, 0x89, 0x50, 0x10, 0x48, 0x89, 0x48, 0x08, 0x55,
    0x53, 0x48, 0x8d, 0x68, 0xe8, 0x48, 0x81, 0xec, 0x08, 0x01, 0x00, 0x00, 0x48, 0x89, 0x70, 0xe8};
template <class T> bool read(std::uintptr_t address, T& output) noexcept {
    if (address < 0x10000 || address > 0x00007fffffffffffULL - sizeof(T))
        return false;
    __try {
        std::memcpy(&output, reinterpret_cast<const void*>(address), sizeof(T));
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        std::memset(&output, 0, sizeof(T));
        return false;
    }
}
template <class T> T value(std::uintptr_t address) noexcept {
    T v{};
    read(address, v);
    return v;
}
std::uintptr_t module_base_of(std::uintptr_t address) noexcept {
    MEMORY_BASIC_INFORMATION info{};
    if (!VirtualQuery(reinterpret_cast<const void*>(address), &info, sizeof(info)))
        return 0;
    return reinterpret_cast<std::uintptr_t>(info.AllocationBase);
}
bool class_name_from(std::uintptr_t descriptor, const char* needle) noexcept {
    char name[160]{};
    for (unsigned i = 0; i < sizeof(name) - 1; ++i) {
        name[i] = value<char>(descriptor + 0x10 + i);
        if (!name[i])
            break;
    }
    return std::strstr(name, needle) != nullptr;
}
// MSVC x64 RTTI stores image-relative offsets in the vtable's -1 slot and in
// the complete object locator. Resolve against the module base, and also
// accept a self-relative layout, so a toolchain change cannot blind us.
bool class_name_contains(std::uintptr_t object, const char* needle) noexcept {
    if (!object)
        return false;
    const auto vtable = value<std::uintptr_t>(object);
    if (!vtable || vtable < 0x10000)
        return false;
    const auto base = module_base_of(vtable);
    if (!base)
        return false;
    // The slot is an absolute complete-object-locator pointer at runtime; the
    // locator's own type-descriptor field is image-relative. Fallbacks keep
    // older/other layouts working.
    const auto absolute = value<std::uintptr_t>(vtable - 8);
    std::int32_t relative = 0;
    if (!read(vtable - 8, relative))
        return false;
    const std::uintptr_t locators[3] = {absolute, base + static_cast<std::intptr_t>(relative),
                                        vtable - 8 + static_cast<std::intptr_t>(relative)};
    for (auto locator : locators) {
        if (!locator || locator < 0x10000)
            continue;
        std::uint32_t signature = 0;
        if (!read(locator, signature) || signature != 1)
            continue;
        std::int32_t type_relative = 0;
        if (!read(locator + 0xc, type_relative))
            continue;
        const std::uintptr_t descriptors[2] = {base + static_cast<std::intptr_t>(type_relative),
                                               locator + 0xc +
                                                   static_cast<std::intptr_t>(type_relative)};
        for (auto descriptor : descriptors)
            if (class_name_from(descriptor, needle))
                return true;
    }
    return false;
}
struct OwnerClass {
    std::uint32_t handle = 0;
    bool player = false;
};
std::array<OwnerClass, 256> ownerClasses{};
std::mutex ownerClassMutex;
unsigned ownerClassCount = 0, ownerClassRotate = 0;
// The scene -> node link and the node's embedded scene-object member move with
// engine updates, so the first classification discovers them inside a bounded
// window and every later one reuses the pair. A player is only ever accepted
// on an exact player-pawn class match; a wrong pair cannot produce one.
unsigned discoveredLinkOffset = 0, discoveredLinkDelta = 0;
bool classifies_as_player(std::uintptr_t scene, unsigned link_offset, unsigned delta) noexcept {
    if (!scene)
        return false;
    const auto link = value<std::uintptr_t>(scene + link_offset);
    if (!link || link < 0x10000)
        return false;
    const auto node = link - delta;
    const auto entity = value<std::uintptr_t>(node + ownerFieldOffset);
    if (!entity || entity < 0x10000)
        return false;
    // The owner entity must point back at the node it was read from; a wrong
    // link cannot satisfy both directions of the schema relation.
    if (sceneNodeFieldOffset && value<std::uintptr_t>(entity + sceneNodeFieldOffset) != node)
        return false;
    return class_name_contains(entity, "CitadelPlayerPawn");
}
// Players-only mode classifies each owner handle through the scene graph:
// scene object -> scene node -> owner entity (schema m_pOwner offset) -> RTTI.
// Attachments share the pawn handle, so they classify with their player.
bool owner_is_player(std::uint32_t handle, std::uintptr_t scene) noexcept {
    if (!playersOnly) {
        for (unsigned n = 0; n < allowedCount; ++n)
            if (handle == allowedHandles[n])
                return true;
        return false;
    }
    std::lock_guard<std::mutex> ownerLock(ownerClassMutex);
    for (unsigned i = 0; i < ownerClassCount; ++i)
        if (ownerClasses[i].handle == handle)
            return ownerClasses[i].player;
    bool player = false;
    {
        if (discoveredLinkOffset) {
            player = classifies_as_player(scene, discoveredLinkOffset, discoveredLinkDelta);
        } else {
            // The scene object is stable, so its link is tried first; the
            // scene node layout moves with client updates, so the node base is
            // searched finely. A wrong pair cannot match a player class.
            static const unsigned link_offsets[] = {
                0x110, 0x108, 0x118, 0x100, 0x120, 0x90,  0x98,  0xa0,  0xa8,  0xb0,
                0xb8,  0xc0,  0xc8,  0xd0,  0xd8,  0xe0,  0xe8,  0xf0,  0xf8,  0x128,
                0x130, 0x138, 0x140, 0x148, 0x150, 0x158, 0x160, 0x168, 0x170, 0x178};
            for (unsigned link_offset : link_offsets)
                for (unsigned delta = 0; delta <= 0x200 && !player; delta += 8)
                    if (classifies_as_player(scene, link_offset, delta)) {
                        discoveredLinkOffset = link_offset;
                        discoveredLinkDelta = delta;
                        player = true;
                        break;
                    }
        }
        unsigned slot = ownerClassCount;
        if (slot < ownerClasses.size()) {
            ++ownerClassCount;
        } else {
            // Never let early non-player owners crowd players out of the
            // cache: reuse a negative slot round-robin, keep every positive.
            for (unsigned step = 0; step < ownerClasses.size(); ++step) {
                const unsigned candidate = (ownerClassRotate + step) % ownerClasses.size();
                if (!ownerClasses[candidate].player) {
                    slot = candidate;
                    ownerClassRotate = (candidate + 1) % ownerClasses.size();
                    break;
                }
            }
        }
        ownerClasses[slot].handle = handle;
        ownerClasses[slot].player = player;
    }
    return player;
}
// A players-only capture admits every player, so the same producer record is
// re-submitted many times inside one renderer frame. Exact duplicates are
// forwarded once but recorded once: the bounded event ring must survive the
// whole take.
ProducerTable<4096> producers;
SubmittedOwners<512, Microsoft::WRL::ComPtr<ID3D11Buffer>> submittedOwners;
void producer_store(std::uint64_t frame, std::uint64_t instance, std::uint64_t records,
                    std::uint32_t owner, const std::uint32_t* record,
                    std::uint64_t event) noexcept {
    ProducerEntry entry{frame, instance, records, event, owner, {}};
    std::copy(record, record + 8, entry.record);
    producers.store(entry);
}
// Return a stable value, never a pointer into concurrently updated storage.
std::optional<ProducerEntry> producer_lookup(std::uint64_t frame, std::uint64_t instance,
                                             std::uint64_t records) noexcept {
    return producers.lookup(frame, instance, records);
}
// Bounded pairing diagnostic: on a miss, report what each tagged identity slot
// computes and whether it hits the live table, so a stream reorder is visible.
void producer_probe(std::uint64_t frame, const Event* e) noexcept {

    static unsigned count = 0;
    if (count >= 8)
        return;
    ++count;
    const auto producerSnapshot = producers.snapshot();
    const auto& producerTable = producerSnapshot.entries;
    const auto producerTableCount = producerSnapshot.count;
    try {
        std::ofstream f(directory + L"dolly_owner_producers.txt",
                        count > 1 ? (std::ios::out | std::ios::app)
                                  : (std::ios::out | std::ios::trunc));
        if (!f)
            return;
        unsigned sameFrame = 0;
        for (unsigned i = 0; i < producerTableCount; ++i)
            if (producerTable[i].frame == frame)
                ++sameFrame;
        f << "lookup frame " << frame << " records " << e->w[25] << " entries "
          << producerTableCount << " sameFrame " << sameFrame << " slots";
        for (unsigned candidate = 0; candidate < 32; ++candidate) {
            if (!(e->w[45] & (1ULL << candidate)))
                continue;
            const auto packed = e->w[49 + candidate * 2];
            const auto off = static_cast<std::uint64_t>(packed >> 32) + e->w[116] + e->w[11] * 4;
            if ((packed & 0xffffffff) != 4 || off > 0xffffffff || off % 4) {
                f << " s" << candidate << ":bad";
                continue;
            }
            const auto idx = static_cast<std::uint64_t>(off / 4);
            std::uint64_t nearDelta = ~std::uint64_t(0), nearFrame = 0;
            for (unsigned i = 0; i < producerTableCount; ++i)
                if (producerTable[i].instance == idx) {
                    const auto d = producerTable[i].frame > frame ? producerTable[i].frame - frame
                                                                  : frame - producerTable[i].frame;
                    if (d < nearDelta) {
                        nearDelta = d;
                        nearFrame = producerTable[i].frame;
                    }
                }
            f << " s" << candidate << ":inst " << idx << " hit "
              << (producer_lookup(frame, idx, e->w[25]) ? 1 : 0) << " near "
              << (nearDelta == ~std::uint64_t(0)
                      ? "-"
                      : std::to_string(nearFrame) + "/" + std::to_string(nearDelta))
                     .c_str();
        }
        f << "\n frame-instances:";
        for (unsigned i = 0; i < producerTableCount && i < 8; ++i)
            if (producerTable[i].frame == frame)
                f << " " << producerTable[i].instance;
        f << "\n";
        f.flush();
    } catch (...) {
    }
}
std::uint64_t stamp() noexcept {
    LARGE_INTEGER t{};
    QueryPerformanceCounter(&t);
    return t.QuadPart;
}
std::uint32_t frame() noexcept {
    return sceneBase ? value<std::uint32_t>(sceneBase + 0x8ce9b8) : 0;
}
std::uintptr_t cpu(std::uintptr_t global) noexcept {
    return sceneBase ? value<std::uintptr_t>(value<std::uintptr_t>(sceneBase + global) + 0x18) : 0;
}
std::uintptr_t buffer(std::uintptr_t global) noexcept {
    return sceneBase ? value<std::uintptr_t>(value<std::uintptr_t>(sceneBase + global) + 0x60) : 0;
}
// Diagnostics keep a chronological log. Shot exports recycle only unreferenced
// slots; GPU evidence stays pinned until its frame has been verified and written.
struct Write {
    Event* event = nullptr;
    bool admitted = false;
    explicit Write(unsigned kind) noexcept {
        if (!armed.load(std::memory_order_acquire))
            return;
        if (samplingDone.load(std::memory_order_acquire) && kind != 9 && kind != 10 && kind != 11 &&
            kind != 12)
            return;
        auto state = admission.load(std::memory_order_acquire);
        do {
            if (state & Closed)
                return;
        } while (!admission.compare_exchange_weak(state, state + 1, std::memory_order_acq_rel));
        admitted = true;
        unsigned index = 0;
        if (sequenceV3) {
            const auto allocated = eventSlots.acquire();
            if (!allocated) {
                dropped.fetch_add(1);
                sequenceStall = true;
                return;
            }
            index = *allocated;
            auto high = used.load();
            while (high < index + 1 && !used.compare_exchange_weak(high, index + 1)) {
            }
            events[index] = Event{};
            completed[index].store(false, std::memory_order_relaxed);
        } else {
            index = used.fetch_add(1, std::memory_order_relaxed);
        }
        if (index >= Capacity) {
            dropped.fetch_add(1, std::memory_order_relaxed);
            return;
        }
        event = &events[index];
        event->w[0] = kind;
        event->w[1] = GetCurrentThreadId();
        event->w[2] = stamp();
        event->w[4] = frame();
    }
    ~Write() {
        if (event) {
            event->w[5] = frame();
            event->w[3] = stamp();
            completed[event - events.data()].store(true, std::memory_order_release);
            if (sequenceV3)
                eventSlots.release(static_cast<unsigned>(event - events.data()));
        }
        if (admitted)
            admission.fetch_sub(1, std::memory_order_acq_rel);
    }
};
int __fastcall hook(std::uintptr_t a, void* object, void* mesh, void* opaque, void* params,
                    std::uint32_t mode, unsigned char* flag, std::uint32_t* outA,
                    std::uint32_t* outB) {
    const auto obj = reinterpret_cast<std::uintptr_t>(object);
    ++producerCalls;
    if (auto observer = poseObserver.load(std::memory_order_acquire))
        observer(value<std::uint32_t>(obj + 0xc0), frame());
    // Hiding runs without a capture: remember the owner for each produced
    // instance so the draw hook can skip exactly that handle. No events, no
    // allocation, no GPU work.
    if (hidingActive.load(std::memory_order_acquire) &&
        !captureActive.load(std::memory_order_acquire) && !sequenceV2) {
        const int result = original(a, object, mesh, opaque, params, mode, flag, outA, outB);
        if (result >= 0 && static_cast<unsigned>(result) < HideSlots)
            hideOwners[result].store((std::uint64_t(frame()) << 32) |
                                         value<std::uint32_t>(obj + 0xc0),
                                     std::memory_order_relaxed);
        return result;
    }
    // Bounded sequences record only reviewed-player producers; every other
    // producer is forwarded exactly once without consuming the event buffer.
    if (captureActive.load(std::memory_order_acquire)) {
        const auto owner = value<std::uint32_t>(obj + 0xc0);
        if (!owner_is_player(owner, obj)) {
            const int result = original(a, object, mesh, opaque, params, mode, flag, outA, outB);
            // Instance indices are recycled, including within one frame. A
            // non-selected owner must replace any old selected-owner entry.
            // Otherwise equal GPU record words can validate a stale identity.
            if (result >= 0 &&
                static_cast<unsigned>(result) < value<std::uint32_t>(sceneBase + 0x8cfde8)) {
                const std::uint32_t empty[8]{};
                producer_store(frame(), static_cast<unsigned>(result), buffer(0x8cfde0), 0, empty,
                               0);
            }
            return result;
        }
    }
    if (sequenceV2) {
        // Shot-aligned captures only ever record during the authored take;
        // preparation frames would otherwise fill the bounded event buffer
        // before the first frame can be sealed.
        if (sequenceV3 && authoredReplayTime.load(std::memory_order_acquire) < 0.0)
            return original(a, object, mesh, opaque, params, mode, flag, outA, outB);
        ++producerKnownCalls;
    }
    if (sequenceV2) {
        // Shot-aligned captures pair draws through the producer table and
        // never consume the bounded event ring: a players-only take would
        // otherwise fill it long before the last frame is sealed.
        const auto engineFrame = frame();
        const int sequenceResult =
            original(a, object, mesh, opaque, params, mode, flag, outA, outB);
        const auto ownerHandle = value<std::uint32_t>(obj + 0xc0);
        const auto stack = value<std::uintptr_t>(sceneBase + 0x8cfce8);
        const auto base = value<std::uintptr_t>(stack + 0x18);
        const auto end = value<std::uintptr_t>(stack + 8);
        const auto capacity = value<std::uint32_t>(sceneBase + 0x8cfde8);
        const auto recordsBuffer = buffer(0x8cfde0);
        if (sequenceResult >= 0 && static_cast<unsigned>(sequenceResult) < capacity && base &&
            end >= base) {
            const auto address = base + static_cast<std::uint64_t>(sequenceResult) * 32;
            std::uint32_t sequenceRecord[8]{};
            if (address >= base && address <= end && end - address >= sizeof(sequenceRecord) &&
                read(address, sequenceRecord))
                producer_store(
                    engineFrame,
                    static_cast<std::uint64_t>(static_cast<std::uint32_t>(sequenceResult)),
                    recordsBuffer, ownerHandle, sequenceRecord, 0);
        }
        return sequenceResult;
    }
    Write write(1);
    auto* e = write.event;
    const auto m = reinterpret_cast<std::uintptr_t>(mesh);
    if (e) {
        e->w[6] = obj;
        e->w[7] = m;
        e->w[8] = value<std::uint32_t>(obj + 0xc0);
        e->w[9] = value<std::uintptr_t>(obj);
        e->w[34] = value<std::uint64_t>(value<std::uintptr_t>(m + 0x48) + 0x10);
        e->w[35] = mode;
    }
    // Exactly one original invocation. The first register argument remains
    // opaque; caller-owned in/out pointers and all nine argument slots survive.
    const int result = original(a, object, mesh, opaque, params, mode, flag, outA, outB);
    const auto engineFrame = frame();
    const auto ownerHandle = value<std::uint32_t>(obj + 0xc0);
    const auto stack = value<std::uintptr_t>(sceneBase + 0x8cfce8);
    const auto base = value<std::uintptr_t>(stack + 0x18);
    const auto end = value<std::uintptr_t>(stack + 8);
    const auto capacity = value<std::uint32_t>(sceneBase + 0x8cfde8);
    const auto recordsBuffer = buffer(0x8cfde0); // CSceneSystem+0x2b00
    std::uint32_t record[8]{};
    bool recordOk = false;
    if (result >= 0 && static_cast<unsigned>(result) < capacity && base && end >= base) {
        const auto address = base + static_cast<std::uint64_t>(result) * 32;
        recordOk = address >= base && address <= end && end - address >= sizeof(record) &&
                   read(address, record);
    }
    if (e) {
        e->w[10] = static_cast<std::uint32_t>(result);
        e->w[11] = base;
        e->w[22] = cpu(0x8cfce0);
        e->w[23] = buffer(0x8cfd40); // CSceneSystem+0x2a60
        e->w[24] = recordsBuffer;
        e->w[25] = buffer(0x8cfc50); // sequential instance IDs
        const auto cache = value<std::uintptr_t>(m + 0x48);
        e->w[26] = value<std::uint32_t>(cache + 0x20);
        e->w[27] = value<std::uint32_t>(cache + 0x28);
        e->w[28] = value<std::uint32_t>(cache + 0x24);
        e->w[29] = ownerHandle;
        if (recordOk) {
            for (unsigned i = 0; i < 8; ++i)
                e->w[14 + i] = record[i];
            e->w[12] = record[1];
            e->w[13] = 1;
        }
        e->w[30] = cpu(0x8cfce8);
        e->w[31] = buffer(0x8cfd40);
        e->w[32] = recordsBuffer;
        e->w[33] = value<std::uint64_t>(cache + 0x10);
        e->w[36] = sceneBase;
    }
    if (recordOk)
        producer_store(engineFrame, static_cast<std::uint64_t>(static_cast<std::uint32_t>(result)),
                       recordsBuffer, ownerHandle, record,
                       e ? static_cast<std::uint64_t>(e - events.data()) + 1 : 0);
    return result;
}
bool install(void* target) noexcept {
    unsigned char bytes[sizeof(Prologue)]{};
    if (!read(reinterpret_cast<std::uintptr_t>(target), bytes) ||
        std::memcmp(bytes, Prologue, sizeof(bytes)))
        return false;
    const auto init = MH_Initialize();
    if (init != MH_OK && init != MH_ERROR_ALREADY_INITIALIZED)
        return false;
    if (MH_CreateHook(target, reinterpret_cast<void*>(&hook),
                      reinterpret_cast<void**>(&original)) != MH_OK)
        return false;
    if (MH_EnableHook(target) != MH_OK) {
        MH_RemoveHook(target);
        return false;
    }
    return true;
}
void status(const char* text) noexcept {
    try {
        std::ofstream f(directory + L"dolly_owner_status.txt", std::ios::trunc);
        f << text << '\n';
        if (!captureRequest.empty())
            f << "request " << captureRequest << '\n';
    } catch (...) {
    }
}
bool dump(const wchar_t* path) noexcept {
    if (admission.load(std::memory_order_acquire) != Closed)
        return false;
    try {
        const std::uint64_t header[8] = {0x31564f52504c4c44ULL,
                                         sequenceV3 ? 14ULL : 13ULL,
                                         sizeof(Event),
                                         std::min(used.load(), Capacity),
                                         dropped.load(),
                                         startFrame,
                                         frame(),
                                         sceneBase};
        std::ofstream f(path, std::ios::binary | std::ios::trunc);
        f.write(reinterpret_cast<const char*>(header), sizeof(header));
        f.write(reinterpret_cast<const char*>(events.data()), header[3] * sizeof(Event));
        f.flush();
        auto saveMatte = [](std::ofstream& m, const MatteSlot& slot) {
            const auto& matte = slot.capture;
            const std::uint32_t header[4] = {matte.width, matte.height,
                                             static_cast<std::uint32_t>(slot.draw),
                                             static_cast<std::uint32_t>(slot.event)};
            m.write(reinterpret_cast<const char*>(header), sizeof(header));
            m.write(reinterpret_cast<const char*>(matte.pixels.data()), matte.pixels.size());
        };
        if (aggregateMode && !sequenceV2) {
            std::ofstream m(directory + L"dolly_owner_color_frame.bin",
                            std::ios::binary | std::ios::trunc);
            for (unsigned i = 0; i < (sequenceMode ? 2u : 1u); ++i) {
                const auto& slot = colorFrames[i];
                const auto& capture = slot.capture;
                if (!capture.complete)
                    continue;
                const std::uint64_t header[8] = {0x314641524c4f4344ULL,
                                                 capture.width,
                                                 capture.height,
                                                 slot.draws,
                                                 slot.first,
                                                 slot.last,
                                                 slot.epoch,
                                                 8};
                m.write(reinterpret_cast<const char*>(header), sizeof(header));
                m.write(reinterpret_cast<const char*>(capture.pixels.data()),
                        capture.pixels.size());
            }
            m.flush();
            if (!m)
                return false;
        }
        if (sequenceV2 && framesSealed > 0) {
            // Pairing metadata for the recorder's take: authored-path replay
            // time, epoch and draw window for every streamed player frame.
            std::ofstream m(directory + L"dolly_owner_frame_meta.bin",
                            std::ios::binary | std::ios::trunc);
            const std::uint64_t header[8] = {
                0x314154454d564f44ULL, 1, 64, framesSealed, 0, 0, 0, 0};
            m.write(reinterpret_cast<const char*>(header), sizeof(header));
            for (unsigned i = 0; i < framesSealed; ++i) {
                std::uint64_t replay = 0;
                std::memcpy(&replay, &frameMeta[i].replay_time, 8);
                const std::uint64_t record[8] = {i,
                                                 replay,
                                                 frameMeta[i].epoch,
                                                 frameMeta[i].draws,
                                                 frameMeta[i].first,
                                                 frameMeta[i].last,
                                                 frameMeta[i].wall_ms,
                                                 frameMeta[i].delta_ms};
                m.write(reinterpret_cast<const char*>(record), sizeof(record));
            }
            m.flush();
            if (!m)
                return false;
        }
        if (meshMode && !aggregateMode) {
            unsigned count = 0;
            for (const auto& slot : mattes)
                if (slot.capture.complete)
                    ++count;
            std::ofstream m(directory +
                                (colorMode ? L"dolly_owner_colors.bin" : L"dolly_owner_mattes.bin"),
                            std::ios::binary | std::ios::trunc);
            const std::uint32_t header[2] = {colorMode ? 0x31434f44u : 0x314d4f44u, count};
            m.write(reinterpret_cast<const char*>(header), sizeof(header));
            for (const auto& slot : mattes)
                if (slot.capture.complete)
                    saveMatte(m, slot);
            m.flush();
            if (!m)
                return false;
        } else if (mattes[0].capture.complete) {
            std::ofstream m(directory + L"dolly_owner_matte.rgba",
                            std::ios::binary | std::ios::trunc);
            saveMatte(m, mattes[0]);
            m.flush();
            if (!m)
                return false;
        }
        return bool(f);
    } catch (...) {
        return false;
    }
}
}
void publish_replay_time(double seconds) noexcept {
    ++publishCalls;
    if (seconds >= 0.0) {
        ++publishValid;
        lastAuthored = seconds;
        lastAuthoredTick = GetTickCount64();
        if (seconds > maxAuthored) {
            maxAuthored = seconds;
            lastAdvancedTick = lastAuthoredTick;
        }
    }
    authoredReplayTime.store(seconds, std::memory_order_release);
}
double current_replay_time() noexcept {
    return authoredReplayTime.load(std::memory_order_acquire);
}
namespace {
std::atomic<unsigned> uploadUpdates{0};
}
void mapped_upload(ID3D11DeviceContext* context, ID3D11Resource* resource, unsigned sub,
                   unsigned map_type, void* data) noexcept {
    if (!captureActive.load(std::memory_order_acquire) || !context || !resource || !data || sub ||
        map_type == D3D11_MAP_READ)
        return;
    Microsoft::WRL::ComPtr<ID3D11Buffer> mappedBuffer;
    if (SUCCEEDED(resource->QueryInterface(IID_PPV_ARGS(&mappedBuffer))) &&
        reinterpret_cast<std::uintptr_t>(mappedBuffer.Get()) == buffer(0x8cfde0))
        submittedOwners.invalidate(); // Unsupported writer: no stale ownership.
}
void configure(std::uintptr_t base, bool hashes_ok) noexcept {
    if (configured)
        return;
    configured = true;
    sceneBase = base;
    hashesOk = hashes_ok;
    layoutRequested.store(hashes_ok, std::memory_order_release);
    HMODULE self = nullptr;
    wchar_t path[32768]{};
    if (GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                               GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           reinterpret_cast<LPCWSTR>(&configure), &self)) {
        const auto n = GetModuleFileNameW(self, path, 32768);
        if (n && n < 32768) {
            directory.assign(path, n);
            directory.resize(directory.find_last_of(L"\\/") + 1);
        }
    }
}
void stall_report() noexcept {
    const auto producerSnapshot = producers.snapshot();
    const auto& producerTable = producerSnapshot.entries;
    const auto producerTableCount = producerSnapshot.count;

    if (!sequenceV2)
        return;
    // Classification is updated by producer workers, not the render thread.
    std::array<OwnerClass, 256> ownerSnapshot;
    unsigned ownerCount = 0, linkOffset = 0, linkDelta = 0;
    {
        std::lock_guard<std::mutex> lock(ownerClassMutex);
        ownerSnapshot = ownerClasses;
        ownerCount = ownerClassCount;
        linkOffset = discoveredLinkOffset;
        linkDelta = discoveredLinkDelta;
    }
    std::ofstream f(directory + L"dolly_owner_stall.txt", std::ios::binary | std::ios::trunc);
    if (!f)
        return;
    f << "framesSealed " << framesSealed << " framesWritten " << framesWritten << " framesDropped "
      << framesDropped << " colorIndex " << colorIndex << " used " << used.load() << " dropped "
      << dropped.load() << " stall " << (sequenceStall ? 1 : 0) << " refused "
      << sequence_writer().refused() << " writerErr " << (sequence_writer().error() ? 1 : 0)
      << " firstSeal " << (firstSeal ? firstSeal - started : 0) << " lastSeal "
      << (lastSeal ? lastSeal - started : 0) << " link " << linkOffset << " delta " << linkDelta
      << " owners " << ownerCount << " now " << (GetTickCount64() - started) << " replay "
      << lastSealedReplayTime << " meshFrameSet " << (meshFrameSet ? 1 : 0) << " meshFrame "
      << meshFrame << " lastDrawEpoch " << lastDrawEpoch << " startPresent " << startPresent;
    unsigned players = 0;
    for (unsigned i = 0; i < ownerCount; ++i)
        if (ownerSnapshot[i].player)
            ++players;
    unsigned tableNow = 0;
    for (unsigned i = 0; i < producerTableCount; ++i)
        if (producerTable[i].frame == frame())
            ++tableNow;
    f << " players " << players << " producers " << producerTableCount << " tableNow " << tableNow
      << "\n";
    f << "blends over " << blendOver << " darken " << blendDarken << " other " << blendOther
      << " skipped " << blendSkip << " records valid " << recordValid << " invalid "
      << recordInvalid << " validateStall " << validateStall << "\n";
    f << "dbg";
    for (int i = 0; i < 24; ++i)
        f << " " << i << "=" << dbg[i];
    f << "\n";
    f << "publish calls " << publishCalls << " valid " << publishValid << " max " << maxAuthored
      << " last " << lastAuthored << " lastAge "
      << (lastAuthoredTick ? GetTickCount64() - lastAuthoredTick : 0) << " lastAdvance "
      << (lastAdvancedTick ? GetTickCount64() - lastAdvancedTick : 0) << "\n";
    f << "present device first " << firstPresentDevice.load() << " last "
      << lastPresentDevice.load() << " changes " << presentDeviceChanges.load() << "\n";
    f << "calls draw " << drawHookCalls.load() << " producers " << producerCalls.load() << " known "
      << producerKnownCalls.load() << "\n";
    f << "buffers records " << buffer(0x8cfde0) << " ids " << buffer(0x8cfc50) << " stack "
      << buffer(0x8cfce8) << " capacity " << buffer(0x8cfde8) << " frame " << frame()
      << " presentEpoch " << presentEpoch.load() << "\n";
    for (unsigned i = 0; i < SequenceRing; ++i) {
        const auto& s = colorFrames[i];
        f << "ring " << i << " sealed " << (s.sealed ? 1 : 0) << " prepared "
          << (s.prepared ? 1 : 0) << " pend " << (s.capture.pending ? 1 : 0) << " complete "
          << (s.capture.complete ? 1 : 0) << " draws " << s.draws << " first " << s.first
          << " last " << s.last << " epoch " << s.epoch << " snapPend "
          << (ring_snapshots_pending(i) ? 1 : 0) << " w " << s.capture.width << " h "
          << s.capture.height << "\n";
    }
    for (std::size_t i = 0; i < gpuSlots.size(); ++i) {
        const auto& g = gpuSlots[i];
        if (g.used || g.pending)
            f << "slot " << i << " used " << (g.used ? 1 : 0) << " pend " << (g.pending ? 1 : 0)
              << " owner " << g.owner << " startEvent " << g.startEvent << " drawEvent "
              << g.drawEvent << "\n";
    }
    f.flush();
}
void arm_capture() noexcept {
    startFrame = frame();
    startPresent = presentEpoch.load(std::memory_order_acquire);
    started = GetTickCount64();
    if (sequenceV2 && !sequenceWriterReady) {
        sequenceWriterReady = sequence_writer().open(directory + L"dolly_owner_color_frame.bin", 2);
        if (!sequenceWriterReady) {
            status("rejected sequence writer open");
            captured = true;
            return;
        }
    }
    admission.store(0, std::memory_order_release);
    captureActive.store(true, std::memory_order_release);
    armed.store(true, std::memory_order_release);
    status(sequenceV2 ? "armed bounded player sequence capture"
                      : "armed four main-window presentation intervals, maximum 1500 ms");
}
// Called only after capture admission is closed and all writers have drained.
void reset_capture() noexcept {
    submittedOwners.invalidate();
    producers.clear();
    for (auto& slot : gpuSlots) {
        slot.snapshot.release();
        slot = GpuSlot{};
    }
    for (auto& slot : mattes) {
        slot.capture.release_gpu();
        slot = MatteSlot{};
    }
    for (auto& slot : colorFrames) {
        slot.capture.release_gpu();
        slot = ColorFrame{};
    }
    for (unsigned i = 0; i < std::min(used.load(), Capacity); ++i) {
        events[i] = Event{};
        completed[i].store(false, std::memory_order_relaxed);
    }
    used.store(0);
    dropped.store(0);
    samplingDone.store(false);
    attempted = captured = false;
    sequenceWriterReady = false;
    colorIndex = framesSealed = framesWritten = framesDropped = 0;
    sequenceStall = false;
    sequenceCadenceLost = false;
    firstSeal = lastSeal = drainStarted = 0;
    lastSealedReplayTime = -1.0;
    meshFrameSet = false;
    meshFrame = meshProducerFrame = 0;
    allowedCount = 0;
    playersOnly = false;
    captureFps = 60;
    captureSpeed = 1.0;
    captureRequest.clear();
    ownerFieldOffset = sceneNodeFieldOffset = 0;
    recordValid = recordInvalid = validateStall = 0;
    blendOver = blendDarken = blendOther = blendSkip = 0;
    for (auto& x : dbg)
        x = 0;
    {
        std::lock_guard<std::mutex> lock(ownerClassMutex);
        ownerClassCount = ownerClassRotate = discoveredLinkOffset = discoveredLinkDelta = 0;
    }
    frameMeta.clear();
    eventSlots.reset();
}
void tick() noexcept {
    if (!configured || directory.empty())
        return;
    std::string requestText;
    if (captured || !attempted) {
        static ULONGLONG lastRequestPoll = 0;
        const auto now = GetTickCount64();
        if (now - lastRequestPoll < 100)
            return;
        lastRequestPoll = now;
        std::ifstream requestFile(directory + L"dolly_owner_start.txt");
        std::getline(requestFile, requestText);
    }
    if (requestText.size() > 2048)
        return;
    if (captured) {
        if (requestText.empty() || requestText == lastMarker || armed.load() ||
            captureActive.load() || admission.load() != Closed)
            return;
        reset_capture();
    }
    if (!attempted) {
        if (GetFileAttributesW((directory + L"dolly_owner_start.txt").c_str()) ==
            INVALID_FILE_ATTRIBUTES)
            return;
        attempted = true;
        if (!hashesOk || !sceneBase ||
            value<std::uintptr_t>(sceneBase + 0x8cd2e0) != sceneBase + 0x5d4fe8) {
            status("rejected exact module/layout gate");
            captured = true;
            return;
        }
        try {
            lastMarker = requestText;
            std::istringstream marker(requestText);
            std::string mode;
            marker >> mode >> allowedCount;
            sequenceV3 = mode == "color-sequence-v3";
            sequenceV2 = sequenceV3 || mode == "color-sequence-v2";
            sequenceMode = sequenceV2 || mode == "color-sequence-v1";
            aggregateMode = sequenceMode || mode == "color-aggregate-v1";
            colorMode = aggregateMode || mode == "color-guarded-v1";
            guardMode = colorMode || mode == "matte-guarded-v1";
            occlusionMode = guardMode || mode == "matte-occluded-v1";
            materialMode = occlusionMode || mode == "matte-material-v1";
            meshMode = materialMode || mode == "matte-meshes-v1";
            matteMode = meshMode || mode == "matte-redirect-v1";
            if ((mode != "gpu-snapshot-v1" && !matteMode) || allowedCount > allowedHandles.size()) {
                status("rejected GPU marker format/count");
                captured = true;
                return;
            }
            // Sequence modes inherit the mesh *selection* behavior, but only the
            // explicit mesh marker requires exactly one fixed owner.
            if (mode == "matte-meshes-v1" && allowedCount != 1) {
                status("rejected mesh mode requires one exact owner");
                captured = true;
                return;
            }
            for (unsigned i = 0; i < allowedCount; ++i)
                if (!(marker >> allowedHandles[i]) || !allowedHandles[i]) {
                    status("rejected GPU marker handles");
                    captured = true;
                    return;
                }
            if (sequenceV2) {
                unsigned cap = 0;
                if (!(marker >> cap) || cap < 2 || cap > SequenceMaxCap) {
                    status("rejected sequence frame cap");
                    captured = true;
                    return;
                }
                sequenceCap = cap;
                frameMeta.resize(cap);
            }
            std::string option;
            if (marker >> option) {
                unsigned offset = 0, back = 0;
                if (option != "players" || !(marker >> offset) || offset < 8 || offset > 0x4000 ||
                    !(marker >> back) || back < 8 || back > 0x4000) {
                    status("rejected GPU marker option");
                    captured = true;
                    return;
                }
                playersOnly = true;
                ownerFieldOffset = offset;
                sceneNodeFieldOffset = back;
            }
            std::string clockOption;
            if (marker >> clockOption) {
                std::string requestOption;
                if (clockOption != "fps" ||
                    !(marker >> captureFps >> requestOption >> captureRequest) ||
                    (captureFps != 30 && captureFps != 60 && captureFps != 120 &&
                     captureFps != 300 && captureFps != 600) ||
                    requestOption != "request" || captureRequest.size() != 32 ||
                    captureRequest.find_first_not_of("0123456789abcdef") != std::string::npos) {
                    status("rejected capture clock/request");
                    captured = true;
                    return;
                }
            }
            std::string speedOption;
            if (marker >> speedOption) {
                if (speedOption != "speed" || !(marker >> captureSpeed) ||
                    !std::isfinite(captureSpeed) || captureSpeed < .05 || captureSpeed > 4) {
                    status("rejected capture speed");
                    captured = true;
                    return;
                }
            }
            if (!playersOnly && allowedCount < 1) {
                status("rejected GPU marker format/count");
                captured = true;
                return;
            }
        } catch (...) {
            status("rejected GPU marker read");
            captured = true;
            return;
        }
        wantDrawHooks.store(true, std::memory_order_release);
        started = GetTickCount64();
        status("waiting for observed game device draw hooks");
    }
    if (!producerInstalled) {
        const int hooks = drawHooks.load(std::memory_order_acquire);
        if (hooks == 0 && GetTickCount64() - started < 2000)
            return;
        if (hooks != 1) {
            status("rejected draw-hook installation or readiness timeout");
            captured = true;
            return;
        }
        if (!install(reinterpret_cast<void*>(sceneBase + 0x564b0))) {
            status("rejected producer signature or hook installation");
            captured = true;
            return;
        }
        producerInstalled = true;
    }
    if (!armed.load() && !samplingDone.load())
        arm_capture();
    if (captured)
        return;
    const auto elapsed = GetTickCount64() - started;
    const auto sinceFirstSeal = firstSeal ? GetTickCount64() - firstSeal : 0;
    // The caller's take can end before the frame cap (a replay returning to the
    // hideout, a stopped recording), so it drops a stop file and the sequence
    // finishes at the frames it actually captured instead of waiting out the
    // whole budget.
    const bool stopRequested =
        sequenceV2 && GetFileAttributesW((directory + L"dolly_owner_stop.txt").c_str()) !=
                          INVALID_FILE_ATTRIBUTES;
    const bool captureFull =
        sequenceV2
            ? (sequenceStall || framesSealed >= sequenceCap || (stopRequested && framesSealed > 0))
            : presentEpoch.load(std::memory_order_acquire) - startPresent >= 4;
    // v3 arms before playback; a long forward seek may consume most of the
    // wall clock, so the capture budget only starts at the first sealed frame
    // (120 s standalone allowance keeps preparation itself bounded).
    const ULONGLONG captureBudget = sequenceV3
                                        ? (firstSeal ? 3000ULL + 1200ULL * sequenceCap : 120000ULL)
                                    : sequenceV2 ? 1500ULL + 500ULL * sequenceCap
                                                 : 1500ULL;
    const auto budgetElapsed = (sequenceV3 && firstSeal) ? sinceFirstSeal : elapsed;
    if (armed.load(std::memory_order_acquire) && !samplingDone.load(std::memory_order_acquire) &&
        (captureFull || budgetElapsed >= captureBudget ||
         (!sequenceV3 && used.load() >= Capacity) || (stopRequested && framesSealed == 0))) {
        drainStarted = GetTickCount64();
        samplingDone.store(true, std::memory_order_release);
        status(sequenceV2 ? "draining bounded sequence readbacks"
                          : "draining previously submitted GPU readbacks, maximum 250 ms");
    }
    const bool sequenceDrained = sequenceV2 && sequenceWriterReady &&
                                 framesWritten >= framesSealed && !any_frame_pending() &&
                                 !any_snapshot_pending();
    if (armed.load(std::memory_order_acquire) && samplingDone.load(std::memory_order_acquire) &&
        (sequenceDrained || GetTickCount64() - drainStarted >= (sequenceV2 ? 3000 : 250) ||
         (!sequenceV3 && used.load() >= Capacity))) {
        armed.store(false, std::memory_order_release);
        captureActive.store(false, std::memory_order_release);
        submittedOwners.invalidate();
        admission.fetch_or(Closed, std::memory_order_acq_rel);
    }
    if (!armed.load(std::memory_order_acquire) &&
        admission.load(std::memory_order_acquire) == Closed) {
        const bool saved = dump((directory + L"dolly_owner_events.bin").c_str());
        stall_report();
        bool snapshotsVerified = recordValid > 0 && recordInvalid == 0;
        for (const auto& slot : gpuSlots)
            if (slot.used && (slot.pending || slot.mismatch))
                snapshotsVerified = false;
        for (const auto& slot : colorFrames)
            if (slot.truncated)
                snapshotsVerified = false;
        // No writer may use a capture object here. Avoid COM releases in DLL teardown.
        for (auto& slot : gpuSlots) {
            slot.snapshot.release();
            slot.pending = false;
        }
        for (auto& slot : mattes)
            slot.capture.release_gpu();
        for (auto& slot : colorFrames)
            slot.capture.release_gpu();
        const bool writerDrained =
            sequenceV2 ? (sequenceWriterReady && sequence_writer().finish()) : true;
        // A take that ended early still completed its capture: the frames that
        // were sealed are the take's frames, and the cap is only the bound.
        const bool sequenceComplete =
            (!sequenceV2 && (!sequenceMode || (colorIndex == 2 && snapshotsVerified))) ||
            (writerDrained && !sequenceStall && framesWritten + framesDropped == framesSealed &&
             framesWritten >= 2 && sequence_writer().refused() == 0 && !sequence_writer().error() &&
             (!sequenceV3 ||
              (!sequenceCadenceLost && framesDropped == 0 && framesWritten == sequenceCap)));
        if (saved && sequenceComplete) {
            if (sequenceV2 && (framesSealed < sequenceCap || framesDropped)) {
                char buf[128];
                std::snprintf(buf, sizeof(buf),
                              "complete short sequence frames=%u dropped=%u cap=%u", framesWritten,
                              framesDropped, sequenceCap);
                status(buf);
            } else
                status(dropped.load() ? "complete with overflow; reject proof" : "complete");
        } else if (saved && sequenceV3 && (sequenceCadenceLost || framesWritten != sequenceCap)) {
            status(
                "failed player capture missed shot frames; keep capture data and retry at a lower FPS or slower recording speed");
        } else if (saved && dbg[6]) {
            char buf[192];
            std::snprintf(
                buf, sizeof(buf),
                "failed player layer exceeded %u draws per image; incomplete output rejected",
                AggregateDrawLimit);
            status(buf);
        } else if (saved) {
            char buf[192];
            std::snprintf(
                buf, sizeof(buf),
                "failed incomplete sequence; reject proof frames=%u written=%u first=%llu last=%llu elapsed=%llu replay=%.3f stalled=%d refused=%u drainPending=%d",
                framesSealed, framesWritten, firstSeal ? firstSeal - started : 0ULL,
                lastSeal ? lastSeal - started : 0ULL, GetTickCount64() - started,
                lastSealedReplayTime, sequenceStall ? 1 : 0,
                sequenceV2 ? sequence_writer().refused() : 0u,
                sequenceV2 ? (any_frame_pending() || any_snapshot_pending()) ? 1 : 0 : 0);
            status(buf);
        } else
            status("failed to write diagnostic");
        captured = true;
    }
}
bool layout_hook_requested() noexcept {
    return layoutRequested.exchange(false, std::memory_order_acq_rel);
}
bool install_layout_hook(ID3D11Device* device) noexcept {
    if (!device || originalLayout)
        return false;
    const auto init = MH_Initialize();
    if (init != MH_OK && init != MH_ERROR_ALREADY_INITIALIZED)
        return false;
    auto** table = *reinterpret_cast<void***>(device);
    auto* target = table[11];
    auto* shaderTarget = table[12];
    auto* pixelTarget = table[15];
    if (MH_CreateHook(target, reinterpret_cast<void*>(&layout_hook),
                      reinterpret_cast<void**>(&originalLayout)) != MH_OK)
        return false;
    if (MH_CreateHook(shaderTarget, reinterpret_cast<void*>(&shader_hook),
                      reinterpret_cast<void**>(&originalShader)) != MH_OK) {
        MH_RemoveHook(target);
        originalLayout = nullptr;
        return false;
    }
    if (MH_CreateHook(pixelTarget, reinterpret_cast<void*>(&pixel_hook),
                      reinterpret_cast<void**>(&originalPixel)) != MH_OK) {
        MH_RemoveHook(target);
        MH_RemoveHook(shaderTarget);
        originalLayout = nullptr;
        originalShader = nullptr;
        return false;
    }
    layoutDevice = device;
    device->AddRef();
    if (MH_EnableHook(target) != MH_OK || MH_EnableHook(shaderTarget) != MH_OK ||
        MH_EnableHook(pixelTarget) != MH_OK) {
        MH_DisableHook(target);
        MH_DisableHook(shaderTarget);
        MH_DisableHook(pixelTarget);
        MH_RemoveHook(target);
        MH_RemoveHook(shaderTarget);
        MH_RemoveHook(pixelTarget);
        originalLayout = nullptr;
        originalShader = nullptr;
        originalPixel = nullptr;
        layoutDevice->Release();
        layoutDevice = nullptr;
        return false;
    }
    return true;
}
bool draw_hooks_requested() noexcept {
    return wantDrawHooks.load(std::memory_order_acquire) &&
           drawHooks.load(std::memory_order_acquire) == 0;
}
void draw_hooks_result(bool installed) noexcept {
    drawHooks.store(installed ? 1 : -1, std::memory_order_release);
}
void set_pose_observer(PoseObserver observer) noexcept {
    poseObserver.store(observer, std::memory_order_release);
    if (observer)
        producerHideRequested.store(true, std::memory_order_release);
}
std::uint32_t pose_scene_frame() noexcept {
    return frame();
}
bool producer_hook_requested() noexcept {
    return producerHideRequested.load(std::memory_order_acquire) && !producerInstalled && sceneBase;
}
bool install_producer_hook() noexcept {
    if (producerInstalled || !sceneBase)
        return producerInstalled;
    producerHideRequested.store(false, std::memory_order_release);
    if (!install(reinterpret_cast<void*>(sceneBase + 0x564b0)))
        return false;
    producerInstalled = true;
    return true;
}
void hide_report() noexcept {
    if (directory.empty())
        return;
    try {
        std::ofstream f(directory + L"dolly_hide_report.txt", std::ios::binary | std::ios::trunc);
        if (!f)
            return;
        f << "seen " << hiddenDrawsSeen.load(std::memory_order_relaxed) << " skipped "
          << hiddenDrawsSkipped.load(std::memory_order_relaxed) << " calls "
          << hiddenSetCalls.load(std::memory_order_relaxed) << " clears "
          << hiddenClearCalls.load(std::memory_order_relaxed) << " probes "
          << hiddenProbeCalls.load(std::memory_order_relaxed) << " lastSet "
          << hiddenLastSetFrame.load(std::memory_order_relaxed) << " lastClear "
          << hiddenLastClearFrame.load(std::memory_order_relaxed) << " setSite "
          << hiddenLastSetSite.load(std::memory_order_relaxed) << " clearSite "
          << hiddenLastClearSite.load(std::memory_order_relaxed) << " hooks "
          << drawHooks.load(std::memory_order_relaxed) << " producer "
          << (producerInstalled ? 1 : 0) << " frame " << frame() << "\n";
        f << "sites";
        for (unsigned i = 0; i < 16; ++i)
            if (auto count = hiddenSiteCounts[i].load(std::memory_order_relaxed))
                f << " " << i << "=" << count;
        f << "\n";
        f << "streamAudit draws " << hideAuditDraws.load() << " alternateTarget "
          << hideAuditOtherSlot.load() << " slots";
        for (unsigned slot = 0; slot < 32; ++slot)
            if (auto n = hideAuditSlots[slot].load())
                f << " " << slot << "=" << n;
        f << "\n";
    } catch (...) {
    }
}
void set_hidden_handle(std::uint32_t handle, unsigned site) noexcept {
    set_hidden_handles(handle, 0, site);
}
void set_hidden_handles(std::uint32_t handle, std::uint32_t other, unsigned site) noexcept {
    if (!handle) {
        handle = other;
        other = 0;
    }
    if (other == handle)
        other = 0;
    if (handle == hiddenHandle.load(std::memory_order_acquire) && other == hiddenOther.load())
        return;
    if (site < 16)
        hiddenSiteCounts[site].fetch_add(1, std::memory_order_relaxed);
    if (handle) {
        hideAuditDraws.store(0);
        hideAuditOtherSlot.store(0);
        for (auto& slot : hideAuditSlots)
            slot.store(0);
        hideAuditRemaining.store(256);
        hideAuditWindow.store(0);
        for (auto& slot : hideOwners)
            slot.store(0, std::memory_order_relaxed);
        hiddenDrawsSeen.store(0, std::memory_order_relaxed);
        hiddenDrawsSkipped.store(0, std::memory_order_relaxed);
        hiddenSetCalls.fetch_add(1, std::memory_order_relaxed);
        hiddenLastSetFrame.store(frame(), std::memory_order_relaxed);
        hiddenLastSetSite.store(site, std::memory_order_relaxed);
        hiddenHandle.store(handle, std::memory_order_release);
        hiddenOther.store(other, std::memory_order_release);
        hidingActive.store(true, std::memory_order_release);
        // The draw/layout hooks may already be installed from a capture; when
        // they are not, request them and the producer hook through the
        // overlay's per-frame install path.
        wantDrawHooks.store(true, std::memory_order_release);
        if (hashesOk)
            layoutRequested.store(true, std::memory_order_release);
        producerHideRequested.store(true, std::memory_order_release);
    } else {
        hidingActive.store(false, std::memory_order_release);
        hiddenHandle.store(0, std::memory_order_release);
        hiddenOther.store(0, std::memory_order_release);
        hiddenClearCalls.fetch_add(1, std::memory_order_relaxed);
        hiddenLastClearFrame.store(frame(), std::memory_order_relaxed);
        hiddenLastClearSite.store(site, std::memory_order_relaxed);
        hide_report();
    }
}
std::uint32_t hidden_handle() noexcept {
    return hiddenHandle.load(std::memory_order_acquire);
}
bool allow_draw(ID3D11DeviceContext* c, unsigned instances, unsigned first) noexcept {
    const std::uint32_t target = hiddenHandle.load(std::memory_order_relaxed);
    const std::uint32_t other = hiddenOther.load(std::memory_order_relaxed);
    if (!target)
        return true;
    hiddenProbeCalls.fetch_add(1, std::memory_order_relaxed);
    if (!hidingActive.load(std::memory_order_acquire) ||
        captureActive.load(std::memory_order_acquire) || !c || instances != 1)
        return true;
    hiddenDrawsSeen.fetch_add(1, std::memory_order_relaxed);
    ID3D11InputLayout* layout = nullptr;
    c->IAGetInputLayout(&layout);
    if (!layout)
        return true;
    LayoutInfo info{};
    UINT size = sizeof(info);
    const bool have =
        SUCCEEDED(layout->GetPrivateData(LayoutKey, &size, &info)) && size == sizeof(info);
    layout->Release();
    if (!have || info.w[2] >= 32)
        return true;
    const unsigned slot = static_cast<unsigned>(info.w[2]);
    ID3D11Buffer* vb = nullptr;
    UINT stride = 0, offset = 0;
    c->IAGetVertexBuffers(slot, 1, &vb, &stride, &offset);
    const auto identity = buffer(0x8cfc50);
    const bool isIdentity = vb && reinterpret_cast<std::uintptr_t>(vb) == identity;
    if (vb)
        vb->Release();
    unsigned primaryIndex = 0;
    const bool primaryMatch =
        isIdentity &&
        hide_instance_index(instances, stride, offset, info.w[4], first, HideSlots, primaryIndex) &&
        hide_owner_matches(hideOwners[primaryIndex].load(), target, frame());
    if (primaryMatch)
        hideAuditWindow.store(16);
    // Observational only: inspect at most 256 single-instance draws per target
    // selection. Alternate streams never authorize a skip in this diagnostic.
    unsigned budget = hideAuditRemaining.load(std::memory_order_relaxed);
    unsigned window = hideAuditWindow.load(std::memory_order_relaxed);
    while (window && !hideAuditWindow.compare_exchange_weak(window, window - 1)) {
    }
    if (!window)
        budget = 0;
    while (budget && !hideAuditRemaining.compare_exchange_weak(budget, budget - 1)) {
    }
    if (budget && identity) {
        hideAuditDraws.fetch_add(1);
        ID3D11Buffer* streams[32]{};
        UINT strides[32]{}, offsets[32]{};
        c->IAGetVertexBuffers(0, 32, streams, strides, offsets);
        bool alternate = false;
        for (unsigned candidate = 0; candidate < 32; ++candidate) {
            const bool tagged = streams[candidate] &&
                                reinterpret_cast<std::uintptr_t>(streams[candidate]) == identity;
            if (streams[candidate])
                streams[candidate]->Release();
            unsigned index = 0;
            if (tagged &&
                hide_instance_index(instances, strides[candidate], offsets[candidate], info.w[4],
                                    first, HideSlots, index) &&
                hide_owner_matches(hideOwners[index].load(), target, frame())) {
                hideAuditSlots[candidate].fetch_add(1);
                alternate |= candidate != slot;
            }
        }
        if (alternate)
            hideAuditOtherSlot.fetch_add(1);
    }
    if (!isIdentity)
        return true;
    unsigned index = 0;
    if (!hide_instance_index(instances, stride, offset, info.w[4], first, HideSlots, index))
        return true;
    const std::uint64_t entry = hideOwners[index].load(std::memory_order_relaxed);
    const std::uint64_t now = frame();
    if (hide_owner_matches(entry, target, now) || hide_owner_matches(entry, other, now)) {
        hiddenDrawsSkipped.fetch_add(1, std::memory_order_relaxed);
        return false;
    }
    return true;
}
void gpu_poll(ID3D11DeviceContext* c) noexcept {
    if (c->GetType() != D3D11_DEVICE_CONTEXT_IMMEDIATE)
        return;
    const auto now = GetTickCount64();
    for (auto& slot : colorFrames)
        if (slot.capture.pending && slot.polled != now) {
            slot.polled = now;
            const int result = slot.capture.poll(c);
            if (result) {
                Write out(11);
                if (out.event) {
                    out.event->w[6] = slot.first;
                    out.event->w[7] = result == 1;
                    out.event->w[8] = static_cast<std::uint32_t>(slot.capture.failure);
                    out.event->w[9] = 1;
                }
            }
            if (sequenceV2 && result != 0 && slot.sealed && !slot.capture.complete)
                sequenceStall = true;
        }
    if (sequenceV2) {
        // Frames stream in capture order; a busy slot or failed write is
        // reported instead of skipping or reordering the take.
        unsigned guard = SequenceRing;
        while (guard-- && framesWritten < framesSealed) {
            const unsigned ring = framesWritten % SequenceRing;
            auto& slot = colorFrames[ring];
            if (!slot.sealed || slot.capture.pending || !slot.capture.complete)
                break;
            // Keep every per-draw GPU record: a frame may finish its color
            // readback while some of its snapshots are still in flight.
            if (ring_snapshots_pending(ring))
                break;
            {
                bool mismatched = slot.truncated;
                for (unsigned i = 0; i < AggregateDrawLimit; ++i)
                    if (gpuSlots[ring * AggregateDrawLimit + i].mismatch)
                        mismatched = true;
                if (mismatched) {
                    // A draw whose record moved between submit and draw is not
                    // trusted: drop this frame and keep the take running so the
                    // layer only ever carries validated draws.
                    ++framesDropped;
                    sequenceStall = true;
                    slot.capture.recycle(c);
                    slot.sealed = false;
                    slot.prepared = false;
                    slot.draws = 0;
                    slot.truncated = false;
                    slot.first = slot.last = slot.epoch = 0;
                    slot.clock = CaptureImageClock{};
                    release_frame_slots(ring);
                    continue;
                }
            }
            {
                // Bounded coverage audit: how much of each streamed frame is
                // really non-transparent, so a silent blend regression shows.
                const auto* px = slot.capture.pixels.data();
                const std::size_t count =
                    static_cast<std::size_t>(slot.capture.width) * slot.capture.height;
                std::size_t rgb = 0, alpha = 0;
                for (std::size_t i = 0; i < count; ++i) {
                    const auto* q = px + i * slot.capture.bytesPerPixel;
                    if (q[0] || q[1] || q[2] || q[3] || q[4] || q[5])
                        ++rgb;
                    if (q[6] || q[7])
                        ++alpha;
                }
                std::ofstream s(directory + L"dolly_owner_frame_stats.txt",
                                framesWritten ? (std::ios::out | std::ios::app)
                                              : (std::ios::out | std::ios::trunc));
                if (s)
                    s << "frame " << framesWritten << " draws " << slot.draws << " rgb " << rgb
                      << " alpha " << alpha << " w " << slot.capture.width << " h "
                      << slot.capture.height << "\n";
            }
            if (!sequenceWriterReady ||
                !sequence_writer().push(slot.header, std::move(slot.capture.pixels))) {
                sequenceStall = true;
                break;
            }
            ++framesWritten;
            slot.capture.pixels =
                sequence_writer().take_buffer(static_cast<std::size_t>(slot.capture.width) *
                                              slot.capture.height * slot.capture.bytesPerPixel);
            slot.capture.recycle(c);
            slot.sealed = false;
            slot.prepared = false;
            slot.draws = 0;
            slot.truncated = false;
            slot.first = slot.last = slot.epoch = 0;
            slot.clock = CaptureImageClock{};
            release_frame_slots(ring);
        }
    }
    for (auto& slot : mattes)
        if (slot.capture.pending && slot.polled != now) {
            slot.polled = now;
            const int result = slot.capture.poll(c);
            if (result) {
                Write out(11);
                if (out.event) {
                    out.event->w[6] = slot.draw;
                    out.event->w[7] = result == 1;
                    out.event->w[8] = static_cast<std::uint32_t>(slot.capture.failure);
                }
            }
        }
    for (auto& slot : gpuSlots)
        if (slot.pending && slot.polled != now) {
            slot.polled = now;
            const int result = slot.snapshot.poll(c);
            if (!result)
                continue;
            if (result != 1) {
                ++recordInvalid;
                slot.mismatch = true;
            }
            Write out(9);
            if (out.event) {
                auto* e = out.event;
                e->w[6] = slot.startEvent;
                e->w[7] = slot.drawEvent;
                e->w[8] = slot.snapshot.id;
                e->w[9] = result == 1;
                e->w[10] = static_cast<std::uint32_t>(slot.snapshot.failure);
                for (unsigned i = 0; i < 8; ++i)
                    e->w[14 + i] = slot.snapshot.record[i];
            }
            // The redirected draw is captured from the game's own state, so the
            // record read back from the GPU is the authority that it is the paired
            // owner's: a mismatch fails the take closed instead of shipping it.
            if (result == 1 && slot.startEvent &&
                slot.startEvent <= used.load(std::memory_order_acquire)) {
                const auto& request = events[slot.startEvent - 1];
                std::uint32_t expected[8]{};
                for (unsigned n = 0; n < 8; ++n)
                    expected[n] = static_cast<std::uint32_t>(request.w[18 + n]);
                const bool ok =
                    record_matches(slot.snapshot.id, static_cast<unsigned>(request.w[8]),
                                   slot.snapshot.record, expected);
                if (ok) {
                    ++recordValid;
                } else {
                    ++recordInvalid;
                    slot.mismatch = true;
                }
            }
            slot.pending = false;
        }
}
// One bounded diagnostic dump of the draws that reach shot-aligned capture.
// It names the reason a draw was not captured and keeps the raw binding
// fields, so a game update's layout drift is visible instead of silent.
void draw_probe(const char* reason, const Event* e) noexcept {
    static unsigned count = 0;
    if (!aggregateMode || count >= 48)
        return;
    try {
        std::ofstream f(directory + L"dolly_owner_draws.txt",
                        count ? (std::ios::out | std::ios::app)
                              : (std::ios::out | std::ios::trunc));
        if (!f)
            return;
        if (!count)
            f << "reason frame epoch type method vertices instances first records ids mask packed w112 w113 w114 w115 w116 w121 w122 w123 w125 w126 w127 w128 w129 w131 w132 w133 w134 w27 w28 w29\n";
        const auto slot = static_cast<unsigned>(e->w[114]);
        // Rejected layouts can carry an invalid slot. Diagnostic logging must
        // not index outside the event while reporting that rejection.
        const auto packed = slot < 32 ? e->w[49 + slot * 2] : 0;
        f << reason << ' ' << e->w[4] << ' ' << e->w[135] << ' ' << e->w[7] << ' ' << e->w[8] << ' '
          << e->w[9] << ' ' << e->w[10] << ' ' << e->w[11] << ' ' << e->w[25] << ' ' << e->w[46]
          << ' ' << e->w[45] << ' ' << packed << ' ' << e->w[112] << ' ' << e->w[113] << ' '
          << e->w[114] << ' ' << e->w[115] << ' ' << e->w[116] << ' ' << e->w[121] << ' '
          << e->w[122] << ' ' << e->w[123] << ' ' << e->w[125] << ' ' << e->w[126] << ' '
          << e->w[127] << ' ' << e->w[128] << ' ' << e->w[129] << ' ' << e->w[131] << ' '
          << e->w[132] << ' ' << e->w[133] << ' ' << e->w[134] << ' ' << e->w[27] << ' ' << e->w[28]
          << ' ' << e->w[29] << '\n';
        f.flush();
        ++count;
    } catch (...) {
    }
}
void gpu_select(ID3D11DeviceContext* c, Event* e, bool scratch) noexcept {
    lastDrawEpoch = e->w[135];
    if (sequenceMode &&
        (e->w[135] == startPresent ||
         (sequenceV2 ? (framesSealed >= sequenceCap || sequenceStall ||
                        colorFrames[colorIndex].sealed || colorFrames[colorIndex].capture.pending ||
                        colorFrames[colorIndex].capture.complete)
                     : colorIndex >= 2))) {
        ++dbg[0];
        return;
    }
    if (sequenceV3) {
        // Match color admission: one image per advancing authored view. Keep
        // real sub-tick timestamps; FPS quantization discards valid images.
        const double authored = authoredReplayTime.load(std::memory_order_acquire);
        if (!capture_phase_advances(lastSealedReplayTime, authored)) {
            ++dbg[1];
            return;
        }
    }
    // Diagnostic mesh-size selection only; all ownership and GPU gates still apply.
    if (matteMode && (e->w[8] != 2 || (!meshMode && e->w[9] < 10000))) {
        ++dbg[2];
        return;
    }
    if (meshMode && meshFrameSet && e->w[135] != meshFrame) {
        ++dbg[3];
        return;
    }
    if (e->w[121] != 1 || e->w[122] != 0 || e->w[123] != 1 || !(e->w[125] & 1) || e->w[126] != 1 ||
        e->w[127] != D3D_SIT_STRUCTURED || e->w[128] != 1 || !e->w[129] ||
        e->w[131] != DXGI_FORMAT_R16G16B16A16_FLOAT || e->w[132] < 640 || e->w[133] < 360 ||
        e->w[134] != 1) {
        ++dbg[4];
        if (e->w[45])
            draw_probe("shape", e);
        return;
    }
    draw_probe("color", e);
    // Ownership is decided where each producer is stored (players are
    // classified there), so this gate describes draw shape only. It must not
    // require a marker-supplied owner list: a players-only take sets no
    // allowedCount and would otherwise be refused before its first pairing.
    if (e->w[7] != 0 || e->w[10] != 1 || (e->w[8] != 2 && e->w[8] != 3)) {
        ++dbg[12];
        ++dbg[5];
        draw_probe("recordgate-a", e);
        return;
    }
    {
        if (e->w[112] != 1 || e->w[113] != 1 || e->w[114] >= 32) {
            ++dbg[13];
            ++dbg[5];
            draw_probe("recordgate-b", e);
            return;
        }
        if (e->w[115] != DXGI_FORMAT_R32_UINT || e->w[116] == 0xffffffff || e->w[117] != 1 ||
            e->w[118] != 1) {
            ++dbg[14];
            ++dbg[5];
            draw_probe("recordgate-c", e);
            return;
        }
    }
    // An identity buffer may occur at several slots. Only the declaration's
    // actual TEXCOORD13 slot identifies the instance consumed by this shader.
    unsigned slot = static_cast<unsigned>(e->w[114]);
    std::uint64_t offset = 0, index = 0;
    bool packedOk = false, shapeOk = false;
    // Resolve against the actual submitted upload; CPU preparation can run
    // ahead of the draw even within a single presentation.
    std::optional<ProducerEntry> producer;
    for (unsigned candidate = 0; candidate < 32 && !producer; ++candidate) {
        if (candidate != e->w[114])
            continue;
        if (!(e->w[45] & (1ULL << candidate)))
            continue;
        const auto packed = e->w[49 + candidate * 2];
        const auto candidateOffset =
            static_cast<std::uint64_t>(packed >> 32) + e->w[116] + e->w[11] * 4;
        if ((packed & 0xffffffff) != 4 || candidateOffset > 0xffffffff || candidateOffset % 4)
            continue;
        packedOk = true;
        const auto candidateIndex = candidateOffset / 4;
        if (e->w[27] != 32 || e->w[28] != 0 || candidateIndex >= e->w[29])
            continue;
        if (!shapeOk) {
            shapeOk = true;
            slot = candidate;
            offset = candidateOffset;
            index = candidateIndex;
        }
        const auto found = submittedOwners.lookup(e->w[6], e->w[25], candidateIndex);
        if (!found)
            continue;
        if (!found->owner)
            continue;
        ++dbg[18];
        producer = found;
        slot = candidate;
        offset = candidateOffset;
        index = candidateIndex;
    }
    if (!packedOk) {
        ++dbg[8];
        draw_probe("packed", e);
        return;
    }
    if (!shapeOk) {
        ++dbg[9];
        draw_probe("record", e);
        return;
    }
    // Two-frame diagnostics exercise the same bounded per-image queue as export.
    const unsigned drawLimit = aggregateMode ? AggregateDrawLimit : meshMode ? 8u : 3u;
    const unsigned firstSlot = sequenceMode ? colorIndex * AggregateDrawLimit : 0;
    GpuSlot* target = nullptr;
    for (unsigned i = firstSlot; i < firstSlot + drawLimit; ++i)
        if (!gpuSlots[i].used) {
            target = &gpuSlots[i];
            break;
        }
    if (!producer) {
        ++dbg[7];
        draw_probe("noproducer", e);
        producer_probe(e->w[4], e);
        return;
    }
    if (!target) {
        ++dbg[6];
        if (aggregateMode) {
            colorFrames[sequenceMode ? colorIndex : 0].truncated = true;
            sequenceStall = true;
        }
        return;
    }
    bool taken = false;
    for (auto& candidate : gpuSlots)
        taken |= candidate.used && candidate.owner == producer->owner;
    if (!meshMode && taken) {
        ++dbg[17];
        ++dbg[7];
        return;
    }
    ID3D11ShaderResourceView* srv = nullptr;
    c->VSGetShaderResources(1, 1, &srv);
    ID3D11Resource* resource = nullptr;
    ID3D11Buffer* records = nullptr;
    if (srv) {
        srv->GetResource(&resource);
        srv->Release();
    }
    if (resource) {
        resource->QueryInterface(__uuidof(ID3D11Buffer), reinterpret_cast<void**>(&records));
        resource->Release();
    }
    ID3D11Buffer* ids = nullptr;
    UINT stride = 0, base = 0;
    c->IAGetVertexBuffers(slot, 1, &ids, &stride, &base);
    if (!(records && ids && reinterpret_cast<std::uintptr_t>(records) == e->w[25] &&
          reinterpret_cast<std::uintptr_t>(ids) == e->w[46])) {
        ++dbg[10];
        draw_probe("identity", e);
    }
    if (records && ids && reinterpret_cast<std::uintptr_t>(records) == e->w[25] &&
        reinterpret_cast<std::uintptr_t>(ids) == e->w[46]) {
        auto commitStart = [&] {
            Write start(8);
            if (!start.event) {
                ++dbg[11];
                draw_probe("noevent", e);
            }
            if (start.event) {
                auto* out = start.event;
                target->used = true;
                target->owner = producer->owner;
                target->startEvent = std::uint64_t(out - events.data()) + 1;
                target->drawEvent = std::uint64_t(e - events.data()) + 1;
                if (sequenceV3) {
                    eventSlots.retain(static_cast<unsigned>(target->startEvent - 1));
                    eventSlots.retain(static_cast<unsigned>(target->drawEvent - 1));
                }
                out->w[6] = target->drawEvent;
                out->w[7] = producer->event;
                out->w[8] = index;
                out->w[9] = target->owner;
                out->w[10] = e->w[25];
                out->w[11] = e->w[46];
                out->w[12] = offset;
                // The guard validates the exact producer record; keeping it in
                // the start event decouples the draw from ring pressure.
                for (unsigned n = 0; n < 8; ++n)
                    out->w[18 + n] = producer->record[n];
                out->w[26] = producer->frame;
                target->pending = target->snapshot.begin(c, records, ids, static_cast<UINT>(offset),
                                                         static_cast<UINT>(index));
                target->mismatch = !target->pending;
                if (!target->pending && sequenceV2)
                    sequenceStall = true;
                out->w[13] = target->pending;
                out->w[14] = static_cast<std::uint32_t>(target->snapshot.failure);
                if (target->pending && matteMode && e->w[8] == 2 &&
                    (meshMode || !mattes[0].attempted)) {
                    pendingMatteDraw = target->drawEvent;
                    pendingMatteSlot =
                        meshMode ? static_cast<unsigned>(target - gpuSlots.data()) : 0;
                    if (meshMode && !meshFrameSet) {
                        meshFrame = e->w[135];
                        meshProducerFrame = producer->frame;
                        meshFrameSet = true;
                    }
                }
            }
        };
        if (scratch) {
            Write drawWrite(2);
            if (!drawWrite.event) {
                records->Release();
                ids->Release();
                return;
            }
            *drawWrite.event = *e;
            e = drawWrite.event;
            // Keep the draw record open while the start record is written, so
            // its timestamps still enclose the start event.
            commitStart();
        } else
            commitStart();
    }
    if (records)
        records->Release();
    if (ids)
        ids->Release();
}
static void gather_draw(ID3D11DeviceContext* c, Event* e, unsigned method, unsigned vertices,
                        unsigned instances, unsigned first) noexcept {
    e->w[135] = presentEpoch.load(std::memory_order_acquire);
    e->w[6] = reinterpret_cast<std::uintptr_t>(c);
    e->w[7] = c->GetType();
    e->w[8] = method;
    e->w[9] = vertices;
    e->w[10] = instances;
    e->w[11] = first;
    e->w[12] = ~std::uint64_t(0);
    const auto identity = buffer(0x8cfc50);
    ID3D11Buffer* vb[32]{};
    UINT strides[32]{}, offsets[32]{};
    c->IAGetVertexBuffers(0, 32, vb, strides, offsets);
    e->w[46] = identity;
    for (unsigned i = 0; i < 32; ++i) {
        e->w[48 + 2 * i] = reinterpret_cast<std::uintptr_t>(vb[i]);
        e->w[49 + 2 * i] = (std::uint64_t(offsets[i]) << 32) | strides[i];
        if (vb[i]) {
            if (reinterpret_cast<std::uintptr_t>(vb[i]) == identity && identity) {
                // Preserve all matching slots; semantic selection remains unproven.
                e->w[45] |= std::uint64_t(1) << i;
                if (e->w[12] != ~std::uint64_t(0))
                    e->w[16] = 1;
                e->w[12] = i;
                e->w[13] = offsets[i];
                e->w[14] = strides[i];
                e->w[15] = identity;
            }
            vb[i]->Release();
        }
    }
    ID3D11ShaderResourceView* srv[4]{};
    c->VSGetShaderResources(0, 4, srv);
    for (unsigned i = 0; i < 4; ++i)
        if (srv[i]) {
            const unsigned at = 18 + i * 6;
            e->w[at] = reinterpret_cast<std::uintptr_t>(srv[i]);
            D3D11_SHADER_RESOURCE_VIEW_DESC view{};
            srv[i]->GetDesc(&view);
            ID3D11Resource* resource = nullptr;
            srv[i]->GetResource(&resource);
            if (resource) {
                ID3D11Buffer* b = nullptr;
                if (SUCCEEDED(resource->QueryInterface(__uuidof(ID3D11Buffer),
                                                       reinterpret_cast<void**>(&b)))) {
                    D3D11_BUFFER_DESC d{};
                    b->GetDesc(&d);
                    e->w[at + 1] = reinterpret_cast<std::uintptr_t>(b);
                    e->w[at + 2] = d.ByteWidth;
                    e->w[at + 3] = d.StructureByteStride;
                    if (view.ViewDimension == D3D11_SRV_DIMENSION_BUFFER ||
                        view.ViewDimension == D3D11_SRV_DIMENSION_BUFFEREX) {
                        e->w[at + 4] = view.Buffer.FirstElement;
                        e->w[at + 5] = view.Buffer.NumElements;
                    }
                    b->Release();
                }
                resource->Release();
            }
            srv[i]->Release();
        }
    ID3D11InputLayout* layout = nullptr;
    c->IAGetInputLayout(&layout);
    e->w[42] = reinterpret_cast<std::uintptr_t>(layout);
    if (layout) {
        LayoutInfo info{};
        UINT size = sizeof(info);
        if (SUCCEEDED(layout->GetPrivateData(LayoutKey, &size, &info)) && size == sizeof(info))
            for (unsigned i = 0; i < 8; ++i)
                e->w[112 + i] = info.w[i];
        layout->Release();
    }
    e->w[43] = buffer(0x8cfd40);
    e->w[44] = buffer(0x8cfde0);
    ID3D11VertexShader* shader = nullptr;
    c->VSGetShader(&shader, nullptr, nullptr);
    e->w[120] = reinterpret_cast<std::uintptr_t>(shader);
    if (shader) {
        ShaderInfo info{};
        UINT size = sizeof(info);
        if (SUCCEEDED(shader->GetPrivateData(ShaderKey, &size, &info)) && size == sizeof(info))
            for (unsigned i = 0; i < 8; ++i)
                e->w[121 + i] = info.w[i];
        shader->Release();
    }
    ID3D11RenderTargetView* target = nullptr;
    ID3D11DepthStencilView* depthView = nullptr;
    c->OMGetRenderTargets(1, &target, &depthView);
    e->w[129] = reinterpret_cast<std::uintptr_t>(target);
    e->w[130] = reinterpret_cast<std::uintptr_t>(depthView);
    if (target) {
        D3D11_RENDER_TARGET_VIEW_DESC desc{};
        target->GetDesc(&desc);
        e->w[131] = desc.Format;
        ID3D11Resource* resource = nullptr;
        target->GetResource(&resource);
        if (resource) {
            ID3D11Texture2D* texture = nullptr;
            if (SUCCEEDED(resource->QueryInterface(__uuidof(ID3D11Texture2D),
                                                   reinterpret_cast<void**>(&texture)))) {
                D3D11_TEXTURE2D_DESC d{};
                texture->GetDesc(&d);
                e->w[132] = d.Width;
                e->w[133] = d.Height;
                e->w[134] = d.SampleDesc.Count;
                texture->Release();
            }
            resource->Release();
        }
        target->Release();
    }
    if (depthView)
        depthView->Release();
}
void draw(ID3D11DeviceContext* c, unsigned method, unsigned vertices, unsigned instances,
          unsigned first) noexcept {
    if (!captureActive.load(std::memory_order_acquire))
        return;
    ++drawHookCalls;
    pendingMatteDraw = 0;
    if (samplingDone.load(std::memory_order_acquire)) {
        static thread_local ULONGLONG lastPoll = 0;
        const auto now = GetTickCount64();
        if (c && c->GetType() == D3D11_DEVICE_CONTEXT_IMMEDIATE && now != lastPoll) {
            lastPoll = now;
            Write pin(12);
            if (pin.event)
                gpu_poll(c);
        }
        return;
    }
    if (!c)
        return;
    if (sequenceV2) {
        // Capture-only events: gather into a scratch record and commit it only
        // when the draw is selected, so unselected draws cannot exhaust the
        // bounded event buffer during a sequence. The scratch needs the same
        // standard header words a Write would have produced, because the
        // producer match compares its engine frame.
        gpu_poll(c);
        Event scratch{};
        scratch.w[0] = 2;
        scratch.w[1] = GetCurrentThreadId();
        scratch.w[2] = stamp();
        scratch.w[4] = frame();
        gather_draw(c, &scratch, method, vertices, instances, first);
        gpu_select(c, &scratch, true);
        return;
    }
    Write write(2);
    auto* e = write.event;
    if (!e)
        return;
    gpu_poll(c);
    gather_draw(c, e, method, vertices, instances, first);
    gpu_select(c, e,
               false); // At most three nonblocking, full-buffer snapshots before original draws.
}
void redirect_indexed(ID3D11DeviceContext* c, unsigned n, unsigned instances, unsigned start,
                      int base, unsigned first, IndexedOriginal originalDraw) noexcept {
    const auto selected = pendingMatteDraw;
    pendingMatteDraw = 0;
    if (!selected || pendingMatteSlot >= mattes.size()) {
        originalDraw(c, n, instances, start, base, first);
        return;
    }
    auto& colorSlot = colorFrames[sequenceMode ? pendingMatteSlot / AggregateDrawLimit : 0];
    auto& colorAggregate = colorSlot.capture;
    auto& colorPrepared = colorSlot.prepared;
    auto& colorFirst = colorSlot.first;
    auto& colorLast = colorSlot.last;
    auto& colorEpoch = colorSlot.epoch;
    auto& colorDraws = colorSlot.draws;
    auto& slot = mattes[pendingMatteSlot];
    auto& matte = slot.capture;
    unsigned blendClass = 0;
    if (aggregateMode) {
        // Coverage only starts from an accumulating "over" pass; a modulation
        // ("darken") pass shades what earlier passes wrote and would leave a
        // cleared target empty, so the first capture of a frame has to be an
        // "over" draw and later modulation passes then shade it.
        Microsoft::WRL::ComPtr<ID3D11BlendState> blend;
        FLOAT factors[4]{};
        UINT mask = 0;
        c->OMGetBlendState(&blend, factors, &mask);
        D3D11_BLEND_DESC desc{};
        if (blend)
            blend->GetDesc(&desc);
        const auto& rt = desc.RenderTarget[0];
        const bool over =
            rt.BlendEnable && rt.SrcBlend == D3D11_BLEND_ONE &&
            rt.DestBlend == D3D11_BLEND_INV_SRC_ALPHA && rt.BlendOp == D3D11_BLEND_OP_ADD &&
            rt.SrcBlendAlpha == D3D11_BLEND_INV_DEST_ALPHA &&
            rt.DestBlendAlpha == D3D11_BLEND_ONE && rt.BlendOpAlpha == D3D11_BLEND_OP_ADD;
        const bool darken =
            rt.BlendEnable && rt.SrcBlend == D3D11_BLEND_ZERO &&
            rt.DestBlend == D3D11_BLEND_SRC_COLOR && rt.BlendOp == D3D11_BLEND_OP_ADD &&
            rt.SrcBlendAlpha == D3D11_BLEND_ONE && rt.DestBlendAlpha == D3D11_BLEND_ONE &&
            rt.BlendOpAlpha == D3D11_BLEND_OP_ADD;
        if (over) {
            ++blendOver;
            blendClass = 1;
        } else if (darken) {
            ++blendDarken;
            blendClass = 2;
        } else {
            ++blendOther;
            blendClass = 3;
        }
    }
    if (slot.attempted) {
        originalDraw(c, n, instances, start, base, first);
        return;
    }
    slot.attempted = true;
    {
        // Bounded record of what the aggregate redirects: draw shape, blend
        // class and pixel shader tell a prepass from the shaded character pass
        // when a frame reads back empty.
        static unsigned captureLog = 0;
        const auto& gpuStart = gpuSlots[pendingMatteSlot];
        if (captureLog < 48 && gpuStart.startEvent &&
            gpuStart.startEvent <= used.load(std::memory_order_acquire)) {
            const auto& draw = events[selected - 1];
            const auto& startEvent = events[gpuStart.startEvent - 1];
            std::ofstream g(directory + L"dolly_owner_captures.txt",
                            captureLog ? (std::ios::out | std::ios::app)
                                       : (std::ios::out | std::ios::trunc));
            if (g)
                g << "capture frame " << draw.w[4] << " index " << startEvent.w[8] << " owner "
                  << startEvent.w[9] << " producerFrame " << startEvent.w[26] << " method "
                  << draw.w[8] << " verts " << draw.w[9] << " instances " << draw.w[10] << " blend "
                  << (blendClass == 1   ? "over"
                      : blendClass == 2 ? "darken"
                                        : "other")
                  << " ps " << draw.w[120] << " target " << colorSlot.capture.width << "x"
                  << colorSlot.capture.height << "\n";
            ++captureLog;
        }
    }
    Write transaction(10);
    auto* out = transaction.event;
    if (!out) {
        originalDraw(c, n, instances, start, base, first);
        return;
    }
    const auto& drawEvent = events[selected - 1];
    slot.draw = selected;
    slot.event = std::uint64_t(out - events.data()) + 1;
    out->w[6] = selected;
    bool called = false, captured = false;
    try {
        Microsoft::WRL::ComPtr<ID3D11PixelShader> material;
        if (colorMode) {
            Microsoft::WRL::ComPtr<ID3D11BlendState> blend;
            FLOAT factors[4]{};
            UINT mask = 0;
            c->OMGetBlendState(&blend, factors, &mask);
            D3D11_BLEND_DESC desc{};
            if (blend)
                blend->GetDesc(&desc);
            else
                desc.RenderTarget[0].RenderTargetWriteMask = 15;
            const auto& rt = desc.RenderTarget[0];
            out->w[28] = rt.BlendEnable;
            out->w[29] = rt.SrcBlend;
            out->w[30] = rt.DestBlend;
            out->w[31] = rt.BlendOp;
            out->w[32] = rt.SrcBlendAlpha;
            out->w[33] = rt.DestBlendAlpha;
            out->w[34] = rt.BlendOpAlpha;
            out->w[35] = rt.RenderTargetWriteMask;
            const bool over =
                rt.BlendEnable && rt.SrcBlend == D3D11_BLEND_ONE &&
                rt.DestBlend == D3D11_BLEND_INV_SRC_ALPHA && rt.BlendOp == D3D11_BLEND_OP_ADD &&
                rt.SrcBlendAlpha == D3D11_BLEND_INV_DEST_ALPHA &&
                rt.DestBlendAlpha == D3D11_BLEND_ONE && rt.BlendOpAlpha == D3D11_BLEND_OP_ADD;
            const bool darken =
                rt.BlendEnable && rt.SrcBlend == D3D11_BLEND_ZERO &&
                rt.DestBlend == D3D11_BLEND_SRC_COLOR && rt.BlendOp == D3D11_BLEND_OP_ADD &&
                rt.SrcBlendAlpha == D3D11_BLEND_ONE && rt.DestBlendAlpha == D3D11_BLEND_ONE &&
                rt.BlendOpAlpha == D3D11_BLEND_OP_ADD;
            if (!aggregateMode &&
                ((rt.BlendEnable && !(over || darken)) || rt.RenderTargetWriteMask != 15))
                throw 3;
            out->w[27] = aggregateMode ? 2 : 1;
        }
        if (aggregateMode) {
            Microsoft::WRL::ComPtr<ID3D11PixelShader> ps;
            c->PSGetShader(&ps, nullptr, nullptr);
            if (!ps)
                throw 1;
            out->w[12] = 2;
            out->w[13] = reinterpret_cast<std::uintptr_t>(ps.Get());
        }
        if (materialMode && !aggregateMode) {
            Microsoft::WRL::ComPtr<ID3D11PixelShader> originalPS;
            c->PSGetShader(&originalPS, nullptr, nullptr);
            UINT size = 0;
            if (!originalPS || FAILED(originalPS->GetPrivateData(PixelBytesKey, &size, nullptr)) ||
                size > 1024 * 1024)
                throw 1;
            std::vector<unsigned char> bytes(size);
            if (FAILED(originalPS->GetPrivateData(PixelBytesKey, &size, bytes.data())))
                throw 1;
            Microsoft::WRL::ComPtr<ID3D11ShaderReflection> reflection;
            if (FAILED(D3DReflect(bytes.data(), bytes.size(), __uuidof(ID3D11ShaderReflection),
                                  reinterpret_cast<void**>(reflection.GetAddressOf()))))
                throw 1;
            D3D11_SHADER_DESC desc{};
            if (FAILED(reflection->GetDesc(&desc)))
                throw 1;
            bool target0 = false;
            for (UINT i = 0; i < desc.OutputParameters; ++i) {
                D3D11_SIGNATURE_PARAMETER_DESC out{};
                if (SUCCEEDED(reflection->GetOutputParameterDesc(i, &out)) &&
                    out.SystemValueType == D3D_NAME_TARGET && out.SemanticIndex == 0 &&
                    out.Register == 0 && out.ComponentType == D3D_REGISTER_COMPONENT_FLOAT32 &&
                    out.Mask == 15)
                    target0 = true;
            }
            if (!target0)
                throw 1;
            auto patched = material_white(bytes.data(), bytes.size(), !colorMode);
            if (patched.empty())
                throw 1;
            Microsoft::WRL::ComPtr<ID3D11Device> device;
            c->GetDevice(&device);
            if (FAILED(
                    device->CreatePixelShader(patched.data(), patched.size(), nullptr, &material)))
                throw 1;
            out->w[12] = 1;
            out->w[13] = reinterpret_cast<std::uintptr_t>(originalPS.Get());
            out->w[14] = bytes.size();
        }
        Microsoft::WRL::ComPtr<ID3D11VertexShader> originalVS, guardedVS;
        // Every selected draw must pass the same exact identity/record guard.
        if (guardMode) {
            c->VSGetShader(&originalVS, nullptr, nullptr);
            UINT size = 0;
            if (!originalVS || FAILED(originalVS->GetPrivateData(VertexBytesKey, &size, nullptr)) ||
                size > 1024 * 1024)
                throw 2;
            std::vector<unsigned char> bytes(size);
            if (FAILED(originalVS->GetPrivateData(VertexBytesKey, &size, bytes.data())))
                throw 2;
            Microsoft::WRL::ComPtr<ID3D11ShaderReflection> reflection;
            if (FAILED(D3DReflect(bytes.data(), bytes.size(), __uuidof(ID3D11ShaderReflection),
                                  reinterpret_cast<void**>(reflection.GetAddressOf()))))
                throw 2;
            D3D11_SHADER_DESC desc{};
            if (FAILED(reflection->GetDesc(&desc)))
                throw 2;
            unsigned position = 99;
            for (UINT i = 0; i < desc.OutputParameters; ++i) {
                D3D11_SIGNATURE_PARAMETER_DESC param{};
                if (SUCCEEDED(reflection->GetOutputParameterDesc(i, &param)) &&
                    param.SystemValueType == D3D_NAME_POSITION && param.Mask == 15)
                    position = param.Register;
            }
            const auto& gpu = gpuSlots[pendingMatteSlot];
            const auto& request = events[gpu.startEvent - 1];
            std::array<std::uint32_t, 8> expected{};
            for (unsigned i = 0; i < 8; ++i) {
                expected[i] = static_cast<std::uint32_t>(request.w[18 + i]);
                out->w[18 + i] = expected[i];
            }
            const auto id = static_cast<unsigned>(request.w[8]);
            auto patched = vertex_record_guard(bytes.data(), bytes.size(),
                                               static_cast<unsigned>(drawEvent.w[124]), position,
                                               id, expected);
            if (patched.empty())
                throw 2;
            Microsoft::WRL::ComPtr<ID3D11Device> device;
            c->GetDevice(&device);
            if (FAILED(device->CreateVertexShader(patched.data(), patched.size(), nullptr,
                                                  &guardedVS)))
                throw 2;
            out->w[16] = 1;
            out->w[17] = id;
            out->w[26] = reinterpret_cast<std::uintptr_t>(originalVS.Get());
        }
        auto renderOriginal = [&] {
            struct RestoreVS {
                ID3D11DeviceContext* c;
                ID3D11VertexShader* vs;
                ~RestoreVS() {
                    if (vs)
                        c->VSSetShader(vs, nullptr, 0);
                }
            } restore{c, originalVS.Get()};
            if (guardedVS)
                c->VSSetShader(guardedVS.Get(), nullptr, 0);
            called = true;
            originalDraw(c, n, instances, start, base, first);
        };
        if (aggregateMode) {
            if (sequenceV3 &&
                !colorSlot.clock.accept(drawEvent.w[135],
                                        authoredReplayTime.load(std::memory_order_acquire))) {
                sequenceCadenceLost = true;
                throw 3;
            }
            if (!colorPrepared) {
                if (colorAggregate.reusable_for(c, static_cast<unsigned>(drawEvent.w[132]),
                                                static_cast<unsigned>(drawEvent.w[133]))) {
                    // Recycled ring slot: the capture keeps its cleared target.
                } else if (!colorAggregate.prepare(c, static_cast<unsigned>(drawEvent.w[132]),
                                                   static_cast<unsigned>(drawEvent.w[133]), nullptr,
                                                   true, true))
                    throw 3;
                colorPrepared = true;
                colorFirst = selected;
                colorEpoch = drawEvent.w[135];
            }
            if (colorEpoch != drawEvent.w[135] || colorAggregate.width != drawEvent.w[132] ||
                colorAggregate.height != drawEvent.w[133])
                throw 3;
            captured = colorAggregate.append(c, renderOriginal, true, true, true);
            out->w[36] = 1;
            if (captured) {
                ++colorDraws;
                colorLast = selected;
            }

        } else
            captured = matte.begin(c, static_cast<unsigned>(drawEvent.w[132]),
                                   static_cast<unsigned>(drawEvent.w[133]), renderOriginal,
                                   material.Get(), occlusionMode, colorMode);
        out->w[15] = occlusionMode;
    } catch (...) {
        matte.failure = E_FAIL;
    }
    if (!called) {
        originalDraw(c, n, instances, start, base, first);
        called = true;
    }
    if (!captured) {
        gpuSlots[pendingMatteSlot].mismatch = true;
        if (sequenceV2)
            sequenceStall = true;
    }
    out->w[7] = called;
    out->w[8] = captured;
    out->w[9] = static_cast<std::uint32_t>(matte.failure);
    out->w[10] = aggregateMode ? colorAggregate.width : matte.width;
    out->w[11] = aggregateMode ? colorAggregate.height : matte.height;
}
void update(ID3D11DeviceContext* c, ID3D11Resource* destination, unsigned sub, const D3D11_BOX* box,
            const void* source, unsigned row, unsigned depth,
            UpdateOriginal originalUpdate) noexcept {
    if (!captureActive.load(std::memory_order_acquire)) {
        originalUpdate(c, destination, sub, box, source, row, depth);
        return;
    }
    Microsoft::WRL::ComPtr<ID3D11Buffer> candidate;
    if (!c || !destination || FAILED(destination->QueryInterface(IID_PPV_ARGS(&candidate))) ||
        reinterpret_cast<std::uintptr_t>(candidate.Get()) != buffer(0x8cfde0)) {
        originalUpdate(c, destination, sub, box, source, row, depth);
        return;
    }
    const auto revision = submittedOwners.invalidate();
    D3D11_BUFFER_DESC desc{};
    candidate->GetDesc(&desc);
    // The upload usually covers active records, not the whole allocation.
    // Keep only wholly covered records; never merge older ownership entries.
    const std::uint64_t left = box ? box->left : 0;
    const std::uint64_t right = box ? box->right : desc.ByteWidth;
    const bool validRange =
        left < right && right <= desc.ByteWidth &&
        (!box || (box->top == 0 && box->front == 0 && box->bottom == 1 && box->back == 1));
    if (!source || sub || !validRange || desc.StructureByteStride != 32 ||
        desc.ByteWidth > 16 * 1024 * 1024 || c->GetType() != D3D11_DEVICE_CONTEXT_IMMEDIATE) {
        originalUpdate(c, destination, sub, box, source, row, depth);
        return;
    }
    const auto generation = frame();
    const auto resource = reinterpret_cast<std::uintptr_t>(candidate.Get());
    const auto snapshot = producers.snapshot();
    std::array<ProducerEntry, 512> selected{};
    unsigned count = 0;
    bool overflow = false;
    for (unsigned i = 0; i < snapshot.count; ++i) {
        const auto& entry = snapshot.entries[i];
        if (!entry.owner || entry.frame != generation || entry.records != resource ||
            entry.instance >= desc.ByteWidth / 32)
            continue;
        std::uint32_t actual[8]{};
        const auto base = reinterpret_cast<std::uintptr_t>(source);
        const auto offset = entry.instance * 32;
        if (offset < left || offset + 32 > right)
            continue;
        const auto address = base + offset - left;
        if (address < base || !read(address, actual) ||
            !record_matches(static_cast<std::uint32_t>(entry.instance),
                            static_cast<std::uint32_t>(entry.instance), actual, entry.record))
            continue;
        if (count == selected.size()) {
            overflow = true;
            break;
        }
        selected[count++] = entry;
    }
    // Publish only after the original submission, never while it is pending.
    // The resource is retained by the table to exclude COM pointer reuse.
    originalUpdate(c, destination, sub, box, source, row, depth);
    const bool published =
        !overflow && submittedOwners.publish(revision, reinterpret_cast<std::uintptr_t>(c),
                                             resource, selected.data(), count, candidate);
    // Small diagnostic summary; no source contents or extra GPU operations.
    if (!sequenceV2 && uploadUpdates.fetch_add(1) < 16) {
        Write write(18);
        if (auto* e = write.event) {
            e->w[6] = reinterpret_cast<std::uintptr_t>(c);
            e->w[7] = resource;
            e->w[8] = generation;
            e->w[9] = count;
            e->w[10] = published;
            e->w[11] = revision;
        }
    }
}
void present_boundary(ID3D11DeviceContext* c) noexcept {
    if (!captureActive.load(std::memory_order_acquire))
        return;
    if (sequenceV2) {
        const auto now = GetTickCount64();
        if (now - lastTimeline >= 1000) {
            lastTimeline = now;
            try {
                std::ofstream t(directory + L"dolly_owner_timeline.txt", std::ios::app);
                t << (now - started) << " draw " << drawHookCalls.load() << " producers "
                  << producerCalls.load() << " known " << producerKnownCalls.load() << " draws "
                  << drawHookCalls.load() << " sealed " << framesSealed << " written "
                  << framesWritten << " presents " << presentEpoch.load() << " devChanges "
                  << presentDeviceChanges.load() << '\n';
            } catch (...) {
            }
        }
    }
    if (c) {
        ID3D11Device* device = nullptr;
        c->GetDevice(&device);
        const auto value = reinterpret_cast<std::uintptr_t>(device);
        if (device)
            device->Release();
        if (firstPresentDevice.load(std::memory_order_relaxed) == 0)
            firstPresentDevice.store(value, std::memory_order_relaxed);
        const auto previous = lastPresentDevice.exchange(value, std::memory_order_relaxed);
        if (previous && previous != value)
            presentDeviceChanges.fetch_add(1, std::memory_order_relaxed);
    }
    if (aggregateMode && (sequenceV2 ? framesSealed < sequenceCap : colorIndex < 2)) {
        auto& slot = colorFrames[colorIndex];
        if (sequenceV3) {
            const auto admission = capture_image_admission(
                lastSealedReplayTime, authoredReplayTime.load(std::memory_order_acquire),
                slot.prepared && !slot.sealed);
            if (admission == CaptureImageAdmission::missing ||
                admission == CaptureImageAdmission::inconsistent) {
                sequenceCadenceLost = true;
                sequenceStall = true;
            }
        }
        if (slot.prepared && !slot.sealed) {
            Write seal(14);
            if (seal.event) {
                const std::uint64_t header[8] = {0x314641524c4f4344ULL,
                                                 slot.capture.width,
                                                 slot.capture.height,
                                                 slot.draws,
                                                 slot.first,
                                                 slot.last,
                                                 slot.epoch,
                                                 8};
                for (unsigned i = 0; i < 8; ++i)
                    slot.header[i] = header[i];
                slot.sealed = true;
                seal.event->w[6] = slot.epoch;
                seal.event->w[7] = slot.draws;
                seal.event->w[8] = slot.capture.finish(c);
                if (sequenceV2 && framesSealed < SequenceMaxCap) {
                    auto& meta = frameMeta[framesSealed];
                    const auto sealNow = GetTickCount64();
                    const double sealPhase = authoredReplayTime.load(std::memory_order_acquire);
                    if (sequenceV3 && !slot.clock.matches(slot.epoch, sealPhase))
                        sequenceCadenceLost = true;
                    meta.replay_time = sequenceV3 ? slot.clock.phase : sealPhase;
                    meta.epoch = slot.epoch;
                    meta.draws = slot.draws;
                    meta.first = slot.first;
                    meta.last = slot.last;
                    meta.wall_ms = firstSeal ? sealNow - firstSeal : 0;
                    meta.delta_ms = lastSeal ? sealNow - lastSeal : 0;
                    if (!firstSeal)
                        firstSeal = sealNow;
                    lastSeal = sealNow;
                    if (sequenceV3) {
                        if (!capture_phase_advances(lastSealedReplayTime, meta.replay_time))
                            sequenceCadenceLost = true;
                        lastSealedReplayTime = meta.replay_time;
                    }
                }
                if (sequenceMode) {
                    ++framesSealed;
                    if (sequenceV2)
                        colorIndex = (colorIndex + 1) % SequenceRing;
                    else
                        ++colorIndex;
                    meshFrameSet = false;
                    meshFrame = meshProducerFrame = 0;
                }
            }
        }
    }
    const auto prior = presentEpoch.fetch_add(1, std::memory_order_acq_rel);
    Write write(13);
    if (write.event) {
        write.event->w[6] = prior;
        write.event->w[7] = prior + 1;
    }
}
void command(ID3D11DeviceContext* c, ID3D11CommandList* list, unsigned kind) noexcept {
    if (!captureActive.load(std::memory_order_acquire))
        return;
    Write write(kind);
    if (!write.event)
        return;
    write.event->w[6] = reinterpret_cast<std::uintptr_t>(c);
    write.event->w[7] = reinterpret_cast<std::uintptr_t>(list);
}
}
