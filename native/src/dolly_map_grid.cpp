// Offline Source 2 map collision extractor and 4-floor top-down grid baker.
//
// VPK v2, compiled resource, binary KV3, and Rubikon physics extraction logic
// is adapted from ValveResourceFormat (MIT License, Copyright (c) 2015
// ValveResourceFormat Contributors) and SourceMVM (MIT License,
// Copyright (c) 2021 advancedfx.org, Copyright (c) 2026 Wockstajit (SOURCE:MVM)).
//
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to deal
// in the Software without restriction, including without limitation the rights
// to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
// copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions:
// The above copyright notice and this permission notice shall be included in
// all copies or substantial portions of the Software.

#include "dolly_map_grid.hpp"
#include "dolly_zstd_lz4.hpp"

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <limits>
#include <numeric>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#ifdef _WIN32
#define NOMINMAX
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <wincodec.h>
#endif

namespace dolly::map_grid {
namespace {

using Vec3 = std::array<float, 3>;

inline Vec3 operator-(const Vec3& a, const Vec3& b) noexcept {
    return {a[0] - b[0], a[1] - b[1], a[2] - b[2]};
}

inline Vec3 cross(const Vec3& a, const Vec3& b) noexcept {
    return {a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]};
}

inline float dot(const Vec3& a, const Vec3& b) noexcept {
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

inline bool finite_vec3(const Vec3& v) noexcept {
    return std::isfinite(v[0]) && std::isfinite(v[1]) && std::isfinite(v[2]);
}

std::string to_lower(std::string s) {
    for (char& c : s) {
        if (c >= 'A' && c <= 'Z')
            c = static_cast<char>(c | 0x20);
    }
    return s;
}

uint64_t fnv1a_64(const void* data, size_t size, uint64_t seed = 14695981039346656037ULL) noexcept {
    const auto* p = static_cast<const uint8_t*>(data);
    uint64_t h = seed;
    for (size_t i = 0; i < size; ++i) {
        h ^= p[i];
        h *= 1099511628211ULL;
    }
    return h;
}

struct Cursor {
    const uint8_t* p = nullptr;
    size_t n = 0;
    size_t at = 0;

    void need(size_t k) const {
        if (k > n || at > n - k)
            throw std::runtime_error("truncated binary data");
    }

    template <class T> T get() {
        need(sizeof(T));
        T v{};
        std::memcpy(&v, p + at, sizeof(T));
        at += sizeof(T);
        return v;
    }

    std::string cstr() {
        const size_t start = at;
        while (true) {
            need(1);
            if (p[at++] == 0)
                break;
        }
        return std::string(reinterpret_cast<const char*>(p + start), at - start - 1);
    }

    const uint8_t* take(size_t k) {
        need(k);
        const uint8_t* r = p + at;
        at += k;
        return r;
    }
};

// -----------------------------------------------------------------------------
// Zero-dependency VPK v1/v2 Reader

class VpkReader {
public:
    explicit VpkReader(const std::filesystem::path& path) : path_(path) {
        std::ifstream in(path, std::ios::binary | std::ios::ate);
        if (!in)
            throw std::runtime_error("cannot open VPK: " + path.string());
        const auto end_pos = in.tellg();
        if (end_pos < 12)
            throw std::runtime_error("truncated VPK: " + path.string());
        const uint64_t file_size = static_cast<uint64_t>(end_pos);
        in.seekg(0);

        uint32_t hdr[7] = {};
        in.read(reinterpret_cast<char*>(hdr), 12);
        if (!in || hdr[0] != 0x55AA1234u)
            throw std::runtime_error("invalid VPK magic: " + path.string());
        const uint32_t version = hdr[1];
        const uint32_t tree_size = hdr[2];
        size_t header_size = 12;
        if (version == 2) {
            in.read(reinterpret_cast<char*>(hdr + 3), 16);
            if (!in)
                throw std::runtime_error("truncated VPK v2 header");
            header_size = 28;
        } else if (version != 1) {
            throw std::runtime_error("unsupported VPK version");
        }
        if (tree_size > file_size - header_size)
            throw std::runtime_error("VPK tree exceeds file size");

        std::vector<uint8_t> tree(tree_size);
        in.read(reinterpret_cast<char*>(tree.data()), static_cast<std::streamsize>(tree_size));
        if (!in)
            throw std::runtime_error("truncated VPK tree");

        data_start_ = header_size + tree_size;
        Cursor c{tree.data(), tree.size(), 0};
        while (true) {
            const std::string ext = c.cstr();
            if (ext.empty())
                break;
            while (true) {
                const std::string dir = c.cstr();
                if (dir.empty())
                    break;
                while (true) {
                    const std::string name = c.cstr();
                    if (name.empty())
                        break;
                    Entry e{};
                    e.crc = c.get<uint32_t>();
                    e.preload_bytes = c.get<uint16_t>();
                    e.archive = c.get<uint16_t>();
                    e.offset = c.get<uint32_t>();
                    e.length = c.get<uint32_t>();
                    if (c.get<uint16_t>() != 0xFFFFu)
                        throw std::runtime_error("bad VPK entry terminator");
                    e.preload_offset = header_size + c.at;
                    c.take(e.preload_bytes);

                    std::string full;
                    if (dir != " ")
                        full = dir + "/";
                    full += name;
                    if (ext != " ")
                        full += "." + ext;
                    entries_.emplace(to_lower(full), e);
                }
            }
        }
    }

    bool read(const std::string& rel_path, std::vector<uint8_t>& out) const {
        const auto it = entries_.find(to_lower(rel_path));
        if (it == entries_.end())
            return false;
        const Entry& e = it->second;
        out.resize(static_cast<size_t>(e.preload_bytes) + static_cast<size_t>(e.length));
        if (e.preload_bytes > 0) {
            if (!read_range(path_, e.preload_offset, e.preload_bytes, out.data()))
                throw std::runtime_error("failed reading VPK preload: " + rel_path);
        }
        if (e.length > 0) {
            const auto archive_file = (e.archive == 0x7FFFu) ? path_ : archive_path(e.archive);
            const uint64_t off =
                (e.archive == 0x7FFFu) ? (data_start_ + e.offset) : static_cast<uint64_t>(e.offset);
            if (!read_range(archive_file, off, e.length, out.data() + e.preload_bytes))
                throw std::runtime_error("failed reading VPK entry: " + rel_path);
        }
        return true;
    }

    std::string find_first_matching(const std::string& suffix_lower) const {
        std::string best;
        for (const auto& [k, v] : entries_) {
            if (k.size() >= suffix_lower.size() &&
                k.compare(k.size() - suffix_lower.size(), suffix_lower.size(), suffix_lower) == 0) {
                if (best.empty() || k < best)
                    best = k;
            }
        }
        return best;
    }

private:
    struct Entry {
        uint32_t crc = 0;
        uint16_t preload_bytes = 0;
        uint16_t archive = 0;
        uint32_t offset = 0;
        uint32_t length = 0;
        uint64_t preload_offset = 0;
    };

    static bool read_range(const std::filesystem::path& file, uint64_t offset, uint64_t size,
                           uint8_t* dst) {
        std::ifstream in(file, std::ios::binary);
        if (!in)
            return false;
        in.seekg(static_cast<std::streamoff>(offset));
        in.read(reinterpret_cast<char*>(dst), static_cast<std::streamsize>(size));
        return static_cast<bool>(in) && static_cast<uint64_t>(in.gcount()) == size;
    }

    std::filesystem::path archive_path(uint16_t idx) const {
        std::string stem = path_.stem().string();
        if (stem.size() > 4 && stem.compare(stem.size() - 4, 4, "_dir") == 0)
            stem.resize(stem.size() - 4);
        char buf[32]{};
        std::snprintf(buf, sizeof(buf), "_%03u.vpk", static_cast<unsigned>(idx));
        return path_.parent_path() / (stem + buf);
    }

    std::filesystem::path path_;
    uint64_t data_start_ = 0;
    std::unordered_map<std::string, Entry> entries_;
};

// -----------------------------------------------------------------------------
// Zero-copy Binary KV3 Decoder (VKV3 legacy + KV3 v1..v5, Raw / LZ4 / ZSTD)

struct Kv3 {
    enum class Type : uint8_t { Null, Bool, Int, UInt, Double, String, Blob, Array, Object };
    Type type = Type::Null;
    uint8_t flag = 0;
    bool b = false;
    int64_t i = 0;
    uint64_t u = 0;
    double d = 0.0;
    std::string s;
    uint32_t blob_offset = 0;
    uint32_t blob_length = 0;
    std::vector<Kv3> arr;
    std::vector<std::pair<std::string, Kv3>> obj;

    static const Kv3& null_value() noexcept {
        static const Kv3 kNull;
        return kNull;
    }

    const Kv3* find(const char* key) const noexcept {
        if (type != Type::Object)
            return nullptr;
        for (const auto& [k, v] : obj) {
            if (k == key)
                return &v;
        }
        return nullptr;
    }

    const Kv3& operator[](const char* key) const noexcept {
        const Kv3* p = find(key);
        return p ? *p : null_value();
    }

    bool is_null() const noexcept { return type == Type::Null; }

    double number(double fallback = 0.0) const noexcept {
        switch (type) {
        case Type::Bool:
            return b ? 1.0 : 0.0;
        case Type::Int:
            return static_cast<double>(i);
        case Type::UInt:
            return static_cast<double>(u);
        case Type::Double:
            return d;
        default:
            return fallback;
        }
    }

    int64_t integer(int64_t fallback = 0) const noexcept {
        switch (type) {
        case Type::Bool:
            return b ? 1 : 0;
        case Type::Int:
            return i;
        case Type::UInt:
            return static_cast<int64_t>(u);
        case Type::Double:
            return static_cast<int64_t>(d);
        default:
            return fallback;
        }
    }

    const std::string& str() const noexcept {
        static const std::string kEmpty;
        return type == Type::String ? s : kEmpty;
    }

    const Kv3& at(size_t idx) const noexcept {
        return (type == Type::Array && idx < arr.size()) ? arr[idx] : null_value();
    }
};

struct Kv3Doc {
    Kv3 root;
    std::vector<uint8_t> blobs_storage;

    const uint8_t* blob_ptr(const Kv3& v) const noexcept {
        if (v.type != Kv3::Type::Blob || v.blob_length == 0)
            return nullptr;
        if (static_cast<size_t>(v.blob_offset) + static_cast<size_t>(v.blob_length) >
            blobs_storage.size())
            return nullptr;
        return blobs_storage.data() + v.blob_offset;
    }
};

namespace kv3_detail {

enum NodeType : uint8_t {
    KV_NULL = 1,
    KV_BOOLEAN = 2,
    KV_INT64 = 3,
    KV_UINT64 = 4,
    KV_DOUBLE = 5,
    KV_STRING = 6,
    KV_BLOB = 7,
    KV_ARRAY = 8,
    KV_OBJECT = 9,
    KV_ARRAY_TYPED = 10,
    KV_INT32 = 11,
    KV_UINT32 = 12,
    KV_TRUE = 13,
    KV_FALSE = 14,
    KV_INT64_ZERO = 15,
    KV_INT64_ONE = 16,
    KV_DOUBLE_ZERO = 17,
    KV_DOUBLE_ONE = 18,
    KV_FLOAT = 19,
    KV_INT16 = 20,
    KV_UINT16 = 21,
    KV_INT8 = 22,
    KV_UINT8 = 23,
    KV_ARRAY_BYTE_LENGTH = 24,
    KV_ARRAY_AUX = 25,
    KV_UINT32_ZERO = 26,
    KV_UINT32_ONE = 27,
};

struct Span {
    const uint8_t* p = nullptr;
    size_t n = 0;

    template <class T> T pop() {
        if (n < sizeof(T))
            throw std::runtime_error("KV3 buffer underrun");
        T v{};
        std::memcpy(&v, p, sizeof(T));
        p += sizeof(T);
        n -= sizeof(T);
        return v;
    }

    Span slice(size_t from, size_t to) const {
        if (from > to || to > n)
            throw std::runtime_error("KV3 slice out of range");
        return Span{p + from, to - from};
    }
};

struct Buffers {
    Span b1, b2, b4, b8;
};

struct Context {
    int version = 0;
    Span types, object_lengths, blob_lengths;
    const uint8_t* blobs_base = nullptr;
    size_t blobs_size = 0;
    size_t blobs_cursor = 0;
    std::vector<uint8_t>* doc_blobs = nullptr;
    std::vector<std::string> strings;
    Buffers buffer, aux;
};

inline void align_up(size_t& offset, size_t alignment) noexcept {
    offset = (offset + alignment - 1) & ~(alignment - 1);
}

std::string read_string(const Context& ctx, int32_t id) {
    return (id >= 0 && static_cast<size_t>(id) < ctx.strings.size())
               ? ctx.strings[static_cast<size_t>(id)]
               : std::string();
}

uint8_t normalize_flag(uint8_t raw, int version) {
    if (version >= 3) {
        if (raw > 6)
            throw std::runtime_error("unexpected KV3 flag");
        return raw;
    }
    if (raw & 4)
        raw ^= 4;
    switch (raw) {
    case 0:
        return 0;
    case 1:
        return 1;
    case 2:
        return 2;
    case 8:
        return 3;
    case 16:
        return 4;
    case 32:
        return 5;
    default:
        throw std::runtime_error("unexpected legacy KV3 flag");
    }
}

std::pair<uint8_t, uint8_t> read_type(Context& ctx) {
    uint8_t type = ctx.types.pop<uint8_t>();
    uint8_t flag = 0;
    if (ctx.version >= 3) {
        if (type & 0x80)
            flag = normalize_flag(ctx.types.pop<uint8_t>(), ctx.version);
        if (type & 0x40)
            ctx.types.pop<uint8_t>();
        type &= 0x3F;
    } else if (type & 0x80) {
        type &= 0x7F;
        flag = normalize_flag(ctx.types.pop<uint8_t>(), ctx.version);
    }
    return {type, flag};
}

Kv3 read_value(Context& ctx, uint8_t type, uint8_t flag);

void add_member(Kv3& parent, std::string name, Kv3 value) {
    for (auto& [k, v] : parent.obj) {
        if (k == name) {
            v = std::move(value);
            return;
        }
    }
    parent.obj.emplace_back(std::move(name), std::move(value));
}

void parse_member(Context& ctx, Kv3& parent) {
    const auto [type, flag] = read_type(ctx);
    if (parent.type == Kv3::Type::Array) {
        parent.arr.push_back(read_value(ctx, type, flag));
        return;
    }
    const int32_t id = ctx.buffer.b4.pop<int32_t>();
    std::string name = read_string(ctx, id);
    add_member(parent, std::move(name), read_value(ctx, type, flag));
}

Kv3 read_value(Context& ctx, uint8_t type, uint8_t flag) {
    Kv3 v;
    v.flag = flag;
    Buffers& buf = ctx.buffer;
    switch (type) {
    case KV_NULL:
        v.type = Kv3::Type::Null;
        break;
    case KV_TRUE:
        v.type = Kv3::Type::Bool;
        v.b = true;
        break;
    case KV_FALSE:
        v.type = Kv3::Type::Bool;
        v.b = false;
        break;
    case KV_INT64_ZERO:
        v.type = Kv3::Type::Int;
        v.i = 0;
        break;
    case KV_INT64_ONE:
        v.type = Kv3::Type::Int;
        v.i = 1;
        break;
    case KV_DOUBLE_ZERO:
        v.type = Kv3::Type::Double;
        v.d = 0.0;
        break;
    case KV_DOUBLE_ONE:
        v.type = Kv3::Type::Double;
        v.d = 1.0;
        break;
    case KV_UINT32_ZERO:
        v.type = Kv3::Type::UInt;
        v.u = 0;
        break;
    case KV_UINT32_ONE:
        v.type = Kv3::Type::UInt;
        v.u = 1;
        break;
    case KV_BOOLEAN:
        v.type = Kv3::Type::Bool;
        v.b = buf.b1.pop<uint8_t>() == 1;
        break;
    case KV_INT8:
        v.type = Kv3::Type::Int;
        v.i = buf.b1.pop<int8_t>();
        break;
    case KV_UINT8:
        v.type = Kv3::Type::UInt;
        v.u = buf.b1.pop<uint8_t>();
        break;
    case KV_INT16:
        v.type = Kv3::Type::Int;
        v.i = buf.b2.pop<int16_t>();
        break;
    case KV_UINT16:
        v.type = Kv3::Type::UInt;
        v.u = buf.b2.pop<uint16_t>();
        break;
    case KV_INT32:
        v.type = Kv3::Type::Int;
        v.i = buf.b4.pop<int32_t>();
        break;
    case KV_UINT32:
        v.type = Kv3::Type::UInt;
        v.u = buf.b4.pop<uint32_t>();
        break;
    case KV_FLOAT:
        v.type = Kv3::Type::Double;
        v.d = buf.b4.pop<float>();
        break;
    case KV_INT64:
        v.type = Kv3::Type::Int;
        v.i = buf.b8.pop<int64_t>();
        break;
    case KV_UINT64:
        v.type = Kv3::Type::UInt;
        v.u = buf.b8.pop<uint64_t>();
        break;
    case KV_DOUBLE:
        v.type = Kv3::Type::Double;
        v.d = buf.b8.pop<double>();
        break;
    case KV_STRING:
        v.type = Kv3::Type::String;
        v.s = read_string(ctx, buf.b4.pop<int32_t>());
        break;
    case KV_BLOB: {
        v.type = Kv3::Type::Blob;
        if (ctx.version < 2) {
            const int32_t len = buf.b4.pop<int32_t>();
            if (len < 0 || static_cast<size_t>(len) > buf.b1.n)
                throw std::runtime_error("KV3 v1 blob out of range");
            v.blob_offset = static_cast<uint32_t>(ctx.doc_blobs->size());
            v.blob_length = static_cast<uint32_t>(len);
            ctx.doc_blobs->insert(ctx.doc_blobs->end(), buf.b1.p, buf.b1.p + len);
            buf.b1 = buf.b1.slice(static_cast<size_t>(len), buf.b1.n);
        } else {
            const int32_t len = ctx.blob_lengths.pop<int32_t>();
            if (len < 0 || static_cast<size_t>(len) > ctx.blobs_size - ctx.blobs_cursor)
                throw std::runtime_error("KV3 blob out of range");
            v.blob_offset = static_cast<uint32_t>(ctx.blobs_cursor);
            v.blob_length = static_cast<uint32_t>(len);
            ctx.blobs_cursor += static_cast<size_t>(len);
        }
        break;
    }
    case KV_ARRAY: {
        v.type = Kv3::Type::Array;
        const int32_t len = buf.b4.pop<int32_t>();
        if (len < 0)
            throw std::runtime_error("negative KV3 array length");
        v.arr.reserve(static_cast<size_t>(len));
        for (int32_t k = 0; k < len; ++k)
            parse_member(ctx, v);
        break;
    }
    case KV_ARRAY_TYPED:
    case KV_ARRAY_BYTE_LENGTH: {
        v.type = Kv3::Type::Array;
        const int32_t len = (type == KV_ARRAY_BYTE_LENGTH)
                                ? static_cast<int32_t>(buf.b1.pop<uint8_t>())
                                : buf.b4.pop<int32_t>();
        if (len < 0)
            throw std::runtime_error("negative KV3 typed array length");
        const auto [sub, sub_flag] = read_type(ctx);
        v.arr.reserve(static_cast<size_t>(len));
        for (int32_t k = 0; k < len; ++k)
            v.arr.push_back(read_value(ctx, sub, sub_flag));
        break;
    }
    case KV_ARRAY_AUX: {
        v.type = Kv3::Type::Array;
        const int32_t len = buf.b1.pop<uint8_t>();
        const auto [sub, sub_flag] = read_type(ctx);
        std::swap(ctx.buffer, ctx.aux);
        v.arr.reserve(static_cast<size_t>(len));
        for (int32_t k = 0; k < len; ++k)
            v.arr.push_back(read_value(ctx, sub, sub_flag));
        std::swap(ctx.buffer, ctx.aux);
        break;
    }
    case KV_OBJECT: {
        v.type = Kv3::Type::Object;
        const int32_t len =
            (ctx.version >= 5) ? ctx.object_lengths.pop<int32_t>() : buf.b4.pop<int32_t>();
        if (len < 0)
            throw std::runtime_error("negative KV3 object length");
        v.obj.reserve(static_cast<size_t>(len));
        for (int32_t k = 0; k < len; ++k)
            parse_member(ctx, v);
        break;
    }
    default:
        throw std::runtime_error("unknown KV3 node type " + std::to_string(type));
    }
    return v;
}

std::vector<std::string> read_strings(Span& s, int count) {
    std::vector<std::string> out;
    out.reserve(static_cast<size_t>(count));
    for (int k = 0; k < count; ++k) {
        const auto* end = static_cast<const uint8_t*>(std::memchr(s.p, 0, s.n));
        if (!end)
            throw std::runtime_error("unterminated KV3 string");
        const size_t len = static_cast<size_t>(end - s.p);
        out.emplace_back(reinterpret_cast<const char*>(s.p), len);
        s = s.slice(len + 1, s.n);
    }
    return out;
}

void lz4_exact(const uint8_t* src, size_t src_sz, uint8_t* dst, size_t dst_sz) {
    size_t written = 0;
    if (!detail::lz4_decompress_block(src, src_sz, dst, dst_sz, nullptr, 0, written) ||
        written != dst_sz) {
        throw std::runtime_error("LZ4 decompression failed");
    }
}

void zstd_exact(const uint8_t* src, size_t src_sz, uint8_t* dst, size_t dst_sz) {
    std::string err;
    if (!detail::zstd_decompress_exact(src, src_sz, dst, dst_sz, &err))
        throw std::runtime_error("ZSTD decompression failed: " + err);
}

bool is_kv3_magic(const uint8_t* data, size_t size) noexcept {
    if (size < 4)
        return false;
    const uint32_t magic = detail::read_u32_le(data);
    return magic == 0x03564B56u ||
           ((magic & 0xFFFFFF00u) == 0x4B563300u && (magic & 0xFFu) >= 1 && (magic & 0xFFu) <= 5);
}

Kv3Doc decode_kv3(const uint8_t* data, size_t size) {
    Kv3Doc doc;
    Cursor c{data, size, 0};
    const uint32_t magic = c.get<uint32_t>();
    const int version = static_cast<int>(magic & 0xFFu);
    if ((magic & 0xFFFFFF00u) != 0x4B563300u || version < 1 || version > 5)
        throw std::runtime_error("unsupported or non-binary KV3 magic");

    Context ctx;
    ctx.version = version;
    ctx.doc_blobs = &doc.blobs_storage;
    c.take(16); // format GUID

    const uint32_t compression = c.get<uint32_t>();
    if (compression > 2)
        throw std::runtime_error("unsupported KV3 compression method " +
                                 std::to_string(compression));

    uint16_t frame_size = 0;
    int32_t count_b1 = 0, count_b4 = 0, count_b8 = 0, count_types = 0;
    int32_t size_u_total = 0, size_c_total = 0, count_blocks = 0, size_blobs = 0, count_b2 = 0;

    if (version == 1) {
        count_b1 = c.get<int32_t>();
        count_b4 = c.get<int32_t>();
        count_b8 = c.get<int32_t>();
        size_u_total = c.get<int32_t>();
        size_c_total = static_cast<int32_t>(size - c.at);
    } else {
        if (c.get<uint16_t>() != 0)
            throw std::runtime_error("KV3 compression dictionaries unsupported");
        frame_size = c.get<uint16_t>();
        count_b1 = c.get<int32_t>();
        count_b4 = c.get<int32_t>();
        count_b8 = c.get<int32_t>();
        count_types = c.get<int32_t>();
        (void)c.get<uint16_t>(); // object count
        (void)c.get<uint16_t>(); // array count
        size_u_total = c.get<int32_t>();
        size_c_total = c.get<int32_t>();
        count_blocks = c.get<int32_t>();
        size_blobs = c.get<int32_t>();
    }
    if (version >= 4) {
        count_b2 = c.get<int32_t>();
        (void)c.get<int32_t>(); // block compressed sizes bytes
    }

    int32_t size_u1 = size_u_total, size_c1 = size_c_total, size_u2 = 0, size_c2 = 0;
    int32_t b2c1 = 0, b2c2 = 0, b2c4 = 0, b2c8 = 0, b2objects = 0;
    if (version >= 5) {
        size_u1 = c.get<int32_t>();
        size_c1 = c.get<int32_t>();
        size_u2 = c.get<int32_t>();
        size_c2 = c.get<int32_t>();
        b2c1 = c.get<int32_t>();
        b2c2 = c.get<int32_t>();
        b2c4 = c.get<int32_t>();
        b2c8 = c.get<int32_t>();
        (void)c.get<int32_t>();
        b2objects = c.get<int32_t>();
        (void)c.get<int32_t>(); // arrays
        (void)c.get<int32_t>();
    }

    const auto sane = [](int32_t v) { return v >= 0 && v <= 1024 * 1024 * 1024; };
    if (!sane(size_u1) || !sane(size_c1) || !sane(size_u2) || !sane(size_c2) || !sane(size_blobs) ||
        count_blocks < 0) {
        throw std::runtime_error("KV3 section sizes out of range");
    }

    // Buffer 1
    const bool zstd_with_blobs = (version < 5 && compression == 2);
    std::vector<uint8_t> buffer1(static_cast<size_t>(size_u1) +
                                 (zstd_with_blobs ? static_cast<size_t>(size_blobs) : 0));
    if (compression == 0) {
        std::memcpy(buffer1.data(), c.take(static_cast<size_t>(size_u1)),
                    static_cast<size_t>(size_u1));
    } else if (compression == 1) {
        lz4_exact(c.take(static_cast<size_t>(size_c1)), static_cast<size_t>(size_c1),
                  buffer1.data(), static_cast<size_t>(size_u1));
    } else {
        zstd_exact(c.take(static_cast<size_t>(size_c1)), static_cast<size_t>(size_c1),
                   buffer1.data(), buffer1.size());
    }

    Span s1{buffer1.data(), static_cast<size_t>(size_u1)};
    Buffers b1;
    size_t offset = 0;
    if (count_b1 > 0) {
        b1.b1 = s1.slice(offset, offset + static_cast<size_t>(count_b1));
        offset += static_cast<size_t>(count_b1);
    }
    if (count_b2 > 0) {
        align_up(offset, 2);
        b1.b2 = s1.slice(offset, offset + static_cast<size_t>(count_b2) * 2);
        offset += static_cast<size_t>(count_b2) * 2;
    }
    if (count_b4 > 0) {
        align_up(offset, 4);
        b1.b4 = s1.slice(offset, offset + static_cast<size_t>(count_b4) * 4);
        offset += static_cast<size_t>(count_b4) * 4;
    }
    if (count_b8 > 0) {
        align_up(offset, 8);
        b1.b8 = s1.slice(offset, offset + static_cast<size_t>(count_b8) * 8);
        offset += static_cast<size_t>(count_b8) * 8;
    } else if (version < 5) {
        align_up(offset, 8);
    }

    const int32_t count_strings = b1.b4.pop<int32_t>();
    if (count_strings < 0)
        throw std::runtime_error("negative KV3 string count");

    Span blob_sizes;
    if (version >= 5) {
        ctx.aux = b1;
        ctx.strings = read_strings(ctx.aux.b1, count_strings);
    } else {
        ctx.buffer = b1;
        ctx.aux = b1;
        Span strings = s1.slice(offset, s1.n);
        const size_t before = strings.n;
        ctx.strings = read_strings(strings, count_strings);
        const size_t strings_start = offset;
        offset += before - strings.n;
        const size_t types_len = (version == 1)
                                     ? (static_cast<size_t>(size_u_total) - offset - 4)
                                     : (static_cast<size_t>(count_types) - offset + strings_start);
        ctx.types = s1.slice(offset, offset + types_len);
        offset += types_len;
        if (count_blocks == 0) {
            Span trailer = s1.slice(offset, s1.n);
            if (trailer.pop<uint32_t>() != 0xFFEEDD00u)
                throw std::runtime_error("bad KV3 v1..4 trailer");
        } else {
            blob_sizes = s1.slice(offset, s1.n);
        }
    }

    // Buffer 2 (KV3 v5)
    std::vector<uint8_t> buffer2;
    if (version >= 5) {
        buffer2.resize(static_cast<size_t>(size_u2));
        if (compression == 0) {
            std::memcpy(buffer2.data(), c.take(static_cast<size_t>(size_u2)),
                        static_cast<size_t>(size_u2));
        } else if (compression == 1) {
            lz4_exact(c.take(static_cast<size_t>(size_c2)), static_cast<size_t>(size_c2),
                      buffer2.data(), static_cast<size_t>(size_u2));
        } else {
            zstd_exact(c.take(static_cast<size_t>(size_c2)), static_cast<size_t>(size_c2),
                       buffer2.data(), static_cast<size_t>(size_u2));
        }

        Span s2{buffer2.data(), buffer2.size()};
        size_t o = static_cast<size_t>(b2objects) * 4;
        ctx.object_lengths = s2.slice(0, o);
        Buffers b;
        if (b2c1 > 0) {
            b.b1 = s2.slice(o, o + static_cast<size_t>(b2c1));
            o += static_cast<size_t>(b2c1);
        }
        if (b2c2 > 0) {
            align_up(o, 2);
            b.b2 = s2.slice(o, o + static_cast<size_t>(b2c2) * 2);
            o += static_cast<size_t>(b2c2) * 2;
        }
        if (b2c4 > 0) {
            align_up(o, 4);
            b.b4 = s2.slice(o, o + static_cast<size_t>(b2c4) * 4);
            o += static_cast<size_t>(b2c4) * 4;
        }
        if (b2c8 > 0) {
            align_up(o, 8);
            b.b8 = s2.slice(o, o + static_cast<size_t>(b2c8) * 8);
            o += static_cast<size_t>(b2c8) * 8;
        }
        ctx.buffer = b;
        ctx.types = s2.slice(o, o + static_cast<size_t>(count_types));
        o += static_cast<size_t>(count_types);
        if (count_blocks == 0) {
            Span trailer = s2.slice(o, s2.n);
            if (trailer.pop<uint32_t>() != 0xFFEEDD00u)
                throw std::runtime_error("bad KV3 v5 trailer");
        } else {
            blob_sizes = s2.slice(o, s2.n);
        }
    }

    // Binary blobs
    if (count_blocks > 0) {
        ctx.blob_lengths = blob_sizes.slice(0, static_cast<size_t>(count_blocks) * 4);
        blob_sizes = blob_sizes.slice(static_cast<size_t>(count_blocks) * 4, blob_sizes.n);
        if (blob_sizes.pop<uint32_t>() != 0xFFEEDD00u)
            throw std::runtime_error("bad KV3 blob length trailer");

        doc.blobs_storage.resize(static_cast<size_t>(size_blobs));
        if (compression == 0) {
            std::memcpy(doc.blobs_storage.data(), c.take(static_cast<size_t>(size_blobs)),
                        static_cast<size_t>(size_blobs));
        } else if (compression == 1) {
            size_t written = 0;
            Span lengths = ctx.blob_lengths;
            while (lengths.n > 0) {
                const int32_t len = lengths.pop<int32_t>();
                if (len < 0 || static_cast<size_t>(len) > doc.blobs_storage.size() - written)
                    throw std::runtime_error("KV3 LZ4 blob out of range");
                const size_t blob_end = written + static_cast<size_t>(len);
                while (written < blob_end) {
                    const uint16_t comp = blob_sizes.pop<uint16_t>();
                    const size_t frame = std::min<size_t>(frame_size, blob_end - written);
                    size_t got = 0;
                    if (!detail::lz4_decompress_block(c.take(comp), comp,
                                                      doc.blobs_storage.data() + written, frame,
                                                      doc.blobs_storage.data(), written, got) ||
                        got == 0) {
                        throw std::runtime_error("KV3 LZ4 chained blob decompression failed");
                    }
                    written += got;
                }
            }
            if (written != doc.blobs_storage.size())
                throw std::runtime_error("KV3 LZ4 blob size mismatch");
        } else if (version >= 5) {
            const int32_t comp_blobs = size_c_total - size_c1 - size_c2;
            if (comp_blobs < 0)
                throw std::runtime_error("invalid KV3 v5 compressed blob size");
            zstd_exact(c.take(static_cast<size_t>(comp_blobs)), static_cast<size_t>(comp_blobs),
                       doc.blobs_storage.data(), doc.blobs_storage.size());
        } else {
            std::memcpy(doc.blobs_storage.data(), buffer1.data() + static_cast<size_t>(size_u1),
                        static_cast<size_t>(size_blobs));
        }
        ctx.blobs_base = doc.blobs_storage.data();
        ctx.blobs_size = doc.blobs_storage.size();
        ctx.blobs_cursor = 0;
        if (c.get<uint32_t>() != 0xFFEEDD00u)
            throw std::runtime_error("bad KV3 final blob trailer");
    }

    const auto [root_type, root_flag] = read_type(ctx);
    doc.root = read_value(ctx, root_type, root_flag);
    return doc;
}

} // namespace kv3_detail

// -----------------------------------------------------------------------------
// Source 2 Compiled Resource (`*_c`) Block Reader

struct ResourceBlock {
    uint32_t type = 0;
    uint32_t offset = 0;
    uint32_t size = 0;
};

class CompiledResource {
public:
    explicit CompiledResource(std::vector<uint8_t> bytes) : bytes_(std::move(bytes)) {
        Cursor c{bytes_.data(), bytes_.size(), 0};
        (void)c.get<uint32_t>(); // declared file size
        if (c.get<uint16_t>() != 12)
            throw std::runtime_error("not a Source 2 v12 compiled resource");
        (void)c.get<uint16_t>(); // version
        const size_t block_off_field = c.at;
        const uint32_t block_off = c.get<uint32_t>();
        const uint32_t block_count = c.get<uint32_t>();
        if (block_count > 256)
            throw std::runtime_error("too many resource blocks");
        c.at = block_off_field + block_off;
        for (uint32_t k = 0; k < block_count; ++k) {
            ResourceBlock b{};
            b.type = c.get<uint32_t>();
            const size_t field = c.at;
            b.offset = static_cast<uint32_t>(field + c.get<uint32_t>());
            b.size = c.get<uint32_t>();
            if (b.size > 0 && (b.offset > bytes_.size() || b.size > bytes_.size() - b.offset))
                throw std::runtime_error("resource block out of range");
            blocks_.push_back(b);
        }
    }

    const ResourceBlock* find_block(const char fourcc[4]) const noexcept {
        const uint32_t t = static_cast<uint32_t>(static_cast<uint8_t>(fourcc[0])) |
                           (static_cast<uint32_t>(static_cast<uint8_t>(fourcc[1])) << 8) |
                           (static_cast<uint32_t>(static_cast<uint8_t>(fourcc[2])) << 16) |
                           (static_cast<uint32_t>(static_cast<uint8_t>(fourcc[3])) << 24);
        for (const auto& b : blocks_) {
            if (b.type == t && b.size > 0)
                return &b;
        }
        return nullptr;
    }

    const std::vector<ResourceBlock>& blocks() const noexcept { return blocks_; }

    Kv3Doc decode_kv3_block(const ResourceBlock& b) const {
        const uint8_t* ptr = bytes_.data() + b.offset;
        if (!kv3_detail::is_kv3_magic(ptr, b.size))
            throw std::runtime_error("resource block is not binary KV3");
        return kv3_detail::decode_kv3(ptr, b.size);
    }

private:
    std::vector<uint8_t> bytes_;
    std::vector<ResourceBlock> blocks_;
};

// -----------------------------------------------------------------------------
// Source 2 Nav Mesh Walkable Polygon Extractor (versions 35 and 36)
// Adapted from SourceMVM EnvironmentGroundFogMath.h (MIT License).

bool parse_nav_mesh(const uint8_t* data, size_t size, std::vector<Vec3>& out_verts,
                    std::vector<uint32_t>& out_tris) {
    out_verts.clear();
    out_tris.clear();
    if (!data || size < 32 || detail::read_u32_le(data) != 0xFEEDFACEu)
        return false;

    const size_t scan_end = std::min<size_t>(8192, size - 8);
    for (size_t table = 12; table < scan_end; ++table) {
        const uint32_t vcount = detail::read_u32_le(data + table);
        if (vcount < 3 || vcount > 4000000u)
            continue;
        const size_t verts_off = table + 4;
        const size_t polys_off = verts_off + static_cast<size_t>(vcount) * 12;
        if (polys_off + 4 > size)
            continue;
        const uint32_t pcount = detail::read_u32_le(data + polys_off);
        if (pcount < 1 || pcount > vcount * 4u)
            continue;

        bool good = true;
        const uint32_t probe_v = std::min<uint32_t>(vcount, 64u);
        for (uint32_t i = 0; i < probe_v; ++i) {
            Vec3 p{};
            std::memcpy(p.data(), data + verts_off + static_cast<size_t>(i) * 12, 12);
            if (!finite_vec3(p) || std::abs(p[0]) >= 65536.0f || std::abs(p[1]) >= 65536.0f ||
                std::abs(p[2]) >= 65536.0f) {
                good = false;
                break;
            }
        }
        if (!good)
            continue;

        std::vector<Vec3> verts(vcount);
        std::memcpy(verts.data(), data + verts_off, static_cast<size_t>(vcount) * 12);
        for (const auto& p : verts) {
            if (!finite_vec3(p) || std::abs(p[0]) >= 65536.0f || std::abs(p[1]) >= 65536.0f ||
                std::abs(p[2]) >= 65536.0f) {
                good = false;
                break;
            }
        }
        if (!good)
            continue;

        std::vector<uint32_t> tris;
        tris.reserve(static_cast<size_t>(pcount) * 6);
        size_t at = polys_off + 4;
        for (uint32_t i = 0; i < pcount && good; ++i) {
            if (at >= size) {
                good = false;
                break;
            }
            const uint32_t sides = data[at++];
            if (sides < 3 || sides > 64 || at + static_cast<size_t>(sides) * 4 + 4 > size) {
                good = false;
                break;
            }
            const uint32_t first = detail::read_u32_le(data + at);
            for (uint32_t k = 0; k < sides; ++k) {
                if (detail::read_u32_le(data + at + static_cast<size_t>(k) * 4) >= vcount) {
                    good = false;
                    break;
                }
            }
            if (!good)
                break;
            for (uint32_t k = 2; k < sides; ++k) {
                tris.push_back(first);
                tris.push_back(detail::read_u32_le(data + at + static_cast<size_t>(k - 1) * 4));
                tris.push_back(detail::read_u32_le(data + at + static_cast<size_t>(k) * 4));
            }
            at += static_cast<size_t>(sides) * 4 + 4;
        }
        if (!good)
            continue;

        out_verts = std::move(verts);
        out_tris = std::move(tris);
        return true;
    }
    return false;
}

// -----------------------------------------------------------------------------
// Solid Collision Attribute Filter & Streaming Rubikon Shape Walker

struct Pose {
    float m[12] = {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0};
    bool identity = true;

    Vec3 apply(const Vec3& p) const noexcept {
        if (identity)
            return p;
        return {p[0] * m[0] + p[1] * m[1] + p[2] * m[2] + m[3],
                p[0] * m[4] + p[1] * m[5] + p[2] * m[6] + m[7],
                p[0] * m[8] + p[1] * m[9] + p[2] * m[10] + m[11]};
    }
};

struct HullEdge {
    uint8_t next;
    uint8_t twin;
    uint8_t origin;
    uint8_t face;
};

bool is_excluded_tag(const std::string& lower_tag) {
    static constexpr const char* kExcludedSubstrings[] = {
        "clip", "sky", "window", "foliage", "passbullets", "ladder", "trigger", "water", "glass",
    };
    for (const char* sub : kExcludedSubstrings) {
        if (lower_tag.find(sub) != std::string::npos)
            return true;
    }
    return false;
}

std::vector<uint8_t> build_solid_ca_table(const Kv3& phys_root,
                                          std::vector<uint32_t>& included_indices) {
    included_indices.clear();
    const auto& attrs = phys_root["m_collisionAttributes"].arr;
    std::vector<uint8_t> solid(attrs.size(), 0);

    for (size_t ca = 0; ca < attrs.size(); ++ca) {
        const Kv3& a = attrs[ca];
        const std::string group = to_lower(a["m_CollisionGroupString"].str());
        const Kv3* tags = a.find("m_InteractAsStrings");
        if (!tags)
            tags = a.find("m_PhysicsTagStrings");

        bool excluded = false;
        bool has_solid_tag = false;
        bool has_any_tag = false;
        if (is_excluded_tag(group))
            excluded = true;
        if (tags && tags->type == Kv3::Type::Array) {
            for (const Kv3& t : tags->arr) {
                const std::string lt = to_lower(t.str());
                if (lt.empty())
                    continue;
                has_any_tag = true;
                if (is_excluded_tag(lt))
                    excluded = true;
                if (lt == "solid")
                    has_solid_tag = true;
            }
        }

        // Include plain Default (no specialized non-solid tags) or any non-excluded
        // attribute explicitly tagged "solid" (e.g. ConditionallySolid world walls).
        const bool is_plain_default = (group == "default") && !has_any_tag;
        if (!excluded && (is_plain_default || has_solid_tag)) {
            solid[ca] = 1;
            included_indices.push_back(static_cast<uint32_t>(ca));
        }
    }
    return solid;
}

struct WalkCounters {
    uint32_t compounds_seen = 0;
    uint32_t solid_hulls = 0;
    uint32_t solid_meshes = 0;
    uint32_t skipped_shapes = 0;
    uint64_t vertices = 0;
    uint64_t triangles = 0;
};

template <class TriFn>
void emit_hull(const Kv3Doc& doc, const Kv3& hull, const Pose& pose, bool is_solid,
               WalkCounters* counters, TriFn&& on_tri) {
    // Vertices: prefer m_VertexPositions (post-2023 Rubikon hull format), else m_Vertices
    const Kv3* vp_node = hull.find("m_VertexPositions");
    if (!vp_node)
        vp_node = hull.find("m_Vertices");
    const Kv3* ed_node = hull.find("m_Edges");
    const Kv3* fc_node = hull.find("m_Faces");
    if (!vp_node || !ed_node || !fc_node)
        return;

    if (!is_solid) {
        if (counters)
            counters->skipped_shapes++;
        return;
    }

    std::vector<Vec3> verts_fallback;
    const Vec3* vptr = nullptr;
    size_t vcount = 0;
    if (vp_node->type == Kv3::Type::Blob) {
        const uint8_t* bp = doc.blob_ptr(*vp_node);
        if (!bp || vp_node->blob_length < 36)
            return;
        vcount = vp_node->blob_length / 12;
        if (pose.identity) {
            vptr = reinterpret_cast<const Vec3*>(bp);
        } else {
            verts_fallback.resize(vcount);
            std::memcpy(verts_fallback.data(), bp, vcount * 12);
            for (Vec3& v : verts_fallback)
                v = pose.apply(v);
            vptr = verts_fallback.data();
        }
    } else if (vp_node->type == Kv3::Type::Array) {
        vcount = vp_node->arr.size();
        if (vcount < 3)
            return;
        verts_fallback.resize(vcount);
        for (size_t i = 0; i < vcount; ++i) {
            const Kv3& e = vp_node->arr[i];
            verts_fallback[i] = pose.apply({static_cast<float>(e.at(0).number()),
                                            static_cast<float>(e.at(1).number()),
                                            static_cast<float>(e.at(2).number())});
        }
        vptr = verts_fallback.data();
    } else {
        return;
    }

    std::vector<HullEdge> edges_fallback;
    const HullEdge* eptr = nullptr;
    size_t ecount = 0;
    if (ed_node->type == Kv3::Type::Blob) {
        const uint8_t* bp = doc.blob_ptr(*ed_node);
        if (!bp || ed_node->blob_length < 12)
            return;
        eptr = reinterpret_cast<const HullEdge*>(bp);
        ecount = ed_node->blob_length / 4;
    } else if (ed_node->type == Kv3::Type::Array) {
        ecount = ed_node->arr.size();
        edges_fallback.reserve(ecount);
        for (const Kv3& e : ed_node->arr) {
            edges_fallback.push_back({static_cast<uint8_t>(e["m_nNext"].integer()),
                                      static_cast<uint8_t>(e["m_nTwin"].integer()),
                                      static_cast<uint8_t>(e["m_nOrigin"].integer()),
                                      static_cast<uint8_t>(e["m_nFace"].integer())});
        }
        eptr = edges_fallback.data();
    } else {
        return;
    }

    std::vector<uint8_t> faces_fallback;
    const uint8_t* fptr = nullptr;
    size_t fcount = 0;
    if (fc_node->type == Kv3::Type::Blob) {
        fptr = doc.blob_ptr(*fc_node);
        fcount = fc_node->blob_length;
        if (!fptr || fcount < 4)
            return;
    } else if (fc_node->type == Kv3::Type::Array) {
        fcount = fc_node->arr.size();
        faces_fallback.reserve(fcount);
        for (const Kv3& f : fc_node->arr)
            faces_fallback.push_back(static_cast<uint8_t>(f["m_nEdge"].integer()));
        fptr = faces_fallback.data();
    } else {
        return;
    }

    uint64_t emitted_tris = 0;
    for (size_t fi = 0; fi < fcount; ++fi) {
        const uint8_t start = fptr[fi];
        if (start >= ecount)
            continue;
        const uint8_t o0 = eptr[start].origin;
        if (o0 >= vcount)
            continue;
        uint8_t edge = eptr[start].next;
        int steps = 0;
        while (edge != start && steps < 64) {
            if (edge >= ecount)
                break;
            const uint8_t nxt = eptr[edge].next;
            if (nxt == start || nxt >= ecount)
                break;
            const uint8_t o1 = eptr[edge].origin;
            const uint8_t o2 = eptr[nxt].origin;
            if (o1 < vcount && o2 < vcount) {
                on_tri(vptr[o0], vptr[o1], vptr[o2]);
                ++emitted_tris;
            }
            edge = nxt;
            ++steps;
        }
    }

    if (emitted_tris > 0 && counters) {
        counters->solid_hulls++;
        counters->vertices += vcount;
        counters->triangles += emitted_tris;
    }
}

template <class TriFn>
void emit_mesh(const Kv3Doc& doc, const Kv3& mesh, const Pose& pose, bool is_solid,
               WalkCounters* counters, TriFn&& on_tri) {
    const Kv3* vb_node = mesh.find("m_Vertices");
    const Kv3* tb_node = mesh.find("m_Triangles");
    if (!vb_node || !tb_node)
        return;

    if (!is_solid) {
        if (counters)
            counters->skipped_shapes++;
        return;
    }

    std::vector<Vec3> verts_fallback;
    const Vec3* vptr = nullptr;
    size_t vcount = 0;
    if (vb_node->type == Kv3::Type::Blob) {
        const uint8_t* bp = doc.blob_ptr(*vb_node);
        if (!bp || vb_node->blob_length < 36)
            return;
        vcount = vb_node->blob_length / 12;
        if (pose.identity) {
            vptr = reinterpret_cast<const Vec3*>(bp);
        } else {
            verts_fallback.resize(vcount);
            std::memcpy(verts_fallback.data(), bp, vcount * 12);
            for (Vec3& v : verts_fallback)
                v = pose.apply(v);
            vptr = verts_fallback.data();
        }
    } else if (vb_node->type == Kv3::Type::Array) {
        vcount = vb_node->arr.size();
        if (vcount < 3)
            return;
        verts_fallback.resize(vcount);
        for (size_t i = 0; i < vcount; ++i) {
            const Kv3& e = vb_node->arr[i];
            verts_fallback[i] = pose.apply({static_cast<float>(e.at(0).number()),
                                            static_cast<float>(e.at(1).number()),
                                            static_cast<float>(e.at(2).number())});
        }
        vptr = verts_fallback.data();
    } else {
        return;
    }

    std::vector<std::array<int32_t, 3>> tris_fallback;
    const std::array<int32_t, 3>* tptr = nullptr;
    size_t tcount = 0;
    if (tb_node->type == Kv3::Type::Blob) {
        const uint8_t* bp = doc.blob_ptr(*tb_node);
        if (!bp || tb_node->blob_length < 12)
            return;
        tptr = reinterpret_cast<const std::array<int32_t, 3>*>(bp);
        tcount = tb_node->blob_length / 12;
    } else if (tb_node->type == Kv3::Type::Array) {
        tcount = tb_node->arr.size();
        tris_fallback.reserve(tcount);
        for (const Kv3& t : tb_node->arr) {
            const Kv3& idx = t["m_nIndex"];
            tris_fallback.push_back({static_cast<int32_t>(idx.at(0).integer()),
                                     static_cast<int32_t>(idx.at(1).integer()),
                                     static_cast<int32_t>(idx.at(2).integer())});
        }
        tptr = tris_fallback.data();
    } else {
        return;
    }

    uint64_t emitted_tris = 0;
    for (size_t i = 0; i < tcount; ++i) {
        const int32_t i0 = tptr[i][0], i1 = tptr[i][1], i2 = tptr[i][2];
        if (i0 < 0 || i1 < 0 || i2 < 0 || static_cast<size_t>(i0) >= vcount ||
            static_cast<size_t>(i1) >= vcount || static_cast<size_t>(i2) >= vcount) {
            continue;
        }
        on_tri(vptr[i0], vptr[i1], vptr[i2]);
        ++emitted_tris;
    }

    if (emitted_tris > 0 && counters) {
        counters->solid_meshes++;
        counters->vertices += vcount;
        counters->triangles += emitted_tris;
    }
}

template <class TriFn>
void stream_solid_physics(const Kv3Doc& doc, const std::vector<uint8_t>& solid_ca,
                          WalkCounters* counters, TriFn&& on_tri) {
    const Kv3& phys = doc.root;

    std::vector<Pose> bind_poses;
    for (const Kv3& m : phys["m_bindPose"].arr) {
        Pose p;
        p.identity = true;
        static constexpr float kIdent[12] = {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0};
        for (size_t k = 0; k < 12; ++k) {
            p.m[k] = static_cast<float>(m.at(k).number(kIdent[k]));
            if (std::abs(p.m[k] - kIdent[k]) > 1e-6f)
                p.identity = false;
        }
        bind_poses.push_back(p);
    }

    const auto ca_is_solid = [&](int64_t ca) -> bool {
        return ca >= 0 && static_cast<size_t>(ca) < solid_ca.size() &&
               solid_ca[static_cast<size_t>(ca)] != 0;
    };

    const auto& parts = phys["m_parts"].arr;
    for (size_t pi = 0; pi < parts.size(); ++pi) {
        const Kv3& shape = parts[pi]["m_rnShape"];
        if (shape.is_null())
            continue;
        const Pose pose = (pi < bind_poses.size()) ? bind_poses[pi] : Pose{};

        // 1) Top-level hulls
        for (const Kv3& d : shape["m_hulls"].arr) {
            const int64_t ca = d["m_nCollisionAttributeIndex"].integer(-1);
            const Kv3* h = d.find("m_Hull");
            if (h)
                emit_hull(doc, *h, pose, ca_is_solid(ca), counters, on_tri);
        }

        // 2) Top-level meshes
        for (const Kv3& d : shape["m_meshes"].arr) {
            const int64_t ca = d["m_nCollisionAttributeIndex"].integer(-1);
            const Kv3* m = d.find("m_Mesh");
            if (m)
                emit_mesh(doc, *m, pose, ca_is_solid(ca), counters, on_tri);
        }

        // 3) Deadlock m_compounds (RnCompoundDesc_t) — 99.8% of dl_midtown geometry!
        const Kv3* comps = shape.find("m_compounds");
        if (!comps)
            comps = shape.find("m_Compounds");
        if (comps && comps->type == Kv3::Type::Array) {
            for (const Kv3& cd : comps->arr) {
                if (counters)
                    counters->compounds_seen++;
                const int64_t comp_ca = cd["m_nCollisionAttributeIndex"].integer(-1);
                const Kv3* comp = cd.find("m_Compound");
                if (!comp)
                    comp = &cd;

                const Kv3* chulls = comp->find("m_Hulls");
                if (!chulls)
                    chulls = comp->find("m_hulls");
                if (chulls && chulls->type == Kv3::Type::Array) {
                    for (const Kv3& hd : chulls->arr) {
                        const Kv3* inner = hd.find("m_Hull");
                        const Kv3* ca_node = hd.find("m_nCollisionAttributeIndex");
                        const int64_t ca = ca_node ? ca_node->integer(comp_ca) : comp_ca;
                        emit_hull(doc, inner ? *inner : hd, pose, ca_is_solid(ca), counters,
                                  on_tri);
                    }
                }

                const Kv3* cmeshes = comp->find("m_Meshes");
                if (!cmeshes)
                    cmeshes = comp->find("m_meshes");
                if (cmeshes && cmeshes->type == Kv3::Type::Array) {
                    for (const Kv3& md : cmeshes->arr) {
                        const Kv3* inner = md.find("m_Mesh");
                        const Kv3* ca_node = md.find("m_nCollisionAttributeIndex");
                        const int64_t ca = ca_node ? ca_node->integer(comp_ca) : comp_ca;
                        emit_mesh(doc, inner ? *inner : md, pose, ca_is_solid(ca), counters,
                                  on_tri);
                    }
                }
            }
        }
    }
}

// -----------------------------------------------------------------------------
// Compact Per-Cell Multi-Floor Tracker (keeps highest 3 + lowest 1 after merge)

struct ColumnFloors {
    static constexpr int kCap = 8;
    float z[kCap] = {kNoFloorZ, kNoFloorZ, kNoFloorZ, kNoFloorZ,
                     kNoFloorZ, kNoFloorZ, kNoFloorZ, kNoFloorZ};
    uint8_t count = 0;

    void insert(float h, float same_floor = 24.0f) noexcept {
        // If within same_floor of an existing entry, keep the higher top surface.
        for (int i = 0; i < count; ++i) {
            if (std::abs(z[i] - h) < same_floor) {
                if (h > z[i])
                    z[i] = h;
                return;
            }
        }
        if (count < kCap) {
            z[count++] = h;
            return;
        }
        // Evict one middle entry while preserving highest and lowest extremes.
        std::sort(z, z + count, [](float a, float b) { return a > b; });
        if (h > z[kCap - 2] || h < z[kCap - 1]) {
            z[kCap - 2] = h;
        }
    }

    std::array<float, kFloorLayers> finalize(float same_floor = 24.0f) const noexcept {
        std::array<float, kFloorLayers> out;
        out.fill(kNoFloorZ);
        if (count == 0)
            return out;
        float tmp[kCap];
        std::memcpy(tmp, z, sizeof(float) * count);
        std::sort(tmp, tmp + count, [](float a, float b) { return a > b; });

        float merged[kCap];
        int m = 0;
        float last = tmp[0];
        merged[m++] = last;
        for (int i = 1; i < count; ++i) {
            if (last - tmp[i] >= same_floor)
                merged[m++] = tmp[i];
            last = tmp[i];
        }
        if (m <= kFloorLayers) {
            for (int i = 0; i < m; ++i)
                out[i] = merged[i];
        } else {
            for (int i = 0; i < kFloorLayers - 1; ++i)
                out[i] = merged[i];
            out[kFloorLayers - 1] = merged[m - 1];
        }
        return out;
    }
};

float percentile_value(std::vector<float>& values, double p) {
    if (values.empty())
        return 0.0f;
    const double idx_f = std::clamp(p, 0.0, 1.0) * static_cast<double>(values.size() - 1);
    const size_t idx = static_cast<size_t>(idx_f);
    std::nth_element(values.begin(), values.begin() + static_cast<std::ptrdiff_t>(idx),
                     values.end());
    return values[idx];
}

// Rasterizes a single triangle's upward/sloped XY coverage analytically.
template <class CellFn>
void rasterize_triangle_xy(const Vec3& a, const Vec3& b, const Vec3& c, float origin_x,
                           float origin_y, float cell, uint32_t grid_n, CellFn&& on_cell) noexcept {
    const float inv_cell = 1.0f / cell;
    const float min_x = std::min({a[0], b[0], c[0]});
    const float max_x = std::max({a[0], b[0], c[0]});
    const float min_y = std::min({a[1], b[1], c[1]});
    const float max_y = std::max({a[1], b[1], c[1]});

    const int i0 = std::max(0, static_cast<int>(std::floor((min_x - origin_x) * inv_cell)));
    const int i1 = std::min(static_cast<int>(grid_n) - 1,
                            static_cast<int>(std::floor((max_x - origin_x) * inv_cell)));
    const int j0 = std::max(0, static_cast<int>(std::floor((min_y - origin_y) * inv_cell)));
    const int j1 = std::min(static_cast<int>(grid_n) - 1,
                            static_cast<int>(std::floor((max_y - origin_y) * inv_cell)));
    if (i0 > i1 || j0 > j1)
        return;

    const float area2 = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
    bool hit_any = false;
    if (std::abs(area2) > 1e-3f) {
        const float inv_area2 = 1.0f / area2;
        for (int j = j0; j <= j1; ++j) {
            const float py = origin_y + (static_cast<float>(j) + 0.5f) * cell;
            for (int i = i0; i <= i1; ++i) {
                const float px = origin_x + (static_cast<float>(i) + 0.5f) * cell;
                const float wa =
                    ((c[0] - b[0]) * (py - b[1]) - (c[1] - b[1]) * (px - b[0])) * inv_area2;
                const float wb =
                    ((a[0] - c[0]) * (py - c[1]) - (a[1] - c[1]) * (px - c[0])) * inv_area2;
                const float wc = 1.0f - wa - wb;
                if (wa >= -1e-4f && wb >= -1e-4f && wc >= -1e-4f) {
                    const float z = wa * a[2] + wb * b[2] + wc * c[2];
                    on_cell(static_cast<uint32_t>(i), static_cast<uint32_t>(j), z);
                    hit_any = true;
                }
            }
        }
    }

    if (!hit_any) {
        const float cx = (a[0] + b[0] + c[0]) * (1.0f / 3.0f);
        const float cy = (a[1] + b[1] + c[1]) * (1.0f / 3.0f);
        const float cz = (a[2] + b[2] + c[2]) * (1.0f / 3.0f);
        const int gx = static_cast<int>(std::floor((cx - origin_x) * inv_cell));
        const int gy = static_cast<int>(std::floor((cy - origin_y) * inv_cell));
        if (gx >= 0 && gx < static_cast<int>(grid_n) && gy >= 0 && gy < static_cast<int>(grid_n))
            on_cell(static_cast<uint32_t>(gx), static_cast<uint32_t>(gy), cz);
    }
}

// Rasterizes a vertical wall triangle (|nz| < 0.55) by sampling along its XY edges/interior
// so thin vertical slabs never slip between cell centers.
template <class CellFn>
void rasterize_wall_triangle(const Vec3& a, const Vec3& b, const Vec3& c, float origin_x,
                             float origin_y, float cell, uint32_t grid_n,
                             CellFn&& on_cell) noexcept {
    const float inv_cell = 1.0f / cell;
    const auto emit_pt = [&](float x, float y, float z) {
        const int gx = static_cast<int>(std::floor((x - origin_x) * inv_cell));
        const int gy = static_cast<int>(std::floor((y - origin_y) * inv_cell));
        if (gx >= 0 && gx < static_cast<int>(grid_n) && gy >= 0 && gy < static_cast<int>(grid_n))
            on_cell(static_cast<uint32_t>(gx), static_cast<uint32_t>(gy), z);
    };

    const auto edge_len = [](const Vec3& p, const Vec3& q) {
        const float dx = p[0] - q[0], dy = p[1] - q[1], dz = p[2] - q[2];
        return std::sqrt(dx * dx + dy * dy + dz * dz);
    };
    const float max_e = std::max({edge_len(a, b), edge_len(b, c), edge_len(c, a)});

    int subdiv = 1;
    if (max_e >= cell * 18.0f)
        subdiv = 20;
    else if (max_e >= cell * 8.0f)
        subdiv = 12;
    else if (max_e >= cell * 2.5f)
        subdiv = 6;
    else if (max_e >= cell * 0.8f)
        subdiv = 3;

    if (subdiv == 1) {
        emit_pt(a[0], a[1], a[2]);
        emit_pt(b[0], b[1], b[2]);
        emit_pt(c[0], c[1], c[2]);
        emit_pt((a[0] + b[0] + c[0]) / 3.0f, (a[1] + b[1] + c[1]) / 3.0f,
                (a[2] + b[2] + c[2]) / 3.0f);
        return;
    }

    const float inv_s = 1.0f / static_cast<float>(subdiv);
    for (int i = 0; i <= subdiv; ++i) {
        const float u = static_cast<float>(i) * inv_s;
        for (int j = 0; j <= subdiv - i; ++j) {
            const float v = static_cast<float>(j) * inv_s;
            const float w = 1.0f - u - v;
            emit_pt(a[0] * u + b[0] * v + c[0] * w, a[1] * u + b[1] * v + c[1] * w,
                    a[2] * u + b[2] * v + c[2] * w);
        }
    }
}

void refine_floors_with_nav(std::vector<std::array<float, kFloorLayers>>& floors,
                            const std::vector<std::array<float, kFloorLayers>>& nav_floors,
                            uint32_t width, uint32_t height, float cell) {
    constexpr float kNavReach = 48.0f;
    constexpr float kNavHeight = 24.0f;
    const auto is_floor = [](float h) { return h > kNoFloorZ * 0.5f; };

    struct Offset {
        int dx, dy;
        float dist;
    };
    std::vector<Offset> offsets;
    const int reach = std::max(0, static_cast<int>(std::ceil(kNavReach / cell)));
    for (int dy = -reach; dy <= reach; ++dy) {
        for (int dx = -reach; dx <= reach; ++dx) {
            const float d = std::sqrt(static_cast<float>(dx * dx + dy * dy)) * cell;
            if (d <= kNavReach)
                offsets.push_back({dx, dy, d});
        }
    }
    std::sort(offsets.begin(), offsets.end(),
              [](const Offset& a, const Offset& b) { return a.dist < b.dist; });

    std::vector<std::array<float, kFloorLayers>> out(floors.size());
    for (uint32_t y = 0; y < height; ++y) {
        for (uint32_t x = 0; x < width; ++x) {
            const size_t idx = static_cast<size_t>(y) * width + x;
            ColumnFloors col;
            for (float h : floors[idx]) {
                if (!is_floor(h))
                    continue;
                bool walked = false;
                for (const auto& o : offsets) {
                    const int nx = static_cast<int>(x) + o.dx;
                    const int ny = static_cast<int>(y) + o.dy;
                    if (nx < 0 || ny < 0 || nx >= static_cast<int>(width) ||
                        ny >= static_cast<int>(height))
                        continue;
                    for (float g : nav_floors[static_cast<size_t>(ny) * width + nx]) {
                        if (is_floor(g) && std::abs(g - h) <= kNavHeight + o.dist * 0.25f) {
                            walked = true;
                            break;
                        }
                    }
                    if (walked)
                        break;
                }
                if (walked)
                    col.insert(h, 24.0f);
            }
            for (float g : nav_floors[idx]) {
                if (!is_floor(g))
                    continue;
                bool covered = false;
                for (int k = 0; k < col.count; ++k) {
                    if (std::abs(col.z[k] - g) <= kNavHeight) {
                        covered = true;
                        break;
                    }
                }
                if (!covered)
                    col.insert(g, 24.0f);
            }
            out[idx] = col.finalize(24.0f);
        }
    }
    floors.swap(out);
}

// -----------------------------------------------------------------------------
// Compact `.dmapgrid` Cache Serialization

constexpr char kCacheMagic[8] = {'D', 'M', 'A', 'P', 'G', 'R', 'D', '1'};
// Bump whenever bake output changes so stale caches are rebaked instead of reused.
constexpr uint32_t kCacheVersion = 2;

#pragma pack(push, 1)
struct CacheHeader {
    char magic[8];
    uint32_t version;
    uint32_t flags; // bit 0: 1 = float32 heights, 0 = int16 0.5u fixed-point heights
    uint64_t source_fingerprint;
    uint32_t width;
    uint32_t height;
    float origin_x;
    float origin_y;
    float span_units;
    float cell_units;
    float world_z_p05;
    float world_z_p95;
    float street_z_median;
    uint32_t reserved;
};
#pragma pack(pop)

inline int16_t pack_height_i16(float z) noexcept {
    if (z <= kNoFloorZ * 0.5f)
        return std::numeric_limits<int16_t>::min();
    const float q = std::clamp(std::round(z * 2.0f), -32767.0f, 32767.0f);
    return static_cast<int16_t>(q);
}

inline float unpack_height_i16(int16_t q) noexcept {
    if (q == std::numeric_limits<int16_t>::min())
        return kNoFloorZ;
    return static_cast<float>(q) * 0.5f;
}

// Heights within +-16000u are cached as 0.5u fixed point; larger maps keep floats.
bool cache_uses_f32(const MapGrid& grid) noexcept {
    const size_t n = static_cast<size_t>(grid.width) * static_cast<size_t>(grid.height);
    for (size_t i = 0; i < n; ++i) {
        if (std::abs(grid.tops[i]) > 16000.0f || std::abs(grid.street_z[i]) > 16000.0f)
            return true;
        for (float h : grid.floor_z[i])
            if (h > kNoFloorZ * 0.5f && std::abs(h) > 16000.0f)
                return true;
    }
    return false;
}

// Give a freshly baked grid exactly the heights a later cache load returns, so
// the first session with a map renders the same as every following one.
void quantize_like_cache(MapGrid& grid) noexcept {
    if (!grid.valid() || cache_uses_f32(grid))
        return;
    const auto round_trip = [](float& h) { h = unpack_height_i16(pack_height_i16(h)); };
    for (float& h : grid.tops)
        round_trip(h);
    for (float& h : grid.street_z)
        round_trip(h);
    for (auto& layers : grid.floor_z)
        for (float& h : layers)
            round_trip(h);
}

} // namespace

uint64_t compute_vpk_fingerprint(const std::filesystem::path& vpk_path) {
    std::ifstream in(vpk_path, std::ios::binary | std::ios::ate);
    if (!in)
        return 0;
    const uint64_t file_size = static_cast<uint64_t>(in.tellg());
    in.seekg(0);
    std::vector<uint8_t> buf(static_cast<size_t>(std::min<uint64_t>(file_size, 131072u)));
    in.read(reinterpret_cast<char*>(buf.data()), static_cast<std::streamsize>(buf.size()));
    uint64_t h = fnv1a_64(&file_size, sizeof(file_size));
    h = fnv1a_64(buf.data(), static_cast<size_t>(in.gcount()), h);
    return h;
}

bool bake_map_grid_from_vpk(const std::filesystem::path& vpk_path, const BakeOptions& opts,
                            MapGrid& out_grid, BakeStats* out_stats, std::string* out_error) {
    try {
        const auto t0 = std::chrono::steady_clock::now();
        const uint32_t grid_n = std::clamp(opts.grid_n, 64u, 2048u);

        VpkReader vpk(vpk_path);
        const std::string map_name = to_lower(vpk_path.stem().string());

        // Locate world physics resource (prefer world_physics.vmdl_c PHYS block, fallback vphys_c)
        std::vector<uint8_t> phys_res_bytes;
        std::string phys_path = "maps/" + map_name + "/world_physics.vmdl_c";
        bool is_vphys = false;
        if (!vpk.read(phys_path, phys_res_bytes)) {
            phys_path = vpk.find_first_matching("/world_physics.vmdl_c");
            if (phys_path.empty() || !vpk.read(phys_path, phys_res_bytes)) {
                phys_path = "maps/" + map_name + "/world_physics.vphys_c";
                if (!vpk.read(phys_path, phys_res_bytes)) {
                    phys_path = vpk.find_first_matching("/world_physics.vphys_c");
                    if (phys_path.empty() || !vpk.read(phys_path, phys_res_bytes))
                        throw std::runtime_error("no world_physics resource found in VPK");
                }
                is_vphys = true;
            }
        }

        CompiledResource res(std::move(phys_res_bytes));
        Kv3Doc phys_doc;
        if (is_vphys) {
            const ResourceBlock* data_blk = res.find_block("DATA");
            if (!data_blk)
                throw std::runtime_error("vphys_c has no DATA block");
            phys_doc = res.decode_kv3_block(*data_blk);
        } else {
            const ResourceBlock* phys_blk = res.find_block("PHYS");
            if (!phys_blk) {
                const ResourceBlock* ctrl_blk = res.find_block("CTRL");
                if (ctrl_blk) {
                    const Kv3Doc ctrl = res.decode_kv3_block(*ctrl_blk);
                    const int64_t idx =
                        ctrl.root["embedded_physics"]["phys_data_block"].integer(-1);
                    if (idx >= 0 && static_cast<size_t>(idx) < res.blocks().size())
                        phys_blk = &res.blocks()[static_cast<size_t>(idx)];
                }
            }
            if (!phys_blk)
                throw std::runtime_error("world_physics.vmdl_c has no PHYS block");
            phys_doc = res.decode_kv3_block(*phys_blk);
        }

        // Optional nav mesh from maps/<map>.nav
        std::vector<Vec3> nav_verts;
        std::vector<uint32_t> nav_tris;
        if (opts.use_nav_refinement) {
            std::vector<uint8_t> nav_bytes;
            std::string nav_path = "maps/" + map_name + ".nav";
            if (!vpk.read(nav_path, nav_bytes)) {
                nav_path = vpk.find_first_matching(".nav");
                if (!nav_path.empty())
                    vpk.read(nav_path, nav_bytes);
            }
            if (!nav_bytes.empty())
                parse_nav_mesh(nav_bytes.data(), nav_bytes.size(), nav_verts, nav_tris);
        }

        std::vector<uint32_t> included_ca;
        const std::vector<uint8_t> solid_ca = build_solid_ca_table(phys_doc.root, included_ca);
        if (included_ca.empty())
            throw std::runtime_error("no solid collision attributes found in world_physics");

        const auto t1 = std::chrono::steady_clock::now();

        // Pass 1: Stream all solid shapes to compute raw bounds, exact shape/vert/tri counts,
        // and subsampled centroids for playable core percentile bounds.
        WalkCounters counters;
        Vec3 raw_min = {1e30f, 1e30f, 1e30f};
        Vec3 raw_max = {-1e30f, -1e30f, -1e30f};
        std::vector<float> sample_cx, sample_cy, sample_up_z;
        sample_cx.reserve(256000);
        sample_cy.reserve(256000);
        sample_up_z.reserve(128000);
        uint64_t valid_tri_idx = 0;

        stream_solid_physics(phys_doc, solid_ca, &counters,
                             [&](const Vec3& a, const Vec3& b, const Vec3& c) {
                                 for (const Vec3& v : {a, b, c}) {
                                     raw_min[0] = std::min(raw_min[0], v[0]);
                                     raw_min[1] = std::min(raw_min[1], v[1]);
                                     raw_min[2] = std::min(raw_min[2], v[2]);
                                     raw_max[0] = std::max(raw_max[0], v[0]);
                                     raw_max[1] = std::max(raw_max[1], v[1]);
                                     raw_max[2] = std::max(raw_max[2], v[2]);
                                 }
                                 const Vec3 cr = cross(b - a, c - a);
                                 const float area2_sq = dot(cr, cr);
                                 if (area2_sq <= 1e-4f)
                                     return;
                                 const uint64_t ti = valid_tri_idx++;
                                 // Subsample every 4th triangle past 200k to keep percentile calculation < 5 ms.
                                 if (ti < 200000 || (ti & 3u) == 0) {
                                     const float cx = (a[0] + b[0] + c[0]) * (1.0f / 3.0f);
                                     const float cy = (a[1] + b[1] + c[1]) * (1.0f / 3.0f);
                                     const float cz = (a[2] + b[2] + c[2]) * (1.0f / 3.0f);
                                     sample_cx.push_back(cx);
                                     sample_cy.push_back(cy);
                                     const float nz = cr[2] / std::sqrt(area2_sq);
                                     if (nz > 0.65f)
                                         sample_up_z.push_back(cz);
                                 }
                             });

        if (valid_tri_idx == 0)
            throw std::runtime_error("no solid triangles extracted from world_physics");

        const float lo_x = percentile_value(sample_cx, 0.005) - 256.0f;
        const float hi_x = percentile_value(sample_cx, 0.995) + 256.0f;
        const float lo_y = percentile_value(sample_cy, 0.005) - 256.0f;
        const float hi_y = percentile_value(sample_cy, 0.995) + 256.0f;
        const float span = std::max({hi_x - lo_x, hi_y - lo_y, 256.0f});
        const float mid_x = 0.5f * (lo_x + hi_x);
        const float mid_y = 0.5f * (lo_y + hi_y);
        const float origin_x = mid_x - 0.5f * span;
        const float origin_y = mid_y - 0.5f * span;
        const float cell = span / static_cast<float>(grid_n);

        const float up_z_p01 =
            sample_up_z.empty() ? raw_min[2] : percentile_value(sample_up_z, 0.01);

        // Adaptive playable street-deck band:
        // Uses nav mesh main-lane elevation band (p05..p80) so high rooftop trim and
        // underground subway/pit slabs never invert building-core vs street detection.
        float street_band_lo = -64.0f;
        float street_band_hi = 620.0f;
        if (!nav_verts.empty()) {
            std::vector<float> nav_z;
            nav_z.reserve(nav_verts.size());
            for (const Vec3& v : nav_verts)
                nav_z.push_back(v[2]);
            const float np05 = percentile_value(nav_z, 0.05);
            const float np80 = percentile_value(nav_z, 0.80);
            street_band_lo = std::min(np05 - 64.0f, -64.0f);
            street_band_hi = np80 + 140.0f;
        } else if (!sample_up_z.empty()) {
            street_band_lo = percentile_value(sample_up_z, 0.08) - 64.0f;
            street_band_hi = percentile_value(sample_up_z, 0.72) + 160.0f;
        }

        // Pass 2: Stream solid triangles directly into tops, street_max, stacked slabs, and 4-floor
        // height columns.
        const size_t total_cells = static_cast<size_t>(grid_n) * static_cast<size_t>(grid_n);
        std::vector<float> tops(total_cells, -1e9f);
        std::vector<float> street_max(total_cells, -1e9f);
        std::vector<uint32_t> floor_slab_mask(total_cells, 0);
        std::vector<ColumnFloors> col_floors(total_cells);

        stream_solid_physics(
            phys_doc, solid_ca, nullptr, [&](const Vec3& a, const Vec3& b, const Vec3& c) {
                const Vec3 cr = cross(b - a, c - a);
                const float area2_sq = dot(cr, cr);
                if (area2_sq <= 1e-4f)
                    return;
                const float nz = cr[2] / std::sqrt(area2_sq);

                const auto record_cell = [&](uint32_t gx, uint32_t gy, float z) {
                    const size_t idx = static_cast<size_t>(gy) * grid_n + gx;
                    if (z > tops[idx])
                        tops[idx] = z;
                    if (nz > 0.72f && z >= street_band_lo && z <= street_band_hi) {
                        if (z > street_max[idx])
                            street_max[idx] = z;
                    }
                    if (nz > 0.65f) {
                        const int bin =
                            std::clamp(static_cast<int>(std::floor((z - up_z_p01) / 96.0f)), 0, 31);
                        floor_slab_mask[idx] |= (1u << static_cast<uint32_t>(bin));
                        col_floors[idx].insert(z, 24.0f);
                    }
                };

                if (std::abs(nz) >= 0.55f) {
                    rasterize_triangle_xy(a, b, c, origin_x, origin_y, cell, grid_n, record_cell);
                } else {
                    rasterize_wall_triangle(a, b, c, origin_x, origin_y, cell, grid_n, record_cell);
                }
            });

        // Compute tops p05/p95 and street_z_median
        std::vector<float> valid_tops, valid_streets;
        valid_tops.reserve(total_cells);
        valid_streets.reserve(total_cells);
        size_t geo_cells = 0;
        for (size_t i = 0; i < total_cells; ++i) {
            if (tops[i] > -1e8f) {
                valid_tops.push_back(tops[i]);
                ++geo_cells;
            }
            if (street_max[i] > -1e8f)
                valid_streets.push_back(street_max[i]);
        }

        const float world_z_p05 = valid_tops.empty() ? 0.0f : percentile_value(valid_tops, 0.05);
        const float world_z_p95 = valid_tops.empty() ? 512.0f : percentile_value(valid_tops, 0.95);
        const float street_med =
            valid_streets.empty() ? world_z_p05 : percentile_value(valid_streets, 0.50);

        std::vector<float> tops_filled(total_cells);
        std::vector<float> street_z(total_cells);
        for (size_t i = 0; i < total_cells; ++i) {
            tops_filled[i] = (tops[i] > -1e8f) ? tops[i] : world_z_p05;
            street_z[i] = (street_max[i] > -1e8f) ? street_max[i] : street_med;
        }

        // Skybridge-immune street deck: the highest in-band surface under a bridge or
        // overpass is the deck, not the street. Pool street_max over a ~1000u window (wider
        // than any bridge) and take a low percentile as the local street reference, so decks
        // and walkways covering most of the window still do not lift it. Each cell then takes
        // the highest floor within [ref - 120, ref + 140]; the lower bound keeps subway and
        // pit slabs below the street from being chosen.
        std::vector<float> wall_band_hi(total_cells, 0.0f);
        {
            constexpr float kRefPercentile = 0.20f;
            constexpr float kBelowRef = 120.0f;
            constexpr float kAboveRef = 140.0f;
            const uint32_t block = std::max(1u, static_cast<uint32_t>(std::lround(320.0f / cell)));
            const uint32_t nb = (grid_n + block - 1) / block;
            std::vector<float> block_ref(static_cast<size_t>(nb) * nb, -1e9f);
            std::vector<float> pool;
            for (uint32_t by = 0; by < nb; ++by) {
                for (uint32_t bx = 0; bx < nb; ++bx) {
                    pool.clear();
                    const uint32_t x0 = bx > 0 ? (bx - 1) * block : 0;
                    const uint32_t y0 = by > 0 ? (by - 1) * block : 0;
                    const uint32_t x1 = std::min(grid_n, (bx + 2) * block);
                    const uint32_t y1 = std::min(grid_n, (by + 2) * block);
                    for (uint32_t y = y0; y < y1; ++y)
                        for (uint32_t x = x0; x < x1; ++x) {
                            const float s = street_max[static_cast<size_t>(y) * grid_n + x];
                            if (s > -1e8f)
                                pool.push_back(s);
                        }
                    if (!pool.empty())
                        block_ref[static_cast<size_t>(by) * nb + bx] =
                            percentile_value(pool, kRefPercentile);
                }
            }
            for (uint32_t y = 0; y < grid_n; ++y) {
                for (uint32_t x = 0; x < grid_n; ++x) {
                    const size_t i = static_cast<size_t>(y) * grid_n + x;
                    if (street_max[i] <= -1e8f)
                        continue;
                    const float ref = block_ref[static_cast<size_t>(y / block) * nb + x / block];
                    if (ref <= -1e8f)
                        continue;
                    float best = -1e9f;
                    const ColumnFloors& cf = col_floors[i];
                    for (int k = 0; k < cf.count; ++k) {
                        const float z = cf.z[k];
                        if (z >= ref - kBelowRef && z <= ref + kAboveRef && z > best)
                            best = z;
                    }
                    if (best > -1e8f)
                        street_z[i] = best;
                }
            }
            // Wind-band ceiling per cell: stop below the next floor above the street so deck
            // railings over an open street are not taken for street-level walls. Keep at least
            // 100u of band, since wall triangles are point-sampled.
            for (size_t i = 0; i < total_cells; ++i) {
                const float sz = street_z[i];
                float ceil_z = 1e9f;
                const ColumnFloors& cf = col_floors[i];
                for (int k = 0; k < cf.count; ++k)
                    if (cf.z[k] > sz + 60.0f && cf.z[k] < ceil_z)
                        ceil_z = cf.z[k];
                wall_band_hi[i] = std::max(std::min(sz + 190.0f, ceil_z - 16.0f), sz + 100.0f);
            }
        }

        // Pass 3: Tunnel- and skybridge-immune vertical wall detection inside local playable
        // wind band [street_z + 28u, wall_band_hi], at most street_z + 190u.
        std::vector<uint8_t> wall_hit(total_cells, 0);
        stream_solid_physics(
            phys_doc, solid_ca, nullptr, [&](const Vec3& a, const Vec3& b, const Vec3& c) {
                const Vec3 cr = cross(b - a, c - a);
                const float area2_sq = dot(cr, cr);
                if (area2_sq <= 1e-4f)
                    return;
                const float nz = cr[2] / std::sqrt(area2_sq);
                if (std::abs(nz) >= 0.55f)
                    return;

                rasterize_wall_triangle(a, b, c, origin_x, origin_y, cell, grid_n,
                                        [&](uint32_t gx, uint32_t gy, float z) {
                                            const size_t idx =
                                                static_cast<size_t>(gy) * grid_n + gx;
                                            const float sz = street_z[idx];
                                            if (z >= sz + 28.0f && z <= wall_band_hi[idx])
                                                wall_hit[idx] = 1;
                                        });
            });

        // Finalize 4-layer walkable floors + optional .nav refinement
        std::vector<std::array<float, kFloorLayers>> floor_z(total_cells);
        for (size_t i = 0; i < total_cells; ++i)
            floor_z[i] = col_floors[i].finalize(24.0f);

        if (!nav_tris.empty()) {
            std::vector<ColumnFloors> nav_cols(total_cells);
            for (size_t t = 0; t + 2 < nav_tris.size(); t += 3) {
                Vec3 a = nav_verts[nav_tris[t]];
                Vec3 b = nav_verts[nav_tris[t + 1]];
                Vec3 c = nav_verts[nav_tris[t + 2]];
                Vec3 cr = cross(b - a, c - a);
                if (cr[2] < 0.0f) {
                    std::swap(b, c);
                    cr = cross(b - a, c - a);
                }
                const float len = std::sqrt(dot(cr, cr));
                if (len < 1e-5f || cr[2] < 0.02f * len)
                    continue;
                rasterize_triangle_xy(a, b, c, origin_x, origin_y, cell, grid_n,
                                      [&](uint32_t gx, uint32_t gy, float z) {
                                          nav_cols[static_cast<size_t>(gy) * grid_n + gx].insert(
                                              z, 24.0f);
                                      });
            }
            std::vector<std::array<float, kFloorLayers>> nav_floors(total_cells);
            for (size_t i = 0; i < total_cells; ++i)
                nav_floors[i] = nav_cols[i].finalize(24.0f);
            refine_floors_with_nav(floor_z, nav_floors, grid_n, grid_n, cell);
        }

        // Building cores, roof cover, and summed-area table openness
        const float core_threshold = std::max(street_band_hi - 60.0f, street_med + 176.0f);
        std::vector<uint8_t> solid_wall(total_cells, 0);
        std::vector<uint8_t> roof_cover(total_cells, 0);
        std::vector<uint8_t> floor_slab_count(total_cells, 0);

        size_t solid_cells = 0, roof_cells = 0;
        size_t mf2 = 0, mf3 = 0, mf4 = 0;

        for (size_t i = 0; i < total_cells; ++i) {
            const bool has_geo = tops[i] > -1e8f;
            const bool has_street = street_max[i] > -1e8f;
            const bool building_core = has_geo && !has_street && (tops_filled[i] > core_threshold);
            const bool is_solid = (wall_hit[i] != 0) || building_core;
            const bool is_roof =
                has_geo &&
                (building_core || (has_street && (tops_filled[i] - street_z[i]) > 140.0f));

            solid_wall[i] = is_solid ? 1u : 0u;
            roof_cover[i] = is_roof ? 255u : 0u;
            if (is_solid)
                ++solid_cells;
            if (is_roof)
                ++roof_cells;

            // Popcount of 96u slab mask
            uint32_t m = floor_slab_mask[i];
            uint32_t cnt = 0;
            while (m) {
                m &= (m - 1u);
                ++cnt;
            }
            floor_slab_count[i] = static_cast<uint8_t>(std::min<uint32_t>(cnt, 6u));
            if (cnt >= 2)
                ++mf2;
            if (cnt >= 3)
                ++mf3;
            if (cnt >= 4)
                ++mf4;
        }

        // Openness via 2D summed-area table of (!is_roof && !solid_wall)
        const size_t sat_stride = static_cast<size_t>(grid_n) + 1;
        std::vector<uint32_t> sat(sat_stride * sat_stride, 0);
        for (uint32_t y = 0; y < grid_n; ++y) {
            for (uint32_t x = 0; x < grid_n; ++x) {
                const size_t idx = static_cast<size_t>(y) * grid_n + x;
                const uint32_t open_cell = (solid_wall[idx] == 0 && roof_cover[idx] == 0) ? 1u : 0u;
                sat[(y + 1) * sat_stride + (x + 1)] = open_cell + sat[y * sat_stride + (x + 1)] +
                                                      sat[(y + 1) * sat_stride + x] -
                                                      sat[y * sat_stride + x];
            }
        }

        const int r_open = std::max(2, static_cast<int>(std::ceil(220.0f / cell)));
        std::vector<uint8_t> air_map(total_cells, 0);
        for (uint32_t y = 0; y < grid_n; ++y) {
            const int y0 = std::max(0, static_cast<int>(y) - r_open);
            const int y1 = std::min(static_cast<int>(grid_n), static_cast<int>(y) + r_open + 1);
            for (uint32_t x = 0; x < grid_n; ++x) {
                const size_t idx = static_cast<size_t>(y) * grid_n + x;
                if (solid_wall[idx]) {
                    air_map[idx] = 255u;
                    continue;
                }
                const int x0 = std::max(0, static_cast<int>(x) - r_open);
                const int x1 = std::min(static_cast<int>(grid_n), static_cast<int>(x) + r_open + 1);
                const uint32_t open_sum = sat[y1 * sat_stride + x1] - sat[y0 * sat_stride + x1] -
                                          sat[y1 * sat_stride + x0] + sat[y0 * sat_stride + x0];
                const float open_frac =
                    static_cast<float>(open_sum) / static_cast<float>((x1 - x0) * (y1 - y0));
                air_map[idx] =
                    static_cast<uint8_t>(std::lround(std::clamp(open_frac, 0.0f, 1.0f) * 127.0f));
            }
        }

        const auto t2 = std::chrono::steady_clock::now();

        out_grid.width = grid_n;
        out_grid.height = grid_n;
        out_grid.origin_x = origin_x;
        out_grid.origin_y = origin_y;
        out_grid.span_units = span;
        out_grid.cell_units = cell;
        out_grid.world_z_p05 = world_z_p05;
        out_grid.world_z_p95 = world_z_p95;
        out_grid.street_z_median = street_med;
        out_grid.tops = std::move(tops_filled);
        out_grid.street_z = std::move(street_z);
        out_grid.air_map = std::move(air_map);
        out_grid.roof_cover = std::move(roof_cover);
        out_grid.floor_z = std::move(floor_z);
        out_grid.floor_slab_count = std::move(floor_slab_count);

        if (out_stats) {
            const float inv_total = 100.0f / static_cast<float>(total_cells);
            out_stats->compounds_seen = counters.compounds_seen;
            out_stats->solid_hulls_extracted = counters.solid_hulls;
            out_stats->solid_meshes_extracted = counters.solid_meshes;
            out_stats->non_solid_shapes_skipped = counters.skipped_shapes;
            out_stats->vertices = counters.vertices;
            out_stats->triangles = counters.triangles;
            out_stats->nav_vertices = static_cast<uint32_t>(nav_verts.size());
            out_stats->nav_triangles = static_cast<uint32_t>(nav_tris.size() / 3);
            out_stats->included_ca_indices = std::move(included_ca);
            out_stats->raw_bounds_min = raw_min;
            out_stats->raw_bounds_max = raw_max;
            out_stats->geometry_coverage_pct = static_cast<float>(geo_cells) * inv_total;
            out_stats->street_obstacle_pct = static_cast<float>(solid_cells) * inv_total;
            out_stats->roof_cover_pct = static_cast<float>(roof_cells) * inv_total;
            out_stats->multi_floor_2plus_pct = static_cast<float>(mf2) * inv_total;
            out_stats->multi_floor_3plus_pct = static_cast<float>(mf3) * inv_total;
            out_stats->multi_floor_4plus_pct = static_cast<float>(mf4) * inv_total;
            out_stats->extract_seconds = std::chrono::duration<double>(t1 - t0).count();
            out_stats->rasterize_seconds = std::chrono::duration<double>(t2 - t1).count();
            out_stats->from_cache = false;
        }
        return true;
    } catch (const std::exception& ex) {
        if (out_error)
            *out_error = ex.what();
        return false;
    }
}

bool save_map_grid_cache(const std::filesystem::path& cache_path, const MapGrid& grid,
                         uint64_t source_fingerprint, std::string* out_error) {
    if (!grid.valid()) {
        if (out_error)
            *out_error = "cannot save invalid MapGrid";
        return false;
    }
    std::error_code ec;
    if (cache_path.has_parent_path())
        std::filesystem::create_directories(cache_path.parent_path(), ec);

    const size_t n = static_cast<size_t>(grid.width) * static_cast<size_t>(grid.height);
    const bool use_f32 = cache_uses_f32(grid);

    CacheHeader hdr{};
    std::memcpy(hdr.magic, kCacheMagic, 8);
    hdr.version = kCacheVersion;
    hdr.flags = use_f32 ? 1u : 0u;
    hdr.source_fingerprint = source_fingerprint;
    hdr.width = grid.width;
    hdr.height = grid.height;
    hdr.origin_x = grid.origin_x;
    hdr.origin_y = grid.origin_y;
    hdr.span_units = grid.span_units;
    hdr.cell_units = grid.cell_units;
    hdr.world_z_p05 = grid.world_z_p05;
    hdr.world_z_p95 = grid.world_z_p95;
    hdr.street_z_median = grid.street_z_median;

    std::ofstream out(cache_path, std::ios::binary | std::ios::trunc);
    if (!out) {
        if (out_error)
            *out_error = "cannot write cache file: " + cache_path.string();
        return false;
    }

    out.write(reinterpret_cast<const char*>(&hdr), sizeof(hdr));
    out.write(reinterpret_cast<const char*>(grid.air_map.data()), static_cast<std::streamsize>(n));
    out.write(reinterpret_cast<const char*>(grid.roof_cover.data()),
              static_cast<std::streamsize>(n));
    out.write(reinterpret_cast<const char*>(grid.floor_slab_count.data()),
              static_cast<std::streamsize>(n));

    if (use_f32) {
        out.write(reinterpret_cast<const char*>(grid.tops.data()),
                  static_cast<std::streamsize>(n * sizeof(float)));
        out.write(reinterpret_cast<const char*>(grid.street_z.data()),
                  static_cast<std::streamsize>(n * sizeof(float)));
        out.write(reinterpret_cast<const char*>(grid.floor_z.data()),
                  static_cast<std::streamsize>(n * kFloorLayers * sizeof(float)));
    } else {
        std::vector<int16_t> q(n * (2 + kFloorLayers));
        size_t off = 0;
        for (size_t i = 0; i < n; ++i)
            q[off++] = pack_height_i16(grid.tops[i]);
        for (size_t i = 0; i < n; ++i)
            q[off++] = pack_height_i16(grid.street_z[i]);
        for (size_t i = 0; i < n; ++i) {
            for (int l = 0; l < kFloorLayers; ++l)
                q[off++] = pack_height_i16(grid.floor_z[i][l]);
        }
        out.write(reinterpret_cast<const char*>(q.data()),
                  static_cast<std::streamsize>(q.size() * sizeof(int16_t)));
    }

    if (!out) {
        if (out_error)
            *out_error = "write error saving cache file: " + cache_path.string();
        return false;
    }
    return true;
}

bool load_map_grid_cache(const std::filesystem::path& cache_path, MapGrid& out_grid,
                         uint64_t* out_fingerprint, std::string* out_error) {
    std::ifstream in(cache_path, std::ios::binary);
    if (!in) {
        if (out_error)
            *out_error = "cannot open cache file: " + cache_path.string();
        return false;
    }

    CacheHeader hdr{};
    in.read(reinterpret_cast<char*>(&hdr), sizeof(hdr));
    if (!in || std::memcmp(hdr.magic, kCacheMagic, 8) != 0 || hdr.version != kCacheVersion) {
        if (out_error)
            *out_error = "invalid cache header";
        return false;
    }
    if (hdr.width < 8 || hdr.height < 8 || hdr.width > 4096 || hdr.height > 4096) {
        if (out_error)
            *out_error = "invalid cache grid dimensions";
        return false;
    }

    const size_t n = static_cast<size_t>(hdr.width) * static_cast<size_t>(hdr.height);
    MapGrid g;
    g.width = hdr.width;
    g.height = hdr.height;
    g.origin_x = hdr.origin_x;
    g.origin_y = hdr.origin_y;
    g.span_units = hdr.span_units;
    g.cell_units = hdr.cell_units;
    g.world_z_p05 = hdr.world_z_p05;
    g.world_z_p95 = hdr.world_z_p95;
    g.street_z_median = hdr.street_z_median;

    g.air_map.resize(n);
    g.roof_cover.resize(n);
    g.floor_slab_count.resize(n);
    g.tops.resize(n);
    g.street_z.resize(n);
    g.floor_z.resize(n);

    in.read(reinterpret_cast<char*>(g.air_map.data()), static_cast<std::streamsize>(n));
    in.read(reinterpret_cast<char*>(g.roof_cover.data()), static_cast<std::streamsize>(n));
    in.read(reinterpret_cast<char*>(g.floor_slab_count.data()), static_cast<std::streamsize>(n));

    if (hdr.flags & 1u) {
        in.read(reinterpret_cast<char*>(g.tops.data()),
                static_cast<std::streamsize>(n * sizeof(float)));
        in.read(reinterpret_cast<char*>(g.street_z.data()),
                static_cast<std::streamsize>(n * sizeof(float)));
        in.read(reinterpret_cast<char*>(g.floor_z.data()),
                static_cast<std::streamsize>(n * kFloorLayers * sizeof(float)));
    } else {
        std::vector<int16_t> q(n * (2 + kFloorLayers));
        in.read(reinterpret_cast<char*>(q.data()),
                static_cast<std::streamsize>(q.size() * sizeof(int16_t)));
        size_t off = 0;
        for (size_t i = 0; i < n; ++i)
            g.tops[i] = unpack_height_i16(q[off++]);
        for (size_t i = 0; i < n; ++i)
            g.street_z[i] = unpack_height_i16(q[off++]);
        for (size_t i = 0; i < n; ++i) {
            for (int l = 0; l < kFloorLayers; ++l)
                g.floor_z[i][l] = unpack_height_i16(q[off++]);
        }
    }

    if (!in) {
        if (out_error)
            *out_error = "truncated cache file";
        return false;
    }

    if (out_fingerprint)
        *out_fingerprint = hdr.source_fingerprint;
    out_grid = std::move(g);
    return true;
}

bool load_or_bake_map_grid(const std::filesystem::path& vpk_path,
                           const std::filesystem::path& cache_dir, const BakeOptions& opts,
                           MapGrid& out_grid, BakeStats* out_stats, std::string* out_error) {
    const uint64_t fp = compute_vpk_fingerprint(vpk_path);
    const std::string stem = to_lower(vpk_path.stem().string());
    const std::filesystem::path cache_path =
        cache_dir / (stem + "_" + std::to_string(opts.grid_n) + ".dmapgrid");

    if (fp != 0 && std::filesystem::is_regular_file(cache_path)) {
        const auto t0 = std::chrono::steady_clock::now();
        uint64_t cached_fp = 0;
        MapGrid cached;
        if (load_map_grid_cache(cache_path, cached, &cached_fp, nullptr) && cached_fp == fp &&
            cached.width == opts.grid_n) {
            const auto t1 = std::chrono::steady_clock::now();
            out_grid = std::move(cached);
            if (out_stats) {
                *out_stats = BakeStats{};
                out_stats->from_cache = true;
                out_stats->extract_seconds = std::chrono::duration<double>(t1 - t0).count();
                const size_t n =
                    static_cast<size_t>(out_grid.width) * static_cast<size_t>(out_grid.height);
                size_t solid = 0, roof = 0, mf2 = 0, mf3 = 0, mf4 = 0;
                for (size_t i = 0; i < n; ++i) {
                    if (out_grid.air_map[i] > 127u)
                        ++solid;
                    if (out_grid.roof_cover[i] > 127u)
                        ++roof;
                    if (out_grid.floor_slab_count[i] >= 2)
                        ++mf2;
                    if (out_grid.floor_slab_count[i] >= 3)
                        ++mf3;
                    if (out_grid.floor_slab_count[i] >= 4)
                        ++mf4;
                }
                const float inv = 100.0f / static_cast<float>(n);
                out_stats->street_obstacle_pct = static_cast<float>(solid) * inv;
                out_stats->roof_cover_pct = static_cast<float>(roof) * inv;
                out_stats->multi_floor_2plus_pct = static_cast<float>(mf2) * inv;
                out_stats->multi_floor_3plus_pct = static_cast<float>(mf3) * inv;
                out_stats->multi_floor_4plus_pct = static_cast<float>(mf4) * inv;
            }
            return true;
        }
    }

    if (!bake_map_grid_from_vpk(vpk_path, opts, out_grid, out_stats, out_error))
        return false;
    quantize_like_cache(out_grid);
    if (!cache_dir.empty())
        save_map_grid_cache(cache_path, out_grid, fp, nullptr);
    return true;
}

float cover_above(const MapGrid& g, float x, float y, float z) noexcept {
    if (!g.valid() || x < g.origin_x || y < g.origin_y ||
        x >= g.origin_x + g.width * g.cell_units || y >= g.origin_y + g.height * g.cell_units)
        return 0.0f;
    const float gx = (x - g.origin_x) / g.cell_units - 0.5f;
    const float gy = (y - g.origin_y) / g.cell_units - 0.5f;
    const int x0 = int(std::floor(gx)), y0 = int(std::floor(gy));
    const float fx = gx - float(x0), fy = gy - float(y0);
    float c[4]{};
    for (int k = 0; k < 4; ++k) {
        const int cx = std::clamp(x0 + (k & 1), 0, int(g.width) - 1);
        const int cy = std::clamp(y0 + (k >> 1), 0, int(g.height) - 1);
        c[k] = g.tops[std::size_t(cy) * g.width + std::size_t(cx)] > z + 24.0f ? 1.0f : 0.0f;
    }
    return (c[0] * (1 - fx) + c[1] * fx) * (1 - fy) + (c[2] * (1 - fx) + c[3] * fx) * fy;
}

std::vector<uint8_t> wall_reach_mask(const MapGrid& g, float reach_units) {
    if (!g.valid())
        return {};
    const int w = static_cast<int>(g.width), h = static_cast<int>(g.height);
    const int r = static_cast<int>(std::ceil(std::max(reach_units, 0.0f) / g.cell_units)) + 3;
    // Separable square dilation of the solid cells (a square covers the circle of reach).
    std::vector<uint8_t> rows(static_cast<size_t>(w) * h, 0), out(rows.size(), 0);
    for (int y = 0; y < h; ++y) {
        int last = -1000000; // last solid column seen, scanning right
        for (int x = 0; x < w; ++x) {
            if (g.air_map[static_cast<size_t>(y) * w + x] > 127u)
                last = x;
            if (x - last <= r)
                rows[static_cast<size_t>(y) * w + x] = 1;
        }
        last = 1000000;
        for (int x = w - 1; x >= 0; --x) {
            if (g.air_map[static_cast<size_t>(y) * w + x] > 127u)
                last = x;
            if (last - x <= r)
                rows[static_cast<size_t>(y) * w + x] = 1;
        }
    }
    for (int x = 0; x < w; ++x) {
        int last = -1000000;
        for (int y = 0; y < h; ++y) {
            if (rows[static_cast<size_t>(y) * w + x])
                last = y;
            if (y - last <= r)
                out[static_cast<size_t>(y) * w + x] = 1;
        }
        last = 1000000;
        for (int y = h - 1; y >= 0; --y) {
            if (rows[static_cast<size_t>(y) * w + x])
                last = y;
            if (last - y <= r)
                out[static_cast<size_t>(y) * w + x] = 1;
        }
    }
    return out;
}

std::vector<float> bake_drift_potential(const MapGrid& g, float wind_x, float wind_y,
                                        float wind_strength, uint32_t out_n) {
    std::vector<float> out;
    if (!g.valid() || out_n < 8 || out_n > 4096)
        return out;
    const uint32_t n = out_n;
    const float cell = g.span_units / float(n); // world units per output cell
    const float wind_len = std::sqrt(wind_x * wind_x + wind_y * wind_y);
    const float strength = wind_len > 1e-3f ? std::clamp(wind_strength, 0.0f, 1.0f) : 0.0f;
    // Upwind: where the shelter comes from.
    const float ux = strength > 0.0f ? -wind_x / wind_len : 0.0f;
    const float uy = strength > 0.0f ? -wind_y / wind_len : 0.0f;

    auto grid_index = [&](float wx, float wy, std::size_t& index) {
        const float gx = (wx - g.origin_x) / g.cell_units;
        const float gy = (wy - g.origin_y) / g.cell_units;
        if (!(gx >= 0.0f && gy >= 0.0f && gx < float(g.width) && gy < float(g.height)))
            return false;
        index = std::size_t(gy) * g.width + std::size_t(gx);
        return true;
    };

    // Solid cells (street-deck walls and building cores) on the output grid.
    std::vector<uint8_t> solid(std::size_t(n) * n, 0);
    for (uint32_t y = 0; y < n; ++y)
        for (uint32_t x = 0; x < n; ++x) {
            std::size_t gi;
            if (grid_index(g.origin_x + (x + 0.5f) * cell, g.origin_y + (y + 0.5f) * cell, gi))
                solid[std::size_t(y) * n + x] = g.air_map[gi] > 127u ? 1 : 0;
        }

    // Shelter is measured in world units, so the march reaches the same distance at any
    // output size: lee zones run several times an obstacle's height downwind.
    constexpr int kSteps = 14;
    const float step = std::max(cell, 24.0f);
    constexpr int kBankReach = 2; // output cells
    std::vector<float> raw(std::size_t(n) * n, 1.0f);
    for (uint32_t y = 0; y < n; ++y) {
        for (uint32_t x = 0; x < n; ++x) {
            const std::size_t oi = std::size_t(y) * n + x;
            if (solid[oi])
                continue; // roofs and wall tops coat like open ground
            const float wx = g.origin_x + (x + 0.5f) * cell;
            const float wy = g.origin_y + (y + 0.5f) * cell;
            std::size_t here;
            if (!grid_index(wx, wy, here))
                continue;
            const float street = g.street_z[here];
            float lee = 0.0f;
            if (strength > 0.0f) {
                for (int k = 1; k <= kSteps; ++k) {
                    const float dist = step * float(k);
                    std::size_t gi;
                    if (!grid_index(wx + ux * dist, wy + uy * dist, gi))
                        break;
                    if (g.air_map[gi] <= 127u)
                        continue;
                    const float h = std::clamp(g.tops[gi] - street, 0.0f, 600.0f);
                    lee = std::max(lee, std::clamp(h / (dist * 0.6f) - 0.15f, 0.0f, 1.0f));
                }
            }
            // Banks against any nearby wall, strongest right at its foot.
            float bank = 0.0f;
            for (int dy = -kBankReach; dy <= kBankReach; ++dy)
                for (int dx = -kBankReach; dx <= kBankReach; ++dx) {
                    const int sx = int(x) + dx, sy = int(y) + dy;
                    if (sx < 0 || sy < 0 || sx >= int(n) || sy >= int(n) ||
                        !solid[std::size_t(sy) * n + std::size_t(sx)])
                        continue;
                    const float d = std::sqrt(float(dx * dx + dy * dy));
                    bank = std::max(bank, 1.0f - d / (kBankReach + 0.5f));
                }
            const float shaped = 0.9f * lee - 0.55f * (1.0f - lee);
            raw[oi] = std::clamp(1.0f + strength * shaped + 0.35f * bank, 0.0f, 2.0f);
        }
    }

    // A 3 x 3 box blur so cell steps never read as edges (the shader adds the detail).
    out.assign(std::size_t(n) * n, 1.0f);
    for (uint32_t y = 0; y < n; ++y)
        for (uint32_t x = 0; x < n; ++x) {
            float sum = 0.0f;
            int count = 0;
            for (int dy = -1; dy <= 1; ++dy)
                for (int dx = -1; dx <= 1; ++dx) {
                    const int sx = std::clamp(int(x) + dx, 0, int(n) - 1);
                    const int sy = std::clamp(int(y) + dy, 0, int(n) - 1);
                    sum += raw[std::size_t(sy) * n + std::size_t(sx)];
                    ++count;
                }
            out[std::size_t(y) * n + x] = sum / float(count);
        }
    return out;
}

bool write_png_rgba8(const std::filesystem::path& png_path, uint32_t width, uint32_t height,
                     const uint8_t* rgba, std::string* out_error) {
    if (!rgba || width == 0 || height == 0) {
        if (out_error)
            *out_error = "invalid image dimensions";
        return false;
    }
    std::error_code ec;
    if (png_path.has_parent_path())
        std::filesystem::create_directories(png_path.parent_path(), ec);

#ifdef _WIN32
    const HRESULT co_hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (FAILED(co_hr) && co_hr != RPC_E_CHANGED_MODE) {
        if (out_error)
            *out_error = "CoInitializeEx failed";
        return false;
    }
    struct WicGuard {
        bool uninit = false;
        IWICImagingFactory* factory = nullptr;
        IWICStream* stream = nullptr;
        IWICBitmapEncoder* encoder = nullptr;
        IWICBitmapFrameEncode* frame = nullptr;
        ~WicGuard() {
            if (frame)
                frame->Release();
            if (encoder)
                encoder->Release();
            if (stream)
                stream->Release();
            if (factory)
                factory->Release();
            if (uninit)
                CoUninitialize();
        }
    } g{SUCCEEDED(co_hr)};

    if (FAILED(CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER,
                                IID_PPV_ARGS(&g.factory))) ||
        FAILED(g.factory->CreateStream(&g.stream)) ||
        FAILED(g.stream->InitializeFromFilename(png_path.wstring().c_str(), GENERIC_WRITE)) ||
        FAILED(g.factory->CreateEncoder(GUID_ContainerFormatPng, nullptr, &g.encoder)) ||
        FAILED(g.encoder->Initialize(g.stream, WICBitmapEncoderNoCache)) ||
        FAILED(g.encoder->CreateNewFrame(&g.frame, nullptr)) ||
        FAILED(g.frame->Initialize(nullptr)) || FAILED(g.frame->SetSize(width, height))) {
        if (out_error)
            *out_error = "WIC PNG initialization failed";
        return false;
    }

    WICPixelFormatGUID fmt = GUID_WICPixelFormat32bppBGRA;
    if (FAILED(g.frame->SetPixelFormat(&fmt))) {
        if (out_error)
            *out_error = "WIC SetPixelFormat failed";
        return false;
    }

    // Convert RGBA -> BGRA for WIC
    const size_t pixels = static_cast<size_t>(width) * static_cast<size_t>(height);
    std::vector<uint8_t> bgra(pixels * 4);
    for (size_t i = 0; i < pixels; ++i) {
        bgra[i * 4 + 0] = rgba[i * 4 + 2];
        bgra[i * 4 + 1] = rgba[i * 4 + 1];
        bgra[i * 4 + 2] = rgba[i * 4 + 0];
        bgra[i * 4 + 3] = rgba[i * 4 + 3];
    }

    if (FAILED(
            g.frame->WritePixels(height, width * 4, static_cast<UINT>(bgra.size()), bgra.data())) ||
        FAILED(g.frame->Commit()) || FAILED(g.encoder->Commit())) {
        if (out_error)
            *out_error = "WIC PNG write failed";
        return false;
    }
    return true;
#else
    (void)png_path;
    if (out_error)
        *out_error = "PNG output requires Windows WIC";
    return false;
#endif
}

bool write_map_grid_diagnostic_png(const std::filesystem::path& png_path, const MapGrid& grid,
                                   const BakeStats* /*stats*/, std::string* out_error) {
    if (!grid.valid()) {
        if (out_error)
            *out_error = "invalid MapGrid";
        return false;
    }

    const uint32_t n = grid.width;
    const uint32_t pad = 12;
    const uint32_t w = n * 3 + pad * 4;
    const uint32_t h = n + pad * 2;
    std::vector<uint8_t> img(static_cast<size_t>(w) * static_cast<size_t>(h) * 4, 255u);
    for (size_t i = 0; i < static_cast<size_t>(w) * h; ++i) {
        img[i * 4 + 0] = 16;
        img[i * 4 + 1] = 18;
        img[i * 4 + 2] = 24;
        img[i * 4 + 3] = 255;
    }

    const float lo = grid.world_z_p05;
    const float hi = std::max(grid.world_z_p95, lo + 1.0f);
    const float inv_range = 1.0f / (hi - lo);

    static constexpr uint8_t kFloorPalette[7][3] = {
        {18, 22, 30},  {62, 82, 108},   {74, 198, 192},  {238, 188, 76},
        {235, 96, 86}, {245, 130, 180}, {255, 220, 240},
    };

    const auto put_px = [&](uint32_t x, uint32_t y, uint8_t r, uint8_t g, uint8_t b) {
        const size_t o = (static_cast<size_t>(y) * w + x) * 4;
        img[o + 0] = r;
        img[o + 1] = g;
        img[o + 2] = b;
        img[o + 3] = 255;
    };

    for (uint32_t gy = 0; gy < n; ++gy) {
        // Flip Y so North (+Y) is up
        const uint32_t py = pad + (n - 1 - gy);
        for (uint32_t gx = 0; gx < n; ++gx) {
            const size_t idx = static_cast<size_t>(gy) * n + gx;

            // Panel 1: Top height (tops)
            const float norm = std::clamp((grid.tops[idx] - lo) * inv_range, 0.0f, 1.0f);
            const uint8_t p1r = static_cast<uint8_t>(22.0f + norm * 222.0f);
            const uint8_t p1g = static_cast<uint8_t>(30.0f + std::pow(norm, 0.85f) * 190.0f);
            const uint8_t p1b = static_cast<uint8_t>(46.0f + (1.0f - norm) * 90.0f + norm * 60.0f);
            put_px(pad + gx, py, p1r, p1g, p1b);

            // Panel 2: Street Obstacles (coral) / Roofs (dark slate) / Open Lanes (warm gold)
            const bool solid = grid.air_map[idx] > 127u;
            const bool roof = grid.roof_cover[idx] > 127u;
            const float op = solid ? 0.0f : static_cast<float>(grid.air_map[idx]) / 127.0f;
            uint8_t p2r, p2g, p2b;
            if (solid) {
                p2r = 196;
                p2g = 92;
                p2b = 88;
            } else if (roof) {
                p2r = 34;
                p2g = 38;
                p2b = 52;
            } else {
                p2r = static_cast<uint8_t>(36.0f + op * 195.0f);
                p2g = static_cast<uint8_t>(54.0f + op * 155.0f);
                p2b = static_cast<uint8_t>(70.0f + op * 70.0f);
            }
            put_px(pad * 2 + n + gx, py, p2r, p2g, p2b);

            // Panel 3: Stacked Floor Layers
            const uint8_t fl = std::min<uint8_t>(grid.floor_slab_count[idx], 6u);
            put_px(pad * 3 + n * 2 + gx, py, kFloorPalette[fl][0], kFloorPalette[fl][1],
                   kFloorPalette[fl][2]);
        }
    }

    return write_png_rgba8(png_path, w, h, img.data(), out_error);
}

} // namespace dolly::map_grid
