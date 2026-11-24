#pragma once
// Live lens rain in the game overlay: the editor's lens rain block, the rendered
// camera, and the per-Present lens pass that draws right after the weather.
//
// Threads: the bridge worker polls the editor block, the game's view thread publishes
// the replay clock and the rendered camera, and the game's Present thread simulates
// and draws. Every entry point is noexcept and does nothing while lens rain is off.

struct ID3D11Device;
struct ID3D11DeviceContext;
struct ID3D11RenderTargetView;

namespace lens_rain {
struct Controls;
}

namespace dolly::lens_rain_runtime {

// Bridge worker: accept a newly published lens rain block from the shared mapping.
void poll_editor_block(const unsigned char* memory) noexcept;

// View thread: the replay clock and the camera this view rendered. `angles` are the
// game's pitch, yaw and roll in degrees. `shot_phase` is the authored shot time while a
// shot plays (negative otherwise): each playback restarts the lens at its first frame,
// so every render of a shot sees the same drops. The picture fills the back buffer
// (Dolly's framing control zooms the view; it never letterboxes).
void publish_view(double replay_time, bool seeking, const float position[3], const float angles[3],
                  double shot_phase) noexcept;

// Present thread: whether the lens pass has anything to do this frame (lens rain on,
// or drops left to clear after it was turned off).
bool wants_render() noexcept;

// Present thread, inside a device-state scope after the weather pass and before
// ReShade and capture. Draws only for the Color and World plates (and outside a
// recording), using the weather's rain, wind and cover at the camera.
void render(ID3D11Device* device, ID3D11DeviceContext* context,
            ID3D11RenderTargetView* target) noexcept;

// The shot's lens rain as last published by the editor, for the in-game Scene drawer.
// False before the editor has published any.
bool panel_settings(lens_rain::Controls& out) noexcept;

// Present thread: drop every GPU resource and the lens state (device loss, resize).
void release() noexcept;

} // namespace dolly::lens_rain_runtime
