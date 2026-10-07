// ImGui layout and actions, independent of DX11 hooks and resource ownership.
#include "dolly_overlay_panel.hpp"
#include "dolly_attach.hpp"
#include "dolly_ui_tokens_generated.hpp"
#include "dolly_visualization_runtime.hpp"
#include "dolly_video.hpp"
#include "dolly_reshade.hpp"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>

namespace dolly {
static ImVec4 panel_color(unsigned rgb, float alpha = 1.0f) {
    return ImVec4(float((rgb >> 16) & 255) / 255.0f, float((rgb >> 8) & 255) / 255.0f,
                  float(rgb & 255) / 255.0f, alpha);
}
void OverlayPanel::style_panel() {
    ImGui::StyleColorsDark();
    auto& style = ImGui::GetStyle();
    // Dolly palette, shared with the desktop Tk UI. The values live in
    // dolly/ui_theme.py and reach here through dolly_ui_tokens_generated.hpp
    // (regenerate with tools/generate_ui_tokens.py); tests/test_ui_theme.py
    // fails if the two drift apart.
    namespace ui = dolly::ui;
    style.WindowPadding = ImVec2(16, 14);
    style.FramePadding = ImVec2(10, 5);
    style.ItemSpacing = ImVec2(8, 6);
    style.ItemInnerSpacing = ImVec2(8, 6);
    style.WindowRounding = 8;
    style.ChildRounding = 6;
    style.FrameRounding = 5;
    style.PopupRounding = 6;
    style.GrabRounding = 5;
    style.ScrollbarRounding = 6;
    style.WindowBorderSize = 1;
    style.ChildBorderSize = 1;
    style.FrameBorderSize = 1;
    style.ScrollbarSize = 10;
    style.GrabMinSize = 14;
    style.DisabledAlpha = .45f;
    // Native-feeling tabs: soft corners and a slim accent overline on the active
    // tab, no heavy tab-bar border.
    style.TabRounding = 4;
    style.TabBarBorderSize = 0;
    style.TabBarOverlineSize = 2;
    style.Colors[ImGuiCol_WindowBg] = panel_color(ui::BG, .97f);
    style.Colors[ImGuiCol_ChildBg] = panel_color(ui::PANEL);
    style.Colors[ImGuiCol_PopupBg] = panel_color(ui::PANEL);
    style.Colors[ImGuiCol_Border] = panel_color(ui::EDGE);
    style.Colors[ImGuiCol_Text] = panel_color(ui::TEXT);
    style.Colors[ImGuiCol_TextDisabled] = panel_color(ui::MUTED);
    style.Colors[ImGuiCol_Button] = panel_color(ui::BUTTON);
    style.Colors[ImGuiCol_ButtonHovered] = panel_color(ui::BUTTON_HOVER);
    style.Colors[ImGuiCol_ButtonActive] = panel_color(ui::BUTTON_ACTIVE);
    // Recessed field surface so checkboxes, dropdowns and inputs read as
    // interactive controls against the panel background.
    style.Colors[ImGuiCol_FrameBg] = panel_color(ui::FIELD);
    style.Colors[ImGuiCol_FrameBgHovered] = panel_color(ui::PANEL_ALT);
    style.Colors[ImGuiCol_FrameBgActive] = panel_color(ui::BUTTON);
    style.Colors[ImGuiCol_SliderGrab] = panel_color(ui::ACCENT);
    style.Colors[ImGuiCol_SliderGrabActive] = panel_color(ui::ACCENT_HOVER);
    style.Colors[ImGuiCol_CheckMark] = panel_color(ui::ACCENT);
    style.Colors[ImGuiCol_Header] = panel_color(ui::SELECTED);
    style.Colors[ImGuiCol_HeaderHovered] = style.Colors[ImGuiCol_ButtonHovered];
    style.Colors[ImGuiCol_HeaderActive] = style.Colors[ImGuiCol_ButtonActive];
    style.Colors[ImGuiCol_Separator] = panel_color(ui::EDGE);
    style.Colors[ImGuiCol_ScrollbarBg] = panel_color(ui::BG, 0);
    style.Colors[ImGuiCol_ScrollbarGrab] = panel_color(ui::EDGE);
    style.Colors[ImGuiCol_ScrollbarGrabHovered] = panel_color(ui::SCROLL_HOVER);
    style.Colors[ImGuiCol_ScrollbarGrabActive] = panel_color(ui::ACCENT);
    style.Colors[ImGuiCol_ResizeGrip] = panel_color(ui::ACCENT, .15f);
    style.Colors[ImGuiCol_ResizeGripHovered] = panel_color(ui::ACCENT, .45f);
    style.Colors[ImGuiCol_ResizeGripActive] = panel_color(ui::ACCENT, .75f);
    style.Colors[ImGuiCol_NavCursor] = panel_color(ui::ACCENT);
    style.Colors[ImGuiCol_TextSelectedBg] = panel_color(ui::SELECTED);
    style.Colors[ImGuiCol_PlotHistogram] = panel_color(ui::ACCENT);
    style.Colors[ImGuiCol_Tab] = panel_color(ui::PANEL);
    style.Colors[ImGuiCol_TabHovered] = panel_color(ui::BUTTON);
    style.Colors[ImGuiCol_TabSelected] = panel_color(ui::SELECTED);
    style.Colors[ImGuiCol_TabSelectedOverline] = panel_color(ui::ACCENT);
    style.Colors[ImGuiCol_TabDimmed] = panel_color(ui::TAB_DIM);
    style.Colors[ImGuiCol_TabDimmedSelected] = panel_color(ui::TAB_DIM_SELECTED);
    style.Colors[ImGuiCol_TabDimmedSelectedOverline] = panel_color(ui::TAB_DIM_OVERLINE);
}
static bool compact_checkbox(const char* label, bool* value, float scale) {
    ImGui::PushStyleVar(ImGuiStyleVar_FramePadding, ImVec2(4 * scale, 2 * scale));
    ImGui::PushStyleVar(ImGuiStyleVar_FrameRounding, 2 * scale);
    const bool changed = ImGui::Checkbox(label, value);
    ImGui::PopStyleVar(2);
    return changed;
}
static ImFont* installed_font(const char* filename, float size) {
    char windows[MAX_PATH]{}, path[MAX_PATH]{};
    const UINT length = GetWindowsDirectoryA(windows, MAX_PATH);
    if (!length || length >= MAX_PATH)
        return nullptr;
    const int written = std::snprintf(path, sizeof(path), "%s\\Fonts\\%s", windows, filename);
    if (written < 0 || written >= int(sizeof(path)))
        return nullptr;
    const DWORD attributes = GetFileAttributesA(path);
    if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & FILE_ATTRIBUTE_DIRECTORY))
        return nullptr;
    ImFontConfig config;
    config.OversampleH = 2;
    config.OversampleV = 2;
    return ImGui::GetIO().Fonts->AddFontFromFileTTF(path, size, &config);
}
void OverlayPanel::load_panel_fonts(float scale) {
    panel_scale = scale;
    // Read the fonts already installed with Windows; no system fonts are
    // distributed with Dolly. Keep a working fallback on minimal Windows images.
    panel_font = installed_font("segoeui.ttf", 16.0f * scale);
    if (!panel_font)
        panel_font = installed_font("arial.ttf", 16.0f * scale);
    if (!panel_font) {
        ImFontConfig config;
        config.SizePixels = 16.0f * scale;
        panel_font = ImGui::GetIO().Fonts->AddFontDefault(&config);
    }
    heading_font = installed_font("seguisb.ttf", 16.0f * scale);
    if (!heading_font)
        heading_font = panel_font;
    title_font = installed_font("seguisb.ttf", 22.0f * scale);
    if (!title_font)
        title_font = heading_font;
    ImGui::GetIO().FontDefault = panel_font;
}

void OverlayPanel::action_button(const char* label, EditorAction action, float width, double value,
                                 bool primary) {
    if (primary) {
        ImGui::PushStyleColor(ImGuiCol_Button, panel_color(dolly::ui::ACCENT));
        ImGui::PushStyleColor(ImGuiCol_Border, panel_color(dolly::ui::ACCENT));
        ImGui::PushStyleColor(ImGuiCol_ButtonHovered, panel_color(dolly::ui::ACCENT_HOVER));
        ImGui::PushStyleColor(ImGuiCol_ButtonActive, panel_color(dolly::ui::ACCENT_ACTIVE));
        ImGui::PushStyleColor(ImGuiCol_Text, panel_color(dolly::ui::ACCENT_INK));
        ImGui::PushFont(heading_font);
    }
    if (ImGui::Button(label, ImVec2(width, 0)))
        editor_enqueue(action, value);
    if (primary) {
        ImGui::PopFont();
        ImGui::PopStyleColor(5);
    }
}
// Stock key-cap glyph: a light cap with a dark label for the bound action,
// rendered from the live editor binding. key_cap_width() lets a caller reserve
// room so the chip never spills past the panel edge; key_cap() draws it.
static bool key_cap_text(EditorAction action, char* out, std::size_t capacity) {
    const EditorBinding binding = editor_binding_snapshot(action);
    const std::uint16_t vk = binding.vk;
    if (!vk || capacity == 0)
        return false;
    std::size_t n = 0;
    auto append = [&](const char* s) {
        while (*s && n < capacity - 1)
            out[n++] = *s++;
    };
    if (binding.modifiers & 1)
        append("CTRL+");
    if (binding.modifiers & 2)
        append("ALT+");
    if (binding.modifiers & 4)
        append("SHIFT+");
    // Function keys and single characters read cleanly; named keys use short forms.
    if (vk >= 0x70 && vk <= 0x87) {
        char fn[4] = {'F', char('1' + (vk - 0x70)), 0, 0};
        append(fn);
    } else if ((vk >= '0' && vk <= '9') || (vk >= 'A' && vk <= 'Z')) {
        char ch[2] = {char(vk), 0};
        append(ch);
    } else if (vk == 0x20) {
        append("SPACE");
    } else if (vk == 0x08) {
        append("BACKSPACE");
    } else if (vk == 0x21) {
        append("PGUP");
    } else if (vk == 0x22) {
        append("PGDN");
    } else if (vk == 0xBC) {
        append(",");
    } else if (vk == 0xBE) {
        append(".");
    } else {
        return false; // Unnamed key: skip the chip rather than guess.
    }
    out[n] = 0;
    return n != 0;
}
// Total horizontal room the chip needs, including the gap after it (0 if none).
static float key_cap_width(EditorAction action, float scale) {
    char text[24]{};
    if (!key_cap_text(action, text, sizeof(text)))
        return 0;
    return ImGui::CalcTextSize(text).x + 18 * scale;
}
// Draw the chip at the current cursor and advance it. Returns the width used.
static float key_cap(EditorAction action, float scale) {
    char text[24]{};
    if (!key_cap_text(action, text, sizeof(text)))
        return 0;
    const ImVec2 size = ImGui::CalcTextSize(text);
    const float pad_x = 6 * scale, pad_y = 3 * scale;
    const ImVec2 origin = ImGui::GetCursorScreenPos();
    const float height = size.y + pad_y * 2;
    const float width = size.x + pad_x * 2;
    auto* draw = ImGui::GetWindowDrawList();
    draw->AddRectFilled(origin, ImVec2(origin.x + width, origin.y + height),
                        ImGui::GetColorU32(panel_color(dolly::ui::KEYCAP)), 4 * scale);
    draw->AddText(ImVec2(origin.x + pad_x, origin.y + pad_y),
                  ImGui::GetColorU32(panel_color(dolly::ui::KEYCAP_INK)), text);
    ImGui::Dummy(ImVec2(width, height));
    return width;
}

void OverlayPanel::roster_label(const EditorRosterEntry& entry, char* out, std::size_t capacity) {
    const char* hero = attach_hero_name(entry.model_path);
    if (hero) {
        std::snprintf(out, capacity, "%s (%u)", hero, entry.entity_index);
        return;
    }
    const char* stem = entry.model_path;
    for (const char* p = entry.model_path; *p; ++p)
        if (*p == '/' || *p == '\\')
            stem = p + 1;
    char readable[64]{};
    std::size_t length = 0;
    while (stem[length] && stem[length] != '.' && length < sizeof(readable) - 1) {
        readable[length] = stem[length] == '_' ? ' ' : stem[length];
        ++length;
    }
    readable[length] = 0;
    std::snprintf(out, capacity, "%s (%u)", readable[0] ? readable : "Unknown", entry.entity_index);
}
// Label, ImGui slider and a right-aligned readout. Ctrl+click types an exact
// value (ImGui built-in), which keeps numeric entry available in game. With a
// timeline, the wheel zooms the visible time window and right-drag pans it so
// a crowded track stays workable. Alt+drag moves a camera tick (the playhead
// stays pinned while a tick is dragged and the new arrival commits on release
// with the current camera-list revision, so the list re-sorts safely). A plain
// drag always scrubs the slider within the visible window.
OverlayPanel::SliderRow OverlayPanel::slider_row(const char* id, const char* label, float* value,
                                                 float minimum, float maximum, const char* format,
                                                 float label_width,
                                                 const VisualizationPath* timeline) {
    SliderRow result;
    const bool timeline_mode = timeline != nullptr && maximum > minimum;
    if (timeline_mode) {
        const float full = maximum - minimum;
        const bool stale = timeline_view_id_ == nullptr
                           || std::strcmp(timeline_view_id_, id) != 0
                           || timeline_view_end_ - timeline_view_start_ <= 0.0f;
        if (stale || timeline_view_end_ > maximum || timeline_view_start_ < minimum
                || timeline_view_end_ - timeline_view_start_ > full) {
            timeline_view_id_ = id;
            timeline_view_start_ = minimum;
            timeline_view_end_ = maximum;
        } else {
            timeline_view_start_ = std::max(timeline_view_start_, minimum);
            timeline_view_end_ = std::min(timeline_view_end_, maximum);
        }
    }
    const float slider_min = timeline_mode ? timeline_view_start_ : minimum;
    const float slider_max = timeline_mode ? timeline_view_end_ : maximum;
    ImGui::AlignTextToFramePadding();
    ImGui::TextDisabled("%s", label);
    const float right = ImGui::GetWindowContentRegionMax().x;
    const float value_width = 55 * panel_scale;
    const float start = label_width * panel_scale;
    ImGui::SameLine(start);
    const float width = std::max(24.0f, right - start - value_width - 10 * panel_scale);
    ImGui::SetNextItemWidth(width);
    ImGui::PushStyleVar(ImGuiStyleVar_FramePadding, ImVec2(0, 2 * panel_scale));
    ImGui::PushStyleVar(ImGuiStyleVar_GrabMinSize, 5 * panel_scale);
    for (const auto color : {ImGuiCol_FrameBg, ImGuiCol_FrameBgHovered, ImGuiCol_FrameBgActive,
                             ImGuiCol_SliderGrab, ImGuiCol_SliderGrabActive, ImGuiCol_Border})
        ImGui::PushStyleColor(color, ImVec4(0, 0, 0, 0));
    ImGui::SliderFloat(id, value, slider_min, slider_max, "", ImGuiSliderFlags_AlwaysClamp);
    result.committed = ImGui::IsItemDeactivatedAfterEdit();
    result.active = ImGui::IsItemActive();
    const bool deactivated = ImGui::IsItemDeactivated();
    // Hovering the Shot timeline claims the wheel so the panel's scroll region
    // does not swallow it; the wheel zooms the timeline instead.
    if (timeline_mode)
        ImGui::SetItemKeyOwner(ImGuiKey_MouseWheelY);
    ImGui::PopStyleColor(6);
    ImGui::PopStyleVar(2);
    // Process-lived like the other per-widget edit caches.
    static int dragged_tick = -1;
    static float pinned_value = 0, dragged_time = 0;
    const auto& io = ImGui::GetIO();
    if (!(result.active && io.WantTextInput)) {
        const auto lo = ImGui::GetItemRectMin(), hi = ImGui::GetItemRectMax();
        const float y = (lo.y + hi.y) * .5f;
        const float x0 = lo.x + 4 * panel_scale, x1 = hi.x - 4 * panel_scale;
        const float t = std::clamp((*value - slider_min) / (slider_max - slider_min), 0.0f, 1.0f);
        const float x = x0 + (x1 - x0) * t;
        auto* draw = ImGui::GetWindowDrawList();
        draw->AddLine(ImVec2(x0, y), ImVec2(x1, y),
                      ImGui::GetColorU32(panel_color(dolly::ui::TRACK)), 6 * panel_scale);
        draw->AddLine(ImVec2(x0, y), ImVec2(x, y),
                      ImGui::GetColorU32(panel_color(dolly::ui::ACCENT_ACTIVE)), 6 * panel_scale);
        int hovered_tick = -1;
        double hovered_time = 0;
        if (timeline_mode) {
            timeline_x0_ = x0;
            timeline_x1_ = x1;
            timeline_y_ = y;
            const float span = timeline_view_end_ - timeline_view_start_;
            const unsigned dragged_color = 0xe8c879;
            const auto in_view = [&](double time) {
                return time >= timeline_view_start_ && time <= timeline_view_end_;
            };
            const auto marker_x = [&](double time) {
                const float current = timeline_view_end_ - timeline_view_start_;
                return x0 + (x1 - x0) *
                                std::clamp(float((time - timeline_view_start_) / current), 0.0f, 1.0f);
            };
            // Wheel zooms around the pointer; right-drag pans the window. The
            // seek preview follows the visible window so its handle stays put.
            if (ImGui::IsItemHovered() && io.MouseWheel != 0.0f) {
                const float full = maximum - minimum;
                const float fraction =
                    std::clamp((io.MousePos.x - x0) / std::max(1.0f, x1 - x0), 0.0f, 1.0f);
                const float anchor = timeline_view_start_ + fraction * span;
                const float factor = io.MouseWheel > 0 ? 0.85f : 1.0f / 0.85f;
                const float min_span = std::min(full, std::max(0.1f, full * 0.002f));
                const float next_span = std::clamp(span * factor, min_span, full);
                const float next_start =
                    std::clamp(anchor - fraction * next_span, minimum, maximum - next_span);
                timeline_view_start_ = next_start;
                timeline_view_end_ = next_start + next_span;
                *value = std::clamp(*value, timeline_view_start_, timeline_view_end_);
            }
            if (ImGui::IsItemHovered() && ImGui::IsMouseClicked(ImGuiMouseButton_Right)
                    && span < maximum - minimum) {
                timeline_panning_ = true;
                timeline_pan_anchor_ = io.MousePos.x;
                timeline_pan_start_ = timeline_view_start_;
                timeline_pan_end_ = timeline_view_end_;
            }
            if (timeline_panning_) {
                if (ImGui::IsMouseDragging(ImGuiMouseButton_Right)) {
                    const float pan_span = timeline_pan_end_ - timeline_pan_start_;
                    const float shift = -(io.MousePos.x - timeline_pan_anchor_) /
                                        std::max(1.0f, x1 - x0) * pan_span;
                    const float next_start = std::clamp(timeline_pan_start_ + shift,
                                                        minimum, maximum - pan_span);
                    timeline_view_start_ = next_start;
                    timeline_view_end_ = next_start + pan_span;
                    *value = std::clamp(*value, timeline_view_start_, timeline_view_end_);
                }
                if (!ImGui::IsMouseDown(ImGuiMouseButton_Right))
                    timeline_panning_ = false;
            }
            // An abandoned drag (panel hidden mid-drag) must never leak into
            // the next time the timeline is drawn.
            if (dragged_tick >= 0 && !result.active && !deactivated)
                dragged_tick = -1;
            if (!result.active && io.KeyAlt && ImGui::IsItemHovered()) {
                float best = 8 * panel_scale;
                for (const auto& camera : timeline->cameras()) {
                    if (!in_view(camera.time))
                        continue;
                    const float distance = std::abs(io.MousePos.x - marker_x(camera.time));
                    if (distance <= best) {
                        best = distance;
                        hovered_tick = int(camera.index);
                        hovered_time = camera.time;
                    }
                }
                if (hovered_tick >= 0) {
                    ImGui::SetTooltip(
                        "Camera %02u arrives at %.2f s. Alt+drag to change it.",
                        hovered_tick + 1, hovered_time);
                    ImGui::SetMouseCursor(ImGuiMouseCursor_ResizeEW);
                }
            }
            if (ImGui::IsItemActivated() && io.KeyAlt) {
                const ImVec2 press = io.MouseClickedPos[0];
                if (press.y >= lo.y - 10 * panel_scale && press.y <= hi.y + 10 * panel_scale) {
                    float best = 8 * panel_scale;
                    for (const auto& camera : timeline->cameras()) {
                        if (!in_view(camera.time))
                            continue;
                        const float distance = std::abs(press.x - marker_x(camera.time));
                        if (distance <= best) {
                            best = distance;
                            dragged_tick = int(camera.index);
                            dragged_time = float(camera.time);
                        }
                    }
                    if (dragged_tick >= 0)
                        pinned_value = *value;
                }
            }
            if (dragged_tick >= 0) {
                // Keep the playhead exactly where it was for the whole drag.
                *value = pinned_value;
                const float fraction = std::clamp((io.MousePos.x - x0) / (x1 - x0), 0.0f, 1.0f);
                dragged_time = timeline_view_start_ +
                               fraction * (timeline_view_end_ - timeline_view_start_);
                const float ghost = x0 + (x1 - x0) * fraction;
                draw->AddLine(ImVec2(ghost, y - 11 * panel_scale),
                              ImVec2(ghost, y + 11 * panel_scale),
                              ImGui::GetColorU32(panel_color(dragged_color)), 2 * panel_scale);
                char text[32]{};
                std::snprintf(text, sizeof(text), "%.2f s", dragged_time);
                draw->AddText(ImVec2(ghost + 5 * panel_scale, y - 20 * panel_scale),
                              ImGui::GetColorU32(panel_color(dragged_color)), text);
                if (deactivated) {
                    EditorCameraList list{};
                    if (editor_camera_list(list)) {
                        CameraPose request{};
                        request[0] = double(list.revision);
                        request[1] = double(dragged_time);
                        if (editor_enqueue(EditorAction::SetCameraTime, double(dragged_tick),
                                           &request))
                            ++timeline_drags_;
                    }
                    dragged_tick = -1;
                }
            }
            for (const auto& camera : timeline->cameras()) {
                if (!in_view(camera.time))
                    continue;
                const float marker = marker_x(camera.time);
                const bool selected = camera.index == timeline->selected_camera();
                const bool highlighted = int(camera.index) == hovered_tick;
                const auto color = selected || highlighted ? 0xe8c879 : 0x9caeb8;
                const float tick_width = highlighted ? 2 * panel_scale : panel_scale;
                draw->AddLine(ImVec2(marker, y - 8 * panel_scale),
                              ImVec2(marker, y + 8 * panel_scale),
                              ImGui::GetColorU32(panel_color(color)), tick_width);
            }
        }
        draw->AddRectFilled(ImVec2(x - 2.5f * panel_scale, y - 6 * panel_scale),
                            ImVec2(x + 2.5f * panel_scale, y + 6 * panel_scale),
                            ImGui::GetColorU32(panel_color(dolly::ui::ACCENT)), 2.5f * panel_scale);
    }
    char readout[32]{};
    std::snprintf(readout, sizeof(readout), format, *value);
    ImGui::SameLine(right - ImGui::CalcTextSize(readout).x);
    ImGui::TextDisabled("%s", readout);
    return result;
}
void OverlayPanel::section_title(const char* title, const char* detail) {
    // Deadlock draws section headers in caps; uppercase the title for a native
    // look without shipping the game font.
    char upper[96]{};
    std::size_t n = 0;
    for (; title[n] && n < sizeof(upper) - 1; ++n)
        upper[n] = (title[n] >= 'a' && title[n] <= 'z') ? char(title[n] - 32) : title[n];
    upper[n] = 0;
    ImGui::PushFont(heading_font);
    ImGui::PushStyleColor(ImGuiCol_Text, panel_color(dolly::ui::MUTED));
    ImGui::TextUnformatted(upper);
    ImGui::PopStyleColor();
    ImGui::PopFont();
    if (detail) {
        const float width = ImGui::CalcTextSize(detail).x;
        const float right = ImGui::GetWindowContentRegionMax().x;
        if (ImGui::GetItemRectSize().x + width + 24 * panel_scale <
            ImGui::GetContentRegionAvail().x) {
            ImGui::SameLine(right - width);
            ImGui::TextDisabled("%s", detail);
        }
    }
}
bool OverlayPanel::begin_panel_card(const char* name) {
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(14 * panel_scale, 12 * panel_scale));
    ImGui::PushStyleColor(ImGuiCol_ChildBg, panel_color(dolly::ui::PANEL));
    return ImGui::BeginChild(name, ImVec2(0, 0),
                             ImGuiChildFlags_Borders | ImGuiChildFlags_AutoResizeY |
                                 ImGuiChildFlags_AlwaysUseWindowPadding,
                             ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse);
}
void OverlayPanel::end_panel_card() {
    ImGui::EndChild();
    ImGui::PopStyleColor();
    ImGui::PopStyleVar();
}
void OverlayPanel::replay_badge(const EditorSnapshot& state) {
    const char* text = state.busy      ? "Working"
                       : !state.ready  ? "Waiting for replay"
                       : state.playing ? "Playing shot"
                       : state.paused  ? "Paused"
                                       : "Playing";
    const ImVec2 start = ImGui::GetCursorScreenPos();
    const ImVec2 text_size = ImGui::CalcTextSize(text);
    const ImVec2 size(text_size.x + 30 * panel_scale, text_size.y + 10 * panel_scale);
    ImGui::GetWindowDrawList()->AddRectFilled(start, ImVec2(start.x + size.x, start.y + size.y),
                                              ImGui::GetColorU32(panel_color(dolly::ui::SELECTED)),
                                              5 * panel_scale);
    ImGui::GetWindowDrawList()->AddCircleFilled(
        ImVec2(start.x + 11 * panel_scale, start.y + size.y / 2), 3 * panel_scale,
        ImGui::GetColorU32(panel_color(dolly::ui::ACCENT)));
    ImGui::GetWindowDrawList()->AddText(
        ImVec2(start.x + 20 * panel_scale, start.y + 5 * panel_scale),
        ImGui::GetColorU32(panel_color(dolly::ui::ACCENT_HOVER)), text);
    ImGui::Dummy(size);
    if (state.tick > 0) {
        char tick[48]{};
        std::snprintf(tick, sizeof(tick), "Replay tick %d", state.tick);
        const float width = ImGui::CalcTextSize(tick).x;
        if (size.x + width + 12 * panel_scale < ImGui::GetContentRegionAvail().x) {
            ImGui::SameLine(ImGui::GetWindowContentRegionMax().x - width);
            ImGui::SetCursorPosY(ImGui::GetCursorPosY() + 5 * panel_scale);
            ImGui::TextDisabled("%s", tick);
        }
    }
}
bool OverlayPanel::guides_visible(const EditorSnapshot& state) noexcept {
    return show_path_guides && state.enabled && state.focused && state.ready && state.paused &&
           state.manual_active && !state.playing && !state.busy && state.view_width > 0 &&
           state.view_height > 0 &&
           (state.owner == EditorOwner::Flight || state.owner == EditorOwner::Panel);
}
bool OverlayPanel::framing_grid_visible(const EditorSnapshot& state) noexcept {
    // Same paused-editing policy as the path guides, but independent of the
    // path-guides checkbox and available with the panel open or closed.
    return editor_framing_grid_enabled() && state.enabled && state.focused && state.ready &&
           state.paused && state.manual_active && !state.playing && !state.busy &&
           state.view_width > 0 && state.view_height > 0 &&
           (state.owner == EditorOwner::Flight || state.owner == EditorOwner::Panel);
}
void OverlayPanel::draw_framing_grid(const EditorSnapshot& state) {
    if (!framing_grid_visible(state))
        return;
    const auto size = ImGui::GetIO().DisplaySize;
    // Fixed After Effects-style standard guide: rule of thirds plus a centre
    // cross, one thin light line at the panel's guide opacity.
    const unsigned grid_color = 0xffffff;
    const ImU32 color = ImGui::GetColorU32(panel_color(grid_color, .35f));
    const float width = panel_scale;
    auto* draw = ImGui::GetBackgroundDrawList();
    draw->PushClipRect(ImVec2(0, 0), size, true);
    for (const float fraction : {1.0f / 3.0f, 2.0f / 3.0f}) {
        draw->AddLine(ImVec2(size.x * fraction, 0), ImVec2(size.x * fraction, size.y), color,
                      width);
        draw->AddLine(ImVec2(0, size.y * fraction), ImVec2(size.x, size.y * fraction), color,
                      width);
    }
    const float arm = 12 * panel_scale;
    const ImVec2 center(size.x * .5f, size.y * .5f);
    draw->AddLine(ImVec2(center.x - arm, center.y), ImVec2(center.x + arm, center.y), color,
                  width);
    draw->AddLine(ImVec2(center.x, center.y - arm), ImVec2(center.x, center.y + arm), color,
                  width);
    draw->PopClipRect();
}
void OverlayPanel::draw_path_guides(const EditorSnapshot& state,
                                    const std::shared_ptr<const VisualizationPath>& path) {
    if (!path || !path->enabled() || !guides_visible(state))
        return;
    const auto size = ImGui::GetIO().DisplaySize;
    VisualizationView view{state.pose, state.horizontal_fov, double(size.x), double(size.y), 1};
    if (!project_visualization(*path, view, guide_geometry))
        return;
    auto* draw = ImGui::GetBackgroundDrawList();
    draw->PushClipRect(ImVec2(0, 0), size, true);
    for (std::size_t i = 0; i < guide_geometry.line_count; ++i) {
        const auto& line = guide_geometry.lines[i];
        const bool selected = line.kind == VisualizationKind::SelectedCamera;
        const auto color = selected                               ? IM_COL32(255, 205, 115, 245)
                           : line.kind == VisualizationKind::Path ? IM_COL32(100, 214, 195, 205)
                                                                  : IM_COL32(153, 185, 226, 220);
        const ImVec2 a(line.a.x, line.a.y), b(line.b.x, line.b.y);
        const float width = (selected ? 2.0f : 1.3f) * panel_scale;
        draw->AddLine(a, b, IM_COL32(8, 12, 17, 170), width + 2 * panel_scale);
        draw->AddLine(a, b, color, width);
    }
    for (std::size_t i = 0; i < guide_geometry.label_count; ++i) {
        const auto& label = guide_geometry.labels[i];
        char text[32]{};
        std::snprintf(text, sizeof(text), "%u", label.camera_index + 1);
        const ImVec2 point(label.point.x + 5 * panel_scale, label.point.y + 5 * panel_scale);
        const auto measured = ImGui::CalcTextSize(text);
        draw->AddRectFilled(
            ImVec2(point.x - 3 * panel_scale, point.y - 2 * panel_scale),
            ImVec2(point.x + measured.x + 3 * panel_scale, point.y + measured.y + 2 * panel_scale),
            IM_COL32(14, 19, 26, 210), 3 * panel_scale);
        draw->AddText(point,
                      label.selected ? IM_COL32(255, 205, 115, 255) : IM_COL32(208, 223, 243, 255),
                      text);
    }
    draw->PopClipRect();
}
// Export encoder labels match the desktop Export tab's codec list; the numeric
// IDs are the same dolly::video::Codec values sent over the editor wire.
static const char* video_codec_label(std::uint32_t id) noexcept {
    switch (id) {
    case 1:
        return "NVIDIA H.264 (NVENC)";
    case 2:
        return "NVIDIA HEVC (NVENC)";
    case 3:
        return "H.264 (Media Foundation)";
    case 4:
        return "Software H.264 (x264)";
    case 5:
        return "Software HEVC (x265)";
    case 6:
        return "Intel H.264 (Quick Sync)";
    case 7:
        return "Intel HEVC (Quick Sync)";
    case 8:
        return "AMD H.264 (AMF)";
    case 9:
        return "AMD HEVC (AMF)";
    case 10:
        return "Lossless FFV1 (.mkv)";
    default:
        return "Auto (hardware when available)";
    }
}
void OverlayPanel::draw_panel(const EditorSnapshot& state) {
    auto& io = ImGui::GetIO();
    const float margin =
        std::min(20.0f * panel_scale, std::min(io.DisplaySize.x, io.DisplaySize.y) * .04f);
    const ImVec2 maximum(std::max(1.0f, io.DisplaySize.x - 2 * margin),
                         std::max(1.0f, io.DisplaySize.y - 2 * margin));
    ImGui::SetNextWindowPos(ImVec2(margin, margin), ImGuiCond_FirstUseEver);
    // Open large enough for the fullest page (Export) so nothing needs a manual
    // resize; the window stays resizable and is clamped to the game window. The
    // width stays clear of the guide overlay's right-hand region.
    ImGui::SetNextWindowSize(ImVec2(std::min(420.0f * panel_scale, maximum.x), maximum.y),
                             ImGuiCond_FirstUseEver);
    ImGui::SetNextWindowSizeConstraints(ImVec2(std::min(380.0f * panel_scale, maximum.x),
                                               std::min(360.0f * panel_scale, maximum.y)),
                                        maximum);
    bool close = false;
    if (ImGui::Begin("DEADLOCK DOLLY", nullptr,
                     ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoCollapse |
                         ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse)) {
        EditorCameraList history{};
        if (state.ready && state.paused && !state.busy && !state.playing && !state.attach_preview &&
            !state.bone_picker && !io.WantTextInput && !ImGui::IsAnyItemActive() &&
            editor_camera_list(history) && history.total == state.camera_count) {
            CameraPose request{};
            request[0] = double(history.revision);
            if ((history.flags & 1u) && ImGui::Shortcut(ImGuiMod_Ctrl | ImGuiKey_Z))
                editor_enqueue(EditorAction::UndoShot, 0, &request);
            if ((history.flags & 2u) &&
                (ImGui::Shortcut(ImGuiMod_Ctrl | ImGuiKey_Y) ||
                 ImGui::Shortcut(ImGuiMod_Ctrl | ImGuiMod_Shift | ImGuiKey_Z)))
                editor_enqueue(EditorAction::RedoShot, 0, &request);
        }
        // Keep the panel reachable when the game changes resolution while it is
        // open, but never fight an active drag or resize: clamping mid-interaction
        // makes the panel stick to one axis and only settle once the mouse is
        // released. Clamp on the frame after the button comes up.
        if (!ImGui::IsMouseDown(ImGuiMouseButton_Left)) {
            const auto position = ImGui::GetWindowPos();
            const auto size = ImGui::GetWindowSize();
            ImGui::SetWindowPos(
                ImVec2(std::clamp(position.x, margin,
                                  std::max(margin, io.DisplaySize.x - size.x - margin)),
                       std::clamp(position.y, margin,
                                  std::max(margin, io.DisplaySize.y - size.y - margin))));
        }
        const float close_width =
            ImGui::CalcTextSize("Close").x + ImGui::GetStyle().FramePadding.x * 2;
        const float right = ImGui::GetWindowContentRegionMax().x;
        ImGui::PushFont(title_font);
        const bool compact_title =
            ImGui::CalcTextSize("DEADLOCK DOLLY").x + close_width + 12 * panel_scale >
            ImGui::GetContentRegionAvail().x;
        if (compact_title) {
            ImGui::PopFont();
            ImGui::PushFont(heading_font);
        }
        ImGui::AlignTextToFramePadding();
        ImGui::TextUnformatted("DEADLOCK DOLLY");
        ImGui::PopFont();
        ImGui::SameLine(right - close_width);
        close = ImGui::Button("Close");
        ImGui::TextWrapped("%s", state.shot_name[0] ? state.shot_name : "Untitled shot");
        replay_badge(state);
        ImGui::Spacing();
        // One vertical scroll region holds the cards; essential exit/stop controls
        // stay visible. There are no nested horizontal scrollbars at small sizes.
        const float message_height = state.message[0]
                                         ? ImGui::CalcTextSize(state.message, nullptr, false,
                                                               ImGui::GetContentRegionAvail().x)
                                                   .y +
                                               ImGui::GetStyle().ItemSpacing.y
                                         : 0;
        const float footer_height = ImGui::GetFrameHeightWithSpacing() +
                                    ImGui::GetTextLineHeightWithSpacing() + message_height +
                                    8 * panel_scale;
        ImGui::PushStyleColor(ImGuiCol_ChildBg, ImVec4(0, 0, 0, 0));
        if (ImGui::BeginChild("##editor-content", ImVec2(0, -footer_height),
                              ImGuiChildFlags_None)) {
            ImGui::BeginTabBar("##dolly-pages", ImGuiTabBarFlags_None);
            if (ImGui::BeginTabItem("CAMERA")) {
                ImGui::BeginDisabled(!state.ready || state.busy);
                if (begin_panel_card("##cameras-card")) {
                    char count[32]{};
                    std::snprintf(count, sizeof(count), "%u saved", state.camera_count);
                    section_title("Cameras", count);
                    const float transport_spacing = ImGui::GetStyle().ItemSpacing.x;
                    // Keep both transport buttons and their key chips inside the
                    // panel; reserve each chip's room rather than drawing past the
                    // edge (which clipped hints on narrow layouts).
                    const float pause_hint = key_cap_width(EditorAction::PlayPause, panel_scale);
                    const float play_hint = key_cap_width(EditorAction::PlayPath, panel_scale);
                    const float reserved =
                        (pause_hint > 0 ? pause_hint : 0) + (play_hint > 0 ? play_hint : 0);
                    const float transport_half =
                        (ImGui::GetContentRegionAvail().x - 3 * transport_spacing - reserved) / 2;
                    action_button(state.paused ? "Play replay" : "Pause replay",
                                  EditorAction::PlayPause, transport_half);
                    if (pause_hint > 0) {
                        ImGui::SameLine();
                        ImGui::AlignTextToFramePadding();
                        key_cap(EditorAction::PlayPause, panel_scale);
                    }
                    ImGui::SameLine();
                    ImGui::BeginDisabled(state.camera_count < 2);
                    action_button("Play shot", EditorAction::PlayPath, transport_half);
                    ImGui::EndDisabled();
                    if (play_hint > 0) {
                        ImGui::SameLine();
                        ImGui::AlignTextToFramePadding();
                        key_cap(EditorAction::PlayPath, panel_scale);
                    }
                    const auto guides = visualization_snapshot();
                    ImGui::BeginDisabled(!state.camera_count || state.playing);
                    static float seek_time = 0;
                    static double last_playhead = -1, last_duration = -1;
                    if (last_playhead != state.playhead || last_duration != state.duration) {
                        seek_time = float(std::clamp(state.playhead, 0.0, state.duration));
                        last_playhead = state.playhead;
                        last_duration = state.duration;
                    }
                    slider_row("##shot-position", "Shot", &seek_time, 0,
                               float(std::max(state.duration, .001)), "%.3f s", 58, guides.get());
                    seek_time = std::clamp(seek_time, 0.0f, float(state.duration));
                    action_button("Seek here", EditorAction::SeekShot,
                                  ImGui::GetContentRegionAvail().x, double(seek_time));
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip(
                            "Seek the replay and apply this point on the camera path. Moving the slider alone does not seek; Alt+drag a camera tick to retime it, wheel zooms the timeline and right-drag pans it. Timeline marks match the saved camera guides.");
                    ImGui::EndDisabled();
                    ImGui::Separator();
                    char tick_label[64]{};
                    std::snprintf(tick_label, sizeof(tick_label), "Replay tick %d", state.tick);
                    ImGui::TextDisabled("%s", tick_label);
                    ImGui::BeginDisabled(state.playing || state.attach_preview);
                    static int tick_step = 1;
                    const float step_width = 88 * panel_scale;
                    const float arrow_width =
                        std::max(1.0f, (ImGui::GetContentRegionAvail().x - step_width -
                                        2 * ImGui::GetStyle().ItemSpacing.x) /
                                           2);
                    action_button("< Back", EditorAction::StepReplayTicks, arrow_width, -tick_step);
                    ImGui::SameLine();
                    ImGui::SetNextItemWidth(step_width);
                    char step_label[24]{};
                    std::snprintf(step_label, sizeof(step_label), "%d tick%s", tick_step,
                                  tick_step == 1 ? "" : "s");
                    if (ImGui::BeginCombo("##tick-step", step_label)) {
                        for (int step : {1, 2, 5, 10, 25}) {
                            char label[24]{};
                            std::snprintf(label, sizeof(label), "%d tick%s", step,
                                          step == 1 ? "" : "s");
                            if (ImGui::Selectable(label, step == tick_step))
                                tick_step = step;
                        }
                        ImGui::EndCombo();
                    }
                    ImGui::SameLine();
                    action_button("Forward >", EditorAction::StepReplayTicks, arrow_width,
                                  tick_step);
                    ImGui::TextDisabled(state.attach_preview ? "Detach to step with a fixed camera."
                                                             : "Tick steps keep the camera fixed.");
                    ImGui::EndDisabled();
                    ImGui::AlignTextToFramePadding();
                    ImGui::TextDisabled("Speed");
                    ImGui::SameLine(58 * panel_scale);
                    ImGui::SetNextItemWidth(-1);
                    char speed_label[24]{};
                    std::snprintf(speed_label, sizeof(speed_label), "%.3g x", state.playback_speed);
                    if (ImGui::BeginCombo("##playback-speed", speed_label)) {
                        for (double speed : {.05, .1, .25, .5, 1.0, 2.0, 4.0}) {
                            char label[24]{};
                            std::snprintf(label, sizeof(label), "%.3g x", speed);
                            if (ImGui::Selectable(label, speed == state.playback_speed))
                                editor_enqueue(EditorAction::SetPlaybackSpeed, speed);
                        }
                        ImGui::EndCombo();
                    }
                    ImGui::Separator();
                    const float capture_hint = key_cap_width(EditorAction::Capture, panel_scale);
                    action_button(
                        "Capture camera here", EditorAction::Capture,
                        ImGui::GetContentRegionAvail().x -
                            (capture_hint > 0 ? capture_hint + ImGui::GetStyle().ItemSpacing.x : 0),
                        0, true);
                    if (capture_hint > 0) {
                        ImGui::SameLine();
                        ImGui::AlignTextToFramePadding();
                        key_cap(EditorAction::Capture, panel_scale);
                    }
                    ImGui::BeginDisabled(!state.ready || state.busy || state.playing ||
                                         !state.camera_count);
                    if (ImGui::Button("Reset camera path",
                                      ImVec2(ImGui::GetContentRegionAvail().x, 0)))
                        ImGui::OpenPopup("Reset camera path?");
                    ImGui::EndDisabled();
                    if (ImGui::BeginPopupModal("Reset camera path?", nullptr,
                                               ImGuiWindowFlags_AlwaysAutoResize)) {
                        ImGui::TextUnformatted("Replace all camera views with the current view?");
                        ImGui::TextUnformatted("Lens and depth-of-field tracks will be kept.");
                        ImGui::BeginDisabled(!state.ready || state.busy || state.playing ||
                                             !state.camera_count);
                        if (ImGui::Button("Reset here") &&
                            editor_enqueue(EditorAction::ResetCameraPath))
                            ImGui::CloseCurrentPopup();
                        ImGui::EndDisabled();
                        ImGui::SameLine();
                        if (ImGui::Button("Cancel"))
                            ImGui::CloseCurrentPopup();
                        ImGui::EndPopup();
                    }
                    EditorCameraList cameras{};
                    const bool camera_list_ready =
                        editor_camera_list(cameras) && cameras.total == state.camera_count;
                    static std::uint32_t chosen_camera = 0, camera_revision = 0,
                                         confirmed_camera = 0;
                    if (camera_revision != cameras.revision ||
                        confirmed_camera != state.selected_camera) {
                        chosen_camera = state.selected_camera;
                        camera_revision = cameras.revision;
                        confirmed_camera = state.selected_camera;
                    }
                    CameraPose camera_request{};
                    camera_request[0] = double(cameras.revision);
                    const auto camera_action = [&](EditorAction action, double value = 0) {
                        return editor_enqueue(action, value, &camera_request);
                    };
                    // Inline Arrive/Bank edits carry the new value in pose[1].
                    const auto camera_edit = [&](EditorAction action, std::uint32_t index,
                                                 double value) {
                        CameraPose request = camera_request;
                        request[1] = value;
                        return editor_enqueue(action, double(index), &request);
                    };
                    // Drafts keep a drag or Ctrl+click text entry stable while the
                    // snapshot list is republished. Refresh them whenever the
                    // published page or revision changes; a rejected enqueue
                    // restores the authored value right away.
                    static std::uint32_t edit_revision = 0, edit_first = 0;
                    static float time_drafts[kEditorCameraListCount]{};
                    static float roll_drafts[kEditorCameraListCount]{};
                    if (edit_revision != cameras.revision || edit_first != cameras.first) {
                        edit_revision = cameras.revision;
                        edit_first = cameras.first;
                        for (std::uint32_t row = 0; row < cameras.count; ++row) {
                            time_drafts[row] = float(cameras.rows[row].time);
                            roll_drafts[row] = float(cameras.rows[row].roll);
                        }
                    }
                    // Bank edits stream during a drag so the placed camera glyph
                    // follows the field. One send per published revision keeps the
                    // camera-list guard valid; a release that lands while the
                    // previous edit is still republishing is retried once the
                    // revision advances so the final value is never lost.
                    static bool stream_sent = false;
                    static std::uint32_t stream_revision = 0;
                    static double stream_time = 0;
                    static float stream_value = 0;
                    static bool release_pending = false;
                    static std::uint32_t release_index = 0;
                    static float release_value = 0;
                    if (release_pending && camera_list_ready &&
                        (!stream_sent || cameras.revision != stream_revision)) {
                        release_pending = false;
                        if (camera_edit(EditorAction::SetCameraRoll, release_index,
                                        double(release_value))) {
                            stream_sent = true;
                            stream_revision = cameras.revision;
                            stream_time = ImGui::GetTime();
                            stream_value = release_value;
                        }
                    } else if (release_pending &&
                               (!camera_list_ready || !state.paused || state.playing ||
                                state.attach_preview)) {
                        // The table is no longer editable; never apply a stale
                        // release against a different camera list.
                        release_pending = false;
                    }
                    ImGui::BeginDisabled(!camera_list_ready || !state.paused || state.playing ||
                                         state.attach_preview);
                    if (camera_list_ready && cameras.count) {
                        const float table_height = ImGui::GetTextLineHeightWithSpacing() *
                                                   (float(std::min(cameras.count, 6u)) + 1.5f);
                        // Editable values sit on the row background like the old
                        // read-only text. Compact padding keeps rows the same
                        // height and the faint tint is the only field chrome.
                        ImGui::PushStyleVar(
                            ImGuiStyleVar_FramePadding,
                            ImVec2(ImGui::GetStyle().FramePadding.x, 2 * panel_scale));
                        ImGui::PushStyleVar(ImGuiStyleVar_FrameBorderSize, 0.0f);
                        ImGui::PushStyleColor(ImGuiCol_FrameBg, ImVec4(0, 0, 0, 0));
                        ImGui::PushStyleColor(ImGuiCol_FrameBgHovered,
                                              panel_color(dolly::ui::FIELD, .5f));
                        ImGui::PushStyleColor(ImGuiCol_FrameBgActive,
                                              panel_color(dolly::ui::FIELD, .8f));
                        if (ImGui::BeginTable(
                                "##camera-list", 4,
                                ImGuiTableFlags_RowBg | ImGuiTableFlags_BordersInnerV |
                                    ImGuiTableFlags_ScrollY | ImGuiTableFlags_SizingStretchProp,
                                ImVec2(0, table_height))) {
                            ImGui::TableSetupColumn("Camera", ImGuiTableColumnFlags_WidthStretch,
                                                    1.3f);
                            ImGui::TableSetupColumn("Arrive / s");
                            ImGui::TableSetupColumn("Aspect");
                            ImGui::TableSetupColumn("Bank");
                            ImGui::TableSetupScrollFreeze(0, 1);
                            ImGui::TableHeadersRow();
                            for (std::uint32_t row = 0; row < cameras.count; ++row) {
                                const auto index = cameras.first + row;
                                const auto& key = cameras.rows[row];
                                ImGui::PushID(int(index));
                                ImGui::TableNextRow();
                                ImGui::TableNextColumn();
                                char name[32]{};
                                std::snprintf(name, sizeof(name), "Camera %02u", index + 1);
                                ImGui::SetNextItemAllowOverlap();
                                if (ImGui::Selectable(name, index == chosen_camera,
                                                      ImGuiSelectableFlags_SpanAllColumns |
                                                          ImGuiSelectableFlags_AllowDoubleClick)) {
                                    const bool jump =
                                        ImGui::IsMouseDoubleClicked(ImGuiMouseButton_Left);
                                    if (camera_action(jump ? EditorAction::ViewCamera
                                                           : EditorAction::SelectCamera,
                                                      double(index)))
                                        chosen_camera = index;
                                }
                                if (ImGui::IsItemHovered())
                                    ImGui::SetTooltip(
                                        "%s camera. Click to select; double-click to view.",
                                        key.source ? "Bone / attached" : "Free path");
                                ImGui::TableNextColumn();
                                ImGui::SetNextItemWidth(-1.0f);
                                ImGui::DragFloat("##arrive", &time_drafts[row], 0.01f, 0.0f,
                                                 1e9f, "%.2f", ImGuiSliderFlags_AlwaysClamp);
                                const bool time_edited = ImGui::IsItemDeactivatedAfterEdit();
                                if (!ImGui::IsItemActive() && ImGui::IsItemHovered())
                                    ImGui::SetTooltip(
                                        "Camera %02u arrival. Drag to adjust; Ctrl+click to type.",
                                        index + 1);
                                if (time_edited && !camera_edit(EditorAction::SetCameraTime, index,
                                                                double(time_drafts[row])))
                                    time_drafts[row] = float(key.time);
                                ImGui::TableNextColumn();
                                ImGui::Text("%.3g", key.aspect);
                                ImGui::TableNextColumn();
                                ImGui::SetNextItemWidth(-1.0f);
                                ImGui::DragFloat("##bank", &roll_drafts[row], 0.25f, -180.0f,
                                                 180.0f, "%.1f", ImGuiSliderFlags_AlwaysClamp);
                                const bool roll_active = ImGui::IsItemActive();
                                const bool roll_edited = ImGui::IsItemDeactivatedAfterEdit();
                                if (ImGui::IsItemActivated()) {
                                    stream_sent = false;
                                    stream_value = roll_drafts[row];
                                    release_pending = false;
                                }
                                if (!roll_active && ImGui::IsItemHovered())
                                    ImGui::SetTooltip(
                                        "Camera %02u bank. Drag to adjust; Ctrl+click to type.",
                                        index + 1);
                                if (roll_active && (!stream_sent || cameras.revision != stream_revision) &&
                                    (!stream_sent || roll_drafts[row] != stream_value) &&
                                    ImGui::GetTime() - stream_time >= .12 &&
                                    camera_edit(EditorAction::SetCameraRoll, index,
                                                double(roll_drafts[row]))) {
                                    stream_sent = true;
                                    stream_revision = cameras.revision;
                                    stream_time = ImGui::GetTime();
                                    stream_value = roll_drafts[row];
                                }
                                if (roll_edited &&
                                    (!stream_sent || roll_drafts[row] != stream_value)) {
                                    if (!stream_sent || cameras.revision != stream_revision) {
                                        if (camera_edit(EditorAction::SetCameraRoll, index,
                                                        double(roll_drafts[row]))) {
                                            stream_sent = true;
                                            stream_revision = cameras.revision;
                                            stream_time = ImGui::GetTime();
                                            stream_value = roll_drafts[row];
                                        } else {
                                            roll_drafts[row] = float(key.roll);
                                        }
                                    } else {
                                        // Wait for the in-flight republish, then send.
                                        release_pending = true;
                                        release_index = index;
                                        release_value = roll_drafts[row];
                                    }
                                }
                                ImGui::PopID();
                            }
                            ImGui::EndTable();
                        }
                        ImGui::PopStyleColor(3);
                        ImGui::PopStyleVar(2);
                        if (cameras.total > kEditorCameraListCount) {
                            ImGui::BeginDisabled(!cameras.first);
                            if (ImGui::SmallButton("Earlier"))
                                camera_action(EditorAction::CameraPage,
                                              double(cameras.first - kEditorCameraListCount));
                            ImGui::EndDisabled();
                            ImGui::SameLine();
                            ImGui::TextDisabled("%u-%u of %u", cameras.first + 1,
                                                cameras.first + cameras.count, cameras.total);
                            ImGui::SameLine();
                            ImGui::BeginDisabled(cameras.first + cameras.count >= cameras.total);
                            if (ImGui::SmallButton("Later"))
                                camera_action(EditorAction::CameraPage,
                                              double(cameras.first + kEditorCameraListCount));
                            ImGui::EndDisabled();
                        }
                    } else {
                        ImGui::TextDisabled(camera_list_ready
                                                ? "No cameras yet. Capture your first view."
                                                : "Camera list is updating...");
                    }
                    const float history_half =
                        (ImGui::GetContentRegionAvail().x - ImGui::GetStyle().ItemSpacing.x) / 2;
                    ImGui::BeginDisabled(!(cameras.flags & 1u));
                    if (ImGui::Button("Undo", ImVec2(history_half, 0)))
                        camera_action(EditorAction::UndoShot);
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip("Undo a shot edit (Ctrl+Z).");
                    ImGui::EndDisabled();
                    ImGui::SameLine();
                    ImGui::BeginDisabled(!(cameras.flags & 2u));
                    if (ImGui::Button("Redo", ImVec2(history_half, 0)))
                        camera_action(EditorAction::RedoShot);
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip("Redo a shot edit (Ctrl+Y or Ctrl+Shift+Z).");
                    ImGui::EndDisabled();
                    ImGui::BeginDisabled(!cameras.count || chosen_camera < cameras.first ||
                                         chosen_camera >= cameras.first + cameras.count);
                    if (ImGui::Button("View selected", ImVec2(history_half, 0)))
                        camera_action(EditorAction::ViewCamera, double(chosen_camera));
                    ImGui::SameLine();
                    if (ImGui::Button("Delete camera", ImVec2(history_half, 0)))
                        camera_action(EditorAction::DeleteCamera, double(chosen_camera));
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip("Delete Camera %02u. Undo restores it.",
                                          chosen_camera + 1);
                    ImGui::EndDisabled();
                    ImGui::EndDisabled();
                    ImGui::BeginDisabled(!state.camera_count || state.playing);
                    action_button("Replace selected", EditorAction::Replace,
                                  ImGui::GetContentRegionAvail().x);
                    const float half =
                        (ImGui::GetContentRegionAvail().x - ImGui::GetStyle().ItemSpacing.x) / 2;
                    action_button("Previous view", EditorAction::PreviousView, half);
                    ImGui::SameLine();
                    action_button("Next view", EditorAction::NextView, half);
                    ImGui::EndDisabled();
                    ImGui::Spacing();
                    compact_checkbox("Show path guides", &show_path_guides, panel_scale);
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip(
                            "Camera positions and spline while editing a paused replay. The selected camera is gold. Guides show through walls and hide during playback.");
                    if (show_path_guides && state.camera_count && !guides) {
                        const auto viewer_state = visualization_runtime_state();
                        if (viewer_state == VisualizationRuntimeState::Invalid ||
                            viewer_state == VisualizationRuntimeState::Unavailable)
                            ImGui::TextWrapped(
                                "Path guides unavailable. Export diagnostics from the desktop editor.");
                    }
                    if (show_path_guides && guides &&
                        guides->camera_count() > guides->cameras().size())
                        ImGui::TextDisabled("%u of %u camera markers; selected included",
                                            unsigned(guides->cameras().size()),
                                            unsigned(guides->camera_count()));
                    ImGui::Spacing();
                    ImGui::AlignTextToFramePadding();
                    ImGui::TextDisabled("Framing guide");
                    const float grid_hint = key_cap_width(EditorAction::FramingGrid, panel_scale);
                    if (grid_hint > 0) {
                        ImGui::SameLine();
                        key_cap(EditorAction::FramingGrid, panel_scale);
                    }
                    ImGui::SameLine();
                    ImGui::TextDisabled("%s", editor_framing_grid_enabled() ? "On" : "Off");
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip(
                            "Thirds and centre cross while editing a paused replay. Toggle with the bound key.");
                }
                end_panel_card();
                if (begin_panel_card("##flight-card")) {
                    // Send one change at the end of a drag, not a settings write per frame.
                    static float speed_draft = 400.0f;
                    static bool speed_editing = false;
                    if (!speed_editing)
                        speed_draft = std::clamp(static_cast<float>(state.speed), 1.0f, 10000.0f);
                    char speed_label[48]{};
                    std::snprintf(speed_label, sizeof(speed_label), "Speed %.0f", speed_draft);
                    section_title("Free camera", speed_label);
                    ImGui::TextWrapped(
                        "Mouse wheel zooms while flying and updates the selected camera's Framing Curve.");
                    ImGui::SetNextItemWidth(-1);
                    ImGui::PushStyleVar(ImGuiStyleVar_FramePadding,
                                        ImVec2(12 * panel_scale, 3 * panel_scale));
                    ImGui::SliderFloat("##flight-speed", &speed_draft, 1.0f, 10000.0f, "",
                                       ImGuiSliderFlags_Logarithmic | ImGuiSliderFlags_AlwaysClamp);
                    ImGui::PopStyleVar();
                    const bool speed_committed = ImGui::IsItemDeactivatedAfterEdit();
                    speed_editing = ImGui::IsItemActive();
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip(
                            "Movement speed in world units per second. Ctrl+click to enter a value.");
                    if (speed_committed)
                        editor_enqueue(EditorAction::SetSpeed, double(speed_draft));
                    ImGui::Spacing();
                    // Fly camera is used after F8 closes Dolly, so no key cap for
                    // it; Heroes / game UI keeps its cap.
                    const float gameui_hint = key_cap_width(EditorAction::GameUI, panel_scale);
                    const float half =
                        (ImGui::GetContentRegionAvail().x - 2 * ImGui::GetStyle().ItemSpacing.x -
                         (gameui_hint > 0 ? gameui_hint : 0)) /
                        2;
                    action_button("Fly camera", EditorAction::Flight, half);
                    ImGui::SameLine();
                    action_button("Heroes / game UI", EditorAction::GameUI, half, 1);
                    if (gameui_hint > 0) {
                        ImGui::SameLine();
                        ImGui::AlignTextToFramePadding();
                        key_cap(EditorAction::GameUI, panel_scale);
                    }
                    ImGui::Spacing();
                    ImGui::BeginDisabled(state.playing);
                    ImGui::TextUnformatted("Updates / s");
                    ImGui::SetNextItemWidth(-1);
                    char playback_rate[32]{};
                    std::snprintf(playback_rate, sizeof(playback_rate), "%u", state.playback_rate);
                    if (ImGui::BeginCombo("##playback-rate", playback_rate)) {
                        for (unsigned value : {30u, 60u, 120u}) {
                            char label[32]{};
                            std::snprintf(label, sizeof(label), "%u", value);
                            if (ImGui::Selectable(label, value == state.playback_rate))
                                editor_enqueue(EditorAction::SetPlaybackRate, double(value));
                        }
                        ImGui::EndCombo();
                    }
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip(
                            "Native monitoring frequency. Camera and supported effects follow each rendered frame; this is not an output FPS setting.");
                    ImGui::EndDisabled();
                }
                end_panel_card();
                bool replay_hud = state.replay_show_hud;
                if (compact_checkbox("Show game HUD during replay", &replay_hud, panel_scale))
                    editor_enqueue(EditorAction::SetReplayHud, replay_hud ? 1 : 0);
                ImGui::EndDisabled();
                ImGui::EndTabItem();
            }
            if (ImGui::BeginTabItem("FOLLOW")) {
                ImGui::BeginDisabled(!state.ready || state.busy);
                if (begin_panel_card("##follow-card")) {
                    section_title("FOLLOW", state.follow_active ? "Active" : "Hero aim");
                    ImGui::TextWrapped("Follow a hero's aim and movement with the game's camera.");
                    EditorRoster roster{};
                    const bool roster_ok = editor_roster_snapshot(roster);
                    static std::uint32_t follow_handle = 0;
                    int follow_index = -1;
                    if (roster_ok)
                        for (std::uint32_t i = 0; i < roster.count && i < kEditorRosterPlayers; ++i)
                            if (roster.players[i].handle == follow_handle)
                                follow_index = int(i);
                    char hero_label[112]{};
                    if (follow_index >= 0)
                        roster_label(roster.players[follow_index], hero_label, sizeof(hero_label));
                    else
                        std::snprintf(hero_label, sizeof(hero_label), "Select a hero...");
                    ImGui::BeginDisabled(!state.follow_available || state.playing ||
                                         !state.paused || state.attach_preview);
                    ImGui::SetNextItemWidth(-1);
                    if (ImGui::BeginCombo("##follow-hero", hero_label)) {
                        if (!roster_ok || !roster.count)
                            ImGui::TextDisabled("Waiting for replay players...");
                        else
                            for (std::uint32_t i = 0; i < roster.count && i < kEditorRosterPlayers;
                                 ++i) {
                                char label[112]{};
                                roster_label(roster.players[i], label, sizeof(label));
                                ImGui::PushID(int(i));
                                if (ImGui::Selectable(label, int(i) == follow_index))
                                    follow_handle = roster.players[i].handle;
                                ImGui::PopID();
                            }
                        ImGui::EndCombo();
                    }
                    static float follow_draft[3] = {135, 34, 0};
                    static bool follow_dirty = false;
                    static double follow_applied[3] = {-1, 0, 0};
                    const double published[3] = {state.follow_distance, state.follow_shoulder,
                                                 state.follow_height};
                    if (published[0] != follow_applied[0] || published[1] != follow_applied[1] ||
                        published[2] != follow_applied[2]) {
                        for (int i = 0; i < 3; ++i) {
                            follow_applied[i] = published[i];
                            follow_draft[i] = float(published[i]);
                        }
                        follow_dirty = false;
                    }
                    const char* names[] = {"Distance", "Shoulder", "Height"};
                    for (int i = 0; i < 3; ++i) {
                        ImGui::TextUnformatted(names[i]);
                        ImGui::SetNextItemWidth(-1);
                        ImGui::PushID(i);
                        if (ImGui::SliderFloat("##follow-offset", &follow_draft[i],
                                               i == 0 ? 0.f : -150.f, i == 0 ? 400.f : 150.f,
                                               "%.0f", ImGuiSliderFlags_AlwaysClamp))
                            follow_dirty = true;
                        if (ImGui::IsItemClicked(ImGuiMouseButton_Right)) {
                            const float defaults[] = {135, 34, 0};
                            follow_draft[i] = defaults[i];
                            follow_dirty = true;
                        }
                        ImGui::PopID();
                        if (ImGui::IsItemHovered())
                            ImGui::SetTooltip(
                                i == 0
                                    ? "Distance behind the hero. Ctrl+click to type. Right-click resets to 135."
                                : i == 1
                                    ? "Negative: left. Positive: right. Right-click resets to 34."
                                    : "Height above or below the pivot. Right-click resets to 0.");
                    }
                    ImGui::BeginDisabled(follow_index < 0);
                    if (ImGui::Button(state.follow_active ? "Apply Follow" : "Preview Follow",
                                      ImVec2(-1, 0)) &&
                        follow_index >= 0) {
                        CameraPose request{};
                        request[0] = follow_draft[1];
                        request[1] = follow_draft[2];
                        request[2] = follow_index;
                        request[3] = roster.players[follow_index].handle;
                        request[4] = roster.players[follow_index].entity_index;
                        request[5] = std::uint32_t(roster.players[follow_index].model);
                        request[6] = std::uint32_t(roster.players[follow_index].model >> 32);
                        editor_enqueue(EditorAction::StartGameFollow, follow_draft[0], &request);
                    }
                    ImGui::EndDisabled();
                    ImGui::EndDisabled();
                    ImGui::BeginDisabled(!state.follow_pending);
                    action_button("Restore rig", EditorAction::StopGameFollow, -1);
                    ImGui::EndDisabled();
                    if (!state.follow_available)
                        ImGui::TextWrapped("Follow unavailable for this game build.");
                    else if (state.attach_preview)
                        ImGui::TextWrapped("Detach the bone preview to use Follow.");
                    else if (follow_dirty)
                        ImGui::TextDisabled("Changes ready to apply");
                    ImGui::TextWrapped(
                        "Restore rig resets camera settings and keeps the selected hero.");
                    bool show_hud = state.replay_show_hud;
                    if (compact_checkbox("Show game HUD during replay", &show_hud, panel_scale))
                        editor_enqueue(EditorAction::SetReplayHud, show_hud ? 1 : 0);
                    ImGui::TextWrapped(
                        "Applies to Follow and ordinary replay playback. Pause restores the previous HUD.");
                }
                end_panel_card();
                ImGui::EndDisabled();
                ImGui::EndTabItem();
            }
            if (ImGui::BeginTabItem("BONE PICKER")) {
                ImGui::BeginDisabled(!state.ready || state.busy);
                if (begin_panel_card("##attach-card")) {
                    section_title("Attach camera");
                    ImGui::TextDisabled("Player point of view or weapon camera");
                    if (!state.camera_count)
                        ImGui::TextWrapped(
                            "Choose a player to start a bone camera. No camera setup needed.");
                    ImGui::BeginDisabled(!state.attach_available);
                    EditorRoster roster{};
                    const bool roster_ok = editor_roster_snapshot(roster);
                    char player_label[112]{};
                    if (state.attach_selected && roster_ok && roster.count &&
                        state.attach_target_index < roster.count) {
                        roster_label(roster.players[state.attach_target_index], player_label,
                                     sizeof(player_label));
                    } else {
                        std::snprintf(player_label, sizeof(player_label), "Select a player...");
                    }
                    ImGui::SetNextItemWidth(-1);
                    if (ImGui::BeginCombo("##attach-player", player_label)) {
                        if (ImGui::Selectable("Free path", !state.attach_selected))
                            editor_enqueue(EditorAction::AttachReset);
                        ImGui::Separator();
                        if (!roster_ok || !roster.count) {
                            ImGui::TextDisabled("No players in the loaded replay");
                        } else {
                            const std::uint32_t count = std::min<std::uint32_t>(
                                roster.count, std::uint32_t(kEditorRosterPlayers));
                            for (std::uint32_t index = 0; index < count; ++index) {
                                char name[112]{};
                                roster_label(roster.players[index], name, sizeof(name));
                                if (ImGui::Selectable(name, index == state.attach_target_index))
                                    editor_enqueue(EditorAction::SetAttachTarget, double(index));
                                if (index == state.attach_target_index)
                                    ImGui::SetItemDefaultFocus();
                            }
                        }
                        ImGui::EndCombo();
                    }
                    ImGui::SetNextItemWidth(-1);
                    if (ImGui::BeginCombo("##attach-point", state.attach_point == 2 ? "Point: Bone"
                                                            : state.attach_point == 1
                                                                ? "Point: Weapon"
                                                                : "Point: Eyes")) {
                        if (ImGui::Selectable("Eyes", state.attach_point == 0))
                            editor_enqueue(EditorAction::SetAttachPoint, 0);
                        if (ImGui::Selectable("Weapon", state.attach_point == 1))
                            editor_enqueue(EditorAction::SetAttachPoint, 1);
                        if (ImGui::Selectable("Bone Picker...", state.attach_point == 2))
                            editor_enqueue(EditorAction::OpenBonePicker);
                        ImGui::EndCombo();
                    }
                    if (state.attach_point == 2) {
                        if (ImGui::Button("Change bone...", ImVec2(-1, 0)))
                            editor_enqueue(EditorAction::OpenBonePicker);
                        ImGui::TextWrapped("Selected joint: %s", state.attach_bone[0]
                                                                     ? state.attach_bone
                                                                     : "Choose a bone");
                    }
                    ImGui::TextDisabled("Offsets");
                    {
                        // Slider rows commit at the end of an edit, not per frame.
                        static float attach_draft[6]{};
                        static bool attach_editing = false;
                        if (!attach_editing) {
                            for (int index = 0; index < 6; ++index)
                                attach_draft[index] = float(state.attach_offsets[index]);
                        }
                        static const char* const kAttachAxis[6] = {"Forward", "L/R", "Up",
                                                                   "Pitch",   "Yaw", "Roll"};
                        bool attach_commit = false, attach_active = false;
                        for (int index = 0; index < 4; ++index) {
                            char id[32]{};
                            std::snprintf(id, sizeof(id), "##attach-offset-%d", index);
                            const bool position = index < 3;
                            const SliderRow row =
                                slider_row(id, kAttachAxis[index], &attach_draft[index],
                                           position ? -10.0f : -180.0f, position ? 10.0f : 180.0f,
                                           position ? "%.2f" : "%.1f", 74.0f);
                            attach_active = attach_active || row.active;
                            attach_commit = attach_commit || row.committed;
                        }
                        if (ImGui::CollapsingHeader("Rotation & transition")) {
                            for (int index = 4; index < 6; ++index) {
                                char id[32]{};
                                std::snprintf(id, sizeof(id), "##attach-offset-%d", index);
                                const auto row =
                                    slider_row(id, kAttachAxis[index], &attach_draft[index], -180,
                                               180, "%.1f", 74.0f);
                                attach_active |= row.active;
                                attach_commit |= row.committed;
                            }
                            static float source_blend = 0;
                            static bool blend_editing = false;
                            if (!blend_editing)
                                source_blend = float(state.source_blend);
                            const auto blend_row = slider_row("##source-blend", "Blend in",
                                                              &source_blend, 0.0f, 10.0f, "%.2f s");
                            blend_editing = blend_row.active;
                            if (blend_row.committed)
                                editor_enqueue(EditorAction::SetSourceBlend, source_blend);
                            if (ImGui::IsItemHovered())
                                ImGui::SetTooltip(
                                    "0 keeps a cut. An eased blend finishes at this view's arrival time, starting no earlier than the previous view. Try 0.5 seconds.");
                        }
                        attach_editing = attach_active;
                        if (attach_commit) {
                            CameraPose offsets{};
                            for (int index = 0; index < 6; ++index)
                                offsets[index] = attach_draft[index];
                            editor_enqueue(EditorAction::SetAttachOffsets, 0, &offsets);
                        }
                        static float attach_smoothing = 0.0f;
                        static bool smoothing_editing = false;
                        if (!smoothing_editing)
                            attach_smoothing =
                                std::clamp(float(state.attach_smoothing), 0.0f, 5.0f);
                        const SliderRow smoothing_row =
                            slider_row("##attach-smoothing", "Smooth", &attach_smoothing, 0.0f,
                                       1.0f, "%.2f s");
                        smoothing_editing = smoothing_row.active;
                        if (smoothing_row.committed)
                            editor_enqueue(EditorAction::SetAttachSmoothing,
                                           double(attach_smoothing));
                        ImGui::TextDisabled("Drag to adjust; Ctrl+click a slider to type.");
                    }
                    bool hide = state.attach_hide;
                    if (compact_checkbox("Hide this hero", &hide, panel_scale))
                        editor_enqueue(EditorAction::SetAttachHide,
                                       (hide ? 1 : 0) | (state.attach_auto_clearance ? 2 : 0));
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip(
                            "Hides this hero's identified body draws in preview and recordings.");
                    ImGui::TextDisabled("Visible-model spacing");
                    ImGui::SetNextItemWidth(-1);
                    if (ImGui::BeginCombo("##attach-clearance", state.attach_auto_clearance
                                                                    ? "Automatic clearance"
                                                                    : "Exact offset")) {
                        if (ImGui::Selectable("Automatic clearance (experimental)",
                                              state.attach_auto_clearance))
                            editor_enqueue(EditorAction::SetAttachHide,
                                           (state.attach_hide ? 1 : 0) | 2);
                        if (ImGui::Selectable("Exact offset", !state.attach_auto_clearance))
                            editor_enqueue(EditorAction::SetAttachHide, state.attach_hide ? 1 : 0);
                        ImGui::EndCombo();
                    }
                    if (state.attach_auto_clearance)
                        ImGui::TextWrapped(
                            "Head POV stays close and slightly forward; other bones use a wider spacing guide. Animated arms and clothing can still cross the view.");
                    else if (!state.attach_hide)
                        ImGui::TextWrapped(
                            "Exact offset: the visible hero may cross or block the camera during motion.");
                    ImGui::Separator();
                    const float attach_half =
                        (ImGui::GetContentRegionAvail().x - ImGui::GetStyle().ItemSpacing.x) / 2;
                    const bool attach_ready = state.attach_selected &&
                                              (state.manual_active || state.follow_active) &&
                                              state.paused && !state.playing;
                    ImGui::BeginDisabled(!attach_ready);
                    action_button(state.attach_preview ? "Detach" : "Attach here",
                                  EditorAction::AttachPreview, attach_half,
                                  state.attach_preview ? 0 : 1, !state.attach_preview);
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip(
                            "Live preview: the camera follows this player. Transfers from Follow automatically; WASD and the mouse edit the offsets.");
                    ImGui::SameLine();
                    ImGui::BeginDisabled(!state.manual_active);
                    action_button("Snap", EditorAction::AttachSnap, attach_half);
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip(
                            "Store offsets that reproduce the current free camera pose. Fly to frame, then Snap.");
                    ImGui::EndDisabled();
                    ImGui::EndDisabled();
                    if (!state.attach_selected)
                        ImGui::TextDisabled("Choose a player to preview or snap.");
                    else if (!state.manual_active && !state.follow_active)
                        ImGui::TextDisabled("Start Fly camera to preview or snap.");
                    else if (state.attach_preview)
                        ImGui::TextDisabled("Preview active; fly to edit the offsets live.");
                    else
                        ImGui::TextDisabled("Fly to frame, then Snap, then Attach here.");
                    ImGui::Spacing();
                    action_button("Cycle target", EditorAction::AttachCycleTarget, attach_half, 1);
                    ImGui::SameLine();
                    action_button("Reset", EditorAction::AttachReset, attach_half);
                    ImGui::EndDisabled();
                    if (!state.attach_available)
                        ImGui::TextDisabled("Load a supported replay to choose a camera target.");
                }
                end_panel_card();
                ImGui::EndDisabled();
                ImGui::EndTabItem();
            }
            if (ImGui::BeginTabItem("LOOK")) {
                ImGui::BeginDisabled(!state.ready || state.busy);
                if (begin_panel_card("##appearance-card")) {
                    section_title("Scene appearance", "Replay view");
                    ImGui::Spacing();
                    ImGui::BeginDisabled(state.playing);
                    action_button("Clear ragdolls", EditorAction::DestroyRagdolls,
                                  ImGui::GetContentRegionAvail().x);
                    ImGui::EndDisabled();
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip(
                            "Clear accumulated ragdolls after repeated shot playback.");
                    ImGui::Spacing();
                    ImGui::BeginDisabled(state.playing);
                    action_button("Toggle Citadel glow", EditorAction::ToggleCitadelGlow,
                                  ImGui::GetContentRegionAvail().x);
                    ImGui::EndDisabled();
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip(
                            "Toggle hero, trooper, boss and health-bar glow together.");
                    ImGui::Spacing();
                    ImGui::BeginDisabled(state.playing);
                    action_button("Toggle floating health bars", EditorAction::ToggleHealthbars,
                                  ImGui::GetContentRegionAvail().x);
                    ImGui::EndDisabled();
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip(
                            "Hide or restore floating unit bars. Replay HUD controls the hero health panel. Bar glow stays with Toggle Citadel glow.");
                    ImGui::Spacing();
                    ImGui::BeginDisabled(state.playing);
                    action_button("Near player opacity fix", EditorAction::NearPlayerOpacityFix,
                                  ImGui::GetContentRegionAvail().x);
                    ImGui::EndDisabled();
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip(
                            "Force full opacity on the near-player camera fades used while editing.");
                    ImGui::Spacing();
                }
                end_panel_card();
                if (begin_panel_card("##confetti-card")) {
                    section_title("Particles", "Native effect");
                    bool enabled = state.confetti_enabled;
                    if (compact_checkbox("Enable particles", &enabled, panel_scale))
                        editor_enqueue(EditorAction::SetConfettiEnabled, enabled ? 1 : 0);
                    static float height_draft = 250.0f;
                    static bool height_editing = false;
                    if (!height_editing)
                        height_draft = float(state.confetti_spawn_height);
                    const auto height_row =
                        slider_row("##confetti-height", "Spawn height", &height_draft, 100.0f,
                                   1500.0f, "%.0f", 92.0f);
                    height_editing = height_row.active;
                    if (height_row.committed)
                        editor_enqueue(EditorAction::SetConfettiSpawnHeight,
                                       double(std::round(height_draft)));
                    bool despawn = state.confetti_despawn_on_ground;
                    if (compact_checkbox("Despawn on ground", &despawn, panel_scale))
                        editor_enqueue(EditorAction::SetConfettiDespawnOnGround, despawn ? 1 : 0);
                    ImGui::PushStyleColor(ImGuiCol_Text,
                                          ImGui::GetStyleColorVec4(ImGuiCol_TextDisabled));
                    ImGui::TextWrapped(
                        "Preset is chosen in Dolly; it follows the rendered camera and game time.");
                    ImGui::PopStyleColor();
                }
                end_panel_card();
                if (begin_panel_card("##dof-card")) {
                    section_title("Native Depth of Field", "Dolly");
                    ImGui::BeginDisabled(!state.dof_available || !state.paused || state.playing ||
                                         !state.camera_count);
                    bool enabled = state.dof[0] != 0 && state.dof[1] != 0 &&
                                   std::any_of(state.dof.begin() + 2, state.dof.begin() + 6,
                                               [](double value) { return value != 0; });
                    if (compact_checkbox("Enable DOF", &enabled, panel_scale))
                        editor_enqueue(EditorAction::SetDofEnabled, enabled ? 1 : 0);
                    static std::array<double, 11> dof_draft{};
                    static std::array<bool, 11> dof_editing{};
                    auto field = [&](unsigned index, const char* label, float step,
                                     double defaults) {
                        if (!dof_editing[index])
                            dof_draft[index] = state.dof[index];
                        ImGui::PushID(int(index));
                        ImGui::TextDisabled("%s", label);
                        ImGui::SetNextItemWidth(-1);
                        ImGui::DragScalar("##value", ImGuiDataType_Double, &dof_draft[index], step,
                                          nullptr, nullptr, "%.2f");
                        bool committed = ImGui::IsItemDeactivatedAfterEdit();
                        dof_editing[index] = ImGui::IsItemActive();
                        if (ImGui::IsItemClicked(ImGuiMouseButton_Right)) {
                            dof_draft[index] = defaults;
                            dof_editing[index] = false;
                            committed = true;
                        }
                        if (ImGui::IsItemHovered())
                            ImGui::SetTooltip(
                                "Drag to adjust. Alt: 100x finer; Shift: 10x faster. Ctrl+click to type an exact value. Right-click to reset to Dolly's default. Applies when released.");
                        if (committed)
                            editor_enqueue(
                                EditorAction(unsigned(EditorAction::SetDofEnabled) + index),
                                dof_draft[index]);
                        ImGui::PopID();
                    };
                    ImGui::BeginDisabled(!enabled);
                    ImGui::PushStyleVar(ImGuiStyleVar_FramePadding,
                                        ImVec2(6 * panel_scale, 3 * panel_scale));
                    ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing,
                                        ImVec2(10 * panel_scale, 4 * panel_scale));
                    ImGui::TextDisabled("Focus ranges");
                    if (ImGui::BeginTable("##focus-ranges", 2, ImGuiTableFlags_SizingStretchSame)) {
                        ImGui::TableNextColumn();
                        field(2, "Near blurry", 1, -100);
                        ImGui::TableNextColumn();
                        field(4, "Far crisp", 1, 180);
                        ImGui::TableNextColumn();
                        field(3, "Near crisp", 1, 0);
                        ImGui::TableNextColumn();
                        field(5, "Far blurry", 1, 2000);
                        ImGui::TableNextColumn();
                        field(10, "Ground tilt", .01f, .5);
                        ImGui::EndTable();
                    }
                    ImGui::PopStyleVar(2);
                    ImGui::EndDisabled();
                    ImGui::EndDisabled();
                    if (!state.camera_count)
                        ImGui::TextWrapped("Capture a camera to author DOF settings.");
                    else
                        ImGui::TextWrapped(
                            "Alt: fine adjust | Ctrl+click: type.\nChanges save to Effects at the playhead.");
                }
                end_panel_card();
                if (begin_panel_card("##citadel-dof-card")) {
                    section_title("Citadel Depth of Field", "Game engine");
                    ImGui::BeginDisabled(!state.citadel_dof_available || !state.paused ||
                                         state.playing || !state.camera_count);
                    bool citadel_enabled = state.citadel_dof_enabled;
                    if (compact_checkbox("Enable DOF", &citadel_enabled, panel_scale))
                        editor_enqueue(EditorAction::SetCitadelDofEnabled, citadel_enabled ? 1 : 0);
                    ImGui::PushStyleVar(ImGuiStyleVar_FramePadding,
                                        ImVec2(6 * panel_scale, 3 * panel_scale));
                    ImGui::PushStyleVar(ImGuiStyleVar_ItemSpacing,
                                        ImVec2(10 * panel_scale, 4 * panel_scale));
                    static float sensor_draft = 0;
                    static bool sensor_editing = false;
                    if (!sensor_editing)
                        sensor_draft = float(state.citadel_dof_sensor);
                    ImGui::TextDisabled("Sensor size");
                    ImGui::SetNextItemWidth(-1);
                    ImGui::SliderFloat("##citadel-sensor", &sensor_draft, .5f, 3.0f, "%.2f",
                                       ImGuiSliderFlags_AlwaysClamp);
                    bool sensor_committed = ImGui::IsItemDeactivatedAfterEdit();
                    sensor_editing = ImGui::IsItemActive();
                    if (ImGui::IsItemClicked(ImGuiMouseButton_Right)) {
                        sensor_draft = 1.0f;
                        sensor_editing = false;
                        sensor_committed = true;
                    }
                    if (sensor_committed)
                        editor_enqueue(EditorAction::SetCitadelDofSensorSize, double(sensor_draft));
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip(
                            "Lens sensor size in inches, 0.5 to 3.0. Right-click to reset to 1.");
                    constexpr double kFocusMaximum = 10000.0;
                    const auto focus_to_slider = [kFocusMaximum](double value) {
                        return float(std::log(1.0 + std::max(0.0, value)) /
                                     std::log(1.0 + kFocusMaximum));
                    };
                    const auto slider_to_focus = [kFocusMaximum](double value) {
                        return std::exp(std::clamp(value, 0.0, 1.0) *
                                        std::log(1.0 + kFocusMaximum)) -
                               1.0;
                    };
                    static float focus_draft = 0;
                    static bool focus_editing = false;
                    if (!focus_editing)
                        focus_draft = focus_to_slider(state.citadel_dof_focus);
                    char focus_label[64]{};
                    std::snprintf(focus_label, sizeof(focus_label), "Focus distance · %.0f in",
                                  slider_to_focus(focus_draft));
                    ImGui::TextDisabled("%s", focus_label);
                    ImGui::SetNextItemWidth(-1);
                    ImGui::SliderFloat("##citadel-focus", &focus_draft, 0.0f, 1.0f, "",
                                       ImGuiSliderFlags_AlwaysClamp);
                    const bool focus_committed = ImGui::IsItemDeactivatedAfterEdit();
                    focus_editing = ImGui::IsItemActive();
                    if (ImGui::IsItemClicked(ImGuiMouseButton_Right)) {
                        focus_draft = focus_to_slider(200.0);
                        focus_editing = false;
                        editor_enqueue(EditorAction::SetCitadelDofFocusDistance, 200.0);
                    } else if (focus_committed)
                        editor_enqueue(EditorAction::SetCitadelDofFocusDistance,
                                       slider_to_focus(focus_draft));
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip(
                            "Focus distance in inches, 0-10000 on a log scale. Right-click to reset to 200.");
                    ImGui::PopStyleVar(2);
                    ImGui::EndDisabled();
                    if (!state.camera_count)
                        ImGui::TextWrapped("Capture a camera to author DOF settings.");
                    else
                        ImGui::TextWrapped("Changes save to Effects at the playhead.");
                }
                end_panel_card();
                if (begin_panel_card("##reshade-card")) {
                    section_title("Effects", "ReShade");
                    if (reshade_available()) {
                        if (ImGui::Button("ReShade menu", ImVec2(-1, 0)))
                            editor_enqueue(EditorAction::ReShade);
                    } else {
                        ImGui::TextWrapped(
                            "Select the ReShade runtime in the desktop Settings to enable it.");
                    }
                }
                end_panel_card();
                ImGui::EndDisabled();
                ImGui::EndTabItem();
            }
            if (ImGui::BeginTabItem("EXPORT")) {
                if (begin_panel_card("##export-camera")) {
                    section_title("Camera source", "Export");
                    const auto capture_state = video::status().state;
                    ImGui::BeginDisabled(state.busy || state.playing ||
                                         capture_state == video::State::starting ||
                                         capture_state == video::State::recording ||
                                         capture_state == video::State::finalizing);
                    int source = state.video_pov ? 1 : 0;
                    if (ImGui::Combo("Source", &source, "Camera path / Bone camera\0Player POV\0"))
                        editor_enqueue(EditorAction::SetVideoSource, source);
                    const bool single_attach = state.camera_count == 1 && state.attach_selected;
                    if (state.video_pov || single_attach) {
                        char duration_label[32];
                        std::snprintf(duration_label, sizeof(duration_label), "%.3g seconds",
                                      state.pov_duration);
                        if (ImGui::BeginCombo("Replay duration", duration_label)) {
                            for (const double seconds : {1., 2., 5., 10., 15., 30., 60., 120.}) {
                                char label[32];
                                std::snprintf(label, sizeof(label), "%.0f seconds", seconds);
                                if (ImGui::Selectable(label, seconds == state.pov_duration))
                                    editor_enqueue(EditorAction::SetPovDuration, seconds);
                            }
                            ImGui::EndCombo();
                        }
                        if (single_attach && !state.video_pov)
                            ImGui::TextWrapped(
                                "Record saves an end time for this bone camera and records the attached shot. Offsets and smoothing are preserved.");
                        else
                            ImGui::TextWrapped(
                                "F9: select a hero and pause at the start. F8: return here. Record POV hides the HUD and stops after this segment.");
                    }
                    ImGui::EndDisabled();
                    if (!state.video_pov && !state.camera_count) {
                        ImGui::TextWrapped("Capture at least two camera views to record a shot.");
                    } else if (!state.video_pov && state.attach_selected) {
                        EditorRoster roster{};
                        char target[112] = "Saved player";
                        if (editor_roster_snapshot(roster) &&
                            state.attach_target_index < roster.count)
                            roster_label(roster.players[state.attach_target_index], target,
                                         sizeof(target));
                        ImGui::TextWrapped("View %u: Attach - %s", state.selected_camera + 1,
                                           target);
                        if (state.attach_point == 2)
                            ImGui::TextWrapped("Bone: %s", state.attach_bone);
                        else
                            ImGui::TextUnformatted(state.attach_point == 1 ? "Point: Weapon"
                                                                           : "Point: Eyes");
                        ImGui::TextDisabled("%u of %u views use attachment", state.attach_keys,
                                            state.shot_keys);
                    } else if (!state.video_pov) {
                        ImGui::TextWrapped("View %u: Free path", state.selected_camera + 1);
                    }
                    if (!state.video_pov)
                        ImGui::TextWrapped(
                            "Export uses saved sources and offsets, including edits made while previewing.");
                }
                end_panel_card();
                // The in-game controls mirror the desktop values through a
                // Python config round trip (~100 ms). Apply a clicked value
                // immediately so a control cannot flicker back to the
                // outgoing value while the acknowledgement travels.
                struct Pending {
                    int id = -1;
                    double value = 0;
                    std::uint64_t until = 0;
                };
                static Pending pending;
                const auto now_ms = GetTickCount64();
                const auto shown = [&](int id, double current) {
                    if (pending.id == id) {
                        if (now_ms < pending.until)
                            return pending.value;
                        pending.id = -1;
                    }
                    return current;
                };
                const auto commit = [&](int id, double value, EditorAction action) {
                    pending = {id, value, now_ms + 1000};
                    editor_enqueue(action, value);
                };
                if (begin_panel_card("##export-passes")) {
                    section_title("Output passes", "Color included");
                    bool depth_master = shown(5, state.video_depth ? 1.0 : 0.0) != 0.0;
                    if (ImGui::Checkbox("Depth master (.mov)", &depth_master))
                        commit(5, depth_master ? 1.0 : 0.0, EditorAction::SetVideoDepth);
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip(
                            "Write a matching ProRes depth.mov and preview video in the take's depth folder. Requires a verified scene depth.");
                    bool depth_exr = shown(7, state.video_depth_exr ? 1.0 : 0.0) != 0.0;
                    if (ImGui::Checkbox("EXR sequence (float)", &depth_exr))
                        commit(7, depth_exr ? 1.0 : 0.0, EditorAction::SetVideoDepthExr);
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip(
                            "Also write the float EXR precision master under the depth folder's exr/ subfolder.");
                    bool layer_world = shown(8, state.video_layer_world ? 1.0 : 0.0) != 0.0;
                    if (ImGui::Checkbox("World layer", &layer_world))
                        commit(8, layer_world ? 1.0 : 0.0, EditorAction::SetVideoLayerWorld);
                    bool layer_players = shown(9, state.video_layer_players ? 1.0 : 0.0) != 0.0;
                    if (ImGui::Checkbox("Players layer", &layer_players))
                        commit(9, layer_players ? 1.0 : 0.0, EditorAction::SetVideoLayerPlayers);
                    bool layer_effects = shown(10, state.video_layer_effects ? 1.0 : 0.0) != 0.0;
                    if (ImGui::Checkbox("Effects layer", &layer_effects))
                        commit(10, layer_effects ? 1.0 : 0.0, EditorAction::SetVideoLayerEffects);
                }
                end_panel_card();
                if (begin_panel_card("##export-settings")) {
                    char bitrate[64]{};
                    std::snprintf(bitrate, sizeof(bitrate), "%u Mbps", state.video_bitrate_mbps);
                    section_title("Capture", bitrate);
                    ImGui::TextUnformatted("Video FPS");
                    ImGui::SetNextItemWidth(-1);
                    const unsigned shown_fps = unsigned(shown(1, state.video_fps));
                    char fps_label[16]{};
                    std::snprintf(fps_label, sizeof(fps_label), "%u", shown_fps);
                    if (ImGui::BeginCombo("##export-fps", fps_label)) {
                        for (unsigned value : {30u, 60u, 120u, 300u, 600u}) {
                            char label[16]{};
                            std::snprintf(label, sizeof(label), "%u", value);
                            if (ImGui::Selectable(label, value == shown_fps))
                                commit(1, double(value), EditorAction::SetVideoFps);
                        }
                        ImGui::EndCombo();
                    }
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip(
                            "30-120 record in real time; 300 and 600 need Fixed-step export.");
                    ImGui::Spacing();
                    if (ImGui::CollapsingHeader("Encoder & quality")) {
                        ImGui::TextUnformatted("Bitrate");
                        ImGui::SetNextItemWidth(-1);
                        const unsigned shown_bitrate = unsigned(shown(2, state.video_bitrate_mbps));
                        char bitrate_label[24]{};
                        std::snprintf(bitrate_label, sizeof(bitrate_label), "%u Mbps",
                                      shown_bitrate);
                        if (ImGui::BeginCombo("##export-bitrate", bitrate_label)) {
                            for (unsigned value : {10u, 20u, 40u}) {
                                char label[24]{};
                                std::snprintf(label, sizeof(label), "%u Mbps", value);
                                if (ImGui::Selectable(label, value == shown_bitrate))
                                    commit(2, double(value), EditorAction::SetVideoBitrate);
                            }
                            ImGui::EndCombo();
                        }
                        ImGui::TextUnformatted("Encoder");
                        ImGui::SetNextItemWidth(-1);
                        const unsigned shown_codec = unsigned(shown(3, state.video_codec));
                        if (ImGui::BeginCombo("##export-encoder", video_codec_label(shown_codec))) {
                            for (std::uint32_t id = 0; id <= 10; ++id) {
                                if (ImGui::Selectable(video_codec_label(id), id == shown_codec))
                                    commit(3, double(id), EditorAction::SetVideoEncoder);
                            }
                            ImGui::EndCombo();
                        }
                    }
                    ImGui::Spacing();
                    bool fixed_step = shown(4, state.video_fixed_step ? 1.0 : 0.0) != 0.0;
                    if (ImGui::Checkbox("Fixed-step export (frame-accurate)", &fixed_step))
                        commit(4, fixed_step ? 1.0 : 0.0, EditorAction::SetVideoFixedStep);
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip(
                            "Render exactly one frame per output frame. Required for 300/600 FPS and removes real-time encoder hitching.");
                    ImGui::Spacing();
                    ImGui::Spacing();
                    ImGui::TextUnformatted("Export speed");
                    ImGui::SetNextItemWidth(-1);
                    const double shown_speed = shown(6, state.video_speed);
                    char export_speed[32]{};
                    std::snprintf(export_speed, sizeof(export_speed), "%.3g x", shown_speed);
                    if (ImGui::BeginCombo("##export-speed", export_speed)) {
                        for (double value : {.05, .1, .25, .5, 1.0, 2.0, 4.0}) {
                            char label[32]{};
                            std::snprintf(label, sizeof(label), "%.3g x", value);
                            if (ImGui::Selectable(label, value == shown_speed))
                                commit(6, value, EditorAction::SetVideoSpeed);
                        }
                        ImGui::EndCombo();
                    }
                    if (ImGui::IsItemHovered())
                        ImGui::SetTooltip(
                            "Slow-motion playback rate for fixed-step export. Shared with the desktop Export tab.");
                }
                end_panel_card();
                if (begin_panel_card("##video-card")) {
                    section_title("Recording", "Video");
                    const auto recording = video::status();
                    const bool active = recording.state == video::State::starting ||
                                        recording.state == video::State::recording;
                    ImGui::BeginDisabled(!state.ready || state.busy);
                    ImGui::BeginDisabled(active || recording.state == video::State::finalizing ||
                                         state.camera_count < 2 || state.playing ||
                                         state.video_pov);
                    action_button("Play shot", EditorAction::PlayPath,
                                  ImGui::GetContentRegionAvail().x);
                    ImGui::EndDisabled();
                    if (active) {
                        ImGui::Text("%.1f s  |  %llu frames",
                                    double(recording.duration_100ns) / 1e7,
                                    static_cast<unsigned long long>(recording.frames_written));
                        action_button("Finish recording", EditorAction::StopVideo,
                                      ImGui::GetContentRegionAvail().x);
                    } else {
                        ImGui::BeginDisabled(recording.state == video::State::finalizing);
                        action_button(recording.state == video::State::finalizing
                                          ? "Finalizing video..."
                                          : (state.video_pov ? "Record POV" : "Record video"),
                                      EditorAction::StartVideo, ImGui::GetContentRegionAvail().x);
                        ImGui::EndDisabled();
                        ImGui::BeginDisabled(recording.state == video::State::finalizing ||
                                             state.playing || state.video_pov);
                        action_button("Screenshot (hero + plate)", EditorAction::TakeScreenshot,
                                      ImGui::GetContentRegionAvail().x);
                        ImGui::EndDisabled();
                        if (ImGui::IsItemHovered())
                            ImGui::SetTooltip("High-res still of the current paused view: plate, "
                                              "players matte and depth. Desktop Export tab.");
                    }
                    ImGui::EndDisabled();
                    if (recording.frames_dropped)
                        ImGui::Text("Missed capture slots: %llu",
                                    static_cast<unsigned long long>(recording.frames_dropped));
                    ImGui::TextDisabled("Output folder and FFmpeg runtime: desktop Export tab.");
                }
                end_panel_card();
                ImGui::EndTabItem();
            }
            ImGui::EndTabBar();
        }
        ImGui::EndChild();
        ImGui::PopStyleColor();
        ImGui::Spacing();
        ImGui::Spacing();
        const float stop_cap = key_cap_width(EditorAction::Stop, panel_scale);
        action_button("Stop / restore", EditorAction::Stop,
                      ImGui::GetContentRegionAvail().x -
                          (stop_cap > 0 ? stop_cap + ImGui::GetStyle().ItemSpacing.x : 0));
        if (stop_cap > 0) {
            ImGui::SameLine();
            ImGui::AlignTextToFramePadding();
            key_cap(EditorAction::Stop, panel_scale);
        }
        if (state.message[0])
            ImGui::TextWrapped("%s", state.message);
        ImGui::TextDisabled("F7  Console");
        if (ImGui::IsItemHovered())
            ImGui::SetTooltip(
                "Open Deadlock's console. Customize editor shortcuts in the launcher.");
    }
    ImGui::End();
    if (close)
        editor_enqueue(EditorAction::Flight);
}

} // namespace dolly
