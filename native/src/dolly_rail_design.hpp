#pragma once
#include "dolly_rail_fields.hpp"
#include <array>
#include <map>
namespace dolly {
struct RailDesignState {
    rail_kit::Context kit;
    struct Number {
        double value = 0, published = 0;
        bool active = false;
        double pending_until = 0;
    };
    std::map<ImGuiID, Number> numbers;
    bool rotation = false, visibility = false, overrides = false, encoder = false;
    bool reset = false, clear = false, remove = false;
    unsigned selected = ~0u;
    std::uint32_t revision = 0, confirm_revision = 0, confirm_selected = 0;
    std::uint32_t follow_handle = 0;
    float follow[3]{135, 34, 0};
    double follow_published[3]{-1, 0, 0};
    bool measuring = false;
    unsigned page = 0;
    float used_height = 0;
    float object_height = 920;
    struct Height {
        float from = 0, to = 0;
        double started = 0;
    } heights[6];
    bool send(EditorAction action, double value = 0, const CameraPose* pose = nullptr) const {
        return !measuring && !(GImGui->CurrentItemFlags & ImGuiItemFlags_Disabled) &&
               editor_enqueue(action, value, pose);
    }
    Number& number(const char* id, double published) {
        auto& n = numbers[ImGui::GetID(id)];
        if (!n.active && (n.pending_until <= ImGui::GetTime() || n.value == published ||
                          n.published != published)) {
            n.value = published;
            n.pending_until = 0;
        }
        n.published = published;
        return n;
    }
    void finish(Number& n, const rail_fields::Edit& e, EditorAction action) {
        n.active = e.active;
        if (e.released && send(action, n.value))
            n.pending_until = ImGui::GetTime() + 1;
    }
};
}
