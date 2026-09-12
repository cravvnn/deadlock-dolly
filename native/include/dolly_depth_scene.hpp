#pragma once
#include "dolly_depth_readback.hpp"
#include <memory>

struct ID3D11DepthStencilView;
struct ID3D11CommandList;

namespace dolly::depth {
struct SceneResources;
enum class SceneResult {
    ready,
    missing,
    cleared,
    ambiguous,
    incompatible_view,
    untracked_commands,
    failed
};
struct SceneFrame {
    SceneResult result = SceneResult::missing;
    std::shared_ptr<SceneResources> resources;
    ID3D11Texture2D* texture() const noexcept;
    std::size_t calibration(ProjectionBuffer* output, std::size_t capacity) const noexcept;
};
// Construct only for a verified Dolly game device/profile. The selector uses
// the reviewed scene texture name, full viewport and reversed depth writes.
// Constant-buffer contents are separately validated after GPU readback.
class SceneTracker {
public:
    SceneTracker(ID3D11Device* device, std::uint32_t width, std::uint32_t height);
    ~SceneTracker();
    SceneTracker(const SceneTracker&) = delete;
    SceneTracker& operator=(const SceneTracker&) = delete;
    void draw(ID3D11DeviceContext* context) noexcept;
    void clear(ID3D11DeviceContext* context, ID3D11DepthStencilView* depth,
               unsigned flags) noexcept;
    void finish(ID3D11DeviceContext* context, ID3D11CommandList* list) noexcept;
    void execute(ID3D11DeviceContext* context, ID3D11CommandList* list) noexcept;
    SceneFrame consume(ID3D11DeviceContext* immediate) noexcept;

private:
    struct Impl;
    std::unique_ptr<Impl> impl;
};
// Hook code stays resident. Null disables observation without changing the
// game's graphics state. Call install only after the existing native gates.
bool install_scene_hooks(ID3D11Device* device, ID3D11DeviceContext* immediate) noexcept;
void set_scene_tracker(std::shared_ptr<SceneTracker> tracker) noexcept;
}
