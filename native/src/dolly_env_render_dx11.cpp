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
#include <cmath>
#include <cstring>
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
    float4 volumeBounds;  // x lowest floor Z, y highest floor Z + reach, z reach, w unused
    float4 floorDecode;   // x lowest floor Z, y floor range: decodes the UNORM16 floor map
    float4 netInfo;       // x 2 = composite from halfMarch (safety net), y edge threshold,
                          // z 1 = driftMap holds a drift potential, w hero count
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
    f = frac(uv * float2(w, h) - 0.5);
    return topsMap.GatherRed(linearClamp, uv);
}

float BlendCells(float4 v, float2 f) {
    return lerp(lerp(v.w, v.z, f.x), lerp(v.x, v.y, f.x), f.y);
}

float SkyOpenAt(float2 uv, float z) {
    if (!OnMap(uv)) return 1.0;
    float2 f;
    float4 tops = GatherTops(uv, f);
    return BlendCells(step(tops, z + 24.0), f);
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

// The highest floor of map cell q at or below zLim, or fallback when it has none.
float FloorBelowCell(int2 q, float zLim, float fallback) {
    float4 codes = floorZMap.Load(int3(q, 0)) * 65535.0;
    [unroll] for (int i = 0; i < 4; ++i) {
        if (codes[i] < 0.5) continue;
        float h = floorDecode.x + (codes[i] - 1.0) / 65534.0 * floorDecode.y;
        if (h <= zLim) return h;
    }
    return fallback;
}

// The floor below p blended between the four nearest cells (those within a curb's step of
// this cell's floor), so heights read from the map have no per-cell stairs.
float SampleFloorBelowSmooth(float3 p) {
    bool hasFloor;
    float ref = SampleFloorBelow(p, hasFloor);
    float2 uv = MapUV(p.xy);
    if (!OnMap(uv)) return ref;
    uint mw, mh;
    floorZMap.GetDimensions(mw, mh);
    float2 g = uv * float2(mw, mh) - 0.5;
    int2 b = int2(floor(g));
    float2 f = g - float2(b);
    float sum = 0.0, wsum = 0.0;
    [unroll] for (int k = 0; k < 4; ++k) {
        int2 o = int2(k & 1, k >> 1);
        int2 q = clamp(b + o, int2(0, 0), int2(mw - 1, mh - 1));
        float h = FloorBelowCell(q, p.z + 16.0, ref);
        float w = (o.x ? f.x : 1.0 - f.x) * (o.y ? f.y : 1.0 - f.y);
        w *= step(abs(h - ref), 24.0);
        sum += h * w;
        wsum += w;
    }
    return wsum > 1e-4 ? sum / wsum : ref;
}

// Round specks (glints, flecks) fixed on the ground: `density` of the cell x cell squares
// hold one. A speck spreads over at least a pixel and dims to keep its energy, so far
// away specks fade into the average instead of flickering as squares.
float RoundSpeck(float3 p, float cell, float density, float footprint, float seed) {
    float3 c = float3(floor(p.xy / cell), floor(p.z / 64.0));
    if (Hash3(c + seed) > density) return 0.0;
    float2 center = (c.xy + 0.2 + 0.6 * float2(Hash3(c + seed + 1.7), Hash3(c + seed + 3.1))) * cell;
    float r = max(0.3, footprint * 0.7);
    float d = length(p.xy - center);
    return (1.0 - smoothstep(r * 0.4, r, d)) * saturate(0.09 / (r * r));
}

// How high a drift banks against a wall at full cover, in world units.
float BankHeight(int preset) {
    if (preset == 6) return 18.0; // snow
    if (preset == 1) return 12.0; // sandstorm sand
    if (preset == 2) return 7.0;  // dust
    if (preset == 4) return 6.0;  // ash
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

// What a puddle at p mirrors along r: rgb, and a = how much of it came from the frame
// itself (0 = the sky only). With reflections allowed, the ray marches through the frame
// (12 growing steps to about 600 u, then 4 halvings); anything it misses, leaves the
// screen for, or finds only faintly falls back smoothly to the sky.
float4 PuddleReflection(float3 p, float3 r, float footprint) {
    float3 sky = SkyReflection(r);
    if (denseColor.w < 0.5) return float4(sky, 0.0);
    float prevT = 0.0, t = 4.0, dt = 10.0;
    [loop] for (int i = 0; i < 12; ++i) {
        float behind;
        float2 uv;
        if (!ReflectProbe(p + r * t, behind, uv)) break;
        float thickness = max(24.0, dt * 1.5);
        if (behind > 0.0 && behind < thickness) {
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
                         (1.0 - smoothstep(380.0, 600.0, b));
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
    float flat = smoothstep(0.93, 0.99, n.z);
    if (flat * open <= 0.001) return 0.0;
    float2 uv = MapUV(p.xy);
    float gutter = 0.0;
    if (OnMap(uv)) {
        float below = streetZMap.SampleLevel(linearClamp, uv, 0) - p.z;
        gutter = saturate((below - 1.0) / 6.0) * (1.0 - step(48.0, below));
    }
    float hollow = Fbm3(float3(p.xy * 0.0035, 7.3));
    float field = hollow + 0.3 * gutter;
    // About an eighth of open flat ground at the storm's default cover, half at full.
    float threshold = 0.95 - 0.45 * coat;
    return smoothstep(threshold, threshold + 0.05, field) * flat * open;
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
    float3 water = baseColor * 0.42;
    float3 pool = lerp(water, mirror.rgb * lerp(lit, 1.0, mirror.a), fresnel);
    return lerp(color, pool, puddle);
}

)HLSL"
                                  R"HLSL(
// Snow, sand and ash cover. Where it settles comes from the per-shot drift potential, so
// it repeats exactly at every replay time; every visible edge comes from this pixel's
// depth and world-fixed noise. Detail finer than a pixel fades instead of crawling.
float3 ApplySurfaceCoating(float3 baseColor, float3 surfPos, float3 surfNormal, float3 viewDir,
                           float cavity, float hitDist, float lumaAround) {
    // Coating settles over time (coatingInfo.x grows 0..1 from the shot start).
    float coat = styleB.y * coatingInfo.x;
    if (sunDir.w < 0.5 || coat <= 0.001) return baseColor;
    float heroMask = HeroMask(surfPos);
    if (heroMask >= 0.999) return baseColor;

    int preset = int(styleA.x + 0.5);
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

    // Top cover. Nothing here reads depth differences (creases): the game's depth bands
    // with distance, and anything built on it drew rings around the camera.
    float noise = Fbm3(surfPos * 0.0085);
    float threshold = 1.0 - 1.25 * amount;
    float soft = 0.22 + saturate(footprint / 48.0) * 0.3;
    float top = smoothstep(threshold, threshold + soft, noise) *
                saturate(amount * 4.0) * upMask * open;
    // Fine breakup along the cover's edge, only where a pixel can resolve it.
    float fine = 1.0 - saturate(footprint / 4.0);
    top = saturate(top + (Fbm3(float3(surfPos.xy * 0.07, 3.1)) - 0.5) * 1.6 * fine * top *
                             (1.0 - top));
    // Blown cover never lies as one even sheet: broad thin patches let the ground show.
    float patchy = (preset == 6) ? 0.12 : 0.4;
    top *= 1.0 - patchy * smoothstep(0.35, 0.7, ValueNoise3(float3(surfPos.xy * 0.0045, 11.0)));

    // Wall foot: a steep surface low above its floor is buried by a bank whose height
    // follows the drift just out from the wall, where the sky is open. The floor height
    // is blended between cells and the bank's top line varies only along the wall, so the
    // bank stays one smooth line.
    float wall = 0.0;
    float bankMax = BankHeight(preset);
    float steep = 1.0 - smoothstep(0.2, 0.5, surfNormal.z);
    if (bankMax > 0.0 && steep > 0.001) {
        float2 outXY = surfPos.xy + normalize(surfNormal.xy + 1e-5) * 28.0;
        float2 outUV = MapUV(outXY);
        float outOpen = SkyOpenAt(outUV, surfPos.z);
        float floorZ = SampleFloorBelowSmooth(float3(outXY, surfPos.z));
        float above = surfPos.z - floorZ;
        float bankDrift = lerp(1.0, DriftAt(outUV), coatingInfo.y);
        float bank = bankMax * saturate(coat * bankDrift * 1.4 - 0.1) * min(bankDrift, 1.3);
        float bankLine = bank * (0.75 + 0.5 * ValueNoise3(float3(surfPos.xy * 0.018, 5.0)));
        // Full at the foot, thinning over the bank's upper part to a soft top.
        float edge = 1.5 + footprint * 1.5;
        wall = steep * outOpen * smoothstep(-10.0, -4.0, above) *
               (1.0 - smoothstep(bankLine * 0.55, bankLine + edge, above));
    }

    float cov = max(top, wall) * (1.0 - heroMask);
    if (cov <= 0.001) return baseColor;

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
    float luma = dot(baseColor, float3(0.2126, 0.7152, 0.0722));
    float light = lerp(0.3, 1.08, saturate(pow(saturate(lumaAround / 0.32), 0.65)));
    float3 chroma = clamp(baseColor / max(luma, 1e-3), 0.0, 2.0);
    float3 lightTint = lerp(float3(1.0, 1.0, 1.0), chroma, 0.3);

    float2 wdir = coatingInfo.z > 1.0 ? windTime.xy / coatingInfo.z : float2(1.0, 0.0);
    float2 perp = float2(-wdir.y, wdir.x);
    float shadeMul = 1.0;
    float3 add = float3(0.0, 0.0, 0.0);
    if (preset == 6) {
        // Snow: soft undulation, cool shade in low light, and a few tiny world-fixed glints
        // that catch the light as the camera moves.
        shadeMul = 0.95 + 0.1 * ValueNoise3(float3(surfPos.xy * 0.02, 1.0));
        lightTint *= lerp(float3(0.86, 0.92, 1.06), float3(1.0, 1.0, 1.0), saturate(light));
        float glint = RoundSpeck(surfPos, 4.0, 0.06, footprint, 17.0);
        float facing = Hash3(floor(surfPos / 4.0) + floor(viewDir * 5.0) * 17.0);
        add += sunColor.rgb * glint * step(0.5, facing) * upMask * 0.7 * light;
    } else if (preset == 1 || preset == 2) {
        // Sand: low ripples across the wind, lit on their sun-facing side, uneven in
        // strength and gone before they would shimmer.
        float warp = Fbm3(float3(surfPos.xy * 0.008, 2.0)) * 9.0;
        float phase = dot(surfPos.xy, wdir) / 9.0 + warp;
        float sunAlong = dot(normalize(sunDir.xy + 1e-4), wdir);
        float ripple = (1.0 - saturate((footprint - 0.2) / 0.6)) *
                       smoothstep(0.3, 0.65, Fbm3(float3(surfPos.xy * 0.006, 9.0)));
        shadeMul = 1.0 + 0.07 * cos(phase * 6.2831853) * sunAlong * ripple * upMask +
                   0.1 * (Fbm3(float3(surfPos.xy * 0.015, 4.0)) - 0.5);
    } else if (preset == 4) {
        // Ash: soft dark flecks over a blotchy layer.
        float fleck = RoundSpeck(surfPos, 5.0, 0.25, footprint, 29.0);
        shadeMul = lerp(1.0, 0.6, fleck) * (0.9 + 0.2 * Fbm3(float3(surfPos.xy * 0.02, 6.0)));
    }

    // Spindrift: where the wind runs fast, soft bands of snow or sand skim over the cover.
    if ((preset == 6 || preset == 1 || preset == 2) && OnMap(uv)) {
        float speed = length(fluidState.SampleLevel(linearClamp, uv, 0).xy);
        float gust = saturate((speed - 120.0) / 380.0);
        if (gust > 0.01) {
            float3 q = float3((dot(surfPos.xy, wdir) - windTime.z * speed * 0.9) * 0.006,
                              dot(surfPos.xy, perp) * 0.02, windTime.z * 0.4);
            float streak = smoothstep(0.5, 0.8, Fbm3(q));
            add += coatAlbedo * light * streak * gust * 0.22 * upMask;
        }
    }

    float3 coated = coatAlbedo * light * lightTint * shadeMul + add;
    return lerp(baseColor, coated, saturate(cov * 0.9));
}

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
    float jitter = InterleavedGradient(px);
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
            // On a floor the map knows, the ground faces up: depth-built normals tilt on the
            // rows where the game's depth steps, which drew arcs around the camera.
            bool hasFloor;
            float onFloor = 1.0 - smoothstep(2.0, 8.0,
                                             abs(surfPos.z - SampleFloorBelow(surfPos, hasFloor)));
            surfNormal = normalize(lerp(surfNormal, float3(0.0, 0.0, 1.0),
                                        onFloor * smoothstep(0.5, 0.8, surfNormal.z)));
            float3 around = baseColor;
            int2 lim = int2(screenInfo.xy) - 1;
            around += sceneColor.Load(int3(clamp(ipx + int2(-4, -4), int2(0, 0), lim), 0)).rgb;
            around += sceneColor.Load(int3(clamp(ipx + int2(4, -4), int2(0, 0), lim), 0)).rgb;
            around += sceneColor.Load(int3(clamp(ipx + int2(-4, 4), int2(0, 0), lim), 0)).rgb;
            around += sceneColor.Load(int3(clamp(ipx + int2(4, 4), int2(0, 0), lim), 0)).rgb;
            float lumaAround = dot(around * 0.2, float3(0.2126, 0.7152, 0.0722));
            baseColor = ApplySurfaceCoating(baseColor, surfPos, surfNormal, -rayDir, cavity,
                                            hitDist, lumaAround);
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
    bool initialized = false;
};

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

    const UINT flags = D3DCOMPILE_ENABLE_STRICTNESS | D3DCOMPILE_OPTIMIZATION_LEVEL3;
    ComPtr<ID3DBlob> vs_code, ps_code, err;
    if (FAILED(D3DCompile(kEnvRenderHlsl, sizeof(kEnvRenderHlsl) - 1, "dolly_env_render.hlsl",
                          nullptr, nullptr, "FullscreenVS", "vs_5_0", flags, 0, &vs_code, &err))) {
        if (out_error)
            *out_error = std::string("FullscreenVS compile failed: ") +
                         (err ? static_cast<const char*>(err->GetBufferPointer()) : "unknown");
        shutdown();
        return false;
    }
    err.Reset();
    if (FAILED(D3DCompile(kEnvRenderHlsl, sizeof(kEnvRenderHlsl) - 1, "dolly_env_render.hlsl",
                          nullptr, nullptr, "EnvCompositePS", "ps_5_0", flags, 0, &ps_code,
                          &err))) {
        if (out_error)
            *out_error = std::string("EnvCompositePS compile failed: ") +
                         (err ? static_cast<const char*>(err->GetBufferPointer()) : "unknown");
        shutdown();
        return false;
    }

    ComPtr<ID3DBlob> half_code;
    err.Reset();
    if (FAILED(D3DCompile(kEnvRenderHlsl, sizeof(kEnvRenderHlsl) - 1, "dolly_env_render.hlsl",
                          nullptr, nullptr, "EnvHalfPS", "ps_5_0", flags, 0, &half_code, &err)) ||
        FAILED(device->CreatePixelShader(half_code->GetBufferPointer(), half_code->GetBufferSize(),
                                         nullptr, &impl_->half_ps))) {
        if (out_error)
            *out_error = std::string("EnvHalfPS compile failed: ") +
                         (err ? static_cast<const char*>(err->GetBufferPointer()) : "unknown");
        shutdown();
        return false;
    }
    ComPtr<ID3DBlob> cs_code;
    err.Reset();
    if (FAILED(D3DCompile(kEnvRenderHlsl, sizeof(kEnvRenderHlsl) - 1, "dolly_env_render.hlsl",
                          nullptr, nullptr, "BakeSunCS", "cs_5_0", flags, 0, &cs_code, &err)) ||
        FAILED(device->CreateComputeShader(cs_code->GetBufferPointer(), cs_code->GetBufferSize(),
                                           nullptr, &impl_->bake_cs))) {
        if (out_error)
            *out_error = std::string("BakeSunCS compile failed: ") +
                         (err ? static_cast<const char*>(err->GetBufferPointer()) : "unknown");
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
    ctx->UpdateSubresource(impl_->constants.Get(), 0, nullptr, &cb, 0, 0);

    D3D11_VIEWPORT vp{
        0.0f, 0.0f, static_cast<float>(camera.width), static_cast<float>(camera.height),
        0.0f, 1.0f};
    ID3D11RenderTargetView* rtvs[1] = {out_rtv};
    ID3D11ShaderResourceView* srvs[12] = {
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
    ctx->PSSetShaderResources(0, 12, srvs);

    ctx->Draw(3, 0);

    // Unbind SRVs/RTVs to prevent hazard warnings in callers
    ID3D11ShaderResourceView* null_srvs[12] = {};
    ID3D11RenderTargetView* null_rtvs[1] = {};
    ctx->PSSetShaderResources(0, 12, null_srvs);
    ctx->OMSetRenderTargets(1, null_rtvs, nullptr);
    return true;
}

bool EnvRendererDX11::ready() const noexcept {
    return impl_->initialized;
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
