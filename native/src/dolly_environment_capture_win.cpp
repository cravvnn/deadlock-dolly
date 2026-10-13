#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <mutex>
#include <atomic>
#include <optional>
#include <cstring>
#include <iterator>
#include <cstdio>
#include "dolly_environment_capture.hpp"
#include "dolly_environment_frame.hpp"
#include "dolly_environment_shaders_generated.hpp"

namespace dolly::environment {
namespace {
std::mutex capture_mutex;
std::atomic<bool> active{false};
CaptureRequest request{};
unsigned last_command=0,state=0,published_bytes=0,draws=0;
ULONGLONG deadline=0;
HdrReadback readback; // touched only by render callbacks under capture_mutex
std::optional<HdrFrame> completed;
bool reset_render=false,sealed=false;
void stop(unsigned reason) noexcept {
    active.store(false,std::memory_order_release);
    state=reason; completed.reset(); reset_render=true; published_bytes=0;
}
bool valid(const CaptureRequest& r,unsigned editor,unsigned game,unsigned camera,unsigned tick) {
    bool token=false;for (auto byte:r.token) token|=byte!=0;
    return !std::memcmp(r.magic,"DLYHDRR1",8) && r.abi==1 && r.command && r.enabled<=1 &&
        r.editor_pid==editor && r.game_pid==game && !r.reserved &&
        (!r.enabled || (token && r.camera_command && r.camera_command==camera && r.tick==tick &&
        r.width && r.height && r.width<=4096 && r.height<=4096 &&
        r.milliseconds && r.milliseconds<=10000));
}
bool transfer() noexcept {
    if (!completed) return false;
    std::array<unsigned char,16> token{};std::memcpy(token.data(),request.token,16);
    const auto header=frame_header(*completed,token);
    if (!header || completed->source.texture.Width!=request.width ||
        completed->source.texture.Height!=request.height) return false;
    wchar_t hex[33]{};
    constexpr wchar_t digits[]=L"0123456789abcdef";
    for(unsigned i=0;i<16;++i){hex[2*i]=digits[token[i]>>4];hex[2*i+1]=digits[token[i]&15];}
    wchar_t name[128]{};
    swprintf_s(name,L"Local\\DollyEnvironmentFrame-%u-%u-%s",request.editor_pid,request.game_pid,hex);
    HANDLE mapping=OpenFileMappingW(FILE_MAP_WRITE,FALSE,name);
    if (!mapping) return false;
    const auto size=header->size()+completed->pixels.size();
    auto* output=static_cast<unsigned char*>(MapViewOfFile(mapping,FILE_MAP_WRITE,0,0,size));
    if (output) {
        std::memcpy(output,header->data(),header->size());
        std::memcpy(output+header->size(),completed->pixels.data(),completed->pixels.size());
        MemoryBarrier();UnmapViewOfFile(output);
        published_bytes=static_cast<unsigned>(size);
    }
    CloseHandle(mapping);return output!=nullptr;
}
void publish(unsigned char* mapping) {
    CaptureStatus out{};std::memcpy(out.magic,"DLYHDRS1",8);
    out.abi=1;out.command=last_command;out.state=state;out.bytes=published_bytes;
    auto* destination=mapping+CaptureStatusOffset;
    auto* sequence=reinterpret_cast<volatile LONG*>(destination+8);
    LONG previous=InterlockedCompareExchange(sequence,0,0),even=(previous|1)+1;
    InterlockedExchange(sequence,even+1);
    std::memcpy(destination,&out,8);
    std::memcpy(destination+12,reinterpret_cast<unsigned char*>(&out)+12,sizeof(out)-12);
    InterlockedExchange(sequence,even+2);
}
}
bool capture_active() noexcept { return active.load(std::memory_order_acquire); }
void capture_worker(unsigned char* mapping,bool owned,unsigned editor,unsigned game,
                    unsigned camera,unsigned tick) noexcept {
    std::lock_guard<std::mutex> lock(capture_mutex);
    if (!owned || !mapping) { if(capture_active()) stop(5); }
    else {
        if(capture_active() && (request.camera_command!=camera || request.tick!=tick)) stop(5);
        CaptureRequest next{};
        auto* source=mapping+CaptureRequestOffset;
        auto* sequence=reinterpret_cast<volatile LONG*>(source+8);
        const auto before=InterlockedCompareExchange(sequence,0,0);
        if (!(before&1)) {
            std::memcpy(&next,source,sizeof(next));MemoryBarrier();
            if(before==InterlockedCompareExchange(sequence,0,0) && next.sequence==unsigned(before) &&
               next.command && next.command!=last_command) {
                last_command=next.command;
                if(!valid(next,editor,game,camera,tick)) stop(6);
                else if(!next.enabled) stop(5);
                else if(capture_active() || reset_render) stop(6);
                else {
                    request=next;state=1;published_bytes=draws=0;sealed=false;
                    completed.reset();deadline=GetTickCount64()+next.milliseconds;
                    active.store(true,std::memory_order_release);
                }
            }
        }
        if(capture_active() && GetTickCount64()>=deadline) stop(7);
        if(capture_active() && state==3) {
            if(transfer()) {state=4;active.store(false,std::memory_order_release);completed.reset();}
            else stop(7);
        }
    }
    if(mapping) publish(mapping);
}
void capture_hooks(bool success) noexcept {
    if(success || !capture_active())return;
    std::lock_guard<std::mutex> lock(capture_mutex);if(capture_active())stop(7);
}
void capture_draw(ID3D11DeviceContext* context,const ShaderContract* contracts,std::size_t count) noexcept {
    if(!capture_active())return;
    std::unique_lock<std::mutex> lock(capture_mutex,std::try_to_lock);
    if(!lock.owns_lock() || !capture_active() || state==3)return;
    if(GetTickCount64()>=deadline || ++draws>100000){stop(7);return;}
    if(state==2 && sealed)return;
    if(!contracts){contracts=ReviewedPostProcess;count=std::size(ReviewedPostProcess);}
    auto source=inspect_source(context,contracts,count);
    if(source.state!=SourceState::Candidate)return;
    if(state==2){stop(7);return;} // ambiguous postprocess sources in acquisition frame
    if(source.texture.Width!=request.width || source.texture.Height!=request.height)return;
    if(readback.begin(context,contracts,count))state=2;
    else stop(7);
}
void capture_present(ID3D11DeviceContext* context) noexcept {
    std::unique_lock<std::mutex> lock(capture_mutex,std::try_to_lock);
    if(!lock.owns_lock())return;
    if(reset_render){readback.cancel();reset_render=false;}
    if(!capture_active())return;
    if(GetTickCount64()>=deadline){stop(7);readback.cancel();reset_render=false;return;}
    if(state==2) {
        sealed=true;
        const auto result=readback.poll(context);
        if(result==ReadbackState::Failed)stop(7);
        else if(result==ReadbackState::Ready){completed=readback.take();state=3;}
    }
}
}
