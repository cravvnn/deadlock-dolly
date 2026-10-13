#pragma once
#include "dolly_protocol.hpp"
#include "dolly_environment_source.hpp"
struct ID3D11DeviceContext;
namespace dolly::environment {
constexpr std::size_t CaptureRequestOffset=kControlBytes+25856;
constexpr std::size_t CaptureStatusOffset=kControlBytes+25984;
#pragma pack(push,1)
struct CaptureRequest {
    char magic[8];
    std::uint32_t sequence,abi,command,enabled,editor_pid,game_pid;
    std::uint32_t camera_command,tick,width,height,milliseconds,reserved;
    unsigned char token[16];
};
struct CaptureStatus {
    char magic[8];
    std::uint32_t sequence,abi,command,state,bytes;
};
#pragma pack(pop)
static_assert(sizeof(CaptureRequest)==72 && sizeof(CaptureStatus)==28);
static_assert(CaptureRequestOffset>=kControlBytes+25752);
static_assert(CaptureRequestOffset+sizeof(CaptureRequest)<=CaptureStatusOffset);
static_assert(CaptureStatusOffset+sizeof(CaptureStatus)<=kMappingBytes);
// Diagnostic pixels only. The caller must separately certify camera/exposure/
// empty scenery. States: idle0,armed1,pending2,cpu-ready3,published4,cancelled5,
// invalid6,failed7. One explicit command; no file I/O or automatic rearm.
void capture_worker(unsigned char*,bool owned,unsigned editor,unsigned game,
                    unsigned camera_command,unsigned tick) noexcept;
bool capture_active() noexcept;
void capture_hooks(bool) noexcept;
void capture_draw(ID3D11DeviceContext*, const ShaderContract* contracts=nullptr,
                  std::size_t count=0) noexcept;
void capture_present(ID3D11DeviceContext*) noexcept;
}
