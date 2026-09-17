#pragma once
#include <cstdint>
struct ID3D11Device;
struct ID3D11DeviceContext;
struct ID3D11CommandList;
struct ID3D11Resource;
struct ID3D11Buffer;
struct D3D11_BOX;
namespace dolly::player_capture {
using Producer = int(__fastcall*)(std::uintptr_t, void*, void*, void*, void*,
                                 std::uint32_t, unsigned char*, std::uint32_t*, std::uint32_t*);
void configure(std::uintptr_t scene, bool hashes_ok) noexcept;
void tick() noexcept;
void present_boundary(ID3D11DeviceContext*) noexcept;
bool layout_hook_requested() noexcept;
bool install_layout_hook(ID3D11Device*) noexcept;
bool draw_hooks_requested() noexcept;
void draw_hooks_result(bool installed) noexcept;
void draw(ID3D11DeviceContext*, unsigned method, unsigned vertices, unsigned instances,
          unsigned first) noexcept;
void command(ID3D11DeviceContext*, ID3D11CommandList*, unsigned kind) noexcept;
using IndexedOriginal=void(__stdcall*)(ID3D11DeviceContext*,unsigned,unsigned,unsigned,int,unsigned);
void redirect_indexed(ID3D11DeviceContext*,unsigned,unsigned,unsigned,int,unsigned,IndexedOriginal) noexcept;
using UpdateOriginal=void(__stdcall*)(ID3D11DeviceContext*,ID3D11Resource*,unsigned,const D3D11_BOX*,const void*,unsigned,unsigned);
void update(ID3D11DeviceContext*,ID3D11Resource*,unsigned,const D3D11_BOX*,const void*,unsigned,unsigned,UpdateOriginal) noexcept;
// Authored-path replay time in seconds, published by the bridge; negative when
// no path is playing. Used to pair player frames with the recorder's take.
void publish_replay_time(double seconds) noexcept;
double current_replay_time() noexcept;
}
