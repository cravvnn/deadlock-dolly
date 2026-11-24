#pragma once
// Vector icons copied from the Figma vector paths (stroked, round caps and joins).
#include "imgui.h"

namespace dolly::rail_icons {

// Scene is drawn between Look and Export; its index is appended so saved layouts and
// tab-index checks for Export and Campaths stay valid.
enum class Rail { Camera, Follow, Bone, Look, Export, Campaths, Scene, Count };

// Rail icon in its 23x23 Figma box, centred at `center` (screen px), stroke 1.4 x scale.
void DrawRail(ImDrawList* dl, Rail id, ImVec2 center, float scale, ImU32 col);

// Strokes an SVG path (absolute M/L/C/Z, as Figma exports vectorPaths) given in design px.
// Screen position = origin + p * scale. Open ends get round caps, sharp corners round joins.
void StrokeSvg(ImDrawList* dl, const char* svg, ImVec2 origin, float scale, float thickness,
               ImU32 col, bool roundCaps = true);

} // namespace dolly::rail_icons
