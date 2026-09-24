// Included inside the overlay's anonymous namespace. No engine access here.
void draw_bone_picker(const EditorSnapshot& state) {
    static PickerFrame snapshot;
    static char search[96]{};
    static bool common_only = true;
    static std::uint32_t request = 0;
    static std::array<int, kPickerMaxBones> candidates{};
    static unsigned candidate_count = 0;
    static std::array<ImVec2, kPickerMaxBones> stable_markers{};
    static std::array<bool, kPickerMaxBones> stable_marker_ready{};
    static CameraPose marker_view{};
    static ImVec2 marker_display{};
    static double marker_fov = 0;
    // A contended camera sample must not disable an active text field for one
    // frame: ImGui drops keyboard focus when that happens. Keep the last
    // bounded snapshot; all mutations independently validate the live session.
    if (!picker_snapshot(snapshot) && (!snapshot.stamp || picker_now() - snapshot.stamp > 250))
        snapshot.ready = false;
    const auto current = snapshot.catalog ? snapshot.catalog->sequence : 0;
    if (request != current) {
        request = current;
        search[0] = 0;
        common_only = true;
        candidate_count = 0;
        stable_marker_ready.fill(false);
        release(picker_portrait_view);
        release(picker_portrait_texture);
        picker_portrait_format = DXGI_FORMAT_UNKNOWN;
        picker_portrait_captured_request = 0;
    }
    if (snapshot.ready && snapshot.selected < 0 && state.attach_bone[0] && snapshot.catalog) {
        for (std::size_t i = 0; i < snapshot.catalog->bones.size(); ++i)
            if (std::strcmp(snapshot.catalog->bones[i].name, state.attach_bone) == 0) {
                picker_select(request, int(i));
                break;
            }
    }
    auto& io = ImGui::GetIO();
    // Screen-space hysteresis must never resist an intentional camera move.
    // Reproject the held world pose using the view actually applied by Camera.
    if (marker_view != snapshot.view || marker_fov != snapshot.fov ||
        marker_display.x != io.DisplaySize.x || marker_display.y != io.DisplaySize.y) {
        stable_marker_ready.fill(false);
        marker_view = snapshot.view;
        marker_fov = snapshot.fov;
        marker_display = io.DisplaySize;
    }
    if (snapshot.ready && !snapshot.preview)
        update_picker_portrait(snapshot);
    const float margin = 24 * panel_scale;
    const float width = std::min(350 * panel_scale, io.DisplaySize.x * .42f);
    const float left = io.DisplaySize.x - width - margin;
    unsigned visible_count = 0;
    if (snapshot.catalog)
        for (const auto& bone : snapshot.catalog->bones)
            visible_count += picker_matches(bone, search, common_only) ? 1u : 0u;
    const float row_height = ImGui::GetTextLineHeight() + 10 * panel_scale;
    const float height =
        std::min(io.DisplaySize.y - margin * 2,
                 std::max(440 * panel_scale,
                          425 * panel_scale +
                              visible_count * (row_height + ImGui::GetStyle().ItemSpacing.y)));
    ImGui::SetNextWindowPos(ImVec2(left, margin), ImGuiCond_Always);
    ImGui::SetNextWindowSize(ImVec2(width, height), ImGuiCond_Always);
    ImGui::SetNextWindowBgAlpha(.94f);
    ImGui::PushStyleVar(ImGuiStyleVar_WindowRounding, 12 * panel_scale);
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(20 * panel_scale, 18 * panel_scale));
    ImGui::Begin("Bone Picker", nullptr,
                 ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove | ImGuiWindowFlags_NoCollapse |
                     ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_NoTitleBar);
    const ImVec2 portrait_min(left + width - 76 * panel_scale, margin + 16 * panel_scale);
    const ImVec2 portrait_max(portrait_min.x + 56 * panel_scale, portrait_min.y + 56 * panel_scale);
    auto* panel_draw = ImGui::GetWindowDrawList();
    panel_draw->AddRectFilled(portrait_min, portrait_max, IM_COL32(37, 55, 57, 255),
                              7 * panel_scale);
    if (picker_portrait_view)
        panel_draw->AddImage(reinterpret_cast<ImTextureID>(picker_portrait_view), portrait_min,
                             portrait_max);
    panel_draw->AddRect(portrait_min, portrait_max, IM_COL32(114, 177, 164, 255), 7 * panel_scale);
    ImGui::TextColored(ImVec4(.51f, .94f, .81f, 1), "DOLLY / BONE PICKER");
    ImGui::PushFont(heading_font);
    ImGui::TextUnformatted("Choose a joint");
    ImGui::PopFont();
    EditorRoster roster{};
    if (editor_roster_snapshot(roster)) {
        char player[112]{};
        for (unsigned i = 0; i < roster.count; ++i)
            if (snapshot.catalog && roster.players[i].handle == snapshot.catalog->handle) {
                roster_label(roster.players[i], player, sizeof(player));
                ImGui::TextWrapped("%s", player);
                break;
            }
    }
    ImGui::TextDisabled("Native replay / Paused");
    ImGui::Separator();
    ImGui::TextColored(ImVec4(.51f, .94f, .81f, 1), "VIEW");
    ImGui::BeginDisabled(!snapshot.ready || snapshot.finishing);
    if (ImGui::RadioButton("Hero overview", !snapshot.preview))
        picker_preview(request, false);
    ImGui::SameLine();
    ImGui::BeginDisabled(snapshot.selected < 0);
    if (ImGui::RadioButton("Attached preview", snapshot.preview))
        picker_preview(request, true);
    ImGui::EndDisabled();
    ImGui::EndDisabled();
    ImGui::Spacing();
    ImGui::TextColored(ImVec4(.51f, .94f, .81f, 1), "BODY JOINTS");
    ImGui::SetNextItemWidth(-1);
    ImGui::InputTextWithHint("##bone-search", "Search the full rig...", search, sizeof(search));
    ImGui::Checkbox("Common body joints", &common_only);
    if (*search)
        ImGui::TextDisabled("Searching all named bones");
    else if (!common_only)
        ImGui::TextDisabled("All %u named bones",
                            snapshot.catalog ? unsigned(snapshot.catalog->bones.size()) : 0u);
    if (snapshot.error[0])
        ImGui::TextWrapped("%s", snapshot.error);
    ImGui::BeginDisabled(!snapshot.ready || snapshot.finishing);
    const float footer = 138 * panel_scale;
    ImGui::BeginChild("##picker-list",
                      ImVec2(0, std::max(65.0f, ImGui::GetContentRegionAvail().y - footer)), false,
                      ImGuiWindowFlags_HorizontalScrollbar);
    // ImGui's navigation cursor looks like a stray left-edge selection line.
    // The filled joint circle is the sole persistent selection indicator.
    ImGui::PushStyleColor(ImGuiCol_NavCursor, IM_COL32(0, 0, 0, 0));
    unsigned matches = 0;
    if (snapshot.catalog) {
        for (std::size_t index = 0; index < snapshot.catalog->bones.size(); ++index) {
            const auto& bone = snapshot.catalog->bones[index];
            if (!picker_matches(bone, search, common_only))
                continue;
            ++matches;
            const char* friendly = picker_friendly_name(bone.name);
            ImGui::PushID(int(index));
            std::array<double, 3> position{};
            const bool valid = picker_position(snapshot.sample, bone, position);
            ImGui::BeginDisabled(!valid);
            const float row_width = ImGui::GetContentRegionAvail().x;
            if (ImGui::InvisibleButton("##bone-row", ImVec2(row_width, row_height)))
                picker_select(request, int(index));
            const auto row = ImGui::GetItemRectMin();
            const auto row_end = ImGui::GetItemRectMax();
            const bool hovered = ImGui::IsItemHovered();
            const auto text_y = row.y + (row_height - ImGui::GetTextLineHeight()) * .5f;
            const ImVec2 dot(row.x + 11 * panel_scale, row.y + row_height * .5f);
            auto* draw = ImGui::GetWindowDrawList();
            draw->AddRectFilled(row, row_end,
                                hovered ? IM_COL32(41, 70, 70, 245) : IM_COL32(29, 45, 50, 225),
                                5 * panel_scale);
            draw->AddRect(row, row_end, IM_COL32(69, 99, 101, 255), 5 * panel_scale);
            draw->AddCircle(dot, 4 * panel_scale, IM_COL32(146, 204, 192, 255));
            if (int(index) == snapshot.selected)
                draw->AddCircleFilled(dot, 2.5f * panel_scale, IM_COL32(153, 247, 216, 255));
            draw->AddText(ImVec2(row.x + 25 * panel_scale, text_y),
                          valid ? IM_COL32(227, 239, 236, 255) : IM_COL32(125, 140, 140, 255),
                          friendly ? friendly : bone.name);
            if (hovered)
                ImGui::SetTooltip("%s%s", bone.name, valid ? "" : " (pose unavailable)");
            ImGui::EndDisabled();
            ImGui::PopID();
        }
    }
    if (!matches)
        ImGui::TextWrapped("No matching bones. Try another search or show all joints.");
    ImGui::PopStyleColor();
    ImGui::EndChild();
    ImGui::Separator();
    ImGui::TextColored(ImVec4(.51f, .94f, .81f, 1), "SELECTION");
    const bool selected = snapshot.catalog && snapshot.selected >= 0 &&
                          std::size_t(snapshot.selected) < snapshot.catalog->bones.size();
    if (selected) {
        const auto& bone = snapshot.catalog->bones[snapshot.selected];
        const auto friendly = picker_friendly_name(bone.name);
        ImGui::TextWrapped("%s", friendly ? friendly : bone.name);
        if (friendly)
            ImGui::TextDisabled("%s", bone.name);
    } else
        ImGui::TextWrapped("Click a joint or choose a bone in the list.");
    ImGui::BeginDisabled(!selected);
    if (ImGui::Button(snapshot.preview ? "Return to hero overview" : "Preview attached view",
                      ImVec2(-1, 0)))
        picker_preview(request, !snapshot.preview);
    ImGui::EndDisabled();
    ImGui::EndDisabled();
    const float half = (ImGui::GetContentRegionAvail().x - ImGui::GetStyle().ItemSpacing.x) * .5f;
    if (ImGui::Button("Cancel", ImVec2(half, 0)) || ImGui::IsKeyPressed(ImGuiKey_Escape, false))
        editor_enqueue(EditorAction::CancelBonePicker);
    ImGui::SameLine();
    ImGui::BeginDisabled(!selected || !snapshot.ready || snapshot.finishing);
    if (ImGui::Button("Use this bone", ImVec2(-1, 0))) {
        CameraPose token{};
        token[0] = request;
        editor_enqueue(EditorAction::FinishBonePicker, double(snapshot.selected), &token);
    }
    ImGui::EndDisabled();
    ImGui::End();
    ImGui::PopStyleVar(2);

    if (!snapshot.preview) {
        auto* guide = ImGui::GetBackgroundDrawList();
        const ImVec2 from(28 * panel_scale, 27 * panel_scale);
        const ImVec2 to(std::min(left - 20 * panel_scale, 395 * panel_scale), 88 * panel_scale);
        guide->AddRectFilled(from, to, IM_COL32(14, 25, 30, 215), 9 * panel_scale);
        guide->AddText(ImVec2(from.x + 14 * panel_scale, from.y + 11 * panel_scale),
                       IM_COL32(148, 243, 211, 255), "LIVE HERO  /  BONE PICKER");
        guide->AddText(ImVec2(from.x + 14 * panel_scale, from.y + 35 * panel_scale),
                       IM_COL32(224, 240, 238, 255), "Select a joint / Hold middle mouse to orbit");
    }

    if (!snapshot.ready || snapshot.preview || !snapshot.catalog || snapshot.finishing)
        return;
    // A real transparent ImGui hit surface owns clicks in the scene. Merely
    // inspecting global mouse transitions outside every window loses clicks
    // when ImGui considers their initial press to belong to the game.
    ImGui::SetNextWindowPos(ImVec2(0, 0), ImGuiCond_Always);
    ImGui::SetNextWindowSize(ImVec2(std::max(1.0f, left - 8), io.DisplaySize.y), ImGuiCond_Always);
    ImGui::Begin("##picker-canvas", nullptr,
                 ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoBackground |
                     ImGuiWindowFlags_NoMove | ImGuiWindowFlags_NoSavedSettings |
                     ImGuiWindowFlags_NoBringToFrontOnFocus | ImGuiWindowFlags_NoNavFocus |
                     ImGuiWindowFlags_NoFocusOnAppearing | ImGuiWindowFlags_NoScrollWithMouse);
    ImGui::SetCursorPos(ImVec2(0, 0));
    const bool scene_clicked = ImGui::InvisibleButton("##joints", ImGui::GetWindowSize(),
                                                      ImGuiButtonFlags_MouseButtonLeft |
                                                          ImGuiButtonFlags_MouseButtonMiddle);
    const bool orbiting = ImGui::IsItemActive() && ImGui::IsMouseDown(ImGuiMouseButton_Middle);
    if (orbiting && !ImGui::IsPopupOpen("##overlapping-bones", ImGuiPopupFlags_AnyPopupId))
        picker_orbit(request, -io.MouseDelta.x * .25 / panel_scale,
                     io.MouseDelta.y * .25 / panel_scale);
    VisualizationView view{snapshot.view, snapshot.fov, double(io.DisplaySize.x),
                           double(io.DisplaySize.y), 1};
    auto* draw = ImGui::GetBackgroundDrawList();
    const float radius = 5 * panel_scale, hit_radius = std::max(12.0f, 12 * panel_scale);
    const bool clicking = scene_clicked && ImGui::IsMouseReleased(ImGuiMouseButton_Left) &&
                          !orbiting && io.MousePos.x < left - 8 &&
                          !ImGui::IsPopupOpen("##overlapping-bones", ImGuiPopupFlags_AnyPopupId);
    unsigned hits = 0;
    for (std::size_t index = 0; index < snapshot.catalog->bones.size(); ++index) {
        const auto& bone = snapshot.catalog->bones[index];
        if (!picker_matches(bone, search, common_only))
            continue;
        std::array<double, 3> point{};
        VisualizationPoint screen{};
        if (!picker_position(snapshot.sample, bone, point) ||
            !project_visualization_point(view, point, screen) || screen.x >= left - 8)
            continue;
        auto& marker = stable_markers[index];
        picker_stabilize_marker(screen.x, screen.y, panel_scale, marker.x, marker.y,
                                stable_marker_ready[index]);
        const ImVec2 center(marker.x, marker.y);
        const float dx = center.x - io.MousePos.x, dy = center.y - io.MousePos.y;
        const bool hover = dx * dx + dy * dy <= hit_radius * hit_radius;
        const bool chosen = int(index) == snapshot.selected;
        draw->AddCircleFilled(center, radius + 2, IM_COL32(12, 20, 25, 240));
        draw->AddCircleFilled(center, radius, IM_COL32(142, 243, 215, 255));
        if (chosen || hover)
            draw->AddCircle(center, radius + 6, IM_COL32(180, 255, 232, 255), 20, 2);
        if (hover && !ImGui::IsPopupOpen("##overlapping-bones")) {
            const char* label = picker_friendly_name(bone.name);
            ImGui::SetTooltip("%s\n%s", label ? label : bone.name, bone.name);
        }
        if (chosen) {
            const char* label = picker_friendly_name(bone.name);
            draw->AddText(ImVec2(center.x + 14, center.y - 10), IM_COL32(240, 255, 250, 255),
                          label ? label : bone.name);
        }
        if (clicking && hover && hits < candidates.size())
            candidates[hits++] = int(index);
    }
    if (clicking) {
        candidate_count = hits;
        if (hits == 1)
            picker_select(request, candidates[0]);
        else if (hits > 1)
            ImGui::OpenPopup("##overlapping-bones");
    }
    if (ImGui::BeginPopup("##overlapping-bones")) {
        ImGui::TextUnformatted("Choose the joint at this point");
        ImGui::Separator();
        for (unsigned i = 0; i < candidate_count; ++i) {
            const auto index = candidates[i];
            if (index < 0 || std::size_t(index) >= snapshot.catalog->bones.size())
                continue;
            const auto& bone = snapshot.catalog->bones[index];
            if (ImGui::Selectable(bone.name))
                picker_select(request, index);
        }
        ImGui::EndPopup();
    }
    ImGui::End();
}
