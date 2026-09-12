// Included inside bridge_win.cpp's anonymous namespace, after compatibility.
// The reviewed SetUpView caches a second camera and its basis before returning.
#pragma once
namespace camera_view {
using AngleVectors = void(__fastcall*)(const float*, float*, float*, float*);
struct Cache {
    std::uintptr_t origin = 0, angles = 0, forward = 0, right = 0, up = 0;
    AngleVectors basis = nullptr;
};
inline constexpr unsigned char kCachePattern[] = {
    0x4c, 0x8d, 0x0d, 0,    0,    0,    0,    0xf3, 0x41, 0x0f, 0x11, 0x45, 0,    0x4c, 0x8d,
    0x05, 0,    0,    0,    0,    0x48, 0x8b, 0xcd, 0x48, 0x8d, 0x15, 0,    0,    0,    0,
    0xe8, 0,    0,    0,    0,    0xf2, 0x41, 0x0f, 0x10, 0x06, 0x48, 0x8d, 0x0d, 0,    0,
    0,    0,    0xf2, 0x0f, 0x11, 0x05, 0,    0,    0,    0,    0x41, 0x8b, 0x46, 0x08, 0x89,
    0x05, 0,    0,    0,    0,    0xf2, 0x0f, 0x10, 0x45, 0,    0xf2, 0x0f, 0x11, 0x05, 0,
    0,    0,    0,    0x8b, 0x45, 0x08, 0x89, 0x05, 0,    0,    0,    0};
inline constexpr unsigned char kAuxiliaryCopy[] = {
    0xf2, 0x41, 0x0f, 0x10, 0x06, 0x41, 0x8b, 0x46, 0x08, 0xf2, 0x0f, 0x11, 0x86, 0x58, 0x05,
    0,    0,    0xf2, 0x0f, 0x10, 0x45, 0,    0x89, 0x86, 0x60, 0x05, 0,    0,    0x8b, 0x45,
    0x08, 0xf2, 0x0f, 0x11, 0x86, 0x64, 0x05, 0,    0,    0x89, 0x86, 0x6c, 0x05, 0,    0};
inline constexpr unsigned char kOriginCopy[] = {
    0xf2, 0x0f, 0x10, 0x86, 0x58, 0x05, 0, 0, 0x8b, 0x86, 0x60, 0x05, 0, 0,
    0xf2, 0x0f, 0x11, 0x86, 0x70, 0x05, 0, 0, 0x89, 0x86, 0x78, 0x05, 0, 0};
inline bool contains_once(const unsigned char* code, std::size_t size, const unsigned char* pattern,
                          std::size_t length) {
    const auto* found = std::search(code, code + size, pattern, pattern + length);
    return found != code + size &&
           std::search(found + 1, code + size, pattern, pattern + length) == code + size;
}
inline Cache decode(const unsigned char* code, std::size_t size, std::uintptr_t address,
                    std::uintptr_t data, std::size_t data_size, std::uintptr_t text,
                    std::size_t text_size) {
    Cache result;
    if (!code || !contains_once(code, size, kAuxiliaryCopy, sizeof(kAuxiliaryCopy)) ||
        !contains_once(code, size, kOriginCopy, sizeof(kOriginCopy)))
        return result;
    std::array<unsigned char, sizeof(kCachePattern)> mask{};
    mask.fill(1);
    for (auto offset : {3u, 16u, 26u, 31u, 43u, 51u, 61u, 74u, 83u})
        std::fill(mask.begin() + offset, mask.begin() + offset + 4, static_cast<unsigned char>(0));
    std::size_t offset = 0;
    if (dolly::find_pattern(code, size, kCachePattern, mask.data(), mask.size(), offset) != 1)
        return result;
    const auto target = [&](unsigned operand) {
        std::int32_t relative = 0;
        std::memcpy(&relative, code + offset + operand, 4);
        return std::uintptr_t(std::int64_t(address + offset + operand + 4) + relative);
    };
    const auto within = [](std::uintptr_t value, std::size_t bytes, std::uintptr_t base,
                           std::size_t length) {
        return value >= base && bytes <= length && value - base <= length - bytes;
    };
    result.up = target(3);
    result.right = target(16);
    result.forward = target(26);
    result.origin = target(51);
    result.angles = target(74);
    const auto basis = target(31);
    if (target(61) != result.origin + 8 || target(83) != result.angles + 8 ||
        result.angles != result.origin + 16 || result.forward != result.angles + 16 ||
        result.right != result.forward + 16 || result.up != result.right + 16 ||
        result.origin % 4 || !within(result.origin, 76, data, data_size) ||
        !within(basis, 32, text, text_size))
        return {};
    result.basis = reinterpret_cast<AngleVectors>(basis);
    return result;
}
inline Cache resolve(HMODULE client, std::uintptr_t setup) {
    std::uintptr_t data = 0, text = 0;
    std::size_t data_size = 0, text_size = 0;
    if (!compat_detail::section_range(client, ".data", data, data_size) ||
        !compat_detail::section_range(client, ".text", text, text_size))
        return {};
    std::array<unsigned char, 0x860> bytes{};
    if (!read_memory(setup, bytes.data(), bytes.size()))
        return {};
    auto result = decode(bytes.data(), bytes.size(), setup, data, data_size, text, text_size);
    // The helper is called with angles, forward, right and up in RCX/RDX/R8/R9.
    // Recheck its reviewed entry even though the module already passed its gate.
    constexpr unsigned char entry[] = {0x48, 0x8b, 0xc4, 0x48, 0x89, 0x58, 0x08, 0x48,
                                       0x89, 0x70, 0x10, 0x57, 0x48, 0x83, 0xec, 0x70};
    unsigned char actual[sizeof(entry)]{};
    if (!result.basis ||
        !read_memory(reinterpret_cast<std::uintptr_t>(result.basis), actual, sizeof(actual)) ||
        std::memcmp(actual, entry, sizeof(entry)))
        return {};
    return result;
}
inline void apply(const Cache& cache, std::uintptr_t view, const float (&xyz)[3],
                  const float (&angles)[3]) {
    std::memcpy(reinterpret_cast<void*>(view + 0x4a0), xyz, sizeof(xyz));
    std::memcpy(reinterpret_cast<void*>(view + 0x4b8), angles, sizeof(angles));
    // Keep all three origins emitted by SetUpView on the authored free camera.
    std::memcpy(reinterpret_cast<void*>(view + 0x558), xyz, sizeof(xyz));
    std::memcpy(reinterpret_cast<void*>(view + 0x564), angles, sizeof(angles));
    std::memcpy(reinterpret_cast<void*>(view + 0x570), xyz, sizeof(xyz));
    // Normal main-view matrices must drive the auxiliary view too. This bit
    // selects an independent auxiliary pose; it does not disable scene culling.
    *reinterpret_cast<unsigned char*>(view + 0x555) &= ~4u;
    cache.basis(angles, reinterpret_cast<float*>(cache.forward),
                reinterpret_cast<float*>(cache.right), reinterpret_cast<float*>(cache.up));
    std::memcpy(reinterpret_cast<void*>(cache.origin), xyz, sizeof(xyz));
    std::memcpy(reinterpret_cast<void*>(cache.angles), angles, sizeof(angles));
}
} // namespace camera_view
