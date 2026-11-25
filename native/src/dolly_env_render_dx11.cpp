// DX11 Screen-Space Volumetric Sandstorm / Ground-Fog Raymarch & Surface Coating Pass.
//
// Adapted from Wockstajit/SourceMVM under the MIT License:
//   Copyright (c) 2021 advancedfx.org
//   Copyright (c) 2026 Wockstajit (SOURCE:MVM)
// Depth-based edge-preserving normal reconstruction follows Turanszki (2019) /
// atyuwen (2021) as adapted in SourceMVM EnvironmentCoatingShaders.h.
//
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to deal
// in the Software without restriction, including without limitation the rights
// to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
// copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions:
// The above copyright notice and this permission notice shall be included in
// all copies or substantial portions of the Software.

#include "dolly_env_render_dx11.hpp"

#define NOMINMAX
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3d11.h>
#include <d3dcompiler.h>
#include <wrl/client.h>

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstring>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

namespace dolly::env {
using Microsoft::WRL::ComPtr;

namespace {

constexpr char kEnvRenderHlsl[] = R"HLSL(
#define EMITTERS 16
static const float kNoFloor = -5.0e5;
static const float kVolumeTop = 1.7; // global volume extent above the floor, in multiples of heightCeiling

struct EmitterGPU {
    float4 posRadius;        // xyz world position, w core radius (u)
    float4 dirSpeed;         // xyz normalized aim direction, w jet speed (u/s)
    float4 colorDensity;     // rgb tint, w density
    float4 coneHeightActive; // x coneCos, y heightMin, z heightMax, w activeWeight
};

cbuffer EnvConstants : register(b0) {
    row_major float4x4 invViewProj;
    row_major float4x4 viewProj;
    float4 cameraPos;     // xyz world position, w = 1 if reversed-Z
    float4 screenInfo;    // x width, y height, z 1/width, w 1/height
    float4 gridWindow;    // xy origin (world), zw 1/span (world)
    float4 windTime;      // xy steady wind (u/s), z sim time (s), w max distance (u)
    float4 styleA;        // x preset, y intensity, z heightCeiling, w densityScale
    float4 styleB;        // x wispStrength, y coatingStrength, z marchSteps, w emitterCount
    float4 sunDir;        // xyz normalized sun direction, w enableCoating
    float4 sunColor;      // rgb sun light, w enableEmitterPlumes
    float4 ambientColor;  // rgb sky/ambient, w nearClip
    float4 thinColor;     // rgb thin scatter albedo, w farClip
    float4 denseColor;    // rgb dense scatter albedo, w 1 = puddles reflect the frame
    float4 coatingInfo;   // x growth 0..1, y drift strength, z steady wind speed, w 1 = gpuView
    float4 volumeBounds;  // x lowest floor Z, y highest floor Z + reach, z reach, w 1 = wallReach set
    float4 floorDecode;   // x lowest floor Z, y floor range: decodes the UNORM16 floor map
    float4 netInfo;       // x 2 = composite from halfMarch (safety net), y edge threshold,
                          // z 1 = driftMap holds a drift potential, w hero count
    float4 trailInfo;     // x 1 = hero trails, y fill-in time (s), z shot start (s), w build-up (s)
    EmitterGPU emitters[EMITTERS];
    float4 heroes[12];    // xyz feet position of a hero the coating leaves clean
};

Texture2D<float4> sceneColor : register(t0);
Texture2D<float> sceneDepth : register(t1);
Texture2D<float4> fluidState : register(t2); // xy velocity (u/s), z carried density, w solid
Texture2D<float> airMap : register(t3);      // >0.5 solid, 0..0.498 openness
Texture2D<float> topsMap : register(t4);     // highest solid Z
Texture2D<float4> floorZMap : register(t5);  // 4 packed floor heights, highest first (UNORM16)
Texture2D<float> streetZMap : register(t6);  // street-deck elevation
Texture3D<float> gustNoise3D : register(t7); // 64^3 seamless Perlin-Worley volume
struct ViewData {
    row_major float4x4 viewProj;    // without TAA jitter, for drawing
    row_major float4x4 invViewProj; // with the frame's jitter, matching its depth
    float4 cameraPos;               // xyz world, w 1 when a valid view was found
    float4 right;
    float4 up;
};
StructuredBuffer<ViewData> gpuView : register(t8);
// Sun optical depth through the volume, baked per frame by BakeSunCS: map X, map Y, and
// height above the floor the ray is over, from -18 to the volume reach (volumeBounds.z).
Texture3D<float> sunBake : register(t9);
Texture2D<float4> halfMarch : register(t10); // safety net: half-resolution march
Texture2D<float> driftMap : register(t11);   // drift potential 0..2, stored halved (UNORM16)
Texture2D<float> wallReach : register(t14);  // 1 where a wall is within the enclosure's reach
// Hero trails: the recorded path segments, and the map they are stamped into each frame.
struct TrailSeg {
    float4 a; // xyz start (world), w its shot time (s)
    float4 b; // xyz end, w its shot time
};
StructuredBuffer<TrailSeg> trailSegs : register(t12);
// Per texel: x the latest shot time a hero trod it, y minus the earliest (both MAX-blended).
// z minus the distance to the nearest path (MAX-blended, so the whole path's distance field,
// from which the rim pushed aside at a track's shoulders is shaped), w the latest shot time a
// hero passed within reach (no shoulder offset), so the rim fades with the track's own age.
Texture2D<float4> trailMap : register(t13);
RWTexture3D<float> sunBakeOut : register(u0);

static float4x4 gInvViewProj;
static float4x4 gViewProj;
static float3 gCamPos;

// The camera comes from the constant buffer, or (coatingInfo.w) from a view the GPU
// built for this exact frame. Returns false when that frame had no valid view.
bool LoadView() {
    if (coatingInfo.w > 0.5) {
        ViewData v = gpuView[0];
        gInvViewProj = v.invViewProj;
        gViewProj = v.viewProj;
        gCamPos = v.cameraPos.xyz;
        return v.cameraPos.w > 0.5;
    }
    gInvViewProj = invViewProj;
    gViewProj = viewProj;
    gCamPos = cameraPos.xyz;
    return true;
}

SamplerState linearClamp : register(s0);
SamplerState linearWrap : register(s1);

struct FullscreenVSOut {
    float4 pos : SV_Position;
    float2 uv : TEXCOORD0;
};

FullscreenVSOut FullscreenVS(uint id : SV_VertexID) {
    FullscreenVSOut o;
    o.uv = float2((id << 1) & 2, id & 2);
    o.pos = float4(o.uv * float2(2.0, -2.0) + float2(-1.0, 1.0), 0.0, 1.0);
    return o;
}

)HLSL"
                                  R"HLSL(
// ---- Hero trails ----
// Every frame the trails are stamped afresh from the recorded paths up to this frame's shot
// time into a square window that follows the camera (snapped to whole texels), keeping per
// texel the latest time a hero trod it. MAX blending makes the result independent of draw
// order, so a frame depends only on the paths and its own time and camera.
static const float kTrailSpan = 2048.0;  // world units across the window
static const float kTrailTexels = 1024.0;
static const float kTrailCore = 7.0;     // half width of the fully trodden middle (a footpath)
static const float kTrailFeather = 9.0;  // soft shoulder outside it, fading to untouched
static const float kTrailNever = -1.0e9;
// Untrodden texels hold a time this many fill-in times ago (set when the map is cleared),
// and a shoulder fades smoothly out to it, so a track's edge blends into the snow around it
// instead of switching on and off one texel at a time.
static const float kTrailFade = 6.0;
// Pieces are drawn this far past the track's edge so the distance field runs smoothly out to
// where the map's clear value (the same distance) takes over.
static const float kTrailReach = kTrailCore + kTrailFeather + 6.0;

float2 TrailOrigin(float3 cam) {
    float texel = kTrailSpan / kTrailTexels;
    return floor(cam.xy / texel) * texel - 0.5 * kTrailSpan;
}

struct TrailVSOut {
    float4 pos : SV_Position;
    float2 world : TEXCOORD0;
    nointerpolation float4 seg : TEXCOORD1;  // xy start, zw end (world)
    nointerpolation float2 times : TEXCOORD2; // shot times at start and end
};

TrailVSOut TrailStampVS(uint vid : SV_VertexID, uint iid : SV_InstanceID) {
    TrailVSOut o;
    o.pos = float4(2.0, 2.0, 0.0, 1.0); // off screen unless this segment is drawn
    o.world = float2(0.0, 0.0);
    o.seg = float4(0.0, 0.0, 0.0, 0.0);
    o.times = float2(kTrailNever, kTrailNever);
    LoadView();
    TrailSeg s = trailSegs[iid];
    float now = windTime.z;
    if (s.a.w > now || s.b.w <= s.a.w)
        return o;
    // A segment still being walked ends where the hero is at this frame's time.
    float f = saturate((now - s.a.w) / (s.b.w - s.a.w));
    float2 a = s.a.xy;
    float2 b = lerp(s.a.xy, s.b.xy, f);
    float tb = lerp(s.a.w, s.b.w, f);
    float2 dir = b - a;
    float len = length(dir);
    dir = len > 1e-3 ? dir / len : float2(1.0, 0.0);
    float2 side = float2(-dir.y, dir.x);
    float r = kTrailReach;
    // Two triangles covering the capsule's bounding box.
    // (0,1,2) = (-,-) (+,-) (-,+); (3,4,5) = (+,-) (+,+) (-,+)
    float2 corner = float2((vid == 1 || vid == 3 || vid == 4) ? 1.0 : -1.0,
                           (vid == 2 || vid == 4 || vid == 5) ? 1.0 : -1.0);
    float2 w = (corner.x < 0.0 ? a - dir * r : b + dir * r) + side * r * corner.y;
    float2 origin = TrailOrigin(gCamPos);
    float2 uv = (w - origin) / kTrailSpan;
    o.pos = float4(uv.x * 2.0 - 1.0, uv.y * 2.0 - 1.0, 0.0, 1.0);
    o.world = w;
    o.seg = float4(a, b);
    o.times = float2(s.a.w, tb);
    return o;
}

float4 TrailStampPS(TrailVSOut i) : SV_Target {
    float2 a = i.seg.xy, b = i.seg.zw;
    float2 ab = b - a;
    float k = saturate(dot(i.world - a, ab) / max(dot(ab, ab), 1e-6));
    float d = length(i.world - (a + ab * k));
    if (d > kTrailReach)
        discard;
    float t = lerp(i.times.x, i.times.y, k);
    // The shoulder reads as older (shallower) track, fading out to untouched at its edge.
    float shoulder = smoothstep(0.0, 1.0, saturate((d - kTrailCore) / kTrailFeather));
    float te = t - shoulder * kTrailFade * trailInfo.y;
    // Minus the distance, so MAX blending keeps the nearest piece: the distance to the whole
    // path, smooth through every joint (a rim shaped per piece ringed each joint).
    return float4(te, -te, -d, t);
}

// Fraction of the cover settled by shot time x (weather::coating_growth, kRate 3).
float CoverGrown(float x) {
    if (trailInfo.w <= 0.0)
        return 1.0;
    float age = max(x - trailInfo.z, 0.0);
    return min((1.0 - exp(-3.0 * age / trailInfo.w)) / (1.0 - exp(-3.0)), 1.0);
}

// How freshly trodden the ground at p is (x: 1 on a track walked just now, fading as falling
// snow or blown sand fills it in; 0 where no hero walked), how churned (y: trodden again and
// again, or stood on for a while) and how high the rim pushed aside there stands (z, 0..1,
// fading with the track). Zero where trails are off.
float3 TrailAt(float3 p) {
    if (trailInfo.x < 0.5)
        return float3(0.0, 0.0, 0.0);
    float2 uv = (p.xy - TrailOrigin(gCamPos)) / kTrailSpan;
    if (any(uv <= 0.0) || any(uv >= 1.0))
        return float3(0.0, 0.0, 0.0);
    // Cubic B-spline filter from four bilinear taps: no texel diamonds up close.
    float2 g = float2(uv.x, 1.0 - uv.y) * kTrailTexels - 0.5;
    float2 f = frac(g), base = floor(g);
    float2 w0 = (1.0 - f) * (1.0 - f) * (1.0 - f) / 6.0;
    float2 w1 = (4.0 - 6.0 * f * f + 3.0 * f * f * f) / 6.0;
    float2 w3 = f * f * f / 6.0;
    float2 w2 = 1.0 - w0 - w1 - w3;
    float2 s0 = w0 + w1, s1 = w2 + w3;
    float2 c0 = (base - 0.5 + w1 / s0) / kTrailTexels;
    float2 c1 = (base + 1.5 + w3 / s1) / kTrailTexels;
    float4 m = s0.y * (s0.x * trailMap.SampleLevel(linearClamp, float2(c0.x, c0.y), 0) +
                       s1.x * trailMap.SampleLevel(linearClamp, float2(c1.x, c0.y), 0)) +
               s1.y * (s0.x * trailMap.SampleLevel(linearClamp, float2(c0.x, c1.y), 0) +
                       s1.x * trailMap.SampleLevel(linearClamp, float2(c1.x, c1.y), 0));
    float t = m.x;
    float age = windTime.z - t;
    if (age < 0.0)
        return float3(0.0, 0.0, 0.0);
    float edge = min(min(uv.x, uv.y), min(1.0 - uv.x, 1.0 - uv.y));
    // Rescaled so untrodden snow (filled in kTrailFade times over) is exactly untouched.
    float floorFresh = exp(-kTrailFade);
    float fresh = (exp(-age / max(trailInfo.y, 1.0)) - floorFresh) / (1.0 - floorFresh);
    // A track only pressed the cover that had settled when it was made; what fell since
    // lies on top of it.
    float now = CoverGrown(windTime.z);
    float pressed = now > 1e-3 ? saturate(CoverGrown(t) / now) : 0.0;
    float tread = saturate(fresh) * smoothstep(0.0, 0.08, edge) * pressed;
    // Trodden over a long span of time: walked again and again, or stood on.
    float churn = smoothstep(2.0, 20.0, t + m.y) * smoothstep(0.05, 0.35, tread);
    // The snow a foot pushes aside heaps into a low rim around the shoulders, none in the
    // middle (so a track never turns into a ridge as it fills in). It fades as the track it
    // belongs to fills in.
    float dist = -m.z;
    float rimShape = smoothstep(kTrailCore * 0.55, kTrailCore + kTrailFeather * 0.35, dist) *
                     (1.0 - smoothstep(kTrailCore + kTrailFeather * 0.35,
                                       kTrailCore + kTrailFeather, dist));
    float rimAge = windTime.z - m.w;
    float rimFresh = (exp(-max(rimAge, 0.0) / max(trailInfo.y, 1.0)) - floorFresh) /
                     (1.0 - floorFresh);
    float rim = rimShape * saturate(rimFresh) * smoothstep(0.0, 0.08, edge) * pressed;
    return float3(tread, churn, rim);
}

bool IsSky(float d) {
    return cameraPos.w > 0.5 ? (d <= 1e-6) : (d >= 1.0 - 1e-6);
}

// Depth may be rendered below the output size (upscaling); map output pixels onto it.
float ReadDepthPx(float2 px) {
    uint dw, dh;
    sceneDepth.GetDimensions(dw, dh);
    int2 q = clamp(int2((px + 0.5) * float2(dw, dh) * screenInfo.zw), int2(0, 0), int2(dw, dh) - 1);
    return sceneDepth.Load(int3(q, 0));
}

float3 WorldFromPixelDepth(float2 px, float d) {
    float2 uv = (px + 0.5) * screenInfo.zw;
    float2 ndc = float2(uv.x * 2.0 - 1.0, 1.0 - uv.y * 2.0);
    float4 clip = float4(ndc, d, 1.0);
    float4 world = mul(gInvViewProj, clip);
    return world.xyz / max(abs(world.w), 1e-6) * (world.w < 0.0 ? -1.0 : 1.0);
}

// Edge-preserving surface normal reconstruction from depth (Turanszki 2019 / atyuwen 2021).
// cavity > 0 in a crease or corner (the neighbours rise above the surface), < 0 on an edge.
float3 CoatingBaseNormal(float2 pixel, float depth, float3 center, float3 fallback,
                         out float cavity) {
    cavity = 0.0;
    float3 bend[2];
    float bendSpan[2];
    bool both[2];
    // With depth rendered below the output size (upscaling), one output pixel steps would
    // land on the same depth texel and flatten the normal; step whole depth texels instead.
    uint dw, dh;
    sceneDepth.GetDimensions(dw, dh);
    float2 texel = max(float2(1.0, 1.0), ceil(screenInfo.xy / float2(dw, dh)));
    float3 tangent[2];
    [unroll] for (int a = 0; a < 2; ++a) {
        float2 s = a ? float2(0.0, texel.y) : float2(texel.x, 0.0);
        float2 l1 = pixel - s, l2 = pixel - 2.0 * s;
        float2 r1 = pixel + s, r2 = pixel + 2.0 * s;
        bool left = all(l2 >= 0.5) && all(l2 < screenInfo.xy - 0.5);
        bool right = all(r2 >= 0.5) && all(r2 < screenInfo.xy - 0.5);
        float dl1 = left ? ReadDepthPx(l1) : 0.0;
        float dl2 = left ? ReadDepthPx(l2) : 0.0;
        float dr1 = right ? ReadDepthPx(r1) : 0.0;
        float dr2 = right ? ReadDepthPx(r2) : 0.0;
        left = left && !IsSky(dl1) && !IsSky(dl2);
        right = right && !IsSky(dr1) && !IsSky(dr2);
        if (!left && !right) return fallback;
        float el = left ? abs(2.0 * dl1 - dl2 - depth) : 1e30;
        float er = right ? abs(2.0 * dr1 - dr2 - depth) : 1e30;
        float3 wl = left ? WorldFromPixelDepth(l1, dl1) : center;
        float3 wr = right ? WorldFromPixelDepth(r1, dr1) : center;
        tangent[a] = (el < er) ? (center - wl) : (wr - center);
        both[a] = left && right;
        bend[a] = 0.5 * (wl + wr) - center;
        bendSpan[a] = max(0.5 * length(wr - wl), 1e-3);
    }
    float3 n = cross(tangent[0], tangent[1]);
    if (dot(n, n) < 1e-12) return fallback;
    n = normalize(n);
    n = dot(n, gCamPos - center) < 0.0 ? -n : n;
    float sum = 0.0, used = 0.0;
    [unroll] for (int b = 0; b < 2; ++b) {
        if (!both[b]) continue;
        sum += dot(bend[b], n) / bendSpan[b];
        used += 1.0;
    }
    cavity = used > 0.0 ? clamp(sum / used * 4.0, -1.0, 1.0) : 0.0;
    return n;
}

float Hash3(float3 p) {
    p = frac(p * float3(0.1031, 0.1030, 0.0973));
    p += dot(p, p.yxz + 33.33);
    return frac((p.x + p.y) * p.z);
}

float ValueNoise3(float3 p) {
    float3 i = floor(p), f = frac(p);
    f = f * f * (3.0 - 2.0 * f);
    float n000 = Hash3(i + float3(0, 0, 0));
    float n100 = Hash3(i + float3(1, 0, 0));
    float n010 = Hash3(i + float3(0, 1, 0));
    float n110 = Hash3(i + float3(1, 1, 0));
    float n001 = Hash3(i + float3(0, 0, 1));
    float n101 = Hash3(i + float3(1, 0, 1));
    float n011 = Hash3(i + float3(0, 1, 1));
    float n111 = Hash3(i + float3(1, 1, 1));
    return lerp(lerp(lerp(n000, n100, f.x), lerp(n010, n110, f.x), f.y),
                lerp(lerp(n001, n101, f.x), lerp(n011, n111, f.x), f.y), f.z);
}

float Fbm3(float3 p) {
    return ValueNoise3(p) * 0.55 + ValueNoise3(p * 2.13 + 7.1) * 0.30 +
           ValueNoise3(p * 4.47 + 13.7) * 0.15;
}

float InterleavedGradient(float2 px) {
    return frac(52.9829189 * frac(dot(px, float2(0.06711056, 0.00583715))));
}

float2 MapUV(float2 worldXY) {
    return (worldXY - gridWindow.xy) * gridWindow.zw;
}

bool OnMap(float2 uv) {
    return all(uv >= 0.0) && all(uv <= 1.0);
}

float SampleOpenness(float2 uv) {
    if (!OnMap(uv)) return 1.0;
    float m = airMap.SampleLevel(linearClamp, uv, 0);
    return m > 0.5 ? 0.0 : saturate(m * 255.0 / 127.0);
}

// Selects the highest walkable floor layer <= z + 16u from the 4-layer map grid,
// falling back to street_z when no nav/walkable floor layer is present.
float SampleFloorBelow(float3 p, out bool hasFloor) {
    float2 uv = MapUV(p.xy);
    hasFloor = false;
    if (!OnMap(uv)) return p.z - 64.0;
    uint mw, mh;
    floorZMap.GetDimensions(mw, mh);
    int2 mq = clamp(int2(floor(uv * float2(mw, mh))), int2(0, 0), int2(mw - 1, mh - 1));
    float4 codes = floorZMap.Load(int3(mq, 0)) * 65535.0;
    float zLim = p.z + 16.0;
    [unroll] for (int i = 0; i < 4; ++i) {
        if (codes[i] < 0.5) continue; // no floor in this layer
        float h = floorDecode.x + (codes[i] - 1.0) / 65534.0 * floorDecode.y;
        if (h <= zLim) {
            hasFloor = true;
            return h;
        }
    }
    hasFloor = true;
    return streetZMap.SampleLevel(linearClamp, uv, 0);
}

float SampleGustNoise3D(float3 q, float2 airXY, float period, float cycle) {
    float2 steady = windTime.xy;
    float3 at = float3(q.xy - steady * windTime.z, q.z) / max(period, 32.0);
    float t = windTime.z / max(cycle, 0.2);
    float a = frac(t) - 0.5;
    float b = frac(t + 0.5) - 0.5;
    float w = abs(a * 2.0);
    float2 relAir = clamp(airXY - steady, -450.0, 450.0);
    float3 drag = float3(relAir * cycle, 0.0) / max(period, 32.0);
    float na = gustNoise3D.SampleLevel(linearWrap, at - drag * a, 0);
    float nb = gustNoise3D.SampleLevel(linearWrap, at - drag * b, 0);
    return lerp(na, nb, w);
}

// Henyey-Greenstein forward scattering phase
float PhaseHG(float cosTheta, float g) {
    float g2 = g * g;
    return (1.0 - g2) / (4.0 * 3.14159265 * pow(max(1.0 + g2 - 2.0 * g * cosTheta, 0.08), 1.5));
}

)HLSL"
                                  R"HLSL(
// 1 where open sky lies above a surface at height z, 0 under a roof, bridge deck or
// overhang. Bilinear over the four nearest map cells gives a one-cell soft drip line.
// The four map cells around uv (one Gather) and the bilinear weights between them.
// Gather order: x = (0,1), y = (1,1), z = (1,0), w = (0,0).
float4 GatherTops(float2 uv, out float2 f) {
    uint w, h;
    topsMap.GetDimensions(w, h);
    float2 g = uv * float2(w, h) - 0.5;
    float2 b = floor(g);
    f = g - b;
    // Gather at the corner the four cells share, so the cells always match `f` (gathering
    // at uv itself can pick a neighbouring quad a sub-texel off and draw dashed lines).
    return topsMap.GatherRed(linearClamp, (b + 1.0) / float2(w, h));
}

float BlendCells(float4 v, float2 f) {
    return lerp(lerp(v.w, v.z, f.x), lerp(v.x, v.y, f.x), f.y);
}

// Soft in height as well as across cells: a hard roof test against this pixel's own z
// flipped cell by cell along height contours and drew straight-edged panels on ramps.
float SkyOpenAt(float2 uv, float z) {
    if (!OnMap(uv)) return 1.0;
    float2 f;
    float4 tops = GatherTops(uv, f);
    f = f * f * (3.0 - 2.0 * f);
    return BlendCells(1.0 - smoothstep(14.0, 34.0, tops - z), f); // centred on 24 u as before
}

// Shadow under roofs, blended per cell so a tall roof's height never bleeds across the
// wall below it (blending heights first did).
float RoofShadowAt(float2 uv, float z) {
    if (!OnMap(uv)) return 1.0;
    float2 f;
    float4 tops = GatherTops(uv, f);
    float4 lit = 1.0 - smoothstep(24.0, 160.0, tops - z);
    return lerp(0.38, 1.0, BlendCells(lit, f));
}

)HLSL"
                                  R"HLSL(
// Drift potential at a map point: 1 on open ground, more in the lee of walls and at their
// foot, less where the wind sweeps (see map_grid::bake_drift_potential). Even without one.
float DriftAt(float2 uv) {
    if (netInfo.z < 0.5 || !OnMap(uv)) return 1.0;
    return driftMap.SampleLevel(linearClamp, uv, 0) * 2.0;
}

// 1 on a hero (feet to head, a little wider than the body), so cover never lands on one.
float HeroMask(float3 p) {
    float m = 0.0;
    int count = int(netInfo.w + 0.5);
    [loop] for (int i = 0; i < 12; ++i) {
        if (i >= count) break;
        float3 d = p - heroes[i].xyz;
        float across = 1.0 - smoothstep(30.0, 42.0, length(d.xy));
        float along = step(-12.0, d.z) * (1.0 - smoothstep(104.0, 118.0, d.z));
        m = max(m, across * along);
    }
    return m;
}

// The hero whose footprint holds p (feet to head, a little wider than the body): returns how
// fully p lies in it (0..1) and that hero's soles height.
float HeroFootprint(float3 p, out float soleZ) {
    float m = 0.0;
    soleZ = p.z;
    int count = int(netInfo.w + 0.5);
    [loop] for (int i = 0; i < 12; ++i) {
        if (i >= count) break;
        float3 d = p - heroes[i].xyz;
        float across = 1.0 - smoothstep(30.0, 42.0, length(d.xy));
        float along = step(-12.0, d.z) * (1.0 - smoothstep(104.0, 118.0, d.z));
        if (across * along > m) {
            m = across * along;
            soleZ = heroes[i].z;
        }
    }
    return m;
}

// How deep a full blanket lies, in world units: ankle-deep snow, a shallow layer of sand.
float BlanketMax(int preset) {
    if (preset == 6) return 8.0; // snow
    if (preset == 1) return 4.0; // sandstorm sand
    if (preset == 2 || preset == 4) return 2.0; // dust, ash
    return 0.0;
}

// The blanket's top above its ground at xy (world units) for a blanket `depth` deep:
// pressed down along tracks, raised a little along their rims.
float BlanketTopAt(float2 xy, float z, float depth) {
    float3 tr = TrailAt(float3(xy, z));
    return depth * (1.0 - 0.6 * tr.x + 0.25 * tr.z);
}

// The blanket's depth for a cover amount: none while it is still a dusting, full once the
// sheet has closed.
float BlanketDepth(int preset, float amount) {
    return BlanketMax(preset) * saturate((amount * 1.45 - 0.6) / 0.9);
}

// The highest floor of map cell q at or below zLim; false when it has none.
bool FloorBelowCell(int2 q, float zLim, out float h) {
    h = 0.0;
    float4 codes = floorZMap.Load(int3(q, 0)) * 65535.0;
    [unroll] for (int i = 0; i < 4; ++i) {
        if (codes[i] < 0.5) continue;
        h = floorDecode.x + (codes[i] - 1.0) / 65534.0 * floorDecode.y;
        if (h <= zLim) return true;
    }
    return false;
}

// The floor below p blended between the four nearest cells (those within a curb's step of
// this cell's floor), so heights read from the map have no per-cell stairs.
// Every weight is continuous in position: a cell with no floor (under a pillar) weighs
// nothing instead of borrowing a neighbour's floor, and a cell can only claim to be the
// highest floor as its own weight fades in. Either cut drew teeth and hard edges.
// One map cell's floor below zLim (FloorBelowCell, with the edge clamp): x height, y 1 when
// the cell has one.
float2 FloorCellAt(int2 q, int2 dims, float zLim) {
    float h;
    bool valid = FloorBelowCell(clamp(q, int2(0, 0), dims - 1), zLim, h);
    return float2(h, valid ? 1.0 : 0.0);
}

// The blend of a 2 x 2 block of cells (c0 = b, c1 = b + x, c2 = b + y, c3 = b + x + y) at
// fraction g - b; see SampleFloorBelowSmooth.
float BlendFloorCells(float2 c0, float2 c1, float2 c2, float2 c3, float2 f, float street) {
    f = f * f * (3.0 - 2.0 * f); // no creases at cell centres
    float2 cells[4] = {c0, c1, c2, c3};
    float hs[4], ws[4];
    float top = -1e9;
    [unroll] for (int k = 0; k < 4; ++k) {
        int2 o = int2(k & 1, k >> 1);
        hs[k] = cells[k].x;
        ws[k] = cells[k].y > 0.5 ? (o.x ? f.x : 1.0 - f.x) * (o.y ? f.y : 1.0 - f.y) : 0.0;
        top = max(top, hs[k] - (1.0 - saturate(ws[k] * 4.0)) * 28.0);
    }
    float sum = 0.0, wsum = 0.0;
    [unroll] for (int j = 0; j < 4; ++j) {
        // Keep floors near the highest one (a curb's step blends; a ledge's drop does not).
        float w = ws[j] * (1.0 - smoothstep(10.0, 28.0, top - hs[j]));
        sum += hs[j] * w;
        wsum += w;
    }
    return lerp(street, sum / max(wsum, 1e-4), saturate(wsum * 4.0));
}

float SampleFloorBelowSmooth(float3 p) {
    float2 uv = MapUV(p.xy);
    if (!OnMap(uv)) return p.z - 64.0;
    float street = streetZMap.SampleLevel(linearClamp, uv, 0);
    uint mw, mh;
    floorZMap.GetDimensions(mw, mh);
    int2 dims = int2(mw, mh);
    float2 g = uv * float2(mw, mh) - 0.5;
    int2 b = int2(floor(g));
    float zLim = p.z + 16.0;
    return BlendFloorCells(FloorCellAt(b, dims, zLim), FloorCellAt(b + int2(1, 0), dims, zLim),
                           FloorCellAt(b + int2(0, 1), dims, zLim),
                           FloorCellAt(b + int2(1, 1), dims, zLim), g - float2(b), street);
}

// Round specks (glints, flecks) fixed on the ground: `density` of the cell x cell squares
// hold one. A speck spreads over at least a pixel and dims to keep its energy, so far
// away specks fade into the average instead of flickering as squares.
float RoundSpeck(float3 p, float cell, float density, float footprint, float seed) {
    // Cells by map position only: the street lies right on z = 256, so height in the cell
    // flipped specks on and off along every height contour.
    float3 c = float3(floor(p.xy / cell), 0.0);
    if (Hash3(c + seed) > density) return 0.0;
    float2 center = (c.xy + 0.2 + 0.6 * float2(Hash3(c + seed + 1.7), Hash3(c + seed + 3.1))) * cell;
    float r = max(0.3, footprint * 0.7);
    float d = length(p.xy - center);
    return (1.0 - smoothstep(r * 0.4, r, d)) * saturate(0.09 / (r * r));
}

// How boxed in by walls a point is: 0 in the open; at a wall's foot about 3/8 beside a
// straight wall and 5/8 in an inside corner (28 u out: about 1/8 and 3/8), where drifts
// pile highest. Eight map taps 36 u around the point.
// `away` points from the walls toward open ground; it turns smoothly around a pillar's
// corners, where per-face normals jump.
// Two staggered rings (36 u and 60 u) of the blended solid map, unsharpened: a single
// sharpened ring stepped cell by cell along a wall and scalloped the bank's top.
float EnclosureDir(float2 xy, out float2 away) {
    float s = 0.0;
    away = float2(0.0, 0.0);
    // No wall within reach of any tap: every tap reads open ground, so the result is
    // exactly 0 and the search can be skipped.
    if (volumeBounds.w > 0.5) {
        float2 ruv = MapUV(xy);
        if (OnMap(ruv)) {
            uint rw, rh;
            wallReach.GetDimensions(rw, rh);
            int2 rq = clamp(int2(floor(ruv * float2(rw, rh))), int2(0, 0), int2(rw, rh) - 1);
            if (wallReach.Load(int3(rq, 0)) < 0.5)
                return 0.0;
        }
    }
    [unroll] for (int k = 0; k < 16; ++k) {
        float a = k * 0.3926991;
        float2 dir = float2(cos(a), sin(a));
        float2 uv = MapUV(xy + dir * ((k & 1) ? 60.0 : 36.0));
        // airMap holds sky openness in 0..0.5 and walls as 1: only the part above 0.5 is wall.
        float solid = OnMap(uv) ? saturate((airMap.SampleLevel(linearClamp, uv, 0) - 0.5) * 2.0) : 0.0;
        s += solid;
        away -= dir * solid;
    }
    return s * 0.0625;
}

float Enclosure(float2 xy) {
    float2 away;
    return EnclosureDir(xy, away);
}

// How high a drift banks against a wall at full cover, in world units.
float BankHeight(int preset) {
    if (preset == 6) return 16.0; // snow
    if (preset == 1) return 10.0; // sandstorm sand
    if (preset == 2) return 6.0;  // dust
    if (preset == 4) return 5.5;  // ash
    return 0.0;
}

)HLSL"
                                  R"HLSL(
// The sky a wet surface mirrors when nothing on screen is in the way: the ambient sky,
// brighter toward the horizon, with the sun's glint.
float3 SkyReflection(float3 r) {
    float up = saturate(r.z);
    float3 horizon = ambientColor.rgb * 1.15 + sunColor.rgb * 0.12;
    float3 zenith = ambientColor.rgb * 0.8;
    float s = saturate(dot(r, sunDir.xyz));
    return lerp(horizon, zenith, sqrt(up)) + sunColor.rgb * (pow(s, 900.0) * 5.0 + pow(s, 48.0) * 0.2);
}

// How far the point q lies behind what the frame shows at its pixel (> 0 = behind), and
// that pixel's uv. False when q leaves the screen or is behind the camera.
bool ReflectProbe(float3 q, out float behind, out float2 uv) {
    behind = -1.0;
    uv = float2(0.0, 0.0);
    float4 clip = mul(gViewProj, float4(q, 1.0));
    if (clip.w <= 1e-3) return false;
    float2 ndc = clip.xy / clip.w;
    if (any(abs(ndc) > 1.0)) return false;
    uv = float2(ndc.x * 0.5 + 0.5, 0.5 - ndc.y * 0.5);
    float2 px = uv * screenInfo.xy - 0.5;
    float d = ReadDepthPx(px);
    if (IsSky(d)) return true; // nothing on screen there: keep marching
    behind = length(q - gCamPos) - length(WorldFromPixelDepth(px, d) - gCamPos);
    return true;
}

// Whether the view toward q (a point just above `face`, on an object's side) passes over that
// object's top: the frame there shows a surface lying beyond q, close by and no higher than q.
// Then the object's top edge lies between `face` and q.
bool OverTopAt(float3 q, float3 face) {
    float4 clip = mul(gViewProj, float4(q, 1.0));
    if (clip.w <= 1e-3)
        return false;
    float2 ndc = clip.xy / clip.w;
    if (any(abs(ndc) > 1.0))
        return false;
    float2 px = float2(ndc.x * 0.5 + 0.5, 0.5 - ndc.y * 0.5) * screenInfo.xy - 0.5;
    float d = ReadDepthPx(px);
    if (IsSky(d))
        return false;
    float3 w = WorldFromPixelDepth(px, d);
    float beyond = length(w - gCamPos) - length(q - gCamPos);
    return beyond > 1.0 && w.z > face.z - 0.5 && w.z < q.z + 2.0 &&
           length(w.xy - face.xy) < 24.0;
}

// What a puddle at p mirrors along r: rgb, and a = how much of it came from the frame
// itself (0 = the sky only). With reflections allowed, the ray marches through the frame
// (12 growing steps to about 600 u, then 4 halvings); anything it misses, leaves the
// screen for, or finds only faintly falls back smoothly to the sky.
float4 PuddleReflection(float3 p, float3 r, float footprint) {
    float3 sky = SkyReflection(r);
    if (denseColor.w < 0.5) return float4(sky, 0.0);
    // A ripple-tilted ray at a low view can point under the ground and "hit" it at once,
    // darkening half of each ring into a crescent.
    r.z = max(r.z, 0.03);
    r = normalize(r);
    // A start fixed per world point (deterministic) keeps hit/miss boundaries from lining
    // up into contours that follow whatever is reflected.
    float prevT = 0.0, t = 4.0 + 6.0 * Hash3(float3(p.xy, 3.0)), dt = 10.0;
    [loop] for (int i = 0; i < 12; ++i) {
        float behind;
        float2 uv;
        if (!ReflectProbe(p + r * t, behind, uv)) break;
        float thickness = max(24.0, dt * 1.5);
        if (behind > 0.0 && behind < thickness) {
            float depthIn = behind;
            float a = prevT, b = t;
            [unroll] for (int k = 0; k < 4; ++k) {
                float m = 0.5 * (a + b);
                float bm;
                float2 um;
                if (ReflectProbe(p + r * m, bm, um) && bm > 0.0) {
                    b = m;
                    uv = um;
                } else {
                    a = m;
                }
            }
            float2 border = min(uv, 1.0 - uv);
            float conf = smoothstep(0.0, 0.06, min(border.x, border.y)) *
                         (1.0 - smoothstep(380.0, 600.0, b)) *
                         (1.0 - smoothstep(0.5, 1.0, depthIn / thickness));
            float3 hit = sceneColor.SampleLevel(linearClamp, uv, 0).rgb;
            return float4(lerp(sky, hit, conf), conf);
        }
        prevT = t;
        t += dt;
        dt *= 1.25;
    }
    return float4(sky, 0.0);
}

// How much of a point is standing water: flat, open ground fills first in broad hollows,
// creases and the gutters below the street deck, and the pools spread as the rain goes on.
float PuddleAmount(float3 p, float3 n, float coat, float open, float cavity) {
    // Ground normals come from the map's own slope on known floors, so this thins water
    // smoothly on ramps instead of cutting it at a fixed angle.
    float flat = smoothstep(0.9, 0.985, n.z);
    if (flat * open <= 0.001) return 0.0;
    // No gutter water: the map's 22 u cells cannot place a curb, and water guessed from
    // them drew arcs across the road that followed no curb.
    // Broad hollows with a broken outline (a lone noise blob is round, and a curb clips it
    // into a half circle), deepening gradually from their edge.
    float hollow = Fbm3(float3(p.xy * 0.0035, 7.3)) +
                   (Fbm3(float3(p.xy * 0.021, 2.9)) - 0.5) * 0.12;
    // About an eighth of open flat ground at the storm's default cover, half at full.
    float threshold = 0.95 - 0.45 * coat;
    float depth = saturate((hollow - threshold) / 0.22);
    return smoothstep(0.0, 0.3, depth) * flat * open;
}

// Raindrop rings on standing water: the surface slope (xy) from a few hashed drops per
// 14 u cell, each ring spreading and fading over its own cycle. Stateless in time.
float2 RippleSlope(float2 xy, float time, float rate) {
    float2 slope = float2(0.0, 0.0);
    [unroll] for (int layer = 0; layer < 3; ++layer) {
        float2 shift = float2(0.37, 0.71) * layer;
        float2 q = xy / 14.0 + shift;
        float2 cell = floor(q);
        float h = Hash3(float3(cell, layer * 13.0));
        float h2 = Hash3(float3(cell, layer * 13.0 + 5.0));
        float2 center = (cell + 0.5 + (float2(h, h2) - 0.5) * 0.3 - shift) * 14.0;
        float phase = frac(time * rate + h * 7.0 + h2 * 3.0);
        float2 d = xy - center;
        float dist = length(d);
        float x = (dist - phase * 6.0) / 0.9;
        float fade = (1.0 - phase) * (1.0 - phase);
        slope += d / max(dist, 1e-3) * (-2.0 * x * exp(-x * x)) * fade;
    }
    return slope;
}

// Rain: open ground darkens as it soaks and takes a thin sheen; standing water pools on
// flat ground and mirrors the sky (and, where allowed, the frame), stirred by raindrops.
float3 ApplyWetness(float3 baseColor, float3 surfPos, float3 surfNormal, float3 viewDir,
                    float coat, float open, float cavity, float footprint) {
    float upMask = smoothstep(0.28, 0.78, surfNormal.z);
    float wet = saturate(coat * 3.0) * open * lerp(0.3, 1.0, upMask);
    if (wet <= 0.001) return baseColor;
    float3 color = baseColor * lerp(1.0, 0.66, wet);

    // Thin water film: a rough, faint mirror on every wet up-facing surface.
    float nv = saturate(dot(surfNormal, viewDir));
    float film = 0.02 + 0.98 * pow(1.0 - nv, 5.0);
    color += SkyReflection(reflect(-viewDir, surfNormal)) * film * wet * upMask * 0.3;

    float puddle = PuddleAmount(surfPos, surfNormal, coat, open, cavity);
    if (puddle <= 0.001) return color;
    // Drops only where a pixel can resolve the rings; they stir harder in heavier rain.
    float stir = (1.0 - saturate(footprint / 1.2)) * saturate(styleA.y) * open;
    float2 slope = stir > 0.001 ? RippleSlope(surfPos.xy, windTime.z, 0.6 + 0.6 * styleA.y) * stir
                                : float2(0.0, 0.0);
    float3 pn = normalize(float3(-slope * 0.35, 1.0));
    float pnv = saturate(dot(pn, viewDir));
    float fresnel = 0.02 + 0.98 * pow(1.0 - pnv, 5.0);
    float4 mirror = PuddleReflection(surfPos, reflect(-viewDir, pn), footprint);
    // The light this pixel already has dims the sun's glint in shadow.
    float luma = dot(baseColor, float3(0.2126, 0.7152, 0.0722));
    float lit = lerp(0.35, 1.0, saturate(luma / 0.25));
    // Shallow at the pool's edge, so it never reads as a dark stain with a hard outline.
    float3 water = baseColor * lerp(0.62, 0.5, puddle);
    float3 pool = lerp(water, mirror.rgb * lerp(lit, 1.0, mirror.a), fresnel);
    return lerp(color, pool, puddle);
}

)HLSL"
                                  R"HLSL(
// Snow, sand and ash cover. Where it settles comes from the per-shot drift potential, so
// it repeats exactly at every replay time; every visible edge comes from this pixel's
// depth and world-fixed noise. Detail finer than a pixel fades instead of crawling.
float3 ApplySurfaceCoating(float3 baseColor, float3 surfPos, float3 surfNormal, float3 viewDir,
                           float cavity, float hitDist, float2 px, float slopeTaper,
                           float onFloor) {
    // Coating settles over time (coatingInfo.x grows 0..1 from the shot start).
    float coat = styleB.y * coatingInfo.x;
    if (sunDir.w < 0.5 || coat <= 0.001) return baseColor;
    int preset = int(styleA.x + 0.5);
    // Rain keeps heroes dry outright; snow and sand bury their feet instead (below).
    float heroMask = (preset == 5) ? HeroMask(surfPos) : 0.0;
    if (heroMask >= 0.999) return baseColor;

    float2 uv = MapUV(surfPos.xy);
    float open = SkyOpenAt(uv, surfPos.z);
    // World size of one pixel at this distance (about a 90 degree view).
    float footprint = hitDist * 2.0 * screenInfo.w;
    if (preset == 5)
        return lerp(ApplyWetness(baseColor, surfPos, surfNormal, viewDir, coat, open, cavity,
                                 footprint),
                    baseColor, heroMask);

    float upMask = smoothstep(0.28, 0.78, surfNormal.z);

    float amount = coat * lerp(1.0, DriftAt(uv), coatingInfo.y);
    // Cover runs thicker along wall feet and thickest in inside corners.
    float bankMax = BankHeight(preset);
    // One enclosure read per pixel, shared with the wall bank below.
    float2 away = float2(0.0, 0.0);
    float enclosedHere = 0.0;
    if (bankMax > 0.0) {
        enclosedHere = EnclosureDir(surfPos.xy, away);
        amount *= 1.0 + 0.35 * smoothstep(0.15, 0.4, enclosedHere) +
                  0.6 * smoothstep(0.38, 0.62, enclosedHere);
    }

    // Top cover. Nothing here reads depth differences (creases): the game's depth bands
    // with distance, and anything built on it drew rings around the camera.
    // Light cover is an even dusting the ground shows through; it closes into a full sheet
    // as cover rises. Only gentle, soft variation: bare holes read as dark stains in haze.
    // (Noise is evaluated only where it can change the result; skipping it elsewhere is
    // exact.)
    float top = 0.0;
    if (upMask * open > 0.0) {
        float noise = Fbm3(surfPos * 0.0085);
        top = saturate(amount * 1.45) * lerp(0.82, 1.12, noise) * upMask * open;
    }
    // Fine grain in the dusting, only where a pixel can resolve it. At top >= 1 it cannot
    // matter (the saturate below hides it) except for sand, whose streaks can pull it back.
    float fine = 1.0 - saturate(footprint / 4.0);
    bool sandy = (preset == 1 || preset == 2);
    if (fine > 0.0 && top > 0.0 && (sandy || top < 1.0))
        top *= 1.0 + (Fbm3(float3(surfPos.xy * 0.07, 3.1)) - 0.5) * 0.5 * fine * (1.0 - top);
    // Blown sand thins in broad, faint streaks along the wind.
    if (preset == 1 || preset == 2)
        top *= 1.0 - 0.15 * smoothstep(0.35, 0.7, ValueNoise3(float3(surfPos.xy * 0.0045, 11.0)));
    top = saturate(top);

    // Wall foot: a steep surface low above its floor is buried by a bank whose height
    // follows the drift just out from the wall, where the sky is open. The floor height
    // is blended between cells and the bank's top line varies only along the wall, so the
    // bank stays one smooth line.
    float wall = 0.0;
    float aboveFloor = 1.0e4; // a steep pixel's height above the map floor (bank block)
    float steep = 1.0 - smoothstep(0.2, 0.5, surfNormal.z);
    if (bankMax > 0.0 && steep > 0.001) {
        // Out from the wall: the face's normal blended continuously with the map's own
        // direction away from walls (which turns smoothly round a pillar's corners). A hard
        // choice between the two made the reach jump about half a cell.
        float2 nxy = surfNormal.xy / max(length(surfNormal.xy), 1e-3);
        float2 outDir = normalize(nxy + away * 0.5 + 1e-5);
        float2 outXY = surfPos.xy + outDir * 28.0;
        float2 outUV = MapUV(outXY);
        // Floor first; the sky is then tested at the bank's foot, so nothing changes up the
        // face, and over two reaches, so one tap always clears the roof map's wall fringe.
        float floorZ = SampleFloorBelowSmooth(float3(outXY, surfPos.z));
        float outOpen = max(SkyOpenAt(outUV, floorZ + 8.0),
                            SkyOpenAt(MapUV(surfPos.xy + outDir * 52.0), floorZ + 8.0));
        // Steps and curbs are finer than the map, which blends a staircase into a ramp. Read
        // just out from the face, that ramp sits about halfway up each riser, so sand fills
        // every step's inner corner the same way instead of baring some risers and burying
        // others (measured only 28 u out, the floor lay steps below and banks ended abruptly).
        float floorNear = SampleFloorBelowSmooth(float3(surfPos.xy + outDir * 6.0, surfPos.z));
        float above = surfPos.z - max(floorZ, min(floorNear, floorZ + 16.0));
        aboveFloor = above;
        float bankDrift = lerp(1.0, DriftAt(outUV), coatingInfo.y);
        // About half enclosed at a straight wall's face, three quarters in an inside corner
        // (drifts pile up to about twice as high there), less round an outside corner (the
        // wind scours it). Gentle ramps only: a tight curve here turned the map's cell
        // creases into teeth.
        float shape = 0.7 + 0.3 * saturate((enclosedHere - 0.2) / 0.3) +
                      0.9 * saturate((enclosedHere - 0.5) / 0.3);
        float bank = bankMax * saturate(coat * bankDrift * 1.4 - 0.1) * min(bankDrift, 1.3) *
                     shape;
        float bankLine = bank * (0.8 + 0.4 * ValueNoise3(float3(surfPos.xy * 0.012, 5.0)));
        // The bank rises from the top of the blanket lying at the wall's foot, and the face
        // stays solid up to a little above that top, so the blanket and the bank meet
        // without a see-through band.
        float footDepth = BlanketDepth(preset, coat * bankDrift) * outOpen;
        float aboveTop = above - footDepth;
        // A drift meets a wall as a curved fillet: solid at the foot, thinning over most
        // of its height, so its top is a soft rise rather than a painted line.
        float edge = 2.0 + footprint * 1.5;
        float h = saturate(aboveTop / max(bankLine + edge, 1.0));
        wall = steep * outOpen * smoothstep(-10.0, -4.0, above) *
               (1.0 - h * h * (3.0 - 2.0 * h)) * saturate(bank / 2.0);
        wall = max(wall, steep * outOpen * smoothstep(-10.0, -4.0, above) *
                             (1.0 - smoothstep(footDepth + 1.0, footDepth + 3.0 + edge, above)) *
                             smoothstep(0.5, 2.0, footDepth));
    }

    // Lips: the cover on an object's top hangs a little over its edges, unevenly along them
    // (a few units of snow, less sand). Near the camera only: from just above this side
    // pixel, find how far below the top edge it lies (the view passes over the top there),
    // and cover the band under the edge.
    float lipMax = (preset == 6) ? 3.0 : (preset == 1) ? 1.25 : (preset == 2 || preset == 4) ? 0.9 : 0.0;
    float lipFade = 1.0 - smoothstep(1200.0, 1600.0, hitDist);
    if (lipMax > 0.0 && steep > 0.001 && lipFade > 0.0) {
        float lipLen = lipMax * saturate(coat * 1.6) *
                       (0.55 + 0.9 * ValueNoise3(float3(surfPos.xy * 0.12, 7.0)));
        float3 lipTop = surfPos + float3(0.0, 0.0, lipLen);
        if (lipLen > 0.25 && OverTopAt(lipTop, surfPos)) {
            // The edge's height above this pixel, to a sixteenth of the lip.
            float lo = 0.0, hi = lipLen;
            [unroll] for (int lb = 0; lb < 4; ++lb) {
                float mid = 0.5 * (lo + hi);
                if (OverTopAt(surfPos + float3(0.0, 0.0, mid), surfPos))
                    hi = mid;
                else
                    lo = mid;
            }
            float below = 0.5 * (lo + hi);
            // Thinning over most of its length, so its lower edge is a soft fringe rather
            // than a line that steps with the depth image's pixels.
            // Not on curbs and steps (the banks cover those faces): on such low faces the
            // edge search lands on the depth image's coarse pixels and draws teeth.
            float lip = (1.0 - smoothstep(lipLen * 0.1, lipLen, below)) * steep * lipFade *
                        smoothstep(10.0, 16.0, aboveFloor) *
                        SkyOpenAt(MapUV(surfPos.xy), surfPos.z + lipLen + 8.0);
            wall = max(wall, lip);
        }
    }

    // Where heroes walked, the cover is pressed thin and darker until it fills back in.
)HLSL"
                                  R"HLSL(
    // Near the camera the blanket has real thickness along tracks: trace the view ray back
    // from the ground point through the last few units to where it meets the blanket's top,
    // and read the tracks there (so they cut into the snow as the view moves), then look
    // toward the sun from that point for a rim standing in the way. Faded with distance and
    // at grazing views; nothing to do away from tracks (the top is then flat and parallel).
    float3 trailPos = surfPos;
    float rimOcc = 0.0;
    {
        // Faded out by a 15 degree view (from 30): nearly edge-on, any trace steps show.
        float fade = (1.0 - smoothstep(1200.0, 1600.0, hitDist)) * smoothstep(0.26, 0.5, viewDir.z);
        // Thinned at curbs and steps (slopeTaper): the shift would otherwise jump where a
        // track crosses a curb's face, which is not traced.
        float depth = BlanketDepth(preset, amount) * upMask * open * fade * slopeTaper;
        // Only the hero's own body skips the trace (its boots are handled by the footprint
        // below). The ground around the feet is traced like any other: skipping the whole
        // footprint drew a round seam around every hero.
        float soles;
        float onHero = HeroFootprint(surfPos, soles) * smoothstep(1.0, 3.0, surfPos.z - soles);
        if (depth > 0.5 && trailInfo.x > 0.5 && onHero <= 0.0) {
            // Height gained over the ground per unit walked back toward the camera, and the
            // reach: the blanket's full height, at most 32 u across the ground.
            float rise = dot(viewDir, surfNormal) / max(surfNormal.z, 0.3);
            float across = max(length(viewDir.xy), 1e-3);
            float sMax = min(depth * 1.35 / max(rise, 0.05), 32.0 / across);
            float3 far3 = TrailAt(surfPos + viewDir * sMax);
            float3 here3 = TrailAt(surfPos);
            if (max(max(far3.x, far3.z), max(here3.x, here3.z)) > 0.001) {
                float sPrev = sMax;
                float ePrev = sMax * rise - BlanketTopAt(surfPos.xy + viewDir.xy * sMax,
                                                         surfPos.z, depth);
                float sHit = 0.0;
                [loop] for (int st = 1; st <= 8; ++st) {
                    float sNow = sMax * (1.0 - st / 8.0);
                    float eNow = sNow * rise - BlanketTopAt(surfPos.xy + viewDir.xy * sNow,
                                                            surfPos.z, depth);
                    if (eNow <= 0.0) {
                        // Halve the crossing step four times, then one secant step: a single
                        // secant step left the 8 steps visible as terraces along rim outlines
                        // seen nearly edge-on.
                        float sIn = sNow, sOut = sPrev, eIn = eNow, eOut = ePrev;
                        [unroll] for (int hb = 0; hb < 4; ++hb) {
                            float sMid = 0.5 * (sIn + sOut);
                            float eMid = sMid * rise - BlanketTopAt(surfPos.xy + viewDir.xy * sMid,
                                                                    surfPos.z, depth);
                            if (eMid <= 0.0) {
                                sIn = sMid;
                                eIn = eMid;
                            } else {
                                sOut = sMid;
                                eOut = eMid;
                            }
                        }
                        sHit = lerp(sIn, sOut, saturate(eIn / min(eIn - eOut, -1e-4)));
                        break;
                    }
                    sPrev = sNow;
                    ePrev = eNow;
                }
                trailPos = float3(surfPos.xy + viewDir.xy * sHit, surfPos.z);
                // Rim shadow: the horizon toward the sun over the next 16 u.
                float3 sun = normalize(sunDir.xyz);
                float sunAcross = length(sun.xy);
                if (sun.z > 0.02 && sunAcross > 1e-3) {
                    float2 toSun = sun.xy / sunAcross;
                    float slope = sun.z / sunAcross;
                    float hHit = sHit * rise;
                    float steps[4] = {2.0, 4.5, 8.5, 14.0};
                    [unroll] for (int k2 = 0; k2 < 4; ++k2) {
                        float hq = BlanketTopAt(trailPos.xy + toSun * steps[k2], surfPos.z, depth);
                        rimOcc = max(rimOcc, hq - (hHit + steps[k2] * slope));
                    }
                }
            }
        }
    }
    float3 trailHere = TrailAt(trailPos);
    float3 trail = trailHere * upMask;
    float tread = trail.x;
    // Churned snow is broken into lumps with the ground showing between them.
    float lumps = (trail.y > 0.0) ? Fbm3(float3(surfPos.xy * 0.09, 21.0)) : 0.5;
    float churn = trail.y;
    float churnGaps = 1.0 - 0.35 * churn * smoothstep(0.35, 0.65, 1.0 - lumps);

    // The blanket lying on this ground (thinned toward curbs and stairs, none off the map's
    // floors) and how opaque it is: thick cover hides the ground entirely.
    // Opacity ignores the taper and the on-floor test: the game's ground and the map's
    // 22 u floors disagree by a few units, and opacity built on that drew scalloped
    // see-through contours across open ground. Those two only shape the thickness.
    float depthHere = BlanketDepth(preset, amount) * upMask * open * (1.0 - 0.6 * tread);
    float solid = smoothstep(0.5, 2.0, depthHere) * churnGaps;

    // Heroes stand in it: inside a hero's footprint, whatever lies below the blanket's top
    // at the hero's soles is snow (the boots sink in); above it the hero stays clean. A hero
    // well above the floor (jumping) has none on the boots.
    float soleZ;
    float inHero = HeroFootprint(surfPos, soleZ);
    float buried = 0.0;
    if (inHero > 0.001) {
        float3 soles = float3(surfPos.xy, soleZ);
        float grounded = 1.0 - smoothstep(6.0, 20.0, soleZ - SampleFloorBelowSmooth(soles));
        float feetDepth = BlanketDepth(preset, amount) * SkyOpenAt(uv, soleZ) * grounded *
                          (1.0 - 0.6 * trailHere.x);
        float a = surfPos.z - soleZ;
        float w = max(1.0, 1.5 * footprint);
        buried = (1.0 - smoothstep(feetDepth - w, feetDepth + w, a)) *
                 smoothstep(0.5, 2.0, feetDepth);
    }
    // Inside a footprint: the buried part of the hero, plus ordinary cover on the ground at
    // the soles; never cover on the hero's body or a bank up its legs.
    float groundAtSoles = 1.0 - smoothstep(1.5, 4.0, surfPos.z - soleZ);
    float covOut = max(top, wall);
    float covIn = max(buried, top * groundAtSoles);
    float cov = lerp(covOut, covIn, inHero) * (1.0 - 0.25 * tread) * churnGaps;
    solid = max(lerp(solid, buried, inHero), 0.0);
    if (max(cov, solid) <= 0.001) return baseColor;

    if (preset == 3) // damp ground fog
        return lerp(baseColor, baseColor * 0.85, cov * 0.82);

    float3 coatAlbedo = thinColor.rgb;
    if (preset == 1 || preset == 2) coatAlbedo = float3(0.76, 0.62, 0.42); // desert sand/dust
    else if (preset == 6) coatAlbedo = float3(0.92, 0.94, 0.97);           // snow
    else if (preset == 4) coatAlbedo = float3(0.28, 0.26, 0.25);           // ash

    // The game's light on this surface, read from its luma against an average ground
    // albedo, so shadows, lamps and dusk still read through the cover; a little of the
    // light's colour carries over too.
    // lumaAround averages a few pixels, so the ground's own fine texture and painted
    // markings do not print through the cover; broad shadows and lamps still do.
    // The light on the ground, read from the frame over a 16 px neighbourhood: wide enough
    // that paving, grout and painted lines do not print through the cover. Read only here,
    // after every early return, so uncoated pixels never pay for it.
    float3 colorAround = float3(0.0, 0.0, 0.0);
    [unroll] for (int k = 0; k < 12; ++k) {
        float ang = k * 0.5235988 + (k & 1) * 0.26;
        float rad = (k & 1) ? 16.0 : 8.0;
        float2 auv = (px + 0.5 + float2(cos(ang), sin(ang)) * rad) * screenInfo.zw;
        colorAround += sceneColor.SampleLevel(linearClamp, auv, 0).rgb;
    }
    colorAround /= 12.0;
    float lumaAround = dot(colorAround, float3(0.2126, 0.7152, 0.0722));
    float light = lerp(0.3, 1.08, saturate(pow(saturate(lumaAround / 0.32), 0.65)));
    float3 chroma = clamp(colorAround / max(lumaAround, 1e-3), 0.0, 2.0);
    float3 lightTint = lerp(float3(1.0, 1.0, 1.0), chroma, 0.3);

    float2 wdir = coatingInfo.z > 1.0 ? windTime.xy / coatingInfo.z : float2(1.0, 0.0);
    float2 perp = float2(-wdir.y, wdir.x);
    float shadeMul = 1.0;
    float3 add = float3(0.0, 0.0, 0.0);
    if (preset == 6) {
        // Snow: soft undulation and a cool shade in low light. (No sparkle specks: they read
        // as white dots stuck to the ground.)
        shadeMul = 0.95 + 0.1 * ValueNoise3(float3(surfPos.xy * 0.02, 1.0));
        lightTint *= lerp(float3(0.86, 0.92, 1.06), float3(1.0, 1.0, 1.0), saturate(light));
    } else if (preset == 1 || preset == 2) {
        // Sand: low ripples across the wind, lit on their sun-facing side, uneven in
        // strength and gone before they would shimmer.
        // Two crossing, skewed trains at short wavelengths: one even cosine read as
        // corrugated steps up close.
        float warp = Fbm3(float3(surfPos.xy * 0.008, 2.0)) * 9.0;
        float p1 = dot(surfPos.xy, wdir) / 5.5 + warp;
        float p2 = dot(surfPos.xy, normalize(wdir + perp * 0.35)) / 7.3 + warp * 0.6;
        float wave = 0.6 * sin(6.2831853 * p1 + 0.5 * sin(6.2831853 * p1)) +
                     0.4 * sin(6.2831853 * p2);
        float sunAlong = dot(normalize(sunDir.xy + 1e-4), wdir);
        float ripple = (1.0 - saturate((footprint - 0.15) / 0.45)) *
                       smoothstep(0.3, 0.65, Fbm3(float3(surfPos.xy * 0.006, 9.0)));
        shadeMul = 1.0 + 0.04 * wave * sunAlong * ripple * upMask +
                   0.1 * (Fbm3(float3(surfPos.xy * 0.015, 4.0)) - 0.5);
    } else if (preset == 4) {
        // Ash: soft dark flecks over a blotchy layer.
        float fleck = RoundSpeck(surfPos, 5.0, 0.25, footprint, 29.0);
        shadeMul = lerp(1.0, 0.6, fleck) * (0.9 + 0.2 * Fbm3(float3(surfPos.xy * 0.02, 6.0)));
    }

    // Spindrift: where the wind runs fast, soft bands of snow or sand skim over the cover.
    if ((preset == 6 || preset == 1 || preset == 2) && upMask > 0.0 && OnMap(uv)) {
        float speed = length(fluidState.SampleLevel(linearClamp, uv, 0).xy);
        float gust = saturate((speed - 120.0) / 380.0);
        if (gust > 0.01) {
            // The streaks travel with the steady wind; the local speed only sets their
            // strength. Advecting by the local speed (time x speed is ~1e5 u minutes into a
            // replay) squeezed the noise into hair-thin lines along the speed contours.
            float travel = windTime.z * coatingInfo.z * 0.9 * 0.006; // ~4e3 at 30 min: fine
            float3 q = float3(dot(surfPos.xy, wdir) * 0.006 - travel,
                              dot(surfPos.xy, perp) * 0.02, windTime.z * 0.4);
            float streak = smoothstep(0.5, 0.8, Fbm3(q));
            add += coatAlbedo * light * streak * gust * 0.22 * upMask;
        }
    }

)HLSL"
                                  R"HLSL(
    // Tracks have height: pressed down in the middle, a low rim at the shoulders. Its slope
    // lights the rims facing the sun and darkens the hollows' far sides, as much as the
    // game's own light here says the spot is sunlit (never inventing sun in a shadow).
    float blanket = BlanketDepth(preset, amount) * upMask * open * slopeTaper;
    if (blanket > 0.3 && (trail.x > 0.001 || trail.z > 0.001)) {
        // Slopes are measured over at least what a pixel covers on the ground, which
        // stretches at grazing views; a narrower step drew thin flickering rim lines.
        float e = max(1.5, footprint / max(viewDir.z, 0.15));
        float3 hxp = TrailAt(trailPos + float3(e, 0.0, 0.0));
        float3 hxm = TrailAt(trailPos - float3(e, 0.0, 0.0));
        float3 hyp = TrailAt(trailPos + float3(0.0, e, 0.0));
        float3 hym = TrailAt(trailPos - float3(0.0, e, 0.0));
        // Height relative to untouched cover, in world units.
        float hx = ((-0.6 * hxp.x + 0.25 * hxp.z) - (-0.6 * hxm.x + 0.25 * hxm.z)) * blanket;
        float hy = ((-0.6 * hyp.x + 0.25 * hyp.z) - (-0.6 * hym.x + 0.25 * hym.z)) * blanket;
        float3 n = normalize(surfNormal + float3(-hx, -hy, 0.0) / (2.0 * e));
        float3 sun = normalize(sunDir.xyz);
        float flatLit = max(dot(surfNormal, sun), 0.15);
        float sunFrac = smoothstep(0.45, 0.9, light);
        float relief = clamp(saturate(dot(n, sun)) / flatLit, 0.35, 1.6);
        float grazeEase = smoothstep(0.08, 0.3, viewDir.z);
        shadeMul *= lerp(1.0, relief, 0.8 * sunFrac * grazeEase);
        // A rim between this point and the sun shades it, a little cooler (sky-lit).
        float shadowed = smoothstep(0.0, 1.5, rimOcc) * sunFrac * grazeEase;
        shadeMul *= lerp(1.0, 0.62, shadowed);
        lightTint *= lerp(float3(1.0, 1.0, 1.0), float3(0.9, 0.95, 1.06), shadowed);
    }
    float3 coated = coatAlbedo * light * lightTint * shadeMul + add;
    // Packed snow and churned sand scatter less light than a fresh, loose surface.
    // A faint raised rim where the shoulder was pushed aside catches a little more light.
    float rim = saturate(tread * (1.0 - tread) * 4.0) * (1.0 - tread);
    coated *= lerp(1.0, (preset == 6) ? 0.9 : 0.92, tread) * (1.0 + 0.08 * rim) *
              (1.0 + churn * (lumps - 0.5) * 0.25);
    return lerp(baseColor, coated, saturate(max(cov * 0.9, solid)));
}

)HLSL"
                                  R"HLSL(
// Evaluates volumetric extinction and local scatter tint at world point q.
float SampleVolumeAt(float3 q, float floorZ, bool noisy, out float3 outTint, out float outLoad) {
    outTint = thinColor.rgb;
    outLoad = 0.0;

    float2 uv = MapUV(q.xy);
    if (!OnMap(uv)) return 0.0;

    float4 air = fluidState.SampleLevel(linearClamp, uv, 0);
    // Do not render fog/sand inside solid building interiors
    if (air.w > 0.65) return 0.0;

    int preset = int(styleA.x + 0.5);
    float intensity = styleA.y;
    float ceilH = max(styleA.z, 24.0);
    float densScale = styleA.w;
    float wisps = styleB.x;

    float h = q.z - floorZ;
    float sigma = 0.0;
    float3 tintAccum = float3(0.0, 0.0, 0.0);
    float tintWeight = 0.0;

    // 1) Global wind-fluid volume (Sandstorm / DustHaze / GroundFog / Ash / Rain / Snow)
    // Every profile below fades to zero by kVolumeTop * ceilH, so this cull never cuts visible density.
    if (h >= -18.0 && h <= ceilH * kVolumeTop) {
        float carried = max(air.z, 0.0);
        float open = SampleOpenness(uv);
        float roofAllow = lerp(0.18, 1.0, open);

        if (preset == 3) {
            // Ground Fog: dense floor-hugging undulating blanket parted by wakes/wind
            float billow = noisy ? SampleGustNoise3D(q, air.xy, 420.0, 2.8) : 0.5;
            float topH = ceilH * lerp(0.65, 1.25, lerp(0.5, billow, wisps));
            float hNorm = saturate(h / max(topH, 8.0));
            float profile = (1.0 - smoothstep(0.62, 1.0, hNorm)) * smoothstep(-16.0, 6.0, h);
            float baseFog = saturate(0.45 + carried * 0.75);
            float gSigma = baseFog * profile * intensity * densScale * 0.011 * lerp(0.45, 1.0, open);
            if (gSigma > 1e-5) {
                sigma += gSigma;
                float3 col = lerp(thinColor.rgb, denseColor.rgb, saturate(baseFog * (1.0 - hNorm * 0.4)));
                tintAccum += col * gSigma;
                tintWeight += gSigma;
                outLoad = max(outLoad, baseFog);
            }
        } else {
            // Gust-driven sandstorm / dust / ash / snow / rain spray
            float load = carried * 1.35;
            if (load > 0.005) {
                float d = min(load, 2.6);
                float n = noisy ? SampleGustNoise3D(q, air.xy, 520.0, 2.0) : 0.5;
                // Billow the top surface with the gust noise so it never reads as a flat slab.
                float hb = h - (n - 0.5) * ceilH * 0.55 * wisps;
                float lift = min(d, 1.5) * ceilH * 0.28;
                float core = 1.0 - smoothstep(0.0, ceilH * 0.85, max(hb - lift, 0.0));
                float back = pow(1.0 - saturate(hb / ceilH), 2.0) * min(d, 1.5);
                float topFade = 1.0 - smoothstep(ceilH * 0.95, ceilH * (kVolumeTop - 0.05), h);
                core *= topFade;
                float erode = (1.0 - n) * 1.45 * wisps;
                float shape = saturate(saturate(core - erode * core * 0.65) +
                                       saturate(0.32 - (1.0 - n) * 0.55 * wisps) * back);
                float baseExt = (preset == 1) ? 0.0085 :
                                (preset == 2) ? 0.0038 :
                                (preset == 4) ? 0.0068 :
                                (preset == 6) ? 0.0072 : 0.0032;
                float gSigma = shape * baseExt * intensity * densScale * roofAllow *
                               smoothstep(-18.0, 4.0, h);
                if (gSigma > 1e-5) {
                    sigma += gSigma;
                    float ramp = saturate(d * 0.55);
                    float3 col = lerp(thinColor.rgb, denseColor.rgb, ramp);
                    tintAccum += col * gSigma;
                    tintWeight += gSigma;
                    outLoad = max(outLoad, d);
                }
            }
        }
    }

    // 2) First-class World-Placed 3D Directional Emitter Plumes (EMITTERS = 16)
    if (sunColor.w > 0.5) {
        int eCount = min(int(styleB.w + 0.5), EMITTERS);
        [loop] for (int i = 0; i < eCount; ++i) {
            float aw = emitters[i].coneHeightActive.w;
            if (aw <= 0.001) continue;
            float hMin = emitters[i].coneHeightActive.y;
            float hMax = emitters[i].coneHeightActive.z;
            if (h < hMin - 24.0 || h > hMax + 48.0) continue;

            float3 rel = q - emitters[i].posRadius.xyz;
            float r = max(emitters[i].posRadius.w, 24.0);
            float dist = length(rel);
            if (dist > r * 3.2) continue;

            float3 eDir = emitters[i].dirSpeed.xyz;
            float dLen = length(eDir);
            eDir = dLen > 0.001 ? (eDir / dLen) : float3(1.0, 0.0, 0.0);

            float along = dot(rel, eDir);
            float coneCos = emitters[i].coneHeightActive.x;
            float coneMask = 1.0;
            if (coneCos > -0.95 && dist > 1.0) {
                float align = along / dist;
                coneMask = smoothstep(coneCos - 0.22, coneCos + 0.16, align);
            }
            float core = 1.0 - smoothstep(r * 0.18, r * 0.72, dist);
            float plumeReach = exp(-(dist * dist) / (r * r * 2.4));
            float vBand = smoothstep(hMin - 16.0, hMin + 16.0, h) *
                          (1.0 - smoothstep(hMax - 24.0, hMax + 24.0, h));

            float n3 = noisy ? SampleGustNoise3D(q - eDir * windTime.z * 90.0, air.xy, 310.0, 1.6) : 0.55;
            float plume = max(core, plumeReach * coneMask) * vBand * lerp(0.55, 1.35, n3);
            float eSigma = plume * emitters[i].colorDensity.w * aw * densScale * 0.014;
            if (eSigma > 1e-5) {
                sigma += eSigma;
                tintAccum += emitters[i].colorDensity.rgb * eSigma;
                tintWeight += eSigma;
                outLoad = max(outLoad, plume * 1.8);
            }
        }
    }

    if (tintWeight > 1e-6) {
        outTint = tintAccum / tintWeight;
    }
    return sigma;
}

static const uint kSunSlices = 32;

float SunSliceCoord(float h) {
    float u = saturate((h + 18.0) / (volumeBounds.z + 18.0));
    return (u * (kSunSlices - 1) + 0.5) / kSunSlices;
}

float SunOpticalDepth(float2 uv, float h) {
    if (!OnMap(uv) || h > volumeBounds.z) return 0.0;
    return sunBake.SampleLevel(linearClamp, float3(uv, SunSliceCoord(h)), 0);
}

// One thread per (map cell, height slice): the same three sun taps the march used, for a
// point at height h above a floor at Z = 0 (the taps only depend on height above that floor).
[numthreads(8, 8, 1)]
void BakeSunCS(uint3 id : SV_DispatchThreadID) {
    uint w, h, d;
    sunBakeOut.GetDimensions(w, h, d);
    if (id.x >= w || id.y >= h || id.z >= d) return;
    float2 uv = (float2(id.xy) + 0.5) / float2(w, h);
    float2 xy = gridWindow.xy + uv / gridWindow.zw;
    float height = lerp(-18.0, volumeBounds.z, float(id.z) / float(d - 1));
    float3 q = float3(xy, height);
    float3 sun = normalize(sunDir.xyz);
    float dsSun = 24.0 / max(sun.z, 0.35);
    float tau = 0.0;
    [unroll] for (int k = 0; k < 3; ++k) {
        float3 tint;
        float load;
        tau += SampleVolumeAt(q + sun * dsSun * (k + 0.5), 0.0, false, tint, load);
    }
    sunBakeOut[id] = tau;
}

)HLSL"
                                  R"HLSL(
// The volumetric march along one view ray: rgb in-scattered light, a transmittance.
float4 MarchRay(float2 px, float3 rayDir, float hitDist) {
    float maxDist = windTime.w;
    // Volumetric raymarch front-to-back
    int steps = clamp(int(styleB.z + 0.5), 16, 128);
    // Per-pixel white noise breaks the step bands into fine grain. Interleaved-gradient
    // noise lined up with the per-cell step schedule and drew contour lines over bright
    // ground cover.
    float jitter = Hash3(float3(px, 91.7));
    float3 sun = normalize(sunDir.xyz);
    float phase = 0.45 + 0.55 * saturate(PhaseHG(dot(rayDir, sun), 0.52) * 3.2);
    float reach = max(styleA.z, 32.0) * kVolumeTop;
    float climb = max(abs(rayDir.z), 0.02);

    float3 scatter = float3(0.0, 0.0, 0.0);
    float trans = 1.0;
    float t = 12.0;

    // Clip the ray to where weather can exist: the map footprint and the height band from
    // the lowest floor to the highest floor plus the volume reach. Density is zero outside,
    // so ending (or starting) the march there changes nothing that can be seen.
    float tEnd = hitDist;
    float2 boxLo = gridWindow.xy;
    float2 boxHi = gridWindow.xy + 1.0 / gridWindow.zw;
    [unroll] for (int a = 0; a < 2; ++a) {
        if (abs(rayDir[a]) > 1e-6) {
            float tLo = (boxLo[a] - gCamPos[a]) / rayDir[a];
            float tHi = (boxHi[a] - gCamPos[a]) / rayDir[a];
            t = max(t, min(tLo, tHi));
            tEnd = min(tEnd, max(tLo, tHi));
        } else if (gCamPos[a] < boxLo[a] || gCamPos[a] > boxHi[a]) {
            tEnd = -1.0;
        }
    }
    if (abs(rayDir.z) > 1e-6) {
        float tLo = (volumeBounds.x - gCamPos.z) / rayDir.z;
        float tHi = (volumeBounds.y - gCamPos.z) / rayDir.z;
        t = max(t, min(tLo, tHi));
        tEnd = min(tEnd, max(tLo, tHi));
    } else if (gCamPos.z < volumeBounds.x || gCamPos.z > volumeBounds.y) {
        tEnd = -1.0;
    }
    t = max(t, 12.0);

    // Step schedule: dt = 6 + g * t grows with distance (fine near the camera). g is solved
    // per ray so the steps exactly cover the clipped span; short rays get g = 0 and stop early
    // in fine steps. Without this, 64 steps ended near 950 u and distant haze cut off hard.
    float growth = 0.0;
    {
        float span = tEnd - t;
        float n = float(steps);
        if (span > 6.0 * n) {
            float lo = 0.0, hi = 0.35;
            [unroll] for (int it = 0; it < 12; ++it) {
                float g = 0.5 * (lo + hi);
                float covered = (t + 6.0 / g) * (pow(1.0 + g, n) - 1.0);
                if (covered < span) lo = g; else hi = g;
            }
            growth = hi;
        }
    }

    [loop] for (int s = 0; s < steps && t < tEnd; ++s) {
        float3 p = gCamPos + rayDir * t;
        bool hasFloor;
        float floorHere = SampleFloorBelow(p, hasFloor);
        float over = p.z - floorHere;
        float above = over - reach;

        float dt = clamp(6.0 + t * growth, 6.0, 400.0);
        if (over < reach * 2.0) {
            dt = min(dt, max((max(above, 0.0) + reach * 0.12) / climb, 4.0));
        }
        if (above > 0.0 && rayDir.z < -0.02) {
            dt = max(dt, min(above / max(-rayDir.z, 0.05) * 0.55, 200.0));
        }
        // Never fall short of the span: the floor refinement above may spend steps early.
        dt = max(dt, (tEnd - t) / float(steps - s));
        dt = min(dt, tEnd - t);
        if (dt <= 0.25) break;

        float3 q = gCamPos + rayDir * (t + dt * jitter);
        t += dt;

        float3 albedo;
        float load;
        float sigma = SampleVolumeAt(q, floorHere, true, albedo, load);
        if (sigma <= 1e-5) continue;

        // Soft distance fade over last 15% of maxDist
        float distFade = 1.0 - smoothstep(maxDist * 0.85, maxDist, t);
        sigma *= distFade;
        if (sigma <= 1e-5) continue;

        // Soft roof/building shadow from topsMap + 3-tap self-shadow toward sun
        float2 quv = MapUV(q.xy);
        float roofShadow = RoofShadowAt(quv, q.z);

        // The three sun taps depend only on map position and height above this floor, so
        // they come from the per-frame bake (BakeSunCS) in one fetch.
        float dsSun = 24.0 / max(sun.z, 0.35);
        float tauSun = SunOpticalDepth(quv, q.z - floorHere);
        float sunTrans = exp(-tauSun * dsSun * 0.65);

        float3 light = ambientColor.rgb * lerp(0.55, 1.0, roofShadow) +
                       sunColor.rgb * phase * sunTrans * roofShadow;
        float alpha = 1.0 - exp(-sigma * dt);
        scatter += trans * alpha * albedo * light;
        trans *= (1.0 - alpha);
        if (trans < 0.015) break;
    }

    return float4(scatter, trans);
}

// Safety net above 4K: rebuild this pixel's march from the four nearest half-resolution
// samples, weighted by distance and depth similarity. Returns false when a neighbour sits
// across a depth edge (or sky boundary); the caller then marches the pixel at full
// resolution, so silhouettes stay exact.
bool UpsampleMarch(float2 px, float depth, out float4 result) {
    result = float4(0.0, 0.0, 0.0, 1.0);
    uint hw, hh;
    halfMarch.GetDimensions(hw, hh);
    float2 h = px * 0.5;
    int2 h0 = int2(floor(h));
    float2 f = h - float2(h0);
    bool skyHere = IsSky(depth);
    float4 acc = float4(0.0, 0.0, 0.0, 0.0);
    float wsum = 0.0;
    [unroll] for (int k = 0; k < 4; ++k) {
        int2 o = int2(k & 1, k >> 1);
        int2 q = clamp(h0 + o, int2(0, 0), int2(hw, hh) - 1);
        float ds = ReadDepthPx(float2(q * 2));
        if (IsSky(ds) != skyHere) return false;
        float rel = skyHere ? 0.0 : abs(ds - depth) / max(depth, 1e-6);
        if (rel > netInfo.y) return false;
        float wb = (o.x ? f.x : 1.0 - f.x) * (o.y ? f.y : 1.0 - f.y) + 1e-4;
        float w = wb * exp(-rel * 60.0);
        acc += halfMarch.Load(int3(q, 0)) * w;
        wsum += w;
    }
    if (wsum <= 0.0) return false;
    result = acc / wsum;
    return true;
}

float4 EnvCompositePS(FullscreenVSOut i) : SV_Target {
    float2 px = floor(i.pos.xy);
    int2 ipx = clamp(int2(px), int2(0, 0), int2(screenInfo.xy) - 1);
    float3 baseColor = sceneColor.Load(int3(ipx, 0)).rgb;
    if (!LoadView())
        return float4(baseColor, 1.0);

    int preset = int(styleA.x + 0.5);
    if (preset == 0 || styleA.y <= 0.001) {
        return float4(baseColor, 1.0);
    }

    float depth = ReadDepthPx(float2(ipx));
    bool sky = IsSky(depth);

    float farZ = cameraPos.w > 0.5 ? 1e-5 : (1.0 - 1e-5);
    float3 farWorld = WorldFromPixelDepth(px, farZ);
    float3 rayDir = normalize(farWorld - gCamPos);

    float maxDist = windTime.w;
    float hitDist = maxDist;
    float3 surfPos = gCamPos + rayDir * maxDist;
    if (!sky) {
        surfPos = WorldFromPixelDepth(px, depth);
        hitDist = min(length(surfPos - gCamPos), maxDist);
        // The depth-normal reconstruction (8 depth loads) runs only where coating can show:
        // coating on and grown, and open sky above the surface.
        // Wall feet stand in their own wall's cell, so pixels near the street qualify too.
        float2 suv = MapUV(surfPos.xy);
        if (sunDir.w > 0.5 && styleB.y * coatingInfo.x > 0.001 &&
            (SkyOpenAt(suv, surfPos.z) > 0.001 ||
             (OnMap(suv) && surfPos.z - streetZMap.SampleLevel(linearClamp, suv, 0) < 64.0))) {
            float cavity;
            float3 surfNormal = CoatingBaseNormal(px, depth, surfPos, -rayDir, cavity);
            // On a floor the map knows, the ground takes the map's own smooth slope:
            // depth-built normals tilt on the rows where the game's depth steps (arcs round
            // the camera), and snapping to straight up against one height per cell switched
            // on and off in cell-sized bands across ramps.
            // Two-sided differences 11 u either way: a one-sided one shifted every slope and
            // taper half a cell toward +x and +y.
            float fz = surfPos.z, fxp = fz, fxm = fz, fyp = fz, fym = fz;
            [branch] if (surfNormal.z > 0.28) {
                fz = SampleFloorBelowSmooth(surfPos);
                fxp = fxm = fyp = fym = fz;
                // Off the map's floors the slope's lerp weight is exactly 0 (onFloor).
                [branch] if (abs(surfPos.z - fz) < 27.0) {
                    fxp = SampleFloorBelowSmooth(surfPos + float3(11.0, 0.0, 0.0));
                    fxm = SampleFloorBelowSmooth(surfPos - float3(11.0, 0.0, 0.0));
                    fyp = SampleFloorBelowSmooth(surfPos + float3(0.0, 11.0, 0.0));
                    fym = SampleFloorBelowSmooth(surfPos - float3(0.0, 11.0, 0.0));
                }
            }
            // A ramp's gentle slope is trusted; a ledge or curb drop within a cell is not a
            // slope, so there the ground stays level.
            float relief = abs(fxp - fxm) + abs(fyp - fym);
            float trust = 1.0 - smoothstep(8.0, 16.0, relief);
            float3 mapN = normalize(float3((fxm - fxp) * trust, (fym - fyp) * trust, 22.0));
            // The blanket thins where the floor changes fast (curbs, stairs, ledges), so their
            // edges stay readable as rounded, snowed-over shapes.
            float slopeTaper =
                1.0 - smoothstep(0.10, 0.40, length(float2(fxp - fxm, fyp - fym)) / 22.0);
            float onFloor = 1.0 - smoothstep(3.0 + relief * trust, 9.0 + 2.0 * relief * trust,
                                             abs(surfPos.z - fz));
            surfNormal = normalize(lerp(surfNormal, mapN,
                                        onFloor * smoothstep(0.3, 0.6, surfNormal.z)));
            baseColor = ApplySurfaceCoating(baseColor, surfPos, surfNormal, -rayDir, cavity,
                                            hitDist, px, slopeTaper, onFloor);
        }
    }

    float4 m;
    if (netInfo.x < 1.5 || !UpsampleMarch(px, depth, m))
        m = MarchRay(px, rayDir, hitDist);
    return float4(baseColor * m.a + m.rgb, 1.0);
}

// Half-resolution march (safety net above 4K): one sample per 2x2 block, at its top-left
// full-resolution pixel.
float4 EnvHalfPS(FullscreenVSOut i) : SV_Target {
    float2 px = floor(i.pos.xy) * 2.0;
    if (!LoadView())
        return float4(0.0, 0.0, 0.0, 1.0);
    int preset = int(styleA.x + 0.5);
    if (preset == 0 || styleA.y <= 0.001)
        return float4(0.0, 0.0, 0.0, 1.0);
    float depth = ReadDepthPx(px);
    float farZ = cameraPos.w > 0.5 ? 1e-5 : (1.0 - 1e-5);
    float3 rayDir = normalize(WorldFromPixelDepth(px, farZ) - gCamPos);
    float maxDist = windTime.w;
    float hitDist = IsSky(depth) ? maxDist
                                 : min(length(WorldFromPixelDepth(px, depth) - gCamPos), maxDist);
    return MarchRay(px, rayDir, hitDist);
}
)HLSL";

struct EmitterGPU {
    float pos_radius[4];
    float dir_speed[4];
    float color_density[4];
    float cone_height_active[4];
};

struct EnvConstants {
    float inv_view_proj[16];
    float view_proj[16];
    float camera_pos[4];
    float screen_info[4];
    float grid_window[4];
    float wind_time[4];
    float style_a[4];
    float style_b[4];
    float sun_dir[4];
    float sun_color[4];
    float ambient_color[4];
    float thin_color[4];
    float dense_color[4];
    float coating_info[4];
    float volume_bounds[4];
    float floor_decode[4];
    float net_info[4];
    float trail_info[4];
    EmitterGPU emitters[kMaxFluidEmitters];
    float heroes[EnvRenderParams::kMaxCoatingHeroes][4];
};
static_assert(sizeof(EnvConstants) % 16 == 0, "EnvConstants must be 16-byte aligned");

void preset_colors(AtmospherePreset preset, float thin[3], float dense[3]) noexcept {
    switch (preset) {
    case AtmospherePreset::Sandstorm:
        thin[0] = 0.78f;
        thin[1] = 0.62f;
        thin[2] = 0.40f;
        dense[0] = 0.94f;
        dense[1] = 0.79f;
        dense[2] = 0.54f;
        break;
    case AtmospherePreset::DustHaze:
        thin[0] = 0.70f;
        thin[1] = 0.62f;
        thin[2] = 0.48f;
        dense[0] = 0.84f;
        dense[1] = 0.75f;
        dense[2] = 0.60f;
        break;
    case AtmospherePreset::GroundFog:
        thin[0] = 0.74f;
        thin[1] = 0.80f;
        thin[2] = 0.86f;
        dense[0] = 0.90f;
        dense[1] = 0.93f;
        dense[2] = 0.96f;
        break;
    case AtmospherePreset::AshEmbers:
        thin[0] = 0.28f;
        thin[1] = 0.26f;
        thin[2] = 0.25f;
        dense[0] = 0.56f;
        dense[1] = 0.50f;
        dense[2] = 0.45f;
        break;
    case AtmospherePreset::Rain:
        thin[0] = 0.44f;
        thin[1] = 0.47f;
        thin[2] = 0.52f;
        dense[0] = 0.82f;
        dense[1] = 0.85f;
        dense[2] = 0.90f;
        break;
    case AtmospherePreset::Snow:
        thin[0] = 0.82f;
        thin[1] = 0.84f;
        thin[2] = 0.88f;
        dense[0] = 0.97f;
        dense[1] = 0.98f;
        dense[2] = 0.99f;
        break;
    default:
        thin[0] = thin[1] = thin[2] = 0.0f;
        dense[0] = dense[1] = dense[2] = 0.0f;
        break;
    }
}

} // namespace

struct EnvRendererDX11::Impl {
    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11VertexShader> vs;
    ComPtr<ID3D11PixelShader> ps;
    ComPtr<ID3D11ComputeShader> bake_cs;
    ComPtr<ID3D11PixelShader> half_ps;
    ComPtr<ID3D11Texture2D> half_tex;
    ComPtr<ID3D11RenderTargetView> half_rtv;
    ComPtr<ID3D11ShaderResourceView> half_srv;
    uint32_t half_w = 0, half_h = 0;
    ComPtr<ID3D11Texture3D> sun_bake;
    ComPtr<ID3D11ShaderResourceView> sun_bake_srv;
    ComPtr<ID3D11UnorderedAccessView> sun_bake_uav;
    ComPtr<ID3D11Buffer> constants;
    ComPtr<ID3D11SamplerState> linear_clamp;
    ComPtr<ID3D11SamplerState> linear_wrap;
    ComPtr<ID3D11RasterizerState> rs_state;
    ComPtr<ID3D11DepthStencilState> ds_state;
    ComPtr<ID3D11BlendState> blend_state;
    ComPtr<ID3D11Texture2D> drift_tex;
    ComPtr<ID3D11ShaderResourceView> drift_srv;
    ComPtr<ID3D11Texture2D> reach_tex;
    ComPtr<ID3D11ShaderResourceView> reach_srv;
    // Hero trails: the recorded segments and the per-frame trail map they are stamped into.
    ComPtr<ID3D11VertexShader> trail_vs;
    ComPtr<ID3D11PixelShader> trail_ps;
    ComPtr<ID3D11BlendState> trail_blend;
    ComPtr<ID3D11Texture2D> trail_tex;
    ComPtr<ID3D11RenderTargetView> trail_rtv;
    ComPtr<ID3D11ShaderResourceView> trail_srv;
    ComPtr<ID3D11Buffer> trail_buf;
    ComPtr<ID3D11ShaderResourceView> trail_buf_srv;
    uint32_t trail_count = 0;
    bool initialized = false;
};

namespace {
constexpr UINT kTrailTexels = 1024; // matches kTrailTexels in the shader

// The shaders are compiled once per process (several seconds at full optimisation) and
// kept; precompile() lets the runtime do that on a worker thread before weather is first
// drawn, so the game's render thread never waits for it.
enum EnvShader { kFullscreenVS, kCompositePS, kHalfPS, kBakeSunCS, kTrailVS, kTrailPS, kEnvShaders };
const char* const kEnvShaderEntry[kEnvShaders][2] = {
    {"FullscreenVS", "vs_5_0"}, {"EnvCompositePS", "ps_5_0"}, {"EnvHalfPS", "ps_5_0"},
    {"BakeSunCS", "cs_5_0"},    {"TrailStampVS", "vs_5_0"},   {"TrailStampPS", "ps_5_0"}};
struct EnvBlobs {
    ComPtr<ID3DBlob> code[kEnvShaders];
    std::string error;
};
std::mutex gEnvBlobsMutex;
std::shared_ptr<const EnvBlobs> gEnvBlobs;
std::atomic<bool> gEnvBlobsReady{false};

std::shared_ptr<const EnvBlobs> env_blobs() {
    std::lock_guard<std::mutex> lock(gEnvBlobsMutex);
    if (gEnvBlobs)
        return gEnvBlobs;
    auto blobs = std::make_shared<EnvBlobs>();
    const UINT flags = D3DCOMPILE_ENABLE_STRICTNESS | D3DCOMPILE_OPTIMIZATION_LEVEL3;
    for (int i = 0; i < kEnvShaders; ++i) {
        ComPtr<ID3DBlob> err;
        if (FAILED(D3DCompile(kEnvRenderHlsl, sizeof(kEnvRenderHlsl) - 1, "dolly_env_render.hlsl",
                              nullptr, nullptr, kEnvShaderEntry[i][0], kEnvShaderEntry[i][1], flags,
                              0, &blobs->code[i], &err)) ||
            !blobs->code[i]) {
            blobs->error = std::string(kEnvShaderEntry[i][0]) + " compile failed: " +
                           (err ? static_cast<const char*>(err->GetBufferPointer()) : "unknown");
            break;
        }
    }
    gEnvBlobs = blobs;
    gEnvBlobsReady.store(true);
    return blobs;
}
} // namespace

void EnvRendererDX11::precompile() noexcept {
    try {
        env_blobs();
    } catch (...) {
    }
}

bool EnvRendererDX11::precompiled() noexcept {
    return gEnvBlobsReady.load();
}

EnvRendererDX11::EnvRendererDX11() : impl_(std::make_unique<Impl>()) {}
EnvRendererDX11::~EnvRendererDX11() = default;

bool EnvRendererDX11::initialize(ID3D11Device* device, std::string* out_error) {
    shutdown();
    if (!device) {
        if (out_error)
            *out_error = "null D3D11 device";
        return false;
    }
    impl_->device = device;

    const auto blobs = env_blobs();
    if (!blobs->error.empty()) {
        if (out_error)
            *out_error = blobs->error;
        shutdown();
        return false;
    }
    ID3DBlob* vs_code = blobs->code[kFullscreenVS].Get();
    ID3DBlob* ps_code = blobs->code[kCompositePS].Get();
    ID3DBlob* half_code = blobs->code[kHalfPS].Get();
    ID3DBlob* cs_code = blobs->code[kBakeSunCS].Get();
    if (FAILED(device->CreatePixelShader(half_code->GetBufferPointer(), half_code->GetBufferSize(),
                                         nullptr, &impl_->half_ps))) {
        if (out_error)
            *out_error = "CreatePixelShader failed for EnvHalfPS";
        shutdown();
        return false;
    }
    if (FAILED(device->CreateComputeShader(cs_code->GetBufferPointer(), cs_code->GetBufferSize(),
                                           nullptr, &impl_->bake_cs))) {
        if (out_error)
            *out_error = "CreateComputeShader failed for BakeSunCS";
        shutdown();
        return false;
    }
    {
        // 256 x 256 map cells (the wind resolution) x 32 height slices of R16F.
        D3D11_TEXTURE3D_DESC td{};
        td.Width = 256;
        td.Height = 256;
        td.Depth = 32;
        td.MipLevels = 1;
        td.Format = DXGI_FORMAT_R16_FLOAT;
        td.Usage = D3D11_USAGE_DEFAULT;
        td.BindFlags = D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_UNORDERED_ACCESS;
        if (FAILED(device->CreateTexture3D(&td, nullptr, &impl_->sun_bake)) ||
            FAILED(device->CreateShaderResourceView(impl_->sun_bake.Get(), nullptr,
                                                    &impl_->sun_bake_srv)) ||
            FAILED(device->CreateUnorderedAccessView(impl_->sun_bake.Get(), nullptr,
                                                     &impl_->sun_bake_uav))) {
            if (out_error)
                *out_error = "failed to create the sun bake volume";
            shutdown();
            return false;
        }
    }

    if (FAILED(device->CreateVertexShader(vs_code->GetBufferPointer(), vs_code->GetBufferSize(),
                                          nullptr, &impl_->vs)) ||
        FAILED(device->CreatePixelShader(ps_code->GetBufferPointer(), ps_code->GetBufferSize(),
                                         nullptr, &impl_->ps))) {
        if (out_error)
            *out_error = "CreateVertexShader/CreatePixelShader failed";
        shutdown();
        return false;
    }

    D3D11_BUFFER_DESC cb{};
    cb.ByteWidth = sizeof(EnvConstants);
    cb.Usage = D3D11_USAGE_DEFAULT;
    cb.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
    if (FAILED(device->CreateBuffer(&cb, nullptr, &impl_->constants))) {
        if (out_error)
            *out_error = "CreateBuffer failed for EnvConstants";
        shutdown();
        return false;
    }

    D3D11_SAMPLER_DESC sd{};
    sd.Filter = D3D11_FILTER_MIN_MAG_MIP_LINEAR;
    sd.AddressU = sd.AddressV = sd.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
    sd.MaxLOD = D3D11_FLOAT32_MAX;
    if (FAILED(device->CreateSamplerState(&sd, &impl_->linear_clamp))) {
        shutdown();
        return false;
    }
    sd.AddressU = sd.AddressV = sd.AddressW = D3D11_TEXTURE_ADDRESS_WRAP;
    if (FAILED(device->CreateSamplerState(&sd, &impl_->linear_wrap))) {
        shutdown();
        return false;
    }

    D3D11_RASTERIZER_DESC rd{};
    rd.FillMode = D3D11_FILL_SOLID;
    rd.CullMode = D3D11_CULL_NONE;
    rd.DepthClipEnable = TRUE;
    if (FAILED(device->CreateRasterizerState(&rd, &impl_->rs_state))) {
        shutdown();
        return false;
    }

    D3D11_DEPTH_STENCIL_DESC dd{};
    dd.DepthEnable = FALSE;
    dd.DepthWriteMask = D3D11_DEPTH_WRITE_MASK_ZERO;
    if (FAILED(device->CreateDepthStencilState(&dd, &impl_->ds_state))) {
        shutdown();
        return false;
    }

    D3D11_BLEND_DESC bd{};
    bd.RenderTarget[0].BlendEnable = FALSE;
    bd.RenderTarget[0].RenderTargetWriteMask = D3D11_COLOR_WRITE_ENABLE_ALL;
    if (FAILED(device->CreateBlendState(&bd, &impl_->blend_state))) {
        shutdown();
        return false;
    }

    // Hero trails: stamp shaders, a MAX blend (order-independent, so deterministic), and
    // the trail map (latest trodden shot time per texel).
    {
        ID3DBlob* tvs = blobs->code[kTrailVS].Get();
        ID3DBlob* tps = blobs->code[kTrailPS].Get();
        if (FAILED(device->CreateVertexShader(tvs->GetBufferPointer(), tvs->GetBufferSize(),
                                              nullptr, &impl_->trail_vs)) ||
            FAILED(device->CreatePixelShader(tps->GetBufferPointer(), tps->GetBufferSize(),
                                             nullptr, &impl_->trail_ps))) {
            if (out_error)
                *out_error = "CreateVertexShader/CreatePixelShader failed for the trail stamp";
            shutdown();
            return false;
        }
        D3D11_BLEND_DESC tb{};
        auto& rt = tb.RenderTarget[0];
        rt.BlendEnable = TRUE;
        rt.SrcBlend = rt.DestBlend = D3D11_BLEND_ONE;
        rt.BlendOp = D3D11_BLEND_OP_MAX;
        rt.SrcBlendAlpha = rt.DestBlendAlpha = D3D11_BLEND_ONE;
        rt.BlendOpAlpha = D3D11_BLEND_OP_MAX;
        rt.RenderTargetWriteMask = D3D11_COLOR_WRITE_ENABLE_ALL;
        D3D11_TEXTURE2D_DESC td{};
        td.Width = td.Height = kTrailTexels;
        td.MipLevels = 1;
        td.ArraySize = 1;
        td.Format = DXGI_FORMAT_R32G32B32A32_FLOAT;
        td.SampleDesc.Count = 1;
        td.Usage = D3D11_USAGE_DEFAULT;
        td.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;
        if (FAILED(device->CreateBlendState(&tb, &impl_->trail_blend)) ||
            FAILED(device->CreateTexture2D(&td, nullptr, &impl_->trail_tex)) ||
            FAILED(device->CreateRenderTargetView(impl_->trail_tex.Get(), nullptr,
                                                  &impl_->trail_rtv)) ||
            FAILED(device->CreateShaderResourceView(impl_->trail_tex.Get(), nullptr,
                                                    &impl_->trail_srv))) {
            if (out_error)
                *out_error = "failed to create the hero trail map";
            shutdown();
            return false;
        }
    }

    impl_->initialized = true;
    return true;
}

void EnvRendererDX11::shutdown() {
    impl_ = std::make_unique<Impl>();
}

bool EnvRendererDX11::render(ID3D11DeviceContext* ctx, ID3D11ShaderResourceView* scene_color_srv,
                             ID3D11ShaderResourceView* scene_depth_srv,
                             ID3D11RenderTargetView* out_rtv, const EnvCamera& camera,
                             const EnvRenderParams& params, const WindFluidSolverDX11& fluid,
                             const DirectionalEmitter* emitters, uint32_t emitter_count) {
    if (!ctx || !impl_->initialized || !scene_color_srv || !scene_depth_srv || !out_rtv ||
        camera.width == 0 || camera.height == 0 || !fluid.has_grid() || !fluid.fluid_srv()) {
        return false;
    }

    const FluidGridInfo mg = fluid.map_grid_info();
    EnvConstants cb{};
    std::memcpy(cb.inv_view_proj, camera.inv_view_proj, sizeof(cb.inv_view_proj));
    std::memcpy(cb.view_proj, camera.view_proj, sizeof(cb.view_proj));
    cb.camera_pos[0] = camera.position[0];
    cb.camera_pos[1] = camera.position[1];
    cb.camera_pos[2] = camera.position[2];
    cb.camera_pos[3] = camera.reversed_z ? 1.0f : 0.0f;

    cb.screen_info[0] = static_cast<float>(camera.width);
    cb.screen_info[1] = static_cast<float>(camera.height);
    cb.screen_info[2] = 1.0f / static_cast<float>(camera.width);
    cb.screen_info[3] = 1.0f / static_cast<float>(camera.height);

    cb.grid_window[0] = mg.origin_x;
    cb.grid_window[1] = mg.origin_y;
    cb.grid_window[2] = 1.0f / (static_cast<float>(mg.width) * mg.cell_units);
    cb.grid_window[3] = 1.0f / (static_cast<float>(mg.height) * mg.cell_units);

    cb.wind_time[0] = params.wind_xy[0];
    cb.wind_time[1] = params.wind_xy[1];
    cb.wind_time[2] = params.sim_time;
    cb.wind_time[3] = std::max(params.max_distance, 256.0f);

    cb.style_a[0] = static_cast<float>(static_cast<uint32_t>(params.preset));
    cb.style_a[1] = std::max(0.0f, params.intensity);
    cb.style_a[2] = std::max(24.0f, params.height_ceiling);
    cb.style_a[3] = std::max(0.0f, params.density_scale);

    const uint32_t clamped_emitters = (params.enable_emitter_plumes && emitters)
                                          ? std::min(emitter_count, kMaxFluidEmitters)
                                          : 0u;

    cb.style_b[0] = std::clamp(params.wisp_strength, 0.0f, 2.0f);
    cb.style_b[1] = std::clamp(params.coating_strength, 0.0f, 1.0f);
    cb.style_b[2] = static_cast<float>(std::clamp(params.march_steps, 16, 128));
    cb.style_b[3] = static_cast<float>(clamped_emitters);

    cb.sun_dir[0] = params.sun_dir[0];
    cb.sun_dir[1] = params.sun_dir[1];
    cb.sun_dir[2] = params.sun_dir[2];
    cb.sun_dir[3] = params.enable_coating ? 1.0f : 0.0f;

    cb.sun_color[0] = params.sun_color[0];
    cb.sun_color[1] = params.sun_color[1];
    cb.sun_color[2] = params.sun_color[2];
    cb.sun_color[3] = params.enable_emitter_plumes ? 1.0f : 0.0f;

    cb.ambient_color[0] = params.ambient_color[0];
    cb.ambient_color[1] = params.ambient_color[1];
    cb.ambient_color[2] = params.ambient_color[2];
    cb.ambient_color[3] = camera.near_clip;

    float thin[3]{}, dense[3]{};
    preset_colors(params.preset, thin, dense);
    cb.thin_color[0] = thin[0];
    cb.thin_color[1] = thin[1];
    cb.thin_color[2] = thin[2];
    cb.thin_color[3] = camera.far_clip;

    cb.dense_color[0] = dense[0];
    cb.dense_color[1] = dense[1];
    cb.dense_color[2] = dense[2];
    cb.dense_color[3] = params.puddle_reflections ? 1.0f : 0.0f;

    cb.coating_info[0] = std::clamp(params.coating_growth, 0.0f, 1.0f);
    cb.coating_info[1] = std::clamp(params.coating_drift, 0.0f, 1.0f);
    cb.coating_info[3] = camera.gpu_view ? 1.0f : 0.0f;
    {
        // Nothing can be lit outside [lowest floor - 18, highest floor + reach], where reach
        // covers the global volume and every active emitter plume above its floor.
        float reach = std::max(24.0f, params.height_ceiling) * 1.7f;
        for (uint32_t i = 0; i < clamped_emitters; ++i)
            if (emitters[i].active_weight > 0.001f)
                reach = std::max(reach, emitters[i].height_max + 48.0f);
        cb.volume_bounds[0] = mg.floor_min - 24.0f;
        cb.volume_bounds[1] = mg.floor_max + reach + 24.0f;
        cb.volume_bounds[2] = reach;
        cb.volume_bounds[3] = impl_->reach_srv ? 1.0f : 0.0f;
        cb.floor_decode[0] = mg.floor_min;
        cb.floor_decode[1] = mg.floor_max - mg.floor_min;
    }
    cb.coating_info[2] =
        std::sqrt(params.wind_xy[0] * params.wind_xy[0] + params.wind_xy[1] * params.wind_xy[1]);

    for (uint32_t i = 0; i < clamped_emitters; ++i) {
        auto& dst = cb.emitters[i];
        const auto& src = emitters[i];
        dst.pos_radius[0] = src.world_pos[0];
        dst.pos_radius[1] = src.world_pos[1];
        dst.pos_radius[2] = src.world_pos[2];
        dst.pos_radius[3] = src.radius;
        dst.dir_speed[0] = src.dir_norm[0];
        dst.dir_speed[1] = src.dir_norm[1];
        dst.dir_speed[2] = src.dir_norm[2];
        dst.dir_speed[3] = src.jet_speed;
        dst.color_density[0] = src.color[0];
        dst.color_density[1] = src.color[1];
        dst.color_density[2] = src.color[2];
        dst.color_density[3] = src.density;
        dst.cone_height_active[0] = src.cone_cos;
        dst.cone_height_active[1] = src.height_min;
        dst.cone_height_active[2] = src.height_max;
        dst.cone_height_active[3] = src.active_weight;
    }

    // Safety net: above the pixel threshold (and never in exports) march at half resolution
    // and rebuild each pixel from it, re-marching depth edges at full resolution.
    const bool net = params.allow_half_res &&
                     uint64_t(camera.width) * camera.height > params.half_res_above_pixels;
    if (net) {
        const uint32_t hw = (camera.width + 1) / 2, hh = (camera.height + 1) / 2;
        ComPtr<ID3D11Device> device;
        ctx->GetDevice(&device);
        if (!impl_->half_tex || impl_->half_w != hw || impl_->half_h != hh) {
            impl_->half_tex.Reset();
            impl_->half_rtv.Reset();
            impl_->half_srv.Reset();
            D3D11_TEXTURE2D_DESC hd{};
            hd.Width = hw;
            hd.Height = hh;
            hd.MipLevels = 1;
            hd.ArraySize = 1;
            hd.Format = DXGI_FORMAT_R16G16B16A16_FLOAT;
            hd.SampleDesc.Count = 1;
            hd.Usage = D3D11_USAGE_DEFAULT;
            hd.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;
            if (FAILED(device->CreateTexture2D(&hd, nullptr, &impl_->half_tex)) ||
                FAILED(device->CreateRenderTargetView(impl_->half_tex.Get(), nullptr,
                                                      &impl_->half_rtv)) ||
                FAILED(device->CreateShaderResourceView(impl_->half_tex.Get(), nullptr,
                                                        &impl_->half_srv)))
                return false;
            impl_->half_w = hw;
            impl_->half_h = hh;
        }
    }
    cb.net_info[0] = net ? 2.0f : 0.0f;
    cb.net_info[1] = 0.08f; // relative depth change that counts as an edge
    cb.net_info[2] = impl_->drift_srv ? 1.0f : 0.0f;
    const uint32_t heroes = std::min(params.hero_count, EnvRenderParams::kMaxCoatingHeroes);
    for (uint32_t i = 0; i < heroes; ++i) {
        cb.heroes[i][0] = params.heroes[i][0];
        cb.heroes[i][1] = params.heroes[i][1];
        cb.heroes[i][2] = params.heroes[i][2];
    }
    cb.net_info[3] = static_cast<float>(heroes);
    // Trails show in snow, sand, dust and ash. Falling snow fills a track in over about two
    // minutes in a steady fall (faster when heavy); blowing sand over about a minute.
    const bool covers = params.preset == AtmospherePreset::Snow ||
                        params.preset == AtmospherePreset::Sandstorm ||
                        params.preset == AtmospherePreset::DustHaze ||
                        params.preset == AtmospherePreset::AshEmbers;
    const bool trails = params.hero_trails && params.enable_coating && covers &&
                        impl_->trail_count > 0 && impl_->trail_buf_srv;
    const float fill_base = params.preset == AtmospherePreset::Snow ? 120.0f : 60.0f;
    cb.trail_info[0] = trails ? 1.0f : 0.0f;
    cb.trail_info[1] = params.trail_fill_seconds > 0.0f
                           ? params.trail_fill_seconds
                           : fill_base / std::max(params.intensity, 0.25f);
    cb.trail_info[2] = params.shot_start_time;
    cb.trail_info[3] = std::max(params.coating_build_up, 0.0f);
    ctx->UpdateSubresource(impl_->constants.Get(), 0, nullptr, &cb, 0, 0);
    ID3D11Buffer* stamp_cbs[1] = {impl_->constants.Get()};
    if (trails) {
        // Stamp every segment reached by this frame's time into the trail map, afresh.
        // Untrodden: a time long filled in (matches kTrailFade in the shader).
        const float filled = params.sim_time - 6.0f * cb.trail_info[1];
        // Far from every path (kTrailReach in the shader), so the distance field meets it.
        const float never[4] = {filled, -filled, -(7.0f + 9.0f + 6.0f), filled};
        ID3D11RenderTargetView* trtv[1] = {impl_->trail_rtv.Get()};
        ctx->ClearRenderTargetView(impl_->trail_rtv.Get(), never);
        ctx->OMSetRenderTargets(1, trtv, nullptr);
        ctx->OMSetBlendState(impl_->trail_blend.Get(), nullptr, 0xFFFFFFFFu);
        ctx->OMSetDepthStencilState(impl_->ds_state.Get(), 0);
        ctx->RSSetState(impl_->rs_state.Get());
        D3D11_VIEWPORT tvp{0.0f, 0.0f, float(kTrailTexels), float(kTrailTexels), 0.0f, 1.0f};
        ctx->RSSetViewports(1, &tvp);
        ctx->IASetInputLayout(nullptr);
        ctx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
        ctx->VSSetShader(impl_->trail_vs.Get(), nullptr, 0);
        ctx->VSSetConstantBuffers(0, 1, stamp_cbs);
        ID3D11ShaderResourceView* vsrvs[13] = {};
        vsrvs[8] = camera.gpu_view;
        vsrvs[12] = impl_->trail_buf_srv.Get();
        ctx->VSSetShaderResources(0, 13, vsrvs);
        ctx->PSSetShader(impl_->trail_ps.Get(), nullptr, 0);
        ctx->PSSetConstantBuffers(0, 1, stamp_cbs);
        ctx->DrawInstanced(6, impl_->trail_count, 0, 0);
        ID3D11ShaderResourceView* null_vsrvs[13] = {};
        ctx->VSSetShaderResources(0, 13, null_vsrvs);
        ID3D11RenderTargetView* null_rtv[1] = {};
        ctx->OMSetRenderTargets(1, null_rtv, nullptr);
    }

    D3D11_VIEWPORT vp{
        0.0f, 0.0f, static_cast<float>(camera.width), static_cast<float>(camera.height),
        0.0f, 1.0f};
    ID3D11RenderTargetView* rtvs[1] = {out_rtv};
    ID3D11ShaderResourceView* srvs[15] = {
        scene_color_srv,
        scene_depth_srv,
        fluid.fluid_srv(),
        fluid.air_map_srv(),
        fluid.tops_srv(),
        fluid.floor_z_srv(),
        fluid.street_z_srv(),
        fluid.noise_3d_srv(),
        camera.gpu_view,
        nullptr,
        nullptr,
        impl_->drift_srv.Get(),
        nullptr,
        trails ? impl_->trail_srv.Get() : nullptr,
        impl_->reach_srv.Get(),
    };
    ID3D11SamplerState* samps[2] = {impl_->linear_clamp.Get(), impl_->linear_wrap.Get()};
    ID3D11Buffer* cbs[1] = {impl_->constants.Get()};

    // Bake the sun optical depth for this frame's wind before the march reads it.
    {
        ctx->CSSetShader(impl_->bake_cs.Get(), nullptr, 0);
        ctx->CSSetConstantBuffers(0, 1, cbs);
        ctx->CSSetSamplers(0, 2, samps);
        ctx->CSSetShaderResources(0, 9, srvs);
        ID3D11UnorderedAccessView* uavs[1] = {impl_->sun_bake_uav.Get()};
        ctx->CSSetUnorderedAccessViews(0, 1, uavs, nullptr);
        ctx->Dispatch(256 / 8, 256 / 8, 32);
        ID3D11UnorderedAccessView* null_uav[1] = {};
        ID3D11ShaderResourceView* null_cs_srvs[9] = {};
        ctx->CSSetUnorderedAccessViews(0, 1, null_uav, nullptr);
        ctx->CSSetShaderResources(0, 9, null_cs_srvs);
        ctx->CSSetShader(nullptr, nullptr, 0);
    }
    srvs[9] = impl_->sun_bake_srv.Get();

    ctx->OMSetRenderTargets(1, rtvs, nullptr);
    ctx->OMSetBlendState(impl_->blend_state.Get(), nullptr, 0xFFFFFFFFu);
    ctx->OMSetDepthStencilState(impl_->ds_state.Get(), 0);
    ctx->RSSetState(impl_->rs_state.Get());
    ctx->RSSetViewports(1, &vp);
    ctx->IASetInputLayout(nullptr);
    ctx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    ctx->VSSetShader(impl_->vs.Get(), nullptr, 0);
    ctx->PSSetShader(impl_->ps.Get(), nullptr, 0);
    ctx->PSSetConstantBuffers(0, 1, cbs);
    ctx->PSSetSamplers(0, 2, samps);
    if (net) {
        // Half-resolution march into its own target, then the full-resolution composite.
        D3D11_VIEWPORT hvp{0.0f, 0.0f, float(impl_->half_w), float(impl_->half_h), 0.0f, 1.0f};
        ID3D11RenderTargetView* half_rtvs[1] = {impl_->half_rtv.Get()};
        ctx->OMSetRenderTargets(1, half_rtvs, nullptr);
        ctx->RSSetViewports(1, &hvp);
        ctx->PSSetShader(impl_->half_ps.Get(), nullptr, 0);
        ctx->PSSetShaderResources(0, 10, srvs);
        ctx->Draw(3, 0);
        ctx->OMSetRenderTargets(1, rtvs, nullptr);
        ctx->RSSetViewports(1, &vp);
        ctx->PSSetShader(impl_->ps.Get(), nullptr, 0);
        srvs[10] = impl_->half_srv.Get();
    }
    ctx->PSSetShaderResources(0, 15, srvs);

    ctx->Draw(3, 0);

    // Unbind SRVs/RTVs to prevent hazard warnings in callers
    ID3D11ShaderResourceView* null_srvs[15] = {};
    ID3D11RenderTargetView* null_rtvs[1] = {};
    ctx->PSSetShaderResources(0, 15, null_srvs);
    ctx->OMSetRenderTargets(1, null_rtvs, nullptr);
    return true;
}

bool EnvRendererDX11::ready() const noexcept {
    return impl_->initialized;
}

bool EnvRendererDX11::set_hero_trails(const TrailSegment* segments, uint32_t count) {
    impl_->trail_buf.Reset();
    impl_->trail_buf_srv.Reset();
    impl_->trail_count = 0;
    if (!impl_->initialized || !impl_->device || !segments || count == 0)
        return count == 0;
    if (count > kMaxTrailSegments)
        return false;
    static_assert(sizeof(TrailSegment) == 32, "TrailSeg layout in the shader");
    D3D11_BUFFER_DESC bd{};
    bd.ByteWidth = count * UINT(sizeof(TrailSegment));
    bd.Usage = D3D11_USAGE_IMMUTABLE;
    bd.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    bd.MiscFlags = D3D11_RESOURCE_MISC_BUFFER_STRUCTURED;
    bd.StructureByteStride = sizeof(TrailSegment);
    D3D11_SUBRESOURCE_DATA init{segments, 0, 0};
    D3D11_SHADER_RESOURCE_VIEW_DESC sv{};
    sv.Format = DXGI_FORMAT_UNKNOWN;
    sv.ViewDimension = D3D11_SRV_DIMENSION_BUFFER;
    sv.Buffer.NumElements = count;
    if (FAILED(impl_->device->CreateBuffer(&bd, &init, &impl_->trail_buf)) ||
        FAILED(impl_->device->CreateShaderResourceView(impl_->trail_buf.Get(), &sv,
                                                        &impl_->trail_buf_srv))) {
        impl_->trail_buf.Reset();
        impl_->trail_buf_srv.Reset();
        return false;
    }
    impl_->trail_count = count;
    return true;
}

bool EnvRendererDX11::set_wall_reach(uint32_t width, uint32_t height, const uint8_t* mask) {
    impl_->reach_tex.Reset();
    impl_->reach_srv.Reset();
    if (!impl_->initialized || !impl_->device || !mask || width < 8 || height < 8 ||
        width > 8192 || height > 8192)
        return false;
    std::vector<uint8_t> texels(std::size_t(width) * height);
    for (std::size_t i = 0; i < texels.size(); ++i)
        texels[i] = mask[i] ? 255u : 0u;
    D3D11_TEXTURE2D_DESC td{};
    td.Width = width;
    td.Height = height;
    td.MipLevels = 1;
    td.ArraySize = 1;
    td.Format = DXGI_FORMAT_R8_UNORM;
    td.SampleDesc.Count = 1;
    td.Usage = D3D11_USAGE_IMMUTABLE;
    td.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    D3D11_SUBRESOURCE_DATA init{texels.data(), width, 0};
    if (FAILED(impl_->device->CreateTexture2D(&td, &init, &impl_->reach_tex)) ||
        FAILED(impl_->device->CreateShaderResourceView(impl_->reach_tex.Get(), nullptr,
                                                        &impl_->reach_srv))) {
        impl_->reach_tex.Reset();
        impl_->reach_srv.Reset();
        return false;
    }
    return true;
}

bool EnvRendererDX11::set_drift_map(uint32_t n, const float* values) {
    impl_->drift_tex.Reset();
    impl_->drift_srv.Reset();
    if (!impl_->initialized || !impl_->device || !values || n < 8 || n > 4096)
        return false;
    std::vector<uint16_t> texels(std::size_t(n) * n);
    for (std::size_t i = 0; i < texels.size(); ++i)
        texels[i] = static_cast<uint16_t>(std::lround(std::clamp(values[i] * 0.5f, 0.0f, 1.0f) *
                                                      65535.0f));
    D3D11_TEXTURE2D_DESC td{};
    td.Width = td.Height = n;
    td.MipLevels = 1;
    td.ArraySize = 1;
    td.Format = DXGI_FORMAT_R16_UNORM;
    td.SampleDesc.Count = 1;
    td.Usage = D3D11_USAGE_IMMUTABLE;
    td.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    D3D11_SUBRESOURCE_DATA init{texels.data(), n * UINT(sizeof(uint16_t)), 0};
    if (FAILED(impl_->device->CreateTexture2D(&td, &init, &impl_->drift_tex)) ||
        FAILED(impl_->device->CreateShaderResourceView(impl_->drift_tex.Get(), nullptr,
                                                        &impl_->drift_srv))) {
        impl_->drift_tex.Reset();
        impl_->drift_srv.Reset();
        return false;
    }
    return true;
}

} // namespace dolly::env
