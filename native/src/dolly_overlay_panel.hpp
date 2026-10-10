// ImGui presentation only. Called with the overlay context current and its render lock held.
#pragma once
#include "dolly_editor.hpp"
#include "dolly_bone_picker.hpp"
#include "dolly_visualization.hpp"
#include "imgui.h"
#include <memory>

namespace dolly {
// Borrowed renderer operations; the panel never creates/releases a GPU resource.
struct PickerPortrait {
    void (*reset)() noexcept;
    void (*update)(const PickerFrame&) noexcept;
    ImTextureID (*texture)() noexcept;
};

// One presentation owner per overlay context; existing per-widget static edit
// caches remain process-lived to preserve device-loss behavior.
class OverlayPanel {
public:
    void style_panel();
    void load_panel_fonts(float scale);
    // Font pointers borrow the current ImGui atlas. Guide preference and widget
    // edit caches deliberately survive device recreation, as they did before.
    void reset_fonts() noexcept {
        panel_font = heading_font = title_font = nullptr;
        panel_scale = 1.0f;
    }
    bool guides_visible(const EditorSnapshot& state) noexcept;
    bool framing_grid_visible(const EditorSnapshot& state) noexcept;
    void draw_path_guides(const EditorSnapshot& state,
                          const std::shared_ptr<const VisualizationPath>& path);
    void draw_framing_grid(const EditorSnapshot& state);
    void draw_panel(const EditorSnapshot& state);
    void draw_bone_picker(const EditorSnapshot& state, const PickerPortrait& portrait);
    // The Object Picker is its own mode and its own right-side window, entered
    // only by its bindable key. It needs no GPU resource (proxy wireframes).
    void draw_object_picker(const EditorSnapshot& state);
    void reset_guides() noexcept { guide_geometry.line_count = guide_geometry.label_count = 0; }
    std::size_t guide_lines() const noexcept { return guide_geometry.line_count; }
    std::size_t guide_labels() const noexcept { return guide_geometry.label_count; }
    // Last drawn Shot-timeline rect (screen coordinates) and committed tick
    // drags; the smoke test reads these through overlay diagnostics.
    float timeline_x0() const noexcept { return timeline_x0_; }
    float timeline_x1() const noexcept { return timeline_x1_; }
    float timeline_y() const noexcept { return timeline_y_; }
    std::uint64_t timeline_drags() const noexcept { return timeline_drags_; }
    float timeline_view_start() const noexcept { return timeline_view_start_; }
    float timeline_view_end() const noexcept { return timeline_view_end_; }

private:
    static void roster_label(const EditorRosterEntry& entry, char* out, std::size_t capacity);
    struct SliderRow {
        bool committed = false, active = false;
    };
    void action_button(const char* label, EditorAction action, float width = 0, double value = 0,
                       bool primary = false);
    SliderRow slider_row(const char* id, const char* label, float* value, float minimum,
                         float maximum, const char* format, float label_width = 58.0f,
                         const VisualizationPath* timeline = nullptr);
    void section_title(const char* title, const char* detail = nullptr);
    bool begin_panel_card(const char* name);
    void end_panel_card();
    void replay_badge(const EditorSnapshot& state);
    ImFont* panel_font = nullptr;
    ImFont* heading_font = nullptr;
    ImFont* title_font = nullptr;
    float panel_scale = 1.0f;
    bool show_path_guides = true;
    VisualizationGeometry guide_geometry;
    float timeline_x0_ = 0, timeline_x1_ = 0, timeline_y_ = 0;
    std::uint64_t timeline_drags_ = 0;
    const char* timeline_view_id_ = nullptr;
    float timeline_view_start_ = 0, timeline_view_end_ = 0;
    bool timeline_panning_ = false;
    float timeline_pan_anchor_ = 0, timeline_pan_start_ = 0, timeline_pan_end_ = 0;
};
} // namespace dolly
