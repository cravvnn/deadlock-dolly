#pragma once
// Self-contained RFC 8878 Zstandard and LZ4 block/chained decompressors for
// offline Source 2 KV3 resource parsing. No external compression libraries.

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

namespace dolly::map_grid::detail {

inline uint16_t read_u16_le(const uint8_t* p) noexcept {
    uint16_t v = 0;
    std::memcpy(&v, p, sizeof(v));
    return v;
}

inline uint32_t read_u32_le(const uint8_t* p) noexcept {
    uint32_t v = 0;
    std::memcpy(&v, p, sizeof(v));
    return v;
}

inline uint64_t read_u64_le(const uint8_t* p) noexcept {
    uint64_t v = 0;
    std::memcpy(&v, p, sizeof(v));
    return v;
}

// -----------------------------------------------------------------------------
// LZ4 block decoder (with optional chained dictionary prefix for KV3 blobs)

inline bool lz4_decompress_block(const uint8_t* src, size_t src_size, uint8_t* dst,
                                 size_t dst_capacity, const uint8_t* dict_base, size_t dict_offset,
                                 size_t& out_written) noexcept {
    const uint8_t* ip = src;
    const uint8_t* const iend = src + src_size;
    uint8_t* op = dst;
    uint8_t* const oend = dst + dst_capacity;

    while (ip < iend) {
        const uint8_t token = *ip++;
        size_t lit_len = token >> 4;
        if (lit_len == 15) {
            uint8_t s = 255;
            while (s == 255) {
                if (ip >= iend)
                    return false;
                s = *ip++;
                lit_len += s;
            }
        }
        if (lit_len > static_cast<size_t>(iend - ip) || lit_len > static_cast<size_t>(oend - op))
            return false;
        if (lit_len > 0) {
            std::memcpy(op, ip, lit_len);
            ip += lit_len;
            op += lit_len;
        }
        if (ip >= iend)
            break;

        if (static_cast<size_t>(iend - ip) < 2)
            return false;
        const uint16_t offset = read_u16_le(ip);
        ip += 2;
        if (offset == 0)
            return false;

        size_t match_len = (token & 0x0Fu) + 4;
        if ((token & 0x0Fu) == 15) {
            uint8_t s = 255;
            while (s == 255) {
                if (ip >= iend)
                    return false;
                s = *ip++;
                match_len += s;
            }
        }
        if (match_len > static_cast<size_t>(oend - op))
            return false;

        const size_t total_written = dict_offset + static_cast<size_t>(op - dst);
        if (offset > total_written)
            return false;

        const uint8_t* ref = (dict_base ? dict_base : dst) + (total_written - offset);
        for (size_t k = 0; k < match_len; ++k)
            op[k] = ref[k];
        op += match_len;
    }

    out_written = static_cast<size_t>(op - dst);
    return true;
}

// -----------------------------------------------------------------------------
// RFC 8878 Zstandard decoder (standard frames, no external dictionary)

namespace zstd_impl {

struct FseEntry {
    uint16_t baseline = 0;
    uint8_t symbol = 0;
    uint8_t nb_bits = 0;
};

struct FseTable {
    int accuracy_log = 0;
    std::vector<FseEntry> entries;
    bool valid() const noexcept { return !entries.empty(); }
};

struct HuffEntry {
    uint8_t symbol = 0;
    uint8_t nb_bits = 0;
};

struct HuffTable {
    int max_bits = 0;
    std::vector<HuffEntry> entries;
    bool valid() const noexcept { return max_bits > 0 && !entries.empty(); }
};

// Forward bit reader (LSB first, for FSE table headers)
struct FwdBitReader {
    const uint8_t* data = nullptr;
    size_t size = 0;
    size_t bit_pos = 0;

    uint32_t peek(int n) const noexcept {
        if (n <= 0)
            return 0;
        const size_t byte_pos = bit_pos >> 3;
        const int bit_off = static_cast<int>(bit_pos & 7);
        uint64_t val = 0;
        const size_t avail = byte_pos < size ? std::min<size_t>(8, size - byte_pos) : 0;
        if (avail > 0)
            std::memcpy(&val, data + byte_pos, avail);
        return static_cast<uint32_t>((val >> bit_off) & ((1ULL << n) - 1ULL));
    }

    void consume(int n) noexcept { bit_pos += static_cast<size_t>(n); }

    size_t bytes_consumed() const noexcept { return (bit_pos + 7) >> 3; }
};

// Backward bit reader (MSB first from sentinel bit, for FSE/Huffman/Sequences)
struct RevBitReader {
    const uint8_t* data = nullptr;
    size_t size = 0;
    int64_t bits_left = 0;

    bool init(const uint8_t* ptr, size_t len) noexcept {
        if (!ptr || len == 0)
            return false;
        const uint8_t last = ptr[len - 1];
        if (last == 0)
            return false;
        int high_bit = 7;
        while (high_bit > 0 && ((last >> high_bit) & 1) == 0)
            --high_bit;
        data = ptr;
        size = len;
        bits_left = static_cast<int64_t>(len - 1) * 8 + high_bit;
        return true;
    }

    uint32_t peek_bits(int n) const noexcept {
        if (n <= 0)
            return 0;
        if (bits_left <= 0)
            return 0;
        const int64_t start = bits_left - n;
        if (start >= 0) {
            const size_t byte_pos = static_cast<size_t>(start >> 3);
            const int bit_off = static_cast<int>(start & 7);
            uint64_t v = 0;
            const size_t avail = byte_pos < size ? std::min<size_t>(8, size - byte_pos) : 0;
            if (avail > 0)
                std::memcpy(&v, data + byte_pos, avail);
            return static_cast<uint32_t>((v >> bit_off) & ((1ULL << n) - 1ULL));
        }
        // Fewer than n bits remain: shift remaining bits up to the top of n bits (zero-padded low).
        const int avail_bits = static_cast<int>(bits_left);
        uint64_t v = 0;
        const size_t avail_bytes = std::min<size_t>(8, size);
        if (avail_bytes > 0)
            std::memcpy(&v, data, avail_bytes);
        const uint32_t rem = static_cast<uint32_t>(v & ((1ULL << avail_bits) - 1ULL));
        return rem << (n - avail_bits);
    }

    uint32_t read_bits(int n) noexcept {
        if (n <= 0)
            return 0;
        const uint32_t val = peek_bits(n);
        bits_left -= n;
        return val;
    }
};

inline int highest_bit32(uint32_t v) noexcept {
    int b = 0;
    while (v >>= 1)
        ++b;
    return b;
}

inline bool build_fse_table(const int16_t* norm, int max_symbol, int accuracy_log, FseTable& out) {
    if (accuracy_log < 0 || accuracy_log > 15)
        return false;
    const uint32_t table_size = 1u << accuracy_log;
    const uint32_t table_mask = table_size - 1u;
    const uint32_t step = (table_size >> 1) + (table_size >> 3) + 3u;

    out.accuracy_log = accuracy_log;
    out.entries.assign(table_size, FseEntry{});

    std::vector<uint32_t> symbol_next(static_cast<size_t>(max_symbol + 1), 0);
    uint32_t high_threshold = table_size - 1u;

    for (int s = 0; s <= max_symbol; ++s) {
        if (norm[s] == -1) {
            out.entries[high_threshold--].symbol = static_cast<uint8_t>(s);
            symbol_next[s] = 1;
        } else if (norm[s] > 0) {
            symbol_next[s] = static_cast<uint32_t>(norm[s]);
        }
    }

    uint32_t pos = 0;
    for (int s = 0; s <= max_symbol; ++s) {
        for (int i = 0; i < norm[s]; ++i) {
            out.entries[pos].symbol = static_cast<uint8_t>(s);
            do {
                pos = (pos + step) & table_mask;
            } while (pos > high_threshold);
        }
    }
    if (pos != 0)
        return false;

    for (uint32_t i = 0; i < table_size; ++i) {
        const uint8_t sym = out.entries[i].symbol;
        const uint32_t next_state = symbol_next[sym]++;
        const int nb = accuracy_log - highest_bit32(next_state);
        out.entries[i].nb_bits = static_cast<uint8_t>(nb);
        out.entries[i].baseline = static_cast<uint16_t>((next_state << nb) - table_size);
    }
    return true;
}

inline bool build_rle_fse_table(uint8_t symbol, FseTable& out) {
    out.accuracy_log = 0;
    out.entries.assign(1, FseEntry{0, symbol, 0});
    return true;
}

inline bool read_fse_table(const uint8_t* src, size_t src_size, int max_symbol_limit,
                           int max_accuracy_log, FseTable& out, size_t& bytes_read) {
    if (src_size == 0)
        return false;
    FwdBitReader br{src, src_size, 0};
    const int accuracy_log = static_cast<int>(br.peek(4)) + 5;
    br.consume(4);
    if (accuracy_log > max_accuracy_log)
        return false;

    int remaining = (1 << accuracy_log) + 1;
    int threshold = 1 << accuracy_log;
    int nb_bits = accuracy_log + 1;

    std::array<int16_t, 256> norm{};
    int symbol = 0;
    bool prev_zero = false;

    while (remaining > 1 && symbol <= max_symbol_limit) {
        if (prev_zero) {
            int repeat = 0;
            while (true) {
                const uint32_t v = br.peek(2);
                br.consume(2);
                repeat += static_cast<int>(v);
                if (v != 3)
                    break;
            }
            symbol += repeat;
            if (symbol > max_symbol_limit + 1)
                return false;
            prev_zero = false;
            if (symbol > max_symbol_limit)
                break;
        }

        const int max_val = 2 * threshold - 1 - remaining;
        uint32_t val = br.peek(nb_bits - 1);
        if (static_cast<int>(val) < max_val) {
            br.consume(nb_bits - 1);
        } else {
            val = br.peek(nb_bits);
            br.consume(nb_bits);
            if (static_cast<int>(val) >= threshold)
                val -= static_cast<uint32_t>(max_val);
        }

        const int prob = static_cast<int>(val) - 1;
        remaining -= (prob < 0) ? -prob : prob;
        if (remaining < 1)
            return false;
        norm[symbol++] = static_cast<int16_t>(prob);
        prev_zero = (prob == 0);

        while (remaining < threshold) {
            --nb_bits;
            threshold >>= 1;
        }
        if (br.bytes_consumed() > src_size)
            return false;
    }

    if (remaining != 1)
        return false;

    bytes_read = br.bytes_consumed();
    if (bytes_read > src_size)
        return false;
    return build_fse_table(norm.data(), symbol - 1, accuracy_log, out);
}

inline bool build_huffman_table(const uint8_t* weights, size_t num_weights, HuffTable& out) {
    if (num_weights == 0 || num_weights > 255)
        return false;
    uint32_t weight_sum = 0;
    for (size_t i = 0; i < num_weights; ++i) {
        if (weights[i] > 11)
            return false;
        if (weights[i] > 0)
            weight_sum += (1u << (weights[i] - 1));
    }
    if (weight_sum == 0)
        return false;

    const int max_bits = highest_bit32(weight_sum) + 1;
    if (max_bits > 11)
        return false;
    const uint32_t total = 1u << max_bits;
    const uint32_t left = total - weight_sum;
    if (left == 0 || (left & (left - 1)) != 0)
        return false;
    const uint8_t last_weight = static_cast<uint8_t>(highest_bit32(left) + 1);

    std::array<uint8_t, 256> all_weights{};
    std::memcpy(all_weights.data(), weights, num_weights);
    all_weights[num_weights] = last_weight;
    const size_t total_symbols = num_weights + 1;

    std::array<uint32_t, 13> rank_count{};
    for (size_t i = 0; i < total_symbols; ++i)
        rank_count[all_weights[i]]++;

    std::array<uint32_t, 13> rank_idx{};
    uint32_t next_idx = 0;
    for (int w = 1; w <= max_bits; ++w) {
        rank_idx[w] = next_idx;
        next_idx += rank_count[w] * (1u << (w - 1));
    }
    if (next_idx != total)
        return false;

    out.max_bits = max_bits;
    out.entries.assign(total, HuffEntry{});
    for (size_t s = 0; s < total_symbols; ++s) {
        const uint8_t w = all_weights[s];
        if (w == 0)
            continue;
        const uint8_t nb = static_cast<uint8_t>(max_bits + 1 - w);
        const uint32_t span = 1u << (w - 1);
        const uint32_t base = rank_idx[w];
        rank_idx[w] += span;
        for (uint32_t k = 0; k < span; ++k) {
            out.entries[base + k].symbol = static_cast<uint8_t>(s);
            out.entries[base + k].nb_bits = nb;
        }
    }
    return true;
}

inline bool read_huffman_table(const uint8_t* src, size_t src_size, HuffTable& out,
                               size_t& bytes_read) {
    if (src_size == 0)
        return false;
    const uint8_t header = src[0];
    std::array<uint8_t, 256> weights{};
    size_t num_weights = 0;

    if (header >= 128) {
        num_weights = static_cast<size_t>(header - 127);
        const size_t packed_bytes = (num_weights + 1) / 2;
        if (1 + packed_bytes > src_size)
            return false;
        for (size_t i = 0; i < num_weights; ++i) {
            const uint8_t b = src[1 + (i >> 1)];
            weights[i] = (i & 1) ? (b & 0x0Fu) : (b >> 4);
        }
        bytes_read = 1 + packed_bytes;
    } else {
        const size_t comp_size = header;
        if (1 + comp_size > src_size || comp_size == 0)
            return false;
        FseTable fse;
        size_t fse_hdr = 0;
        if (!read_fse_table(src + 1, comp_size, 12, 6, fse, fse_hdr))
            return false;
        if (fse_hdr >= comp_size)
            return false;
        RevBitReader br;
        if (!br.init(src + 1 + fse_hdr, comp_size - fse_hdr))
            return false;
        uint32_t s1 = br.read_bits(fse.accuracy_log);
        uint32_t s2 = br.read_bits(fse.accuracy_log);
        if (br.bits_left < 0)
            return false;

        while (num_weights < 255) {
            const FseEntry& e1 = fse.entries[s1];
            weights[num_weights++] = e1.symbol;
            s1 = e1.baseline + br.read_bits(e1.nb_bits);
            if (br.bits_left < 0) {
                if (num_weights < 255)
                    weights[num_weights++] = fse.entries[s2].symbol;
                break;
            }
            if (num_weights >= 255)
                break;
            const FseEntry& e2 = fse.entries[s2];
            weights[num_weights++] = e2.symbol;
            s2 = e2.baseline + br.read_bits(e2.nb_bits);
            if (br.bits_left < 0) {
                if (num_weights < 255)
                    weights[num_weights++] = fse.entries[s1].symbol;
                break;
            }
        }
        bytes_read = 1 + comp_size;
    }

    return build_huffman_table(weights.data(), num_weights, out);
}

inline bool decode_huff_stream(const uint8_t* src, size_t src_size, const HuffTable& huff,
                               uint8_t* dst, size_t dst_size) noexcept {
    RevBitReader br;
    if (!br.init(src, src_size))
        return false;
    const int max_bits = huff.max_bits;
    for (size_t i = 0; i < dst_size; ++i) {
        if (br.bits_left <= 0)
            return false;
        const uint32_t code = br.peek_bits(max_bits);
        const HuffEntry& e = huff.entries[code];
        dst[i] = e.symbol;
        br.bits_left -= e.nb_bits;
    }
    return br.bits_left == 0;
}

inline void build_predefined_tables(FseTable& ll, FseTable& of, FseTable& ml) {
    static constexpr int16_t kLLNorm[36] = {4, 3, 2, 2, 2, 2, 2, 2, 2,  2,  2,  2,
                                            2, 1, 1, 1, 2, 2, 2, 2, 2,  2,  2,  2,
                                            2, 3, 2, 1, 1, 1, 1, 1, -1, -1, -1, -1};
    static constexpr int16_t kOFNorm[29] = {1, 1, 1, 1, 1, 1, 2, 2, 2, 1,  1,  1,  1,  1, 1,
                                            1, 1, 1, 1, 1, 1, 1, 1, 1, -1, -1, -1, -1, -1};
    static constexpr int16_t kMLNorm[53] = {
        1, 4, 3, 2, 2, 2, 2, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,  1,  1,  1,  1,  1,  1, 1,
        1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, -1, -1, -1, -1, -1, -1, -1};
    build_fse_table(kLLNorm, 35, 6, ll);
    build_fse_table(kOFNorm, 28, 5, of);
    build_fse_table(kMLNorm, 52, 6, ml);
}

static constexpr uint32_t kLLBaseline[36] = {
    0,  1,  2,  3,  4,  5,  6,  7,  8,   9,   10,  11,   12,   13,   14,   15,    16,    18,
    20, 22, 24, 28, 32, 40, 48, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65536};
static constexpr uint8_t kLLBits[36] = {0, 0, 0, 0, 0, 0,  0,  0,  0,  0,  0,  0,
                                        0, 0, 0, 0, 1, 1,  1,  1,  2,  2,  3,  3,
                                        4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16};

static constexpr uint32_t kMLBaseline[53] = {
    3,  4,  5,  6,  7,  8,  9,  10,  11,  12,  13,   14,   15,   16,   17,    18,    19,   20,
    21, 22, 23, 24, 25, 26, 27, 28,  29,  30,  31,   32,   33,   34,   35,    37,    39,   41,
    43, 47, 51, 59, 67, 83, 99, 131, 259, 515, 1027, 2051, 4099, 8195, 16387, 32771, 65539};
static constexpr uint8_t kMLBits[53] = {0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,  0,  0,  0,  0,  0,  0, 0,
                                        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,  0,  0,  0,  1,  1,  1, 1,
                                        2, 2, 3, 3, 4, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16};

struct FrameState {
    HuffTable huff;
    FseTable ll_tbl, of_tbl, ml_tbl;
    FseTable pre_ll, pre_of, pre_ml;
    uint32_t rep[3] = {1, 4, 8};
    std::vector<uint8_t> literals;

    FrameState() { build_predefined_tables(pre_ll, pre_of, pre_ml); }
};

inline bool decode_compressed_block(const uint8_t* src, size_t src_size, FrameState& st,
                                    uint8_t* dst_base, size_t& dst_pos, size_t dst_cap,
                                    std::string* err) {
    const uint8_t* p = src;
    const uint8_t* const end = src + src_size;
    if (p >= end)
        return false;

    // 1) Literals section
    const uint8_t b0 = *p;
    const uint8_t lit_type = b0 & 3;
    const uint8_t size_fmt = (b0 >> 2) & 3;
    size_t regen_size = 0;
    size_t comp_size = 0;
    int num_streams = 1;

    if (lit_type == 0 || lit_type == 1) {
        if (size_fmt == 0 || size_fmt == 2) {
            regen_size = b0 >> 3;
            p += 1;
        } else if (size_fmt == 1) {
            if (static_cast<size_t>(end - p) < 2)
                return false;
            regen_size = read_u16_le(p) >> 4;
            p += 2;
        } else {
            if (static_cast<size_t>(end - p) < 3)
                return false;
            regen_size = (static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) |
                          (static_cast<uint32_t>(p[2]) << 16)) >>
                         4;
            p += 3;
        }

        st.literals.resize(regen_size);
        if (lit_type == 0) {
            if (static_cast<size_t>(end - p) < regen_size)
                return false;
            if (regen_size > 0)
                std::memcpy(st.literals.data(), p, regen_size);
            p += regen_size;
        } else {
            if (p >= end)
                return false;
            const uint8_t rle_byte = *p++;
            if (regen_size > 0)
                std::memset(st.literals.data(), rle_byte, regen_size);
        }
    } else {
        if (size_fmt == 0 || size_fmt == 1) {
            if (static_cast<size_t>(end - p) < 3)
                return false;
            const uint32_t h = static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) |
                               (static_cast<uint32_t>(p[2]) << 16);
            regen_size = (h >> 4) & 0x3FFu;
            comp_size = (h >> 14) & 0x3FFu;
            num_streams = (size_fmt == 0) ? 1 : 4;
            p += 3;
        } else if (size_fmt == 2) {
            if (static_cast<size_t>(end - p) < 4)
                return false;
            const uint32_t h = read_u32_le(p);
            regen_size = (h >> 4) & 0x3FFFu;
            comp_size = (h >> 18) & 0x3FFFu;
            num_streams = 4;
            p += 4;
        } else {
            if (static_cast<size_t>(end - p) < 5)
                return false;
            const uint64_t h =
                static_cast<uint64_t>(read_u32_le(p)) | (static_cast<uint64_t>(p[4]) << 32);
            regen_size = static_cast<size_t>((h >> 4) & 0x3FFFFu);
            comp_size = static_cast<size_t>((h >> 22) & 0x3FFFFu);
            num_streams = 4;
            p += 5;
        }

        if (static_cast<size_t>(end - p) < comp_size)
            return false;
        const uint8_t* const lit_end = p + comp_size;

        if (lit_type == 2) {
            size_t tree_bytes = 0;
            if (!read_huffman_table(p, comp_size, st.huff, tree_bytes)) {
                if (err)
                    *err = "ZSTD invalid Huffman table";
                return false;
            }
            p += tree_bytes;
        } else if (!st.huff.valid()) {
            if (err)
                *err = "ZSTD treeless literals without prior Huffman table";
            return false;
        }

        st.literals.resize(regen_size);
        const size_t stream_bytes = static_cast<size_t>(lit_end - p);
        if (num_streams == 1) {
            if (!decode_huff_stream(p, stream_bytes, st.huff, st.literals.data(), regen_size)) {
                if (err)
                    *err = "ZSTD 1-stream Huffman decode failed";
                return false;
            }
        } else {
            if (stream_bytes < 6)
                return false;
            const size_t s1 = read_u16_le(p);
            const size_t s2 = read_u16_le(p + 2);
            const size_t s3 = read_u16_le(p + 4);
            if (6 + s1 + s2 + s3 > stream_bytes)
                return false;
            const size_t s4 = stream_bytes - 6 - s1 - s2 - s3;
            const size_t o1 = (regen_size + 3) / 4;
            if (o1 * 3 > regen_size)
                return false;
            const size_t o4 = regen_size - o1 * 3;
            const uint8_t* sp = p + 6;
            if (!decode_huff_stream(sp, s1, st.huff, st.literals.data(), o1) ||
                !decode_huff_stream(sp + s1, s2, st.huff, st.literals.data() + o1, o1) ||
                !decode_huff_stream(sp + s1 + s2, s3, st.huff, st.literals.data() + o1 * 2, o1) ||
                !decode_huff_stream(sp + s1 + s2 + s3, s4, st.huff, st.literals.data() + o1 * 3,
                                    o4)) {
                if (err)
                    *err = "ZSTD 4-stream Huffman decode failed";
                return false;
            }
        }
        p = lit_end;
    }

    // 2) Sequences section
    if (p >= end)
        return false;
    uint32_t num_seq = *p++;
    if (num_seq >= 128) {
        if (num_seq < 255) {
            if (p >= end)
                return false;
            num_seq = ((num_seq - 128u) << 8) + (*p++);
        } else {
            if (static_cast<size_t>(end - p) < 2)
                return false;
            num_seq = static_cast<uint32_t>(p[0]) + (static_cast<uint32_t>(p[1]) << 8) + 0x7F00u;
            p += 2;
        }
    }

    size_t lit_pos = 0;
    if (num_seq > 0) {
        if (p >= end)
            return false;
        const uint8_t modes = *p++;
        if ((modes & 3) != 0)
            return false;

        const auto load_seq_table = [&](uint8_t mode, const FseTable& predef, int max_sym,
                                        int max_log, FseTable& tbl) -> bool {
            if (mode == 0) {
                tbl = predef;
                return true;
            }
            if (mode == 1) {
                if (p >= end)
                    return false;
                return build_rle_fse_table(*p++, tbl);
            }
            if (mode == 2) {
                size_t used = 0;
                if (!read_fse_table(p, static_cast<size_t>(end - p), max_sym, max_log, tbl, used))
                    return false;
                p += used;
                return true;
            }
            return tbl.valid();
        };

        if (!load_seq_table((modes >> 6) & 3, st.pre_ll, 35, 9, st.ll_tbl) ||
            !load_seq_table((modes >> 4) & 3, st.pre_of, 31, 8, st.of_tbl) ||
            !load_seq_table((modes >> 2) & 3, st.pre_ml, 52, 9, st.ml_tbl)) {
            if (err)
                *err = "ZSTD invalid sequence FSE table";
            return false;
        }

        RevBitReader br;
        if (!br.init(p, static_cast<size_t>(end - p))) {
            if (err)
                *err = "ZSTD invalid sequence bitstream";
            return false;
        }

        uint32_t ll_state = br.read_bits(st.ll_tbl.accuracy_log);
        uint32_t of_state = br.read_bits(st.of_tbl.accuracy_log);
        uint32_t ml_state = br.read_bits(st.ml_tbl.accuracy_log);
        if (br.bits_left < 0)
            return false;

        for (uint32_t i = 0; i < num_seq; ++i) {
            const FseEntry& lle = st.ll_tbl.entries[ll_state];
            const FseEntry& ofe = st.of_tbl.entries[of_state];
            const FseEntry& mle = st.ml_tbl.entries[ml_state];

            const uint8_t ll_code = lle.symbol;
            const uint8_t of_code = ofe.symbol;
            const uint8_t ml_code = mle.symbol;
            if (ll_code > 35 || ml_code > 52 || of_code > 31)
                return false;

            const uint32_t of_extra = br.read_bits(of_code);
            const uint32_t ml_extra = br.read_bits(kMLBits[ml_code]);
            const uint32_t ll_extra = br.read_bits(kLLBits[ll_code]);

            const uint32_t ll = kLLBaseline[ll_code] + ll_extra;
            const uint32_t ml = kMLBaseline[ml_code] + ml_extra;

            if (i + 1 < num_seq) {
                ll_state = lle.baseline + br.read_bits(lle.nb_bits);
                ml_state = mle.baseline + br.read_bits(mle.nb_bits);
                of_state = ofe.baseline + br.read_bits(ofe.nb_bits);
            }
            if (br.bits_left < 0) {
                if (err)
                    *err = "ZSTD sequence bitstream underrun";
                return false;
            }

            uint32_t offset = 0;
            if (of_code > 1) {
                offset = ((1u << of_code) + of_extra) - 3u;
                st.rep[2] = st.rep[1];
                st.rep[1] = st.rep[0];
                st.rep[0] = offset;
            } else {
                uint32_t of_val = (1u << of_code) + of_extra;
                if (ll == 0)
                    ++of_val;
                if (of_val == 1) {
                    offset = st.rep[0];
                } else if (of_val == 2) {
                    offset = st.rep[1];
                    st.rep[1] = st.rep[0];
                    st.rep[0] = offset;
                } else if (of_val == 3) {
                    offset = st.rep[2];
                    st.rep[2] = st.rep[1];
                    st.rep[1] = st.rep[0];
                    st.rep[0] = offset;
                } else {
                    if (st.rep[0] <= 1)
                        return false;
                    offset = st.rep[0] - 1u;
                    st.rep[2] = st.rep[1];
                    st.rep[1] = st.rep[0];
                    st.rep[0] = offset;
                }
            }

            if (lit_pos + ll > st.literals.size() || dst_pos + ll + ml > dst_cap)
                return false;
            if (ll > 0) {
                std::memcpy(dst_base + dst_pos, st.literals.data() + lit_pos, ll);
                lit_pos += ll;
                dst_pos += ll;
            }
            if (offset == 0 || offset > dst_pos)
                return false;
            const size_t match_src = dst_pos - offset;
            if (offset >= ml) {
                std::memcpy(dst_base + dst_pos, dst_base + match_src, ml);
            } else {
                for (uint32_t k = 0; k < ml; ++k)
                    dst_base[dst_pos + k] = dst_base[match_src + k];
            }
            dst_pos += ml;
        }
    }

    const size_t rem_lit = st.literals.size() - lit_pos;
    if (dst_pos + rem_lit > dst_cap)
        return false;
    if (rem_lit > 0) {
        std::memcpy(dst_base + dst_pos, st.literals.data() + lit_pos, rem_lit);
        dst_pos += rem_lit;
    }
    return true;
}

} // namespace zstd_impl

inline bool zstd_decompress_exact(const uint8_t* src, size_t src_size, uint8_t* dst,
                                  size_t dst_size, std::string* err = nullptr) {
    size_t pos = 0;
    size_t out_pos = 0;

    while (pos < src_size) {
        if (src_size - pos < 4) {
            if (err)
                *err = "ZSTD truncated frame header";
            return false;
        }
        const uint32_t magic = read_u32_le(src + pos);
        pos += 4;

        if ((magic & 0xFFFFFFF0u) == 0x184D2A50u) {
            if (src_size - pos < 4)
                return false;
            const uint32_t skip = read_u32_le(src + pos);
            pos += 4;
            if (skip > src_size - pos)
                return false;
            pos += skip;
            continue;
        }

        if (magic != 0xFD2FB528u) {
            if (err)
                *err = "ZSTD invalid frame magic";
            return false;
        }
        if (pos >= src_size)
            return false;

        const uint8_t fhd = src[pos++];
        const uint8_t fcs_flag = (fhd >> 6) & 3;
        const bool single_segment = ((fhd >> 5) & 1) != 0;
        const bool content_checksum = ((fhd >> 2) & 1) != 0;
        const uint8_t dict_id_flag = fhd & 3;
        if (((fhd >> 3) & 1) != 0)
            return false;

        if (!single_segment) {
            if (pos >= src_size)
                return false;
            ++pos; // Window_Descriptor
        }

        const size_t dict_bytes = (dict_id_flag == 3) ? 4u : static_cast<size_t>(dict_id_flag);
        if (dict_bytes > src_size - pos)
            return false;
        uint32_t dict_id = 0;
        for (size_t i = 0; i < dict_bytes; ++i)
            dict_id |= static_cast<uint32_t>(src[pos + i]) << (i * 8);
        pos += dict_bytes;
        if (dict_id != 0) {
            if (err)
                *err = "ZSTD dictionaries are unsupported";
            return false;
        }

        size_t fcs_bytes = 0;
        if (fcs_flag == 0)
            fcs_bytes = single_segment ? 1 : 0;
        else if (fcs_flag == 1)
            fcs_bytes = 2;
        else if (fcs_flag == 2)
            fcs_bytes = 4;
        else
            fcs_bytes = 8;
        if (fcs_bytes > src_size - pos)
            return false;
        pos += fcs_bytes;

        zstd_impl::FrameState st;
        bool last_block = false;
        while (!last_block) {
            if (src_size - pos < 3)
                return false;
            const uint32_t bh = static_cast<uint32_t>(src[pos]) |
                                (static_cast<uint32_t>(src[pos + 1]) << 8) |
                                (static_cast<uint32_t>(src[pos + 2]) << 16);
            pos += 3;
            last_block = (bh & 1u) != 0;
            const uint32_t block_type = (bh >> 1) & 3u;
            const uint32_t block_size = bh >> 3;

            if (block_type == 0) {
                if (block_size > src_size - pos || block_size > dst_size - out_pos)
                    return false;
                if (block_size > 0)
                    std::memcpy(dst + out_pos, src + pos, block_size);
                pos += block_size;
                out_pos += block_size;
            } else if (block_type == 1) {
                if (pos >= src_size || block_size > dst_size - out_pos)
                    return false;
                const uint8_t b = src[pos++];
                if (block_size > 0)
                    std::memset(dst + out_pos, b, block_size);
                out_pos += block_size;
            } else if (block_type == 2) {
                if (block_size > src_size - pos)
                    return false;
                if (!zstd_impl::decode_compressed_block(src + pos, block_size, st, dst, out_pos,
                                                        dst_size, err))
                    return false;
                pos += block_size;
            } else {
                if (err)
                    *err = "ZSTD reserved block type";
                return false;
            }
        }

        if (content_checksum) {
            if (src_size - pos < 4)
                return false;
            pos += 4;
        }
    }

    if (out_pos != dst_size) {
        if (err)
            *err = "ZSTD decoded size mismatch";
        return false;
    }
    return true;
}

} // namespace dolly::map_grid::detail
