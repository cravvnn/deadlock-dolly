// Kara's production component/layout port. All values come from editor publications;
// all changes use the existing bounded editor action queue, never preview simulation.
#include "dolly_overlay_panel.hpp"
#include "dolly_rail_design.hpp"
#include "dolly_reshade.hpp"
#include "dolly_weather_runtime.hpp"
#include "dolly_lens_rain_runtime.hpp"
#include "dolly_lens_rain_wire.hpp"
#include "dolly_capture_estimate.hpp"
#include "imgui_internal.h"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <optional>
#include <string>
#include <vector>
namespace dolly {
namespace k = rail_kit;
namespace f = rail_fields;
namespace t = rail_theme;
namespace {
// Frames of the take the current Export settings would record; 0 when its
// length is unknown (a free-camera take). Same rule as the desktop estimate.
std::uint64_t export_take_frames(const EditorSnapshot& s) noexcept {
    double seconds = 0;
    if (s.video_pov || (s.camera_count == 1 && s.attach_selected))
        seconds = s.pov_duration;
    else if (s.camera_count >= 2)
        seconds = s.duration;
    return estimate::take_frames(seconds, s.video_fps, s.video_speed, s.video_fixed_step);
}
// Estimate and progress lines keep two lines of height, so the divider and the
// controls below do not jump when the text wraps or its numbers grow.
void two_line_text(const std::string& text, const ImVec4& color) {
    const float top = k::CursorY();
    f::Wrapped(text.c_str(), color);
    const float used = k::CursorY() - top;
    if (used < 32)
        k::VGap(32 - used);
}
double export_take_seconds(const EditorSnapshot& s) noexcept {
    const double seconds =
        s.video_pov || (s.camera_count == 1 && s.attach_selected) ? s.pov_duration : s.duration;
    return estimate::video_seconds(seconds, s.video_speed, s.video_fixed_step);
}
constexpr double speeds[]{.05, .1, .25, .5, 1, 2, 4};
const char* speed_labels[]{"0.05x", "0.1x", "0.25x", "0.5x", "1x", "2x", "4x"};
int nearest(double v, const double* items, int count) {
    int n = 0;
    for (int i = 1; i < count; ++i)
        if (std::abs(v - items[i]) < std::abs(v - items[n]))
            n = i;
    return n;
}
void split() {
    k::VGap(15);
    k::Divider();
    k::VGap(15);
}
std::string binding(EditorAction action, const char* fallback = "") {
    auto b = editor_binding_snapshot(action);
    if (!b.vk)
        return fallback;
    std::string text;
    if (b.modifiers & 1)
        text += "Ctrl+";
    if (b.modifiers & 2)
        text += "Alt+";
    if (b.modifiers & 4)
        text += "Shift+";
    char name[48]{};
    LONG scan = MapVirtualKeyW(b.vk, MAPVK_VK_TO_VSC) << 16;
    if (b.vk >= VK_PRIOR && b.vk <= VK_DOWN)
        scan |= 1 << 24;
    GetKeyNameTextA(scan, name, sizeof(name));
    text += name;
    return text;
}
bool row_dropdown(const char* label, const char* id, int* value, const char* const* items,
                  int count, bool off = false, float indent = 0, float label_width = 84) {
    k::BeginRow(30, 10);
    k::HGap(indent);
    k::Label(label, label_width);
    bool changed = k::Dropdown(id, value, items, count, 0, off);
    k::EndRow();
    return changed;
}
}
void OverlayPanel::draw_kara_footer(const EditorSnapshot& s, bool record_action) {
    auto& d = *rail_design_;
    k::BeginRow(30, 8);
    if (k::Button("Stop / restore", 104))
        d.send(EditorAction::Stop);
    const auto stop = binding(EditorAction::Stop, "Backspace");
    k::ShortcutChip(stop.c_str(), 74);
    const auto rec = editor_recording_status();
    if (record_action && editor_recording_locks_cameras(rec.state)) {
        if (k::Button(rec.state == video::State::finalizing ? "Finalizing video..."
                                                            : "Finish recording",
                      140, k::Btn::Primary, rec.state == video::State::finalizing))
            d.send(EditorAction::StopVideo);
    } else if (lite_page_ == 3) {
        if (reshade_available()) {
            if (k::Button("ReShade menu", 110))
                d.send(EditorAction::ReShade);
        } else
            k::Text("ReShade not set", t::text_dim, k::Role::Body, 18, 94);
    } else if (k::Avail() > 240)
        f::Truncated(s.message, k::Avail() - 100, t::text_dim);
    k::Spring(84);
    k::ShortcutChip("F7", 29);
    if (k::Button("Console", 47, k::Btn::Quiet))
        d.send(EditorAction::Console);
    k::EndRow();
}
void OverlayPanel::draw_kara_drawer(const EditorSnapshot& s, unsigned tab, bool measuring) {
    auto& d = *rail_design_;
    d.measuring = measuring;
    const auto rec = editor_recording_status();
    const bool locked = editor_recording_locks_cameras(rec.state);
    const bool live = s.ready && !s.busy;
    EditorCameraList list{};
    const bool list_ok = editor_camera_list(list) && list.total == s.camera_count;
    // Never carry a scalar gesture onto a different camera or a replacement shot.
    // Campaths rows are keyed by their own camera and guard their own edits: a
    // Bank drag selects its camera with the first step and must carry on.
    if (!measuring && tab != 5 &&
        (d.selected != s.selected_camera || (list_ok && d.revision != list.revision))) {
        bool active = false;
        for (auto& it : d.numbers)
            active |= it.second.active;
        if (active)
            ImGui::ClearActiveID();
        d.numbers.clear();
        d.selected = s.selected_camera;
        d.revision = list_ok ? list.revision : 0;
    }
    auto button = [&](const char* label, EditorAction action, float width = 0, double value = 0,
                      bool off = false, k::Btn kind = k::Btn::Secondary) {
        if (k::Button(label, width, kind, off))
            d.send(action, value);
    };
    auto check = [&](const char* label, bool value, EditorAction action, bool off = false) {
        if (k::Checkbox(label, &value, off))
            d.send(action, value ? 1 : 0);
    };
    auto scalar = [&](const char* label, double published, float low, float high, const char* fmt,
                      EditorAction action, bool row = true, f::SliderOpts opt = f::SliderOpts{}) {
        auto& n = d.number(label, published);
        float value = float(n.value);
        auto e = row ? f::SliderRow(label, &value, low, high, fmt, opt)
                     : f::SliderLine(label, &value, low, high, fmt, 64, 0, opt);
        n.value = value;
        d.finish(n, e, action);
    };
    if (tab == 0) {
        {
            f::Greyed dim(!live);
            char saved[32];
            std::snprintf(saved, sizeof(saved), "%u saved", s.camera_count);
            k::SectionTitle("Replay and shot", nullptr, saved);
            k::VGap(15);
            constexpr int steps[]{1, 2, 5, 10, 25};
            const char* labels[]{"1 tick", "2 ticks", "5 ticks", "10 ticks", "25 ticks"};
            int step = 0;
            for (int i = 0; i < 5; ++i)
                if (steps[i] == lite_tick_step_)
                    step = i;
            const bool step_off = s.playing || s.attach_preview;
            k::BeginRow(30, 8);
            if (k::Dropdown("##cam-step", &step, labels, 5, 88, step_off))
                lite_tick_step_ = steps[step];
            k::Spring(65);
            if (k::ChevronButton("##cam-back", k::Dir::Left, step_off))
                d.send(EditorAction::StepReplayTicks, -lite_tick_step_);
            k::HGap(-3);
            if (k::ChevronButton("##cam-next", k::Dir::Right, step_off))
                d.send(EditorAction::StepReplayTicks, lite_tick_step_);
            k::EndRow();
            k::VGap(10);
            int speed = nearest(s.playback_speed, speeds, 7);
            if (row_dropdown("Speed", "##cam-speed", &speed, speed_labels, 7, false, 0, 48))
                d.send(EditorAction::SetPlaybackSpeed, speeds[speed]);
            split();
            k::BeginRow(30, 8);
            const auto cap = binding(EditorAction::Capture);
            const float cap_width =
                cap.empty() ? 0 : std::max(72.f, k::TextWidth(cap.c_str(), k::Role::Chip) + 14);
            button("Capture camera here", EditorAction::Capture, cap_width ? -(cap_width + 8) : 0,
                   0, locked, k::Btn::Primary);
            if (cap_width)
                k::ShortcutChip(cap.c_str(), cap_width);
            k::EndRow();
            k::VGap(10);
            if (k::Button("Reset camera path", 0, k::Btn::Secondary,
                          s.playing || !s.camera_count || locked)) {
                d.reset = true;
                d.confirm_revision = list_ok ? list.revision : 0;
            }
            split();
            k::BeginRow(30, 8);
            button("Previous view", EditorAction::PreviousView, 197.5f, 0,
                   !s.camera_count || s.playing);
            button("Next view", EditorAction::NextView, 0, 0, !s.camera_count || s.playing);
            k::EndRow();
            k::VGap(10);
            k::Checkbox("Show path guides", &show_path_guides);
            k::VGap(10);
            k::BeginRow(18, 12);
            k::Label("Framing guide", 81);
            bool grid = framing_grid_visible(s);
            if (k::Checkbox("Rule of thirds", &grid))
                d.send(EditorAction::FramingGrid);
            k::EndRow();
        }
        split();
        {
            f::Greyed dim(!live);
            k::SectionTitle("Free camera",
                            "Set movement speed. Mouse wheel adjusts framing while flying.");
            k::VGap(15);
            scalar("Movement speed", s.speed, 1, 10000, "%.0f", EditorAction::SetSpeed, true,
                   f::Opts(f::Map::Log));
            k::VGap(10);
            k::BeginRow(30, 8);
            button("Fly camera", EditorAction::Flight, 178.5);
            button("Heroes / game UI", EditorAction::GameUI, 178.5, 1);
            k::ShortcutChip(binding(EditorAction::GameUI, "F9").c_str(), 30);
            k::EndRow();
            k::VGap(10);
            const char* rates[]{"30", "60", "120"};
            int rate = s.playback_rate == 30 ? 0 : s.playback_rate == 120 ? 2 : 1;
            if (row_dropdown("Updates / s", "##cam-updates", &rate, rates, 3, s.playing, 0, 76))
                d.send(EditorAction::SetPlaybackRate, rate == 0 ? 30 : rate == 1 ? 60 : 120);
            k::VGap(10);
            check("Show game HUD during replay", s.replay_show_hud, EditorAction::SetReplayHud);
        }
        split();
        draw_kara_footer(s);
    } else if (tab == 1) {
        {
            f::Greyed dim(!live);
            k::SectionTitle("Follow a player", nullptr,
                            s.follow_active ? "Active" : "Choose a target");
            k::VGap(15);
            EditorRoster roster{};
            const bool roster_ok = editor_roster_snapshot(roster);
            std::vector<std::string> names;
            std::vector<const char*> items;
            int index = -1;
            if (roster_ok)
                for (unsigned i = 0; i < std::min<unsigned>(roster.count, kEditorRosterPlayers);
                     ++i) {
                    char name[112];
                    roster_label(roster.players[i], name, sizeof(name));
                    names.emplace_back(name);
                    if (roster.players[i].handle == d.follow_handle)
                        index = i;
                }
            for (auto& name : names)
                items.push_back(name.c_str());
            const bool off = !s.follow_available || s.playing || !s.paused || s.attach_preview;
            k::BeginRow(30, 10);
            k::Label("Replay hero", 80);
            int chosen = f::Combo("##follow-hero", index, items.data(), int(items.size()),
                                  index >= 0 ? items[index] : "Select a hero...", 0, off,
                                  "Waiting for replay players...");
            if (chosen >= 0)
                d.follow_handle = roster.players[chosen].handle;
            k::EndRow();
            k::VGap(10);
            button("Restore rig", EditorAction::StopGameFollow, 0, 0, !s.follow_pending);
            if (!s.follow_available || s.attach_preview) {
                k::VGap(10);
                k::Notice(s.attach_preview ? "Detach the bone preview to use Follow."
                                           : "Follow unavailable for this game build.");
            }
            k::VGap(10);
            k::BeginRow(18, 8);
            check("Show game HUD during replay", s.replay_show_hud, EditorAction::SetReplayHud);
            k::InfoIcon(
                "Applies to Follow and ordinary replay playback. Pause restores the previous HUD.");
            k::EndRow();
            split();
            k::SectionTitle("Camera position", nullptr, "Follow rig");
            k::VGap(15);
            double published[]{s.follow_distance, s.follow_shoulder, s.follow_height};
            for (int i = 0; i < 3; ++i)
                if (d.follow_published[i] != published[i]) {
                    d.follow[i] = float(published[i]);
                    d.follow_published[i] = published[i];
                }
            {
                f::Inert inert(off);
                const char* labels[]{"Distance", "Shoulder", "Height"};
                for (int i = 0; i < 3; ++i) {
                    f::SliderLine(labels[i], &d.follow[i], i ? -150.f : 0.f, i ? 150.f : 400.f,
                                  "%.0f", 64, 0,
                                  f::Resets(i == 0   ? 135.f
                                            : i == 1 ? 34.f
                                                     : 0.f));
                    if (i < 2)
                        k::VGap(10);
                }
                k::VGap(10);
                if (k::Button(s.follow_active ? "Apply Follow" : "Preview Follow", 0,
                              k::Btn::Secondary, index < 0) &&
                    index >= 0) {
                    CameraPose p{};
                    p[0] = d.follow[1];
                    p[1] = d.follow[2];
                    p[2] = index;
                    p[3] = roster.players[index].handle;
                    p[4] = roster.players[index].entity_index;
                    p[5] = std::uint32_t(roster.players[index].model);
                    p[6] = std::uint32_t(roster.players[index].model >> 32);
                    d.send(EditorAction::StartGameFollow, d.follow[0], &p);
                }
            }
        }
        split();
        draw_kara_footer(s);
    } else if (tab == 2) {
        {
            f::Greyed dim(!live);
            k::BeginColumns(30);
            k::SectionTitle("Bone camera",
                            "Choose a player, pick an attachment point, then frame the camera.",
                            "Player or weapon");
            k::VGap(15);
            EditorRoster roster{};
            bool roster_ok = editor_roster_snapshot(roster);
            std::vector<std::string> names{"Free path"};
            std::vector<const char*> items;
            if (roster_ok)
                for (unsigned i = 0; i < std::min<unsigned>(roster.count, kEditorRosterPlayers);
                     ++i) {
                    char name[112];
                    roster_label(roster.players[i], name, sizeof(name));
                    names.emplace_back(name);
                }
            for (auto& name : names)
                items.push_back(name.c_str());
            int target = s.attach_selected ? int(s.attach_target_index + 1) : 0;
            target = std::clamp(target, 0, int(items.size() - 1));
            if (k::Dropdown("##attach-player", &target, items.data(), int(items.size()), 0,
                            !s.attach_available))
                d.send(target ? EditorAction::SetAttachTarget : EditorAction::AttachReset,
                       target ? target - 1 : 0);
            k::VGap(10);
            const char* points[]{"Eyes", "Weapon", "Bone Picker..."};
            const char* closed = s.attach_point == 2   ? "Point: Bone"
                                 : s.attach_point == 1 ? "Point: Weapon"
                                                       : "Point: Eyes";
            int point = f::Combo("##attach-point", s.attach_point, points, 3, closed, 0,
                                 !s.attach_available);
            if (point >= 0)
                d.send(point == 2 ? EditorAction::OpenBonePicker : EditorAction::SetAttachPoint,
                       point == 2 ? 0 : point);
            if (s.attach_point == 2)
                k::ItemTooltip(s.attach_bone[0] ? s.attach_bone : "Choose a bone");
            split();
            const bool attach_ready = s.attach_available && s.attach_selected &&
                                      (s.manual_active || s.follow_active) && s.paused &&
                                      !s.playing;
            k::BeginRow(30, 8);
            button(s.attach_preview ? "Detach" : "Attach here", EditorAction::AttachPreview, 0,
                   s.attach_preview ? 0 : 1, !attach_ready,
                   s.attach_preview ? k::Btn::Secondary : k::Btn::Primary);
            k::EndRow();
            k::VGap(10);
            k::BeginRow(30, 8);
            button("Cycle target", EditorAction::AttachCycleTarget, 197.5, 1, !s.attach_available);
            button("Reset", EditorAction::AttachReset, 0, 0, !s.attach_available);
            k::EndRow();
            k::VGap(15);
            k::Divider();
            k::NextColumn();
            k::SectionTitle("Frame the camera", "Drag to adjust; Ctrl+click a slider to type.",
                            "Offsets");
            k::VGap(15);
            {
                f::Inert inert(!s.attach_available);
                const bool aim_off = !s.attach_selected || !s.attach_point || !s.paused ||
                                     s.playing || s.attach_preview || s.bone_picker || locked ||
                                     !list_ok || s.selected_camera >= list.total;
                bool aim = s.attach_lock_vertical_aim;
                if (k::Checkbox("Lock vertical aim", &aim, aim_off)) {
                    CameraPose p{};
                    p[0] = list.revision;
                    p[1] = s.selected_camera;
                    d.send(EditorAction::SetAttachVerticalAimLock, aim ? 1 : 0, &p);
                }
                k::ItemTooltip(
                    "Follow the player without tilting with aim. Detach, change this setting, then Attach here again to keep your view.");
                k::VGap(10);
                CameraPose offsets{};
                bool commit = false, active = false;
                const char* axes[]{"Forward", "L/R", "Up", "Pitch", "Yaw", "Roll"};
                for (int i = 0; i < 6; ++i)
                    offsets[i] = s.attach_offsets[i];
                auto offset = [&](int i, float indent = 0) {
                    auto& n = d.number(axes[i], s.attach_offsets[i]);
                    float v = float(n.value);
                    const float range =
                        i < 3 ? std::min(float(kAttachOffsetLimit),
                                         std::max(10.f, float(std::abs(s.attach_offsets[i])) + 10))
                              : 180.f;
                    auto opt = f::Opts();
                    opt.unclamped_entry = i < 3;
                    auto e = f::SliderLine(axes[i], &v, -range, range, i < 3 ? "%.2f" : "%.1f",
                                           64 - indent, indent, opt);
                    if (i < 3)
                        v = attach_offset_entry(v, s.attach_offsets[i]);
                    n.value = v;
                    n.active = e.active;
                    offsets[i] = v;
                    commit |= e.released;
                    active |= e.active;
                };
                for (int i = 0; i < 4; ++i) {
                    offset(i);
                    k::VGap(10);
                }
                k::Disclosure("Rotation & transition", &d.rotation);
                k::VGap(10);
                if (d.rotation) {
                    offset(4, 14);
                    k::VGap(10);
                    offset(5, 14);
                    k::VGap(10);
                    scalar("Blend in", s.source_blend, 0, 10, "%.2f s",
                           EditorAction::SetSourceBlend, false);
                    k::VGap(10);
                }
                if (commit && d.send(EditorAction::SetAttachOffsets, 0, &offsets)) {
                    for (int i = 0; i < 6; ++i) {
                        auto found = d.numbers.find(ImGui::GetID(axes[i]));
                        if (found != d.numbers.end())
                            found->second.pending_until = ImGui::GetTime() + 1;
                    }
                }
                scalar("Smooth", s.attach_smoothing, 0, 1, "%.2f s",
                       EditorAction::SetAttachSmoothing, false);
                k::VGap(10);
                k::Disclosure("Model visibility", &d.visibility);
                if (d.visibility) {
                    k::VGap(10);
                    bool hide = s.attach_hide;
                    if (k::Checkbox("Hide this hero", &hide))
                        d.send(EditorAction::SetAttachHide,
                               (hide ? 1 : 0) | (s.attach_auto_clearance ? 2 : 0));
                    k::VGap(10);
                    k::BeginRow(18, 8);
                    k::Label("Visible-model spacing");
                    k::InfoIcon(
                        "Automatic clearance is experimental; animated arms or clothing may cross the view.");
                    k::EndRow();
                    k::VGap(10);
                    const char* spacing[]{"Automatic clearance (experimental)", "Exact offset"};
                    int mode = s.attach_auto_clearance ? 0 : 1;
                    k::BeginRow(30, 0);
                    k::HGap(14);
                    if (k::Dropdown("##clearance", &mode, spacing, 2))
                        d.send(EditorAction::SetAttachHide,
                               (s.attach_hide ? 1 : 0) | (mode == 0 ? 2 : 0));
                    k::EndRow();
                }
            }
            k::VGap(15);
            k::Divider();
            k::EndColumns();
        }
        k::VGap(15);
        draw_kara_footer(s);
    } else if (tab == 3) {
        {
            f::Greyed dim(!live);
            k::BeginColumns(30);
            auto focus = [&](int index, const char* label, float width = 0) {
                k::Label(label);
                k::VGap(4);
                char id[24];
                std::snprintf(id, sizeof(id), "##dof%d", index);
                auto& n = d.number(id, s.dof[index]);
                auto e = f::DragField(id, &n.value, index == 10 ? .01 : 1,
                                      index == 2 || index == 6   ? -100
                                      : index == 4 || index == 8 ? 180
                                      : index == 5 || index == 9 ? 2000
                                      : index == 10              ? .5
                                                                 : 0,
                                      width);
                d.finish(n, e, EditorAction(unsigned(EditorAction::SetDofEnabled) + index));
            };
            auto pair = [&](int a, const char* la, int b, const char* lb, float width) {
                k::BeginColumns(8, width);
                focus(a, la);
                k::NextColumn();
                focus(b, lb);
                k::EndColumns();
            };
            auto citadel = [&]() {
                k::SectionTitle("Citadel DOF", "Changes save to Effects at the playhead.",
                                "Game engine");
                k::VGap(15);
                f::Greyed off(!s.citadel_dof_available || !s.paused || s.playing ||
                              !s.camera_count);
                check("Enable DOF##citadel", s.citadel_dof_enabled,
                      EditorAction::SetCitadelDofEnabled);
                k::VGap(10);
                scalar("Sensor size", s.citadel_dof_sensor, .5, 3, "%.2f",
                       EditorAction::SetCitadelDofSensorSize, true, f::Resets(1));
                k::VGap(10);
                char label[64];
                std::snprintf(label, sizeof(label), "Focus distance \xC2\xB7 %.0f in###focus",
                              s.citadel_dof_focus);
                auto o = f::Resets(200);
                o.map = f::Map::Log1p;
                scalar(label, s.citadel_dof_focus, 0, 10000, nullptr,
                       EditorAction::SetCitadelDofFocusDistance, true, o);
            };
            k::SectionTitle(
                "Depth of field",
                "Alt: fine adjust | Ctrl+click: type. Changes save to Effects at the playhead.",
                "Dolly lens");
            k::VGap(15);
            {
                f::Greyed off(!s.dof_available || !s.paused || s.playing || !s.camera_count);
                bool enabled = s.dof[0] != 0 && s.dof[1] != 0 &&
                               std::any_of(s.dof.begin() + 2, s.dof.begin() + 6,
                                           [](double v) { return v != 0; });
                check("Enable DOF##lens", enabled, EditorAction::SetDofEnabled);
                k::VGap(10);
                f::Greyed fields(!enabled);
                k::Text("Focus ranges", t::text_dim);
                k::VGap(10);
                pair(2, "Near blurry", 4, "Far crisp", 197.5);
                k::VGap(10);
                pair(3, "Near crisp", 5, "Far blurry", 197.5);
                k::VGap(10);
                focus(10, "Ground tilt", 197.5);
                k::VGap(10);
                k::Disclosure("Focus overrides", &d.overrides);
                if (d.overrides) {
                    k::VGap(10);
                    k::BeginColumns(0, 14);
                    k::NextColumn();
                    pair(6, "Near blurry", 8, "Far crisp", 190.5);
                    k::VGap(8);
                    pair(7, "Near crisp", 9, "Far blurry", 190.5);
                    k::EndColumns();
                }
            }
            if (!d.overrides) {
                split();
                citadel();
                k::VGap(15);
                k::Divider();
            }
            k::NextColumn();
            if (d.overrides) {
                citadel();
                split();
            }
            {
                // Classic particle presets (dolly/particles.py ids); snow and rain moved to
                // Scene weather.
                struct Classic {
                    const char* tab;
                    const char* name;
                    int id;
                };
                static const Classic classics[]{{"Confetti##particles", "Confetti", 0},
                                                {"Frozen flakes##particles", "Frozen flakes", 5},
                                                {"Crystals##particles", "Crystals", 7}};
                const char* current = "Native effect";
                for (const auto& c : classics)
                    if (c.id == s.confetti_preset)
                        current = c.name;
                k::SectionTitle("Particles", "Follows the rendered camera and game time.",
                                current);
                k::VGap(15);
                k::BeginRow(30, 4);
                const float width = (k::Avail() - 2 * 4) / 3;
                for (const auto& c : classics)
                    if (k::Tab(c.tab, c.id == s.confetti_preset, width) &&
                        c.id != s.confetti_preset)
                        d.send(EditorAction::SetParticlePreset, c.id);
                k::EndRow();
                k::VGap(10);
                check("Enable particles", s.confetti_enabled, EditorAction::SetConfettiEnabled);
                k::VGap(10);
                {
                    f::Greyed off(!s.confetti_enabled);
                    scalar("Height###particle-height", s.confetti_spawn_height, 100, 1500,
                           "%.0f u", EditorAction::SetConfettiSpawnHeight, false);
                    k::VGap(10);
                    scalar("Intensity###particle-intensity", s.confetti_intensity, .25f, 3.f,
                           "%.2fx", EditorAction::SetParticleIntensity, false, f::Resets(1));
                    k::VGap(10);
                    check("Despawn on ground", s.confetti_despawn_on_ground,
                          EditorAction::SetConfettiDespawnOnGround);
                }
            }
            split();
            k::SectionTitle("Scene appearance");
            k::VGap(15);
            auto scene_toggle = [&](const char* label, bool value, EditorAction action, bool off) {
                if (k::Checkbox(label, &value, off))
                    d.send(action); // existing toggle actions carry no scalar payload
            };
            scene_toggle("Hide Citadel glow", (s.scene_appearance_flags & 1) != 0,
                  EditorAction::ToggleCitadelGlow, s.playing || locked);
            k::ItemTooltip("Hide hero, trooper, boss and health-bar glow together.");
            k::VGap(10);
            scene_toggle("Hide floating health bars", (s.scene_appearance_flags & 2) != 0,
                  EditorAction::ToggleHealthbars, s.playing || locked);
            k::ItemTooltip("Hide floating unit bars with Dolly; uncheck to restore the saved values. Replay HUD controls the hero health panel.");
            k::VGap(10);
            scene_toggle("Near player opacity fix", (s.scene_appearance_flags & 4) != 0,
                  EditorAction::NearPlayerOpacityFix, s.playing || locked);
            k::ItemTooltip("Force full opacity on near-player camera fades; uncheck to restore both original fade values.");
            k::VGap(15);
            k::BeginRow(30, 0);
            button("Clear stuck effects", EditorAction::DestroyRagdolls, 0, 0,
                   s.playing || locked);
            k::EndRow();
            if (!d.overrides) {
                k::VGap(15);
                k::Divider();
            }
            k::EndColumns();
            if (d.overrides) {
                k::VGap(15);
                k::Divider();
            }
        }
        k::VGap(15);
        draw_kara_footer(s);
    } else if (tab == 6) {
        {
            f::Greyed dim(!live);
            weather::Settings w;
            const bool published = weather_runtime::panel_settings(w);
            const bool on = published && w.enabled();
            static const char* const presets[]{"Off##weather",       "Light snow##weather",
                                               "Blizzard##weather",  "Rain storm##weather",
                                               "Sandstorm##weather", "Dust haze##weather",
                                               "Ground fog##weather", "Ash##weather"};
            static const char* const names[]{"Off",       "Light snow", "Blizzard",
                                             "Rain storm", "Sandstorm",  "Dust haze",
                                             "Ground fog", "Ash"};
            const int preset = int(w.preset);
            // Compact one-line rows (Follow's rig style); Intensity stays the full row.
            auto line = [&](const char* label, double value, float low, float high, const char* fmt,
                            EditorAction action, f::SliderOpts opt = f::SliderOpts{}) {
                scalar(label, value, low, high, fmt, action, false, opt);
            };
            char wind_detail[48], flakes_detail[24], ground_detail[32];
            std::snprintf(wind_detail, sizeof(wind_detail), "%.0f\xC2\xB0 \xC2\xB7 %.0f u/s",
                          w.wind_direction, w.wind_speed);
            const char* kinds[]{"None", "Snow", "Snow", "Rain", "Sand", "Sand", "None", "Ash"};
            std::snprintf(flakes_detail, sizeof(flakes_detail), "%s",
                          kinds[std::clamp(preset, 0, 7)]);
            // Tracks: ready over the whole shot, or filling in as it plays.
            const bool tracks_here = w.hero_tracks && w.coating > 0.0f &&
                                     weather::takes_tracks(static_cast<weather::Preset>(preset));
            const double track_cover =
                tracks_here ? weather_runtime::hero_track_coverage() : -1.0;
            std::snprintf(ground_detail, sizeof(ground_detail), "%.0f%% coating%s",
                          w.coating * 100.0, track_cover >= 1.0 ? " \xC2\xB7 tracks" : "");
            k::BeginColumns(30);
            k::SectionTitle("Weather",
                            "Saved with the shot. Playback, scrubbing and export match exactly.",
                            published ? names[std::clamp(preset, 0, 7)] : "Dolly weather");
            k::VGap(15);
            {
                f::Greyed none(!published);
                // All eight presets visible at once: two rows of four Kara tabs.
                for (int row = 0; row < 2; ++row) {
                    if (row)
                        k::VGap(4);
                    k::BeginRow(30, 4);
                    const float width = (k::Avail() - 3 * 4) / 4;
                    for (int i = row * 4; i < row * 4 + 4; ++i)
                        if (k::Tab(presets[i], i == preset, width) && i != preset)
                            d.send(EditorAction::SetWeatherPreset, i);
                    k::EndRow();
                }
            }
            if (published && !on) {
                k::VGap(10);
                k::Notice("Pick a preset to turn on wind, flakes and haze.", t::text_dim);
            }
            k::VGap(15);
            {
                f::Greyed off(!on);
                scalar("Intensity###weather-intensity", w.intensity, weather::kIntensityMin,
                       weather::kIntensityMax, "%.2f", EditorAction::SetWeatherIntensity, true,
                       f::Resets(1));
                k::VGap(10);
                k::Disclosure("Timing and gusts", &d.weather_timing);
                if (d.weather_timing) {
                    k::VGap(10);
                    auto build_up = f::Resets(0);
                    build_up.map = f::Map::Log1p;
                    line("Build-up###weather-build-up", w.build_up, weather::kBuildUpMin,
                         weather::kBuildUpMax, "%.0f s", EditorAction::SetWeatherBuildUp,
                         build_up);
                    k::ItemTooltip("Seconds from the shot start for snow, sand or wetness to "
                                   "settle. 0 starts settled.");
                    k::VGap(10);
                    line("Gusts###wind-gusts", w.gusts, weather::kGustsMin, weather::kGustsMax,
                         "%.2f", EditorAction::SetWindGusts, f::Resets(1));
                }
            }
            split();
            k::SectionTitle("Wind", "Moves haze, flakes and drifts around the map.",
                            on ? wind_detail : "Wind sim");
            k::VGap(15);
            {
                f::Greyed off(!on);
                line("Direction###wind-direction", w.wind_direction, 0, 360, "%.0f\xC2\xB0",
                     EditorAction::SetWindDirection, f::Resets(weather::kWindDirectionDefault));
                k::VGap(10);
                line("Speed###wind-speed", w.wind_speed, weather::kWindSpeedMin,
                     weather::kWindSpeedMax, "%.0f u/s", EditorAction::SetWindSpeed);
                k::VGap(10);
                check("Hero wakes", w.hero_wakes, EditorAction::SetWeatherHeroWakes);
                k::ItemTooltip("Heroes part the wind, haze and flakes around them.");
            }
            k::VGap(15);
            k::Divider();
            k::NextColumn();
            k::SectionTitle("Precipitation", "Snow, rain, sand and ash flakes.",
                            on ? flakes_detail : "Flakes");
            k::VGap(15);
            {
                f::Greyed off(!on);
                line("Size###flake-size", w.flake_size, weather::kFlakeSizeMin,
                     weather::kFlakeSizeMax, "%.2fx", EditorAction::SetFlakeSize, f::Resets(1));
                k::VGap(10);
                line("Density###flake-density", w.density, weather::kDensityMin,
                     weather::kDensityMax, "%.2fx", EditorAction::SetPrecipitationDensity,
                     f::Resets(1));
            }
            split();
            k::SectionTitle("Ground and haze",
                            "Coating settles where open sky is above; haze rises to the ceiling.",
                            on ? ground_detail : "Surfaces");
            k::VGap(15);
            {
                f::Greyed off(!on);
                line("Coating###weather-coating", w.coating, weather::kCoatingMin,
                     weather::kCoatingMax, "%.2f", EditorAction::SetWeatherCoating);
                k::VGap(10);
                {
                    f::Greyed no_cover(!weather::takes_tracks(static_cast<weather::Preset>(preset)));
                    check("Hero tracks", w.hero_tracks, EditorAction::SetWeatherHeroTracks);
                    k::ItemTooltip("Heroes leave footprints in snow, sand, dust and ash. Dolly "
                                   "keeps where they walk while the replay plays.");
                }
                if (on && tracks_here && track_cover >= 0.0 && track_cover < 1.0) {
                    k::VGap(6);
                    char note[64];
                    if (track_cover > 0.0)
                        std::snprintf(note, sizeof(note),
                                      "Tracks ready for %.0f%% of the shot. Play it to fill in.",
                                      std::floor(track_cover * 100.0));
                    else
                        std::snprintf(note, sizeof(note), "Tracks appear as the shot plays.");
                    k::Notice(note, t::text_dim);
                }
                k::VGap(10);
                k::Disclosure("Haze and layers", &d.weather_haze);
                if (d.weather_haze) {
                    k::VGap(10);
                    line("Ceiling###weather-ceiling", w.height_ceiling,
                         weather::kHeightCeilingMin, weather::kHeightCeilingMax, "%.0f u",
                         EditorAction::SetWeatherHeightCeiling);
                    k::VGap(10);
                    check("Include in Effects layer", w.include_in_effects,
                          EditorAction::SetWeatherIncludeEffects);
                    k::ItemTooltip(
                        "Weather is always in Color and World, never in Players or Depth.");
                }
            }
            split();
            lens_rain::Controls lens;
            const bool lens_published = lens_rain_runtime::panel_settings(lens);
            const bool raining = on && preset == int(weather::Preset::RainStorm);
            k::SectionTitle("Lens", "Water on the camera lens while the shot rains.",
                            lens_published && lens.enabled ? (raining ? "Rain on lens" : "Waiting for rain")
                                                           : "Dry lens");
            k::VGap(15);
            {
                f::Greyed off(!on || !lens_published);
                check("Rain on the lens", lens.enabled, EditorAction::SetLensRain);
                k::ItemTooltip("Drops land, run down and leave trails. Under a roof no new drops "
                               "land. Drawn on Color and World only.");
                k::VGap(10);
                f::Greyed lens_off(!lens.enabled);
                line("Amount###lens-amount", lens.look.amount, lens_rain::kRanges[0].low,
                     lens_rain::kRanges[0].high, "%.2f", EditorAction::SetLensAmount,
                     f::Resets(.35));
                k::VGap(10);
                line("Drop size###lens-size", lens.look.size, lens_rain::kRanges[1].low,
                     lens_rain::kRanges[1].high, "%.2fx", EditorAction::SetLensSize, f::Resets(1));
                k::VGap(10);
                k::Disclosure("Runoff and glass", &d.lens_detail);
                if (d.lens_detail) {
                    k::VGap(10);
                    line("Slide###lens-slide", lens.look.slide, lens_rain::kRanges[2].low,
                         lens_rain::kRanges[2].high, "%.2f", EditorAction::SetLensSlide,
                         f::Resets(1));
                    k::VGap(10);
                    line("Trails###lens-trails", lens.look.trails, lens_rain::kRanges[3].low,
                         lens_rain::kRanges[3].high, "%.2f", EditorAction::SetLensTrails,
                         f::Resets(.35));
                    k::VGap(10);
                    line("Refraction###lens-refraction", lens.look.refraction,
                         lens_rain::kRanges[4].low, lens_rain::kRanges[4].high, "%.2f",
                         EditorAction::SetLensRefraction, f::Resets(.75));
                    k::VGap(10);
                    line("Softness###lens-softness", lens.look.softness,
                         lens_rain::kRanges[5].low, lens_rain::kRanges[5].high, "%.2f",
                         EditorAction::SetLensSoftness, f::Resets(.5));
                }
            }
            k::VGap(15);
            k::Divider();
            k::EndColumns();
        }
        k::VGap(15);
        draw_kara_footer(s);
    } else if (tab == 4) {
        {
            // Optional so the RenderLock section can leave the busy dimming:
            // a running .crav export keeps Dolly busy but must stay cancellable.
            std::optional<f::Greyed> dim(std::in_place, !live);
            const bool recording =
                rec.state == video::State::starting || rec.state == video::State::recording;
            const bool finishing = rec.state == video::State::finalizing;
            k::BeginColumns(30);
            k::SectionTitle("Preview and stills",
                            "Output folder and FFmpeg runtime: desktop Export tab.", "Video");
            k::VGap(15);
            button("Play shot", EditorAction::PlayPath, 0, 0,
                   locked || s.camera_count < 2 || s.playing || s.video_pov);
            k::VGap(10);
            if (recording) {
                char line[80];
                if (const auto total = export_take_frames(s)) {
                    // "1,810 / 15,720 frames · about 21 min left" from this take's own rate.
                    LARGE_INTEGER now{};
                    QueryPerformanceCounter(&now);
                    const double rate = estimate::capture_rate(
                        rec.frames_written,
                        estimate::elapsed(rec.first_qpc, std::uint64_t(now.QuadPart),
                                          rec.qpc_frequency));
                    two_line_text(estimate::progress_line(
                                      rec.frames_written, total,
                                      estimate::seconds_left(rec.frames_written, total, rate)),
                                  t::text_primary);
                } else {
                    std::snprintf(line, sizeof(line), "%.1f s  |  %llu frames",
                                  rec.duration_100ns / 1e7,
                                  static_cast<unsigned long long>(rec.frames_written));
                    k::Text(line);
                }
                if (rec.frames_dropped) {
                    std::snprintf(line, sizeof(line), "Missed capture slots: %llu",
                                  static_cast<unsigned long long>(rec.frames_dropped));
                    k::Text(line, t::status_error);
                }
            } else
                button("Screenshot (hero + plate)", EditorAction::TakeScreenshot, 0, 0,
                       finishing || s.playing || s.video_pov);
            split();
            char bitrate[32];
            std::snprintf(bitrate, sizeof(bitrate), "%u Mbps", s.video_bitrate_mbps);
            k::SectionTitle("Capture", nullptr, bitrate);
            k::VGap(15);
            button(recording     ? "Finish recording"
                   : finishing   ? "Finalizing video..."
                   : s.video_pov ? "Record POV"
                                 : "Record video",
                   recording ? EditorAction::StopVideo : EditorAction::StartVideo, 0, 0, finishing,
                   k::Btn::Primary);
            k::VGap(15);
            {
                f::Inert inert(locked || s.playing);
                const char* fps[]{"30", "60", "120", "300", "600"};
                const int values[]{30, 60, 120, 300, 600};
                int v = 1;
                for (int i = 0; i < 5; ++i)
                    if (s.video_fps == values[i])
                        v = i;
                if (row_dropdown("Video FPS", "##fps", &v, fps, 5))
                    d.send(EditorAction::SetVideoFps, values[v]);
                k::VGap(10);
                k::Disclosure("Encoder & quality", &d.encoder);
                k::VGap(10);
                if (d.encoder) {
                    const char* rates[]{"10 Mbps", "20 Mbps", "40 Mbps"};
                    int rate = s.video_bitrate_mbps == 10 ? 0 : s.video_bitrate_mbps == 40 ? 2 : 1;
                    if (row_dropdown("Bitrate", "##bitrate", &rate, rates, 3, false, 14, 70))
                        d.send(EditorAction::SetVideoBitrate, rate == 0 ? 10 : rate == 1 ? 20 : 40);
                    k::VGap(10);
                    const char* codecs[]{"Auto (hardware)", "NVIDIA H.264", "NVIDIA HEVC",
                                         "Windows H.264",   "CPU H.264",    "CPU HEVC",
                                         "Intel H.264",     "Intel HEVC",   "AMD H.264",
                                         "AMD HEVC",        "Lossless FFV1"};
                    int codec = s.video_codec;
                    if (row_dropdown("Encoder", "##encoder", &codec, codecs, 11, false, 14, 70))
                        d.send(EditorAction::SetVideoEncoder, codec);
                    k::VGap(10);
                }
                check("Fixed-step export", s.video_fixed_step, EditorAction::SetVideoFixedStep);
                k::VGap(10);
                int speed = nearest(s.video_speed, speeds, 7);
                if (row_dropdown("Export speed", "##speed", &speed, speed_labels, 7))
                    d.send(EditorAction::SetVideoSpeed, speeds[speed]);
                if (const auto frames = export_take_frames(s)) {
                    // "15,720 frames · 26.2 s of video · about 24 min to record"; the
                    // time appears once a fixed-step color take of this kind has
                    // finished. Layered exports give the per-pass size only.
                    k::VGap(10);
                    const bool layered =
                        s.video_layer_world || s.video_layer_players || s.video_layer_effects;
                    two_line_text(estimate::estimate_line(
                                      frames, export_take_seconds(s),
                                      s.video_fixed_step ? video::learned_capture_rate(
                                                               s.video_depth, s.video_depth_exr)
                                                         : 0,
                                      layered),
                                  t::text_muted);
                }
            }
            k::VGap(15);
            k::Divider();
            k::NextColumn();
            k::SectionTitle("Camera source", nullptr, "Export");
            k::VGap(15);
            if (!s.video_pov && !s.camera_count) {
                k::Notice("Capture at least two camera views to record a shot.");
                k::VGap(10);
            }
            {
                f::Inert inert(locked || s.playing);
                const char* sources[]{"Camera path / Bone camera", "Player POV"};
                int source = s.video_pov ? 1 : 0;
                if (row_dropdown("Source", "##source", &source, sources, 2))
                    d.send(EditorAction::SetVideoSource, source);
                if (s.video_pov || (s.camera_count == 1 && s.attach_selected)) {
                    k::VGap(10);
                    const char* durations[]{"1 second",   "2 seconds",  "5 seconds",
                                            "10 seconds", "15 seconds", "30 seconds",
                                            "60 seconds", "120 seconds"};
                    const double values[]{1, 2, 5, 10, 15, 30, 60, 120};
                    int duration = nearest(s.pov_duration, values, 8);
                    if (row_dropdown("Replay duration", "##duration", &duration, durations, 8,
                                     false, 0, 89))
                        d.send(EditorAction::SetPovDuration, values[duration]);
                }
                k::VGap(10);
                check("Export After Effects camera (.jsx)", s.video_ae_camera,
                      EditorAction::SetVideoAeCamera);
                k::VGap(10);
                check("Export USD camera (.usda)", s.video_usd_camera,
                      EditorAction::SetVideoUsdCamera);
            }
            {
                // RenderLock camera: a subheader under Camera source's camera files. It
                // leaves the drawer's busy dimming only while its own export runs, so
                // Cancel stays live; otherwise it dims with the drawer.
                dim.reset();
                f::Greyed own(!live && !s.renderlock_exporting);
                k::VGap(15);
                k::Text("RenderLock camera (.crav)", t::text_primary, k::Role::BodyMedium, 18);
                k::ItemTooltip("Saves this shot's camera as .crav beside your video output, "
                               "without recording video. Normal speed; uses Video FPS.");
                k::VGap(10);
                if (s.renderlock_exporting)
                    button("Cancel camera export", EditorAction::CancelRenderLock);
                else
                    button("Export .crav", EditorAction::ExportRenderLock, 0, 0,
                           !live || locked || recording || finishing || s.busy || s.playing ||
                               s.video_pov || s.camera_count < 2);
            }
            dim.emplace(!live);
            {
                f::Inert inert(locked || s.playing);
                split();
                k::SectionTitle("Capture layers", nullptr, "Color included");
                k::VGap(15);
                check("Depth master (.mov)", s.video_depth, EditorAction::SetVideoDepth);
                k::VGap(10);
                check("EXR sequence (float)", s.video_depth_exr, EditorAction::SetVideoDepthExr,
                      !s.video_depth);
                k::VGap(10);
                check("World layer", s.video_layer_world, EditorAction::SetVideoLayerWorld);
                k::VGap(10);
                check("Players layer", s.video_layer_players, EditorAction::SetVideoLayerPlayers);
                k::VGap(10);
                check("Effects layer", s.video_layer_effects, EditorAction::SetVideoLayerEffects);
            }
            k::EndColumns();
        }
        k::VGap(15);
        draw_kara_footer(s, false);
    }
    if (tab == 5) {
        const bool can = live && s.paused && !s.playing && !s.attach_preview &&
                         !s.bone_picker && !locked && list_ok;
        auto request = [&](EditorAction action, double value, double arg = 0) {
            CameraPose p{};
            p[0] = list.revision;
            p[1] = arg;
            return d.send(action, value, &p);
        };
        if (d.clear) {
            k::SectionTitle("Clear cameras?");
            k::VGap(15);
            k::Text("Remove every camera from this shot?", t::text_muted, k::Role::Body, 16);
            k::VGap(10);
            const bool changed = !list_ok || list.revision != d.confirm_revision;
            if (changed) {
                k::Notice("Path changed. Cancel and review it again.");
                k::VGap(10);
            }
            k::VGap(6);
            k::BeginRow(30, 8);
            if (k::Button("Clear", k::HUG, k::Btn::Primary, !can || changed) &&
                request(EditorAction::ClearCameraPath, 0))
                d.clear = false;
            if (k::Button("Cancel##clear") || ImGui::IsKeyPressed(ImGuiKey_Escape))
                d.clear = false;
            k::EndRow();
        } else {
            char placed[48];
            std::snprintf(placed, sizeof(placed), "%u placed", s.camera_count);
            k::SectionTitle("Campaths", nullptr, placed);
            k::VGap(15);
            f::Greyed dim(!live);
            if (locked) {
                k::Notice(rec.state == video::State::finalizing
                              ? "Saving video. Please wait."
                              : "Finish recording before editing cameras.");
                k::VGap(10);
            }
            if (s.bone_picker) {
                k::Notice("Finish or cancel Bone Picker before editing cameras.");
                k::VGap(10);
                button("Cancel Bone Picker", EditorAction::CancelBonePicker, 0, 0,
                       !live || locked);
                k::VGap(10);
            } else if (s.attach_preview) {
                k::Notice("Detach preview before deleting or clearing cameras.");
                k::VGap(10);
                button("Detach preview", EditorAction::AttachPreview, 0, 0,
                       !live || !s.paused || s.playing || locked);
                k::VGap(10);
            }
            if (!list_ok) {
                k::Notice("Waiting for the camera list...");
            } else if (!list.total) {
                k::VGap(2);
                k::Text("Your first camera starts here", t::text_primary, k::Role::BodyMedium, 16);
                k::VGap(6);
                f::Wrapped(
                    "Frame a view, then capture it. Add another view to create a cinematic path.",
                    t::text_muted);
                k::VGap(10);
                button("Capture camera", EditorAction::Capture, 0, 0, !can, k::Btn::Primary);
            }
            constexpr unsigned rows = 12;
            const bool block_changed = lite_arrive_first_ != list.first;
            if (camera_pending_first_ != ~0u && (list.revision != camera_pending_revision_ ||
                                                 ImGui::GetTime() - camera_pending_at_ > 2))
                camera_pending_first_ = ~0u;
            const bool requested = camera_pending_first_ == list.first;
            if (requested) {
                camera_row_page_ = camera_pending_page_;
                camera_pending_first_ = ~0u;
            }
            if (!requested &&
                (block_changed || !have_camera_total_ || list.total > previous_camera_total_) &&
                s.selected_camera >= list.first && s.selected_camera < list.first + list.count)
                camera_row_page_ = (s.selected_camera - list.first) / rows;
            if (have_camera_total_ && list.total > previous_camera_total_ &&
                s.selected_camera >= list.first + list.count) {
                camera_pending_first_ = std::min<unsigned>(
                    (s.selected_camera / kEditorCameraListCount) * kEditorCameraListCount,
                    list.total > kEditorCameraListCount
                        ? list.total - unsigned(kEditorCameraListCount)
                        : 0u);
                camera_pending_page_ = 0;
                camera_pending_revision_ = list.revision;
                camera_pending_at_ = ImGui::GetTime();
                request(EditorAction::CameraPage, camera_pending_first_);
            }
            previous_camera_total_ = list.total;
            have_camera_total_ = true;
            lite_arrive_first_ = list.first;
            camera_row_page_ = std::min(camera_row_page_, (std::max(1u, list.count) - 1) / rows);
            const unsigned begin = std::min(camera_row_page_ * rows,
                                            list.count > rows ? list.count - rows : 0u),
                           end = std::min(list.count, begin + rows);
            if (bank_release_pending_ &&
                (!can || bank_release_index_ < list.first ||
                 bank_release_index_ >= list.first + list.count ||
                 list.rows[bank_release_index_ - list.first].time != bank_anchor_time_))
                bank_release_pending_ = false;
            if (bank_release_pending_ && list.revision != bank_sent_revision_) {
                request(EditorAction::SetCameraRoll, bank_release_index_, bank_release_value_);
                bank_release_pending_ = false;
            }
            const float scale = k::Scale();
            auto* dl = ImGui::GetWindowDrawList();
            constexpr float xs[]{8, 149, 231, 313};
            auto cell = [&](const ImRect& r, int col, const char* text, k::Role role,
                            const ImVec4& color) {
                k::PaintText(dl, role, text,
                             ImVec2(r.Min.x + xs[col] * scale,
                                    r.Min.y + (r.GetHeight() / scale - 16) * .5f * scale),
                             16, t::U32(color));
            };
            auto soft = [&]() {
                auto r = k::Place(0, 1);
                dl->AddRectFilled(r.Min, r.Max, t::U32(t::border_soft));
            };
            if (list_ok && list.total) {
                auto head = k::Place(0, 24);
                const char* labels[]{"Camera", "Arrive / s", "Aspect", "Bank"};
                for (int i = 0; i < 4; ++i)
                    cell(head, i, labels[i], k::Role::Body, t::text_dim);
                for (unsigned i = begin; i < end; ++i) {
                    const unsigned index = list.first + i;
                    const auto& cam = list.rows[i];
                    k::VGap(3);
                    soft();
                    k::VGap(3);
                    auto row = k::Place(0, 28);
                    ImGui::PushID(int(index));
                    const auto id = ImGui::GetID("##camera");
                    ImGui::SetNextItemAllowOverlap();
                    ImGui::ItemAdd(row, id);
                    bool hov = false, held = false;
                    if (live && !s.playing && !locked &&
                        ImGui::ButtonBehavior(row, id, &hov, &held,
                                              ImGuiButtonFlags_PressedOnClickRelease |
                                                  ImGuiButtonFlags_PressedOnDoubleClick))
                        request(ImGui::IsMouseDoubleClicked(0) ? EditorAction::ViewCamera
                                                               : EditorAction::SelectCamera,
                                index);
                    const auto color = index == s.selected_camera ? t::text_primary : t::text_muted;
                    char text[48];
                    std::snprintf(text, sizeof(text), "Camera %02u", index + 1);
                    cell(row, 0, text, k::Role::BodyMedium, color);
                    std::snprintf(text, sizeof(text), "%.2f", cam.aspect);
                    cell(row, 2, text, k::Role::BodyMedium, color);
                    for (int bank = 0; bank < 2; ++bank) {
                        const char* name = bank ? "##bank" : "##arrive";
                        auto& n = d.number(name, bank ? cam.roll : cam.time);
                        float value = float(n.value);
                        ImRect r(ImVec2(row.Min.x + (xs[bank ? 3 : 1] - 4) * scale,
                                        row.Min.y + 2 * scale),
                                 ImVec2(row.Min.x + (xs[bank ? 3 : 1] + 78) * scale,
                                        row.Max.y - 2 * scale));
                        auto e =
                            f::DragCell(name, r, &value, bank ? .25f : .01f, bank ? -180.f : 0.f,
                                        bank ? 180.f : 1e9f, bank ? "%.1f" : "%.2f", !can);
                        if (e.active && !n.active) {
                            lite_arrive_edit_revisions_[i] = list.revision;
                            if (bank) {
                                bank_sent_revision_ = 0;
                                bank_release_pending_ = false;
                                bank_anchor_time_ = cam.time;
                            }
                        }
                        n.value = value;
                        n.active = e.active;
                        if (!bank && e.released) {
                            CameraPose p{};
                            p[0] = lite_arrive_edit_revisions_[i];
                            p[1] = value;
                            p[2] = 1;
                            d.send(EditorAction::SetCameraTime, index, &p);
                        }
                        if (bank) {
                            if ((e.active || e.released) && cam.time != bank_anchor_time_) {
                                ImGui::ClearActiveID();
                                n.active = false;
                                e.active = e.released = false;
                                bank_release_pending_ = false;
                            }
                            if ((e.released || (e.active && std::abs(value - cam.roll) > .001)) &&
                                list.revision != bank_sent_revision_) {
                                if (request(EditorAction::SetCameraRoll, index, value)) {
                                    bank_sent_revision_ = list.revision;
                                    bank_sent_value_ = value;
                                }
                            } else if (e.released && value != bank_sent_value_) {
                                bank_release_pending_ = true;
                                bank_release_index_ = index;
                                bank_release_value_ = value;
                            }
                        }
                        if (!e.typing) {
                            std::snprintf(text, sizeof(text), bank ? "%.1f" : "%.2f", value);
                            cell(row, bank ? 3 : 1, text, k::Role::BodyMedium,
                                 can ? color : t::text_dim);
                        }
                        if (e.hovered && !e.active)
                            k::ShowTooltip(bank ? "Drag to rotate; Ctrl+click to type."
                                                : "Drag to adjust arrival; Ctrl+click to type.");
                    }
                    ImGui::PopID();
                }
            }
            k::VGap(10);
            k::BeginRow(30, 8);
            const unsigned first = list.first + begin;
            if (k::ArrowButton("##prev", k::Dir::Left, false,
                               !can || !first || camera_pending_first_ != ~0u)) {
                if (camera_row_page_)
                    --camera_row_page_;
                else if (list.first) {
                    camera_pending_first_ =
                        ((list.first - 1) / kEditorCameraListCount) * kEditorCameraListCount;
                    camera_pending_page_ = (list.first - camera_pending_first_ - 1) / rows;
                    camera_pending_revision_ = list.revision;
                    camera_pending_at_ = ImGui::GetTime();
                    if (!request(EditorAction::CameraPage, camera_pending_first_))
                        camera_pending_first_ = ~0u;
                }
            }
            char range[48];
            if (list.total)
                std::snprintf(range, sizeof(range), "%u-%u of %u", first + 1, list.first + end,
                              list.total);
            else
                std::snprintf(range, sizeof(range), "0 cameras");
            k::Text(range, t::text_muted, k::Role::Body, 16);
            k::Spring(30);
            if (k::ArrowButton("##next", k::Dir::Right, false,
                               !can || first + rows >= list.total ||
                                   camera_pending_first_ != ~0u)) {
                if ((camera_row_page_ + 1) * rows < list.count)
                    ++camera_row_page_;
                else {
                    camera_pending_first_ = std::min<unsigned>(
                        ((list.first / kEditorCameraListCount) + 1) * kEditorCameraListCount,
                        list.total > kEditorCameraListCount
                            ? list.total - unsigned(kEditorCameraListCount)
                            : 0u);
                    camera_pending_page_ = (list.first + list.count - camera_pending_first_) / rows;
                    camera_pending_revision_ = list.revision;
                    camera_pending_at_ = ImGui::GetTime();
                    if (!request(EditorAction::CameraPage, camera_pending_first_))
                        camera_pending_first_ = ~0u;
                }
            }
            k::EndRow();
            k::VGap(10);
            soft();
            k::VGap(10);
            k::BeginRow(30, 8);
            if (k::Button("Undo", 197.5, k::Btn::Secondary, !can || !(list.flags & 1)))
                request(EditorAction::UndoShot, 0);
            if (k::Button("Redo", 0, k::Btn::Secondary, !can || !(list.flags & 2)))
                request(EditorAction::RedoShot, 0);
            k::EndRow();
            k::VGap(10);
            if (k::Button("Seek camera", 0, k::Btn::Secondary,
                          !can || s.selected_camera >= list.total))
                request(EditorAction::SeekCamera, s.selected_camera);
            k::VGap(10);
            button("Replace selected", EditorAction::Replace, 0, 0,
                   !s.camera_count || s.playing || locked);
            k::VGap(10);
            button("Save Path", EditorAction::SaveShot, 0, 0, !can, k::Btn::Primary);
            k::VGap(10);
            k::BeginRow(30, 8);
            if (k::Button("Delete", 197.5, k::Btn::Secondary,
                          !can || s.selected_camera >= list.total))
                request(EditorAction::DeleteCamera, s.selected_camera);
            if (k::Button("Clear All", 0, k::Btn::Secondary, !can || !list.total)) {
                d.clear = true;
                d.confirm_revision = list.revision;
            }
            k::EndRow();
        }
    }
    d.used_height = k::EndRegion();
    // Confirmation binds to the camera publication that was actually reviewed.
    if (tab == 0) {
        if (d.reset && !measuring) {
            ImGui::OpenPopup("##reset-camera-path");
            d.reset = false;
        }
        const float scale = k::Scale();
        const auto display = ImGui::GetIO().DisplaySize;
        ImGui::SetNextWindowPos(
            ImVec2((display.x - 380 * scale) * .5f, (display.y - 144 * scale) * .5f));
        ImGui::SetNextWindowSize(ImVec2(380 * scale, 144 * scale));
        ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(0, 0));
        ImGui::PushStyleVar(ImGuiStyleVar_WindowRounding, 6 * scale);
        ImGui::PushStyleColor(ImGuiCol_PopupBg, t::bg_surface);
        if (ImGui::BeginPopupModal("##reset-camera-path", nullptr,
                                   ImGuiWindowFlags_NoDecoration |
                                       ImGuiWindowFlags_NoSavedSettings)) {
            k::BeginRegion(ImGui::GetWindowPos(), 380);
            k::At(17, 17);
            k::Text("Reset camera path?", t::text_primary, k::Role::Title);
            k::At(17, 47);
            k::Text("Replace all camera views with the current view?");
            k::At(17, 67);
            k::Text("Lens and depth-of-field tracks will be kept.", t::text_muted);
            const bool off = !live || s.playing || !s.camera_count || locked || !list_ok ||
                             list.revision != d.confirm_revision;
            k::At(17, 97);
            if (k::Button("Reset here", 87, k::Btn::Primary, off) &&
                d.send(EditorAction::ResetCameraPath))
                ImGui::CloseCurrentPopup();
            k::At(112, 97);
            if (k::Button("Cancel##reset", 66) || ImGui::IsKeyPressed(ImGuiKey_Escape))
                ImGui::CloseCurrentPopup();
            k::EndRegion();
            ImGui::EndPopup();
        }
        ImGui::PopStyleColor();
        ImGui::PopStyleVar(2);
    }
    d.measuring = false;
}
}
