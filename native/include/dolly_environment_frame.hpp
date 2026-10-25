#pragma once
#include "dolly_environment_readback.hpp"
#include <cstring>
#include <cmath>

namespace dolly::environment {
// Wire version 1: little-endian scalars, never serialize a C++ struct or pointer.
// This envelope is diagnostic acquisition data, not an empty/linear certification.
inline std::optional<std::array<unsigned char,88>> frame_header(
    const HdrFrame& frame, const std::array<unsigned char,16>& request) noexcept {
    const auto width=frame.source.texture.Width, height=frame.source.texture.Height;
    bool token=false; for (auto byte:request) token |= byte!=0;
    if (!token || frame.source.state!=SourceState::Candidate ||
        frame.source.identity_state!=5 ||
        frame.source.srv.Format!=DXGI_FORMAT_R16G16B16A16_FLOAT ||
        !width || !height || width>4096 || height>4096 ||
        frame.pixels.size()!=std::uint64_t(width)*height*8) return std::nullopt;
    for (float exposure:frame.exposure)
        if (!std::isfinite(exposure) || exposure<=0) return std::nullopt;
    std::array<unsigned char,88> result{};
    auto integer=[&](unsigned at,std::uint64_t value,unsigned bytes) {
        for (unsigned i=0;i<bytes;++i) result[at+i]=static_cast<unsigned char>(value>>(i*8));
    };
    std::memcpy(result.data(),"DLYHDR01",8);
    integer(8,1,4); integer(12,result.size(),4);
    std::memcpy(result.data()+16,request.data(),request.size());
    integer(32,width,4); integer(36,height,4); integer(40,frame.pixels.size(),8);
    for (unsigned i=0;i<2;++i) {
        static_assert(sizeof(float)==4);
        std::uint32_t bits=0; std::memcpy(&bits,&frame.exposure[i],4);
        integer(48+i*4,bits,4);
    }
    std::memcpy(result.data()+56,frame.source.sha256.data(),32);
    return result;
}
}
