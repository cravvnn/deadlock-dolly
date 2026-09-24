#pragma once
// Attach camera runtime provider (POV / weapon). Included from bridge_win.cpp
// inside its anonymous namespace after the checked-memory helpers, the compat
// runtime and the editor API.
//
// The worker thread resolves one target per command with a bounded read-only
// entity walk; the render callback re-validates identity with a few small
// reads and never allocates, locks or walks the entity list. Nothing here
// writes game memory. A missing target, moved pointer or unreadable pose fails
// closed with a static message instead of rendering a guessed camera.

#include <cmath>

namespace attach_runtime {

struct Offsets {
    std::uint32_t scene_node = 0, owner = 0, origin = 0, angles = 0, view_offset = 0,
                  eye_angles = 0, child = 0, sibling = 0;
};

struct Cache {
    bool ready = false;
    dolly::AttachResolution resolution;
    std::uintptr_t pawn = 0, identity = 0, node = 0, name_pointer = 0;
    std::uint32_t name_offset = 0, handle = 0, entity_index = 0;
    std::uint64_t model = 0;
    // Weapon point: resolved bone index and its per-frame transform array.
    std::uintptr_t bone_array = 0;
    std::uintptr_t model_handle_slot = 0, model_handle = 0, model_object = 0, model_state = 0;
    std::uint32_t bone_count = 0;
    std::uint32_t bone_index = 0;
    bool bone_ready = false;
    dolly::EditorBones bones{};
    std::shared_ptr<dolly::PickerCatalog> picker;
    const char* error = nullptr;
};

inline Offsets offsets_from(const EditorAttachConfig& config) noexcept {
    Offsets offsets;
    offsets.scene_node = config.offsets[0];
    offsets.owner = config.offsets[1];
    offsets.origin = config.offsets[2];
    offsets.angles = config.offsets[3];
    offsets.view_offset = config.offsets[4];
    offsets.eye_angles = config.offsets[5];
    offsets.child = config.offsets[6];
    offsets.sibling = config.offsets[7];
    return offsets;
}

// FNV-1a 64 over a model path, matching dolly.native_effects.model_token.
inline std::uint64_t model_token(const char* text) noexcept {
    std::uint64_t value = 14695981039346656037ull;
    for (const unsigned char* p = reinterpret_cast<const unsigned char*>(text); *p; ++p) {
        value ^= *p;
        value *= 1099511628211ull;
    }
    return value;
}

inline bool text_looks_like_model(const char* text) noexcept {
    if (std::strncmp(text, "models/", 7) != 0)
        return false;
    std::size_t length = 0;
    while (length < 255 && text[length]) {
        const unsigned char c = static_cast<unsigned char>(text[length]);
        if (c < 32 || c > 126)
            return false;
        ++length;
    }
    return length >= 11 && length < 255 && std::strcmp(text + length - 5, ".vmdl") == 0;
}

struct PlayerEntity {
    std::uintptr_t instance = 0, identity = 0;
    std::uint32_t handle = 0;
};

inline bool read_class_name(std::uintptr_t identity, char* out, std::size_t capacity) noexcept {
    std::uintptr_t pointer = 0;
    if (!capacity || !read_value(identity + 0x20, pointer) || !pointer)
        return false;
    unsigned char raw[64]{};
    if (!read_memory(pointer, raw, sizeof(raw) - 1))
        return false;
    std::memcpy(out, raw, capacity - 1);
    out[capacity - 1] = 0;
    return true;
}

inline bool identity_list_has_player(std::uintptr_t entity_system, unsigned head) noexcept {
    std::uintptr_t identity = 0;
    if (!read_value(entity_system + head, identity))
        return false;
    for (unsigned step = 0; step < 64 && identity; ++step) {
        char name[64]{};
        if (read_class_name(identity, name, sizeof(name)) && std::strcmp(name, "player") == 0)
            return true;
        if (!read_value(identity + 0x58, identity))
            return false;
    }
    return false;
}

inline bool locate_entity_system(HMODULE client, std::uintptr_t& out) noexcept {
    if (!client)
        return false;
    const std::uintptr_t base = reinterpret_cast<std::uintptr_t>(client);
    IMAGE_DOS_HEADER dos{};
    IMAGE_NT_HEADERS64 nt{};
    if (!read_value(base, dos) || dos.e_magic != IMAGE_DOS_SIGNATURE || dos.e_lfanew <= 0 ||
        !read_value(base + dos.e_lfanew, nt) || nt.Signature != IMAGE_NT_SIGNATURE)
        return false;
    const std::uintptr_t size = nt.OptionalHeader.SizeOfImage;
    const std::uintptr_t vtable = compat_detail::locate_vtable(client, "CGameEntitySystem");
    if (!vtable)
        return false;
    constexpr std::uintptr_t previous_rva = 0x391EDA8, window = 0x40000;
    auto valid = [&](std::uintptr_t candidate) {
        std::uintptr_t first = 0;
        return candidate && read_value(candidate, first) && first == vtable;
    };
    std::uintptr_t previous = 0;
    if (read_value(base + previous_rva, previous) && valid(previous)) {
        out = previous;
        return true;
    }
    const std::uintptr_t first = base + (previous_rva > window ? previous_rva - window : 0);
    const std::uintptr_t last = base + std::min<std::uintptr_t>(size, previous_rva + window);
    std::uintptr_t fallback = 0;
    for (std::uintptr_t address = first; address + 8 <= last; address += 8) {
        const std::uintptr_t value = *reinterpret_cast<const std::uintptr_t*>(address);
        if (value < 0x10000000000ull || value > 0x7f0000000000ull || (value & 7) ||
            (value >= base && value < base + size) || !valid(value))
            continue;
        if (identity_list_has_player(value, 0x210) || identity_list_has_player(value, 0x230)) {
            out = value;
            return true;
        }
        if (!fallback)
            fallback = value;
    }
    if (fallback) {
        out = fallback;
        return true;
    }
    return false;
}

inline bool walk_players(std::uintptr_t entity_system, std::vector<PlayerEntity>& out) noexcept {
    out.clear();
    std::vector<std::uintptr_t> seen;
    for (unsigned head : {0x210u, 0x230u}) {
        std::uintptr_t identity = 0;
        if (!read_value(entity_system + head, identity))
            continue;
        while (identity) {
            if (seen.size() >= 20000)
                return false;
            bool duplicate = false;
            for (std::uintptr_t visited : seen)
                if (visited == identity) {
                    duplicate = true;
                    break;
                }
            if (duplicate)
                break;
            seen.push_back(identity);
            std::uintptr_t instance = 0, backlink = 0;
            if (!read_value(identity, instance) || !instance ||
                !read_value(instance + 0x10, backlink) || backlink != identity) {
                if (!read_value(identity + 0x58, identity))
                    break;
                continue;
            }
            char name[64]{};
            if (read_class_name(identity, name, sizeof(name)) && std::strcmp(name, "player") == 0) {
                PlayerEntity player;
                player.instance = instance;
                player.identity = identity;
                read_value(identity + 0x10, player.handle);
                out.push_back(player);
            }
            if (!read_value(identity + 0x58, identity))
                break;
        }
    }
    return !out.empty();
}

inline bool discover_model_name(std::uintptr_t node, std::uint32_t& offset, std::uintptr_t& pointer,
                                std::uint64_t& token) noexcept {
    for (std::uintptr_t candidate = 0x100; candidate + 8 <= 0x400; candidate += 8) {
        std::uintptr_t value = 0;
        if (!read_value(node + candidate, value) || value < 0x10000)
            continue;
        char text[128]{};
        if (!read_memory(value, text, sizeof(text) - 1))
            continue;
        text[sizeof(text) - 1] = 0;
        if (!text_looks_like_model(text))
            continue;
        offset = std::uint32_t(candidate);
        pointer = value;
        token = model_token(text);
        return true;
    }
    return false;
}

// ---------------------------------------------------------------------------
// Weapon point: resolve a bone name (weapon_bone_R and fallbacks) to the
// selected model's pose transform index. Resolve the resource through that
// model's own binding and verify its name before reading its complete skeleton.
// The current pose buffer can include merged bones beyond the base skeleton.
// Resolution runs on the worker; the callback validates cached identities and
// reads one 32-byte transform per frame.
// ---------------------------------------------------------------------------

inline bool read_c_string(std::uintptr_t address, char* out, std::size_t capacity) noexcept {
    if (!out || capacity < 2)
        return false;
    out[0] = 0;
    // Bone wire names hold at most 63 characters plus NUL. Never copy a
    // caller-sized range from a smaller stack buffer, or accept a truncated
    // valid-looking prefix of an invalid string.
    unsigned char raw[64]{};
    const auto size = (std::min)(capacity, sizeof(raw));
    if (!address)
        return false;
    const bool block_read = read_memory(address, raw, size);
    for (std::size_t length = 0; length < size; ++length) {
        // A short NUL-terminated string may end immediately before an
        // inaccessible page. Do not require readable padding past its NUL.
        if (!block_read && !read_value(address + length, raw[length]))
            return false;
        if (raw[length] == 0) {
            if (!length)
                return false;
            std::memcpy(out, raw, length + 1);
            return true;
        }
        if (raw[length] < 32 || raw[length] > 126)
            return false;
    }
    return false;
}

inline bool valid_bone_name(const char* text) noexcept {
    if (!text || !text[0])
        return false;
    if (std::strncmp(text, "$cloth_m", 8) == 0) {
        const char* p = text + 8;
        if (*p < '0' || *p > '9')
            return false;
        while (*p >= '0' && *p <= '9')
            ++p;
        if (*p++ != 'p' || *p < '0' || *p > '9')
            return false;
        while (*p >= '0' && *p <= '9')
            ++p;
        return *p == 0;
    }
    for (const char* p = text; *p; ++p) {
        const char c = *p;
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
              c == '_' || c == '.'))
            return false;
    }
    return true;
}

inline unsigned skeleton_name_score(std::uintptr_t data, std::uint32_t entries,
                                    std::uint32_t stride) noexcept;

// Rank all candidate headers within the existing bounded object window.
// A reduced physics skeleton may precede the render skeleton in one object.
inline bool find_bone_vector(std::uintptr_t object, std::uintptr_t& data, std::uint32_t& entries,
                             std::uint32_t& entry_stride) noexcept {
    unsigned best_score = 0;
    bool found = false;
    unsigned name_budget = 4096; // Shared across candidate headers in this object.
    for (int pass = 0; pass < 2; ++pass) {
        const std::uint32_t stride = pass == 0 ? 8 : 16;
        for (std::uint32_t offset = 0; offset + 0x18 <= 0x600; offset += 8) {
            std::uintptr_t pointer = 0;
            // Count fields are 32-bit. Adjacent bytes can be nonzero in
            // live resource objects; reading QWORDs made valid models fail
            // depending on those unrelated bytes.
            std::uint32_t count = 0, capacity = 0;
            if (!read_value(object + offset, pointer) || !read_value(object + offset + 8, count) ||
                !read_value(object + offset + 0x10, capacity))
                continue;
            if (count < 16 || count > 4096 || capacity < count || capacity > 200000)
                continue;
            if (pointer < 0x10000 || pointer > 0x7fffffffffff || (pointer & 7))
                continue;
            std::uint32_t index = 0;
            for (; index < count && name_budget; ++index) {
                --name_budget;
                std::uintptr_t text = 0;
                char name[65]{};
                if (!read_value(pointer + index * stride, text) ||
                    !read_c_string(text, name, sizeof(name)) || !valid_bone_name(name))
                    break;
                // Reviewed render skeletons begin with root_motion. Reduced
                // physics/attachment tables use different index spaces and must
                // not be paired with the render pose array by name alone.
                if (index == 0 && std::strcmp(name, "root_motion") != 0)
                    break;
                if (stride == 16) {
                    std::uint32_t length = 0;
                    if (!read_value(pointer + index * stride + 8, length) || length < 1 ||
                        length > 96)
                        break;
                }
            }
            if (index != count)
                continue;
            const unsigned score =
                skeleton_name_score(pointer, static_cast<std::uint32_t>(count), stride);
            if (score &&
                (!found || score > best_score || (score == best_score && count > entries))) {
                data = pointer;
                entries = static_cast<std::uint32_t>(count);
                entry_stride = stride;
                best_score = score;
                found = true;
            }
        }
    }
    return found;
}

struct VectorHit {
    std::uintptr_t object = 0, data = 0;
    std::uint32_t entries = 0, stride = 8;
};

inline const char* const* weapon_bone_candidates(unsigned& count) noexcept {
    static const char* const kNames[] = {"weapon_bone_R", "weapon_bone_L", "weaponHand_R",
                                         "weaponHand_L",  "hand_R",        "hand_L"};
    count = sizeof(kNames) / sizeof(kNames[0]);
    return kNames;
}

// How many skeleton markers a vector names: the bone list scores high, while
// attachment/prop lists score low or zero.
inline unsigned skeleton_name_score(std::uintptr_t data, std::uint32_t entries,
                                    std::uint32_t stride) noexcept {
    static const char* const kMarkers[] = {"pelvis", "spine_0", "neck_0",      "head",
                                           "hand_L", "hand_R",  "arm_upper_L", "leg_upper_L"};
    unsigned score = 0;
    bool seen[8]{};
    const std::uint32_t limit = entries < 256 ? entries : 256;
    for (std::uint32_t entry = 0; entry < limit; ++entry) {
        std::uintptr_t text = 0;
        char name[65]{};
        if (!read_value(data + entry * stride, text) || !read_c_string(text, name, sizeof(name)))
            continue;
        for (unsigned marker = 0; marker < 8; ++marker)
            if (!seen[marker] && std::strcmp(name, kMarkers[marker]) == 0) {
                seen[marker] = true;
                ++score;
                break;
            }
    }
    return score;
}

// Pose transform array near the model state: transform 0 must sit on the node
// origin (the same gate the field verification used).
inline bool locate_bone_array(const Offsets& offsets, std::uintptr_t node,
                              std::uintptr_t model_state, std::uintptr_t& out) noexcept {
    float origin[3]{};
    if (!read_memory(node + offsets.origin, origin, sizeof(origin)))
        return false;
    for (std::uintptr_t candidate = 0x40; candidate + 8 <= 0x140; candidate += 8) {
        std::uintptr_t pointer = 0;
        if (!read_value(model_state + candidate, pointer) || pointer < 0x10000 ||
            pointer > 0x7fffffffffff || (pointer & 7))
            continue;
        float first[8]{};
        if (!read_memory(pointer, first, sizeof(first)))
            continue;
        const double dx = first[0] - origin[0], dy = first[1] - origin[1],
                     dz = first[2] - origin[2];
        if (dx * dx + dy * dy + dz * dz > 25.0)
            continue;
        const double scale = first[3];
        const double norm = std::sqrt(double(first[4]) * first[4] + double(first[5]) * first[5] +
                                      double(first[6]) * first[6] + double(first[7]) * first[7]);
        if (!(scale > 0.01 && scale < 100.0) || !(norm > 0.5 && norm < 1.5))
            continue;
        out = pointer;
        return true;
    }
    return false;
}

[[maybe_unused]] inline void quat_to_angles(const float* transform,
                                            std::array<double, 3>& out) noexcept {
    double x = transform[4], y = transform[5], z = transform[6], w = transform[7];
    const double norm = std::sqrt(x * x + y * y + z * z + w * w);
    if (norm < 1e-6) {
        x = y = z = 0;
        w = 1;
    } else {
        x /= norm;
        y /= norm;
        z /= norm;
        w /= norm;
    }
    const double r00 = 1 - 2 * (y * y + z * z), r10 = 2 * (x * y + w * z),
                 r20 = 2 * (x * z - w * y), r21 = 2 * (y * z + w * x),
                 r22 = 1 - 2 * (x * x + y * y);
    constexpr double pi = 3.14159265358979323846;
    out[0] = -std::asin(std::fmin(1.0, std::fmax(-1.0, r20))) * 180.0 / pi;
    out[1] = std::atan2(r10, r00) * 180.0 / pi;
    out[2] = std::atan2(-r21, r22) * 180.0 / pi;
}

// Worker thread: resolve the attach bone for a target whose node is known.
// Weapon points pick the best-ranked candidate bone; bone points match the
// authored 64-bit name hash against the skeleton name vector.
inline bool resolve_bone(const Offsets& offsets, std::uintptr_t node,
                         const dolly::AttachSegment& segment, Cache& cache, const char*& error,
                         bool collect_picker = false) noexcept {
    // The reviewed CModelState stores the resource binding immediately before
    // its model-name field (0xa0/0xa8). The binding's first pointer is CModel;
    // CModel's name at +8 must identify the SAME model before inspecting its
    // skeleton. Never walk neighboring bindings or arbitrary nested pointers.
    if (cache.name_offset < 0xa8) {
        error = "The selected model resource layout is unavailable. Choose Eyes.";
        return false;
    }
    const std::uintptr_t model_state = node + cache.name_offset - 0xa8;
    std::uintptr_t binding = 0, object = 0, resource_name = 0;
    char resource_path[256]{};
    if (!read_value(model_state + 0xa0, binding) || !binding || !read_value(binding, object) ||
        !object || !read_value(object + 8, resource_name) ||
        !read_memory(resource_name, resource_path, sizeof(resource_path) - 1) ||
        !text_looks_like_model(resource_path) || model_token(resource_path) != cache.model) {
        error = "The selected model resource could not be verified. Choose Eyes.";
        return false;
    }
    VectorHit hit{};
    if (!find_bone_vector(object, hit.data, hit.entries, hit.stride)) {
        error = "No complete render skeleton was found for this model. Choose Eyes.";
        return false;
    }
    cache.model_handle_slot = model_state + 0xa0;
    cache.model_handle = binding;
    cache.model_object = object;
    cache.model_state = model_state;
    unsigned candidate_count = 0;
    const char* const* candidates = weapon_bone_candidates(candidate_count);
    cache.bones.total = hit.entries;
    for (std::uint32_t entry = 0; entry < hit.entries && cache.bones.count < kEditorBoneCount;
         ++entry) {
        std::uintptr_t text = 0;
        char name[65]{};
        if (!read_value(hit.data + entry * hit.stride, text) ||
            !read_c_string(text, name, sizeof(name)) || !valid_bone_name(name) ||
            !((name[0] >= 'A' && name[0] <= 'Z') || (name[0] >= 'a' && name[0] <= 'z') ||
              name[0] == '_' || name[0] == '$'))
            continue;
        std::memcpy(cache.bones.names[cache.bones.count++], name, std::strlen(name));
    }
    std::uint32_t index = 0;
    bool found = false;
    if (segment.point == dolly::AttachPoint::bone) {
        for (std::uint32_t entry = 0; entry < hit.entries && !found; ++entry) {
            std::uintptr_t text = 0;
            char name[65]{};
            if (!read_value(hit.data + entry * hit.stride, text) ||
                !read_c_string(text, name, sizeof(name)))
                continue;
            if (model_token(name) == segment.bone_hash) {
                index = entry;
                found = true;
            }
        }
        if (!found) {
            error = "The attach bone name was not found on this model.";
            return false;
        }
    } else if (segment.point != dolly::AttachPoint::eyes) {
        std::uint32_t best_rank = candidate_count;
        for (std::uint32_t entry = 0; entry < hit.entries; ++entry) {
            std::uintptr_t text = 0;
            char name[65]{};
            if (!read_value(hit.data + entry * hit.stride, text) ||
                !read_c_string(text, name, sizeof(name)))
                continue;
            for (unsigned candidate = 0; candidate < candidate_count; ++candidate)
                if (std::strcmp(name, candidates[candidate]) == 0) {
                    if (candidate < best_rank) {
                        index = entry;
                        best_rank = candidate;
                    }
                    break;
                }
        }
        if (best_rank == candidate_count) {
            error = "The attach weapon point found no weapon bone on this model.";
            return false;
        }
    }
    std::uintptr_t bone_array = 0;
    if (!locate_bone_array(offsets, node, model_state, bone_array)) {
        error = "The attach weapon point could not locate the target pose transforms.";
        return false;
    }
    std::uintptr_t current_array = 0;
    std::uint32_t pose_count = 0;
    if (!read_value(model_state + 0x80, current_array) || current_array != bone_array ||
        !read_value(model_state + 0x90, pose_count) || pose_count < hit.entries ||
        pose_count > 4096) {
        error = "The model skeleton does not match its current pose buffer. Choose Eyes.";
        return false;
    }
    cache.bone_count = pose_count;
    cache.bone_array = bone_array;
    cache.bone_index = index;
    cache.bone_ready = true;
    if (collect_picker) {
        try {
            auto catalog = std::make_shared<dolly::PickerCatalog>();
            catalog->total = hit.entries;
            catalog->handle = cache.handle;
            catalog->entity = cache.entity_index;
            catalog->model = cache.model;
            catalog->bones.reserve(hit.entries);
            for (std::uint32_t entry = 0; entry < hit.entries; ++entry) {
                std::uintptr_t text = 0;
                dolly::PickerBone bone{};
                bone.source_index = entry;
                if (!read_value(hit.data + entry * hit.stride, text) ||
                    !read_c_string(text, bone.name, sizeof(bone.name)) ||
                    !valid_bone_name(bone.name)) {
                    error = "The full bone catalog changed while it was read. Reopen the picker.";
                    return false;
                }
                // These are the same serializable names accepted by AttachKey.
                if ((bone.name[0] >= 'A' && bone.name[0] <= 'Z') ||
                    (bone.name[0] >= 'a' && bone.name[0] <= 'z') || bone.name[0] == '_' ||
                    bone.name[0] == '$')
                    catalog->bones.push_back(bone);
            }
            cache.picker = std::move(catalog);
        } catch (...) {
            error = "Could not allocate the bounded bone catalog.";
            return false;
        }
    }
    return true;
}

// Worker thread: resolve the recorded target by handle first, then by a unique
// model match. Handles are recycled, so a handle match additionally requires
// the model to agree when the payload recorded one. The weapon point also
// resolves the target's weapon bone here.
inline bool resolve(HMODULE client, const Offsets& offsets, const dolly::AttachTarget& expected,
                    const dolly::AttachSegment& segment, Cache& out, const char*& error,
                    bool collect_picker = false) noexcept {
    out = Cache{};
    out.resolution = {segment.point, segment.bone_hash};
    std::uintptr_t entity_system = 0;
    if (!locate_entity_system(client, entity_system)) {
        error = "The attach camera could not locate the game entity system on this build.";
        return false;
    }
    std::vector<PlayerEntity> players;
    if (!walk_players(entity_system, players)) {
        error = "The attach camera found no players in the loaded replay.";
        return false;
    }
    struct Found {
        PlayerEntity entity;
        std::uintptr_t node, name_pointer;
        std::uint32_t name_offset, entity_index;
        std::uint64_t model;
    };
    std::vector<Found> handle_matches, model_matches;
    for (const auto& player : players) {
        std::uintptr_t node = 0, owner = 0;
        if (!read_value(player.instance + offsets.scene_node, node) || !node ||
            !read_value(node + offsets.owner, owner) || owner != player.instance)
            continue;
        Found found{};
        found.entity = player;
        found.node = node;
        found.entity_index = player.handle & 0x7fffu;
        if (!discover_model_name(node, found.name_offset, found.name_pointer, found.model))
            continue;
        if (expected.handle && player.handle == expected.handle)
            handle_matches.push_back(found);
        if (expected.model && found.model == expected.model)
            model_matches.push_back(found);
    }
    const Found* selected = nullptr;
    if (!handle_matches.empty()) {
        for (const auto& found : handle_matches)
            if (!expected.model || found.model == expected.model) {
                selected = &found;
                break;
            }
    } else if (expected.model) {
        if (model_matches.size() == 1) {
            selected = &model_matches.front();
        } else if (model_matches.size() > 1) {
            error = "Several players share the recorded attach model; select the target again.";
            return false;
        }
    }
    if (!selected) {
        error = "The attach target is not present in the loaded replay at this time.";
        return false;
    }
    out.ready = true;
    out.pawn = selected->entity.instance;
    out.identity = selected->entity.identity;
    out.node = selected->node;
    out.handle = selected->entity.handle;
    out.entity_index = selected->entity_index;
    out.name_offset = selected->name_offset;
    out.name_pointer = selected->name_pointer;
    out.model = selected->model;
    out.bones.handle = out.handle;
    out.bones.entity_index = out.entity_index;
    out.bones.model = out.model;
    // Eyes needs only the validated player fields. Discover a skeleton only
    // when Weapon/Bone is requested; optional picker work must not stall Eyes.
    if ((collect_picker || segment.point == dolly::AttachPoint::weapon ||
         segment.point == dolly::AttachPoint::bone) &&
        !resolve_bone(offsets, out.node, segment, out, error, collect_picker)) {
        out.ready = false;
        return false;
    }
    return true;
}

// Render callback: allocation-free identity re-check and pose read.
inline bool sample(const Offsets& offsets, const Cache& cache, const dolly::AttachSegment& segment,
                   dolly::AttachSample& out, const char*& error) noexcept {
    if (!cache.ready) {
        error = cache.error ? cache.error
                            : "The attach target was not resolved for this shot; re-play the shot.";
        return false;
    }
    std::uintptr_t identity = 0, instance = 0;
    std::uint32_t handle = 0;
    if (!read_value(cache.pawn + 0x10, identity) || identity != cache.identity ||
        !read_value(identity, instance) || instance != cache.pawn ||
        !read_value(identity + 0x10, handle) || handle != cache.handle) {
        error = "The attach target is no longer the same entity; retry the attach camera.";
        return false;
    }
    std::uintptr_t node = 0, owner = 0, name_pointer = 0;
    if (!read_value(cache.pawn + offsets.scene_node, node) || node != cache.node ||
        !read_value(node + offsets.owner, owner) || owner != cache.pawn ||
        !read_value(node + cache.name_offset, name_pointer) || name_pointer != cache.name_pointer) {
        error = "The attach target changed since it was resolved; re-play the shot.";
        return false;
    }
    if (segment.point == dolly::AttachPoint::weapon || segment.point == dolly::AttachPoint::bone) {
        if (!cache.bone_ready) {
            error = "The attach bone was not resolved for this shot; re-play the shot.";
            return false;
        }
        std::uintptr_t binding = 0, object = 0, array = 0;
        std::uint32_t count = 0;
        if (!read_value(cache.model_handle_slot, binding) || binding != cache.model_handle ||
            !read_value(binding, object) || object != cache.model_object ||
            !read_value(cache.model_state + 0x80, array) || array != cache.bone_array ||
            !read_value(cache.model_state + 0x90, count) || count != cache.bone_count ||
            cache.bone_index >= count) {
            error = "The model or bone pose buffer changed; retry the attach camera.";
            return false;
        }
        float transform[8]{};
        if (!read_memory(cache.bone_array + std::uint64_t(cache.bone_index) * 32, transform,
                         sizeof(transform))) {
            error = "The attach weapon bone transform was not readable; the shot stopped.";
            return false;
        }
        if (!std::isfinite(transform[0]) || !std::isfinite(transform[1]) ||
            !std::isfinite(transform[2]) || std::abs(transform[0]) > 1e8 ||
            std::abs(transform[1]) > 1e8 || std::abs(transform[2]) > 1e8) {
            error = "The attach weapon bone pose was outside the verified range; the shot stopped.";
            return false;
        }
        // Position follows the weapon bone; the default aim follows the
        // player's eye angles (the bone quaternion points along the rig's own
        // axes and would face the camera into the body). Authored offsets
        // still adjust both.
        float aim[3]{};
        float player_origin[3]{};
        float root_transform[8]{};
        if (!read_memory(cache.pawn + offsets.eye_angles, aim, sizeof(aim))) {
            error = "The attach weapon aim was not readable; the shot stopped.";
            return false;
        }
        if (!read_memory(node + offsets.origin, player_origin, sizeof(player_origin)) ||
            !std::isfinite(player_origin[0]) || !std::isfinite(player_origin[1]) ||
            !std::isfinite(player_origin[2]) || std::abs(player_origin[0]) > 1e8 ||
            std::abs(player_origin[1]) > 1e8 || std::abs(player_origin[2]) > 1e8) {
            error = "The attach player motion was not readable; the shot stopped.";
            return false;
        }
        // At view setup the readable skeleton may still contain the previous
        // root translation. Mesh submission later uses the current scene origin.
        // Preserve the bone's local animation, then rebase the whole skeleton
        // onto that current origin before camera offsets or smoothing.
        if (!read_memory(cache.bone_array, root_transform, sizeof(root_transform)) ||
            !std::isfinite(root_transform[0]) || !std::isfinite(root_transform[1]) ||
            !std::isfinite(root_transform[2]) || std::abs(root_transform[0]) > 1e8 ||
            std::abs(root_transform[1]) > 1e8 || std::abs(root_transform[2]) > 1e8) {
            error = "The attach skeleton root was not readable; the shot stopped.";
            return false;
        }
        out.valid = true;
        out.point = segment.point;
        out.target.handle = cache.handle;
        out.target.entity_id = cache.entity_index;
        out.target.model = cache.model;
        for (unsigned axis = 0; axis < 3; ++axis)
            out.origin[axis] = double(transform[axis]) - root_transform[axis] + player_origin[axis];
        out.motion_anchor = {player_origin[0], player_origin[1], player_origin[2]};
        out.angles = {aim[0], aim[1], aim[2]};
        out.aim = out.angles;
        out.eye_local = {0.0, 0.0, 0.0};
        return true;
    }
    float origin[3]{}, angles[3]{}, aim[3]{}, local[3]{};
    if (!read_memory(node + offsets.origin, origin, sizeof(origin)) ||
        !read_memory(node + offsets.angles, angles, sizeof(angles)) ||
        !read_memory(cache.pawn + offsets.eye_angles, aim, sizeof(aim)) ||
        !read_memory(cache.pawn + offsets.view_offset + 16, &local[0], 4) ||
        !read_memory(cache.pawn + offsets.view_offset + 24, &local[1], 4) ||
        !read_memory(cache.pawn + offsets.view_offset + 32, &local[2], 4)) {
        error = "The attach target pose was not readable; the shot stopped.";
        return false;
    }
    out.valid = true;
    out.point = segment.point;
    out.target.handle = cache.handle;
    out.target.entity_id = cache.entity_index;
    out.target.model = cache.model;
    out.origin = {origin[0], origin[1], origin[2]};
    out.motion_anchor = out.origin;
    out.angles = {angles[0], angles[1], angles[2]};
    out.aim = {aim[0], aim[1], aim[2]};
    if (!dolly::attach_view_offset({local[0], local[1], local[2]}, out.eye_local)) {
        error = "The attach eye offset was outside its verified range; the shot stopped.";
        return false;
    }
    return true;
}

// Worker thread: publish the live player roster for the editor target picker.
inline void publish_bones(unsigned char* mapping, const Cache* cache) noexcept {
    if (!mapping)
        return;
    EditorBones bones = cache ? cache->bones : EditorBones{};
    std::memcpy(bones.magic, "DLYBONE1", 8);
    bones.abi = 1;
    auto destination = mapping + kEditorBonesOffset;
    auto sequence = reinterpret_cast<volatile LONG*>(destination + 8);
    const LONG current = InterlockedCompareExchange(sequence, 0, 0);
    const LONG odd = (current & 1) ? current + 2 : current + 1;
    InterlockedExchange(sequence, odd);
    MemoryBarrier();
    std::memcpy(destination, &bones, 8);
    std::memcpy(destination + 12, reinterpret_cast<const unsigned char*>(&bones) + 12,
                sizeof(bones) - 12);
    MemoryBarrier();
    InterlockedExchange(sequence, odd + 1);
}

// Worker thread: publish the live player roster for the editor target picker.
// The seqlock mirrors the status writer: odd while writing, even when done.
inline void publish_roster(unsigned char* mapping, HMODULE client) noexcept {
    if (!mapping)
        return;
    EditorRoster roster{};
    std::memcpy(roster.magic, "DLYROS01", 8);
    roster.abi = kEditorRosterAbi;
    EditorAttachConfig config{};
    if (editor_attach_config(config)) {
        roster.flags = 1;
        const Offsets offsets = offsets_from(config);
        std::uintptr_t entity_system = 0;
        std::vector<PlayerEntity> players;
        if (locate_entity_system(client, entity_system) && walk_players(entity_system, players)) {
            for (const auto& player : players) {
                if (roster.count >= kEditorRosterPlayers)
                    break;
                std::uintptr_t node = 0, owner = 0;
                if (!read_value(player.instance + offsets.scene_node, node) || !node ||
                    !read_value(node + offsets.owner, owner) || owner != player.instance)
                    continue;
                EditorRosterEntry entry{};
                entry.handle = player.handle;
                entry.entity_index = player.handle & 0x7fffu;
                std::uint32_t name_offset = 0;
                std::uintptr_t name_pointer = 0;
                std::uint64_t token = 0;
                if (discover_model_name(node, name_offset, name_pointer, token) &&
                    read_memory(name_pointer, entry.model_path, sizeof(entry.model_path) - 1)) {
                    entry.model_path[sizeof(entry.model_path) - 1] = 0;
                    entry.model = token;
                }
                roster.players[roster.count++] = entry;
            }
        }
    }
    auto sequence = reinterpret_cast<volatile LONG*>(mapping + kEditorRosterOffset + 8);
    const LONG current = InterlockedCompareExchange(sequence, 0, 0);
    const LONG odd = (current & 1) ? current + 2 : current + 1;
    InterlockedExchange(sequence, odd);
    MemoryBarrier();
    unsigned char* destination = mapping + kEditorRosterOffset;
    std::memcpy(destination, &roster, 8);
    std::memcpy(destination + 12, reinterpret_cast<unsigned char*>(&roster) + 12,
                sizeof(roster) - 12);
    MemoryBarrier();
    InterlockedExchange(sequence, odd + 1);
}

} // namespace attach_runtime
