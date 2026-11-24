// DX11 Compute 2D Stable Fluids Wind Solver.
//
// Fluid solver architecture, Voronoi FoamLace lifting, Catmull-Rom density
// advection, roof-escape divergence scaling, and 3D Perlin-Worley noise are
// adapted from Wockstajit/SourceMVM under the MIT License:
//   Copyright (c) 2021 advancedfx.org
//   Copyright (c) 2026 Wockstajit (SOURCE:MVM)
//
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to deal
// in the Software without restriction, including without limitation the rights
// to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
// copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions:
// The above copyright notice and this permission notice shall be included in
// all copies or substantial portions of the Software.

#include "dolly_wind_fluid_dx11.hpp"

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

// -----------------------------------------------------------------------------
// 3D Seamless Perlin-Worley Noise (adapted from SourceMVM EnvironmentWindFluidMath.h)

inline uint32_t noise_hash(int x, int y, int z, uint32_t seed) noexcept {
    uint32_t h = static_cast<uint32_t>(x) * 73856093u ^ static_cast<uint32_t>(y) * 19349663u ^
                 static_cast<uint32_t>(z) * 83492791u ^ seed * 2654435761u;
    h ^= h >> 13;
    h *= 0x5bd1e995u;
    h ^= h >> 15;
    return h;
}

inline float noise_unit(uint32_t h) noexcept {
    return static_cast<float>(h & 0xffffffu) / 16777216.0f;
}

inline int wrap_period(int v, int period) noexcept {
    return ((v % period) + period) % period;
}

inline float tiled_value(float px, float py, float pz, int period, uint32_t seed) noexcept {
    const float x = px * period, y = py * period, z = pz * period;
    const int ix = static_cast<int>(std::floor(x));
    const int iy = static_cast<int>(std::floor(y));
    const int iz = static_cast<int>(std::floor(z));
    const auto smooth = [](float t) { return t * t * (3.0f - 2.0f * t); };
    const float fx = smooth(x - ix), fy = smooth(y - iy), fz = smooth(z - iz);
    const auto at = [&](int dx, int dy, int dz) {
        return noise_unit(noise_hash(wrap_period(ix + dx, period), wrap_period(iy + dy, period),
                                     wrap_period(iz + dz, period), seed));
    };
    const auto lerp = [](float a, float b, float t) { return a + (b - a) * t; };
    return lerp(lerp(lerp(at(0, 0, 0), at(1, 0, 0), fx), lerp(at(0, 1, 0), at(1, 1, 0), fx), fy),
                lerp(lerp(at(0, 0, 1), at(1, 0, 1), fx), lerp(at(0, 1, 1), at(1, 1, 1), fx), fy),
                fz);
}

inline float tiled_worley(float px, float py, float pz, int period, uint32_t seed) noexcept {
    const float x = px * period, y = py * period, z = pz * period;
    const int ix = static_cast<int>(std::floor(x));
    const int iy = static_cast<int>(std::floor(y));
    const int iz = static_cast<int>(std::floor(z));
    float best = 9.0f;
    for (int dz = -1; dz <= 1; ++dz) {
        for (int dy = -1; dy <= 1; ++dy) {
            for (int dx = -1; dx <= 1; ++dx) {
                const int cx = ix + dx, cy = iy + dy, cz = iz + dz;
                const uint32_t h = noise_hash(wrap_period(cx, period), wrap_period(cy, period),
                                              wrap_period(cz, period), seed);
                const float fx = cx + noise_unit(h) - x;
                const float fy = cy + noise_unit(h * 747796405u + 1u) - y;
                const float fz = cz + noise_unit(h * 2891336453u + 7u) - z;
                best = std::min(best, fx * fx + fy * fy + fz * fz);
            }
        }
    }
    return 1.0f - std::min(std::sqrt(best), 1.0f);
}

// -----------------------------------------------------------------------------
// HLSL Stable Fluids Compute Shaders (cs_5_0, 8x8x1 threads)
// Adapted from SourceMVM EnvironmentWindFluidShaders.h (MIT License) with
// first-class support for up to 16 world-placed directional emitters.

constexpr char kWindFluidHlsl[] = R"HLSL(
#define BODIES 12
#define EMITTERS 16

struct EmitterGPU {
    float4 posRadius;        // xyz world position, w core radius (u)
    float4 dirSpeed;         // xyz normalized aim direction, w jet speed (u/s)
    float4 colorDensity;     // rgb tint, w density injection rate
    float4 coneHeightActive; // x coneCos, y heightMin, z heightMax, w activeWeight
};

cbuffer WindStep : register(b0) {
    float4 stepInfo;       // x dt (s), y sim cell size (u), z sim time (s), w swirl strength
    float4 windInfo;       // x steady windX (u/s), y steady windY (u/s), z front speed (u/s), w relax share
    float4 gridInfo;       // xy sim grid min corner (world), zw sim grid cells (w, h)
    float4 bodyInfo;       // x body count, y 1 = obstacle map bound, z carry decay, w emitter count
    float4 styleInfo;      // x gust strength, y intensity, z map origin X, w map origin Y
    float4 mapScaleInfo;   // x 1/mapSpanX, y 1/mapSpanY, z Blend fraction, w reserved
    float4 bodies[BODIES];       // xy world pos, z radius, w active
    float4 bodyVelocity[BODIES]; // xy velocity (u/s), zw unused
    EmitterGPU emitters[EMITTERS];
};

Texture2D<float4> stateIn : register(t0);
Texture2D<float> scalarIn : register(t1);
Texture2D<float> pressureIn : register(t2);
Texture2D<float> obstacleIn : register(t3); // R8_UNORM: >0.5 solid, 0..0.498 openness
Texture2D<float4> stateNext : register(t4); // Blend only: the next tick's state
RWTexture2D<float4> stateOut : register(u0);
RWTexture2D<float> scalarOut : register(u1);
SamplerState linearClamp : register(s0);

int2 Cells() { return int2(gridInfo.zw); }
bool Outside(int2 q) { return any(q < 0) || any(q >= Cells()); }
int2 Inside(int2 q) { return clamp(q, int2(0, 0), Cells() - 1); }

float2 CellWorld(int2 q) { return gridInfo.xy + (float2(q) + 0.5) * stepInfo.y; }

float SampleAirMap(int2 q) {
    float2 world = CellWorld(Inside(q));
    float2 uv = (world - styleInfo.zw) * mapScaleInfo.xy;
    if (any(uv < 0.0) || any(uv > 1.0)) return 127.0 / 255.0;
    uint mw, mh;
    obstacleIn.GetDimensions(mw, mh);
    int2 mq = clamp(int2(floor(uv * float2(mw, mh))), int2(0, 0), int2(mw - 1, mh - 1));
    return obstacleIn.Load(int3(mq, 0));
}

bool Solid(int2 q) {
    return bodyInfo.y > 0.5 && SampleAirMap(q) > 0.5;
}

float Open(int2 q) {
    if (bodyInfo.y <= 0.5) return 1.0;
    float m = SampleAirMap(q);
    return m > 0.5 ? 0.0 : saturate(m * 255.0 / 127.0);
}

float4 State(int2 q) { return stateIn.Load(int3(Inside(q), 0)); }
float2 Velocity(int2 q) { return Solid(q) ? float2(0.0, 0.0) : State(q).xy; }
float Scalar(int2 q) { return scalarIn.Load(int3(Inside(q), 0)); }

float Hash(float2 p) {
    p = frac(p * float2(0.1031, 0.1030));
    p += dot(p, p.yx + 33.33);
    return frac((p.x + p.y) * p.x);
}

float ValueNoise(float2 p) {
    float2 a = floor(p), f = frac(p);
    f = f * f * (3.0 - 2.0 * f);
    return lerp(lerp(Hash(a), Hash(a + float2(1.0, 0.0)), f.x),
                lerp(Hash(a + float2(0.0, 1.0)), Hash(a + 1.0), f.x), f.y);
}

float2 CurlNoise(float2 p) {
    float e = 0.5, n0 = ValueNoise(p);
    return float2(ValueNoise(p + float2(0.0, e)) - n0, -(ValueNoise(p + float2(e, 0.0)) - n0)) / e;
}

float2 Hash2(float2 p) { return float2(Hash(p), Hash(p + 19.19)); }

// Voronoi cell-edge FoamLace (F2 - F1 over two scales) for torn lifted sand/snow threads.
float Lace(float2 p) {
    float2 a = floor(p), f = p - a;
    float f1 = 9.0, f2 = 9.0;
    [unroll] for (int y = -1; y <= 1; ++y) {
        [unroll] for (int x = -1; x <= 1; ++x) {
            float2 o = float2(x, y);
            float2 c = o + Hash2(a + o) * 0.85 + 0.075 - f;
            float d = dot(c, c);
            if (d < f1) { f2 = f1; f1 = d; }
            else if (d < f2) { f2 = d; }
        }
    }
    return 1.0 - smoothstep(0.02, 0.20, sqrt(f2) - sqrt(f1));
}

float FoamLace(float2 p) { return max(Lace(p), Lace(p * 2.3 + 5.1) * 0.7); }

float Lifted(float2 world) {
    float t = stepInfo.z;
    float a = FoamLace(world / 100.0);
    float b = FoamLace(world / 50.0 + t * float2(0.05, 0.10));
    // The coarse lace torn by the drifting fine lace into threads.
    float d = max(a - 0.5 * b, 0.0);
    return d * d;
}

float2 Along() {
    float s = length(windInfo.xy);
    return s > 1.0 ? windInfo.xy / s : float2(1.0, 0.0);
}

float Front(float2 world) {
    float2 d = Along();
    float2 q = float2(dot(world, d) - stepInfo.z * windInfo.z, dot(world, float2(-d.y, d.x)));
    float n = ValueNoise(q / float2(1100.0, 2800.0) + stepInfo.z * float2(0.0, 0.05)) * 0.65 +
              ValueNoise(q / float2(380.0, 900.0) + 7.3 - stepInfo.z * float2(0.0, 0.09)) * 0.35;
    return smoothstep(0.46, 0.76, n);
}

float Banks(float2 world) {
    float2 d = Along(), n = float2(-d.y, d.x);
    float2 q = float2(dot(world, d) - stepInfo.z * windInfo.z * 0.7, dot(world, n));
    float b = ValueNoise(q / float2(620.0, 380.0) + float2(0.0, stepInfo.z * 0.03)) * 0.62 +
              ValueNoise(q / float2(240.0, 150.0) + 5.3 - float2(0.0, stepInfo.z * 0.05)) * 0.38;
    return smoothstep(0.40, 0.70, b);
}

float2 Target(float2 world) {
    float baseSpeed = length(windInfo.xy);
    float surge = (140.0 + baseSpeed) * 1.7 * styleInfo.x * (0.3 + 0.7 * Front(world));
    return Along() * (baseSpeed + surge);
}

float Sheltered(int2 q) { return lerp(0.5, 1.0, Open(q)); }

float EmitterWeight(EmitterGPU e, float2 world, out float2 jetDir) {
    jetDir = float2(1.0, 0.0);
    float aw = e.coneHeightActive.w;
    if (aw <= 0.001) return 0.0;
    float r = max(e.posRadius.w, 16.0);
    float2 rel = world - e.posRadius.xy;
    float d = length(rel);
    if (d > r * 2.5) return 0.0;

    float2 dxy = e.dirSpeed.xy;
    float dlen = length(dxy);
    if (dlen > 0.01) {
        jetDir = dxy / dlen;
    } else {
        jetDir = d > 1.0 ? rel / d : float2(1.0, 0.0);
    }

    float radial = exp(-(d * d) / (r * r));
    float core = 1.0 - smoothstep(r * 0.20, r * 0.60, d);
    float coneCos = e.coneHeightActive.x;
    float cone = 1.0;
    if (coneCos > -0.95 && d > 1.0) {
        float align = dot(rel / d, jetDir);
        cone = smoothstep(coneCos - 0.18, coneCos + 0.18, align);
    }
    return max(core, radial * cone) * aw;
}

[numthreads(8, 8, 1)]
void Curl(uint3 id : SV_DispatchThreadID) {
    int2 q = int2(id.xy);
    if (Outside(q)) return;
    scalarOut[q] = (Velocity(q + int2(1, 0)).y - Velocity(q - int2(1, 0)).y -
                    Velocity(q + int2(0, 1)).x + Velocity(q - int2(0, 1)).x) / (2.0 * stepInfo.y);
}

[numthreads(8, 8, 1)]
void Force(uint3 id : SV_DispatchThreadID) {
    int2 q = int2(id.xy);
    if (Outside(q)) return;
    if (Solid(q)) {
        stateOut[q] = float4(0.0, 0.0, 0.0, 1.0);
        return;
    }

    float dt = stepInfo.x;
    float h = stepInfo.y;
    float4 s = State(q);
    float2 world = CellWorld(q);
    float2 target = Target(world) * Sheltered(q);
    float2 v = s.xy + (target - s.xy) * windInfo.w;

    // Vorticity confinement
    float2 slope = float2(abs(Scalar(q + int2(1, 0))) - abs(Scalar(q - int2(1, 0))),
                          abs(Scalar(q + int2(0, 1))) - abs(Scalar(q - int2(0, 1))));
    float steep = length(slope);
    if (steep > 1e-5) {
        v += float2(slope.y, -slope.x) / steep * Scalar(q) * h * stepInfo.w * dt;
    }

    // Multi-scale divergence-free turbulence scaled by air speed
    float spd = length(v);
    v += (CurlNoise(world * 0.0045 + stepInfo.z * float2(0.11, -0.08)) * 1.6 +
          CurlNoise(world * 0.013 + 4.7 - stepInfo.z * float2(0.19, 0.15)) +
          CurlNoise(world * 0.04 + 9.1 + stepInfo.z * float2(0.4, 0.3)) * 0.7) * spd * 0.9 * dt;
    v += CurlNoise((world - Along() * stepInfo.z * windInfo.z * 0.25) / 490.0) * spd * 1.4 * dt;

    // Ambient Voronoi FoamLace lifted sand/dust
    float front = 0.3 + 0.7 * Front(world);
    float lift = (0.35 + 0.65 * Banks(world)) * (0.55 + 0.45 * saturate(Lifted(world) * 3.0)) *
                 front * Sheltered(q) * styleInfo.y;
    float carried = s.z * bodyInfo.z + lift * 1.4 * dt;

    // 12 Hero Wakes (BODIES)
    int bCount = min(int(bodyInfo.x), BODIES);
    [loop] for (int i = 0; i < bCount; ++i) {
        if (bodies[i].w <= 0.001) continue;
        float2 away = world - bodies[i].xy;
        float r = max(bodies[i].z, h * 1.25);
        float d = length(away);
        if (d >= r * 3.0) continue;
        float share = saturate(1.0 - d / r) * bodies[i].w;
        float2 bv = bodyVelocity[i].xy;
        float bspd = length(bv);
        v = lerp(v, bv, share * share * 0.85);
        // Bow-wave push + dust kickup proportional to hero running speed
        if (d > 1.0) {
            v += (away / d) * bspd * share * 0.35 * min(dt * 12.0, 1.0);
        }
        carried += share * saturate(bspd / 280.0) * 1.8 * dt;
    }

    // 16 World-Placed Directional Emitters (EMITTERS)
    int eCount = min(int(bodyInfo.w), EMITTERS);
    [loop] for (int ei = 0; ei < eCount; ++ei) {
        float2 jetDir;
        float w = EmitterWeight(emitters[ei], world, jetDir);
        if (w <= 0.001) continue;
        float jetSpd = emitters[ei].dirSpeed.w;
        v += jetDir * jetSpd * w * min(dt * 6.0, 1.0);
        carried += emitters[ei].colorDensity.w * w * 3.2 * dt;
    }

    // Open inflow boundary at map perimeter
    if (any(q < 2) || any(q >= Cells() - 2)) {
        v = target;
    }
    stateOut[q] = float4(v, clamp(carried, 0.0, 2.4), 0.0);
}

[numthreads(8, 8, 1)]
void Advect(uint3 id : SV_DispatchThreadID) {
    int2 q = int2(id.xy);
    if (Outside(q)) return;
    if (Solid(q)) {
        stateOut[q] = float4(0.0, 0.0, 0.0, 1.0);
        return;
    }

    float2 from = float2(q) + 0.5 - State(q).xy * stepInfo.x / stepInfo.y;
    float4 s = stateIn.SampleLevel(linearClamp, from / gridInfo.zw, 0);

    // Clamped 4x4 Catmull-Rom advection for carried density to preserve sharp wisps & threads
    float2 f = from - 0.5;
    int2 b = int2(floor(f));
    float2 t = f - float2(b);
    float2 w[4] = {
        t * (-0.5 + t * (1.0 - 0.5 * t)),
        1.0 + t * t * (-2.5 + 1.5 * t),
        t * (0.5 + t * (2.0 - 1.5 * t)),
        t * t * (-0.5 + 0.5 * t)
    };
    float sum = 0.0, lo = 1e9, hi = -1e9;
    [unroll] for (int y = 0; y < 4; ++y) {
        [unroll] for (int x = 0; x < 4; ++x) {
            float c = State(b + int2(x - 1, y - 1)).z;
            sum += c * w[x].x * w[y].y;
            if ((x == 1 || x == 2) && (y == 1 || y == 2)) {
                lo = min(lo, c);
                hi = max(hi, c);
            }
        }
    }
    stateOut[q] = float4(s.xy, clamp(sum, lo, hi), 0.0);
}

[numthreads(8, 8, 1)]
void Divergence(uint3 id : SV_DispatchThreadID) {
    int2 q = int2(id.xy);
    if (Outside(q)) return;
    float open = Open(q);
    scalarOut[q] = (Velocity(q + int2(1, 0)).x - Velocity(q - int2(1, 0)).x +
                    Velocity(q + int2(0, 1)).y - Velocity(q - int2(0, 1)).y) /
                   (2.0 * stepInfo.y) * open * open;
}

float Pressure(int2 q, float here) {
    return Solid(q) ? here : pressureIn.Load(int3(Inside(q), 0));
}

[numthreads(8, 8, 1)]
void Jacobi(uint3 id : SV_DispatchThreadID) {
    int2 q = int2(id.xy);
    if (Outside(q)) return;
    float here = pressureIn.Load(int3(q, 0));
    scalarOut[q] = (Pressure(q + int2(1, 0), here) + Pressure(q - int2(1, 0), here) +
                    Pressure(q + int2(0, 1), here) + Pressure(q - int2(0, 1), here) -
                    Scalar(q) * stepInfo.y * stepInfo.y) * 0.25;
}

[numthreads(8, 8, 1)]
void Project(uint3 id : SV_DispatchThreadID) {
    int2 q = int2(id.xy);
    if (Outside(q)) return;
    if (Solid(q)) {
        stateOut[q] = float4(0.0, 0.0, 0.0, 1.0);
        return;
    }

    float4 s = State(q);
    float here = pressureIn.Load(int3(q, 0));
    float2 v = s.xy - float2(Pressure(q + int2(1, 0), here) - Pressure(q - int2(1, 0), here),
                             Pressure(q + int2(0, 1), here) - Pressure(q - int2(0, 1), here)) /
                      (2.0 * stepInfo.y);

    float2 world = CellWorld(q);
    float most = length(Target(world)) * Sheltered(q) * 1.35 + 80.0;
    int eCount = min(int(bodyInfo.w), EMITTERS);
    [loop] for (int ei = 0; ei < eCount; ++ei) {
        if (emitters[ei].coneHeightActive.w <= 0.001) continue;
        float r = max(emitters[ei].posRadius.w, 16.0);
        float d = length(world - emitters[ei].posRadius.xy);
        if (d < r * 3.0) {
            float boost = (1.0 - smoothstep(r * 0.5, r * 3.0, d)) * abs(emitters[ei].dirSpeed.w);
            most = max(most, boost * 1.25 + 120.0);
        }
    }

    float now = length(v);
    if (now > most) v *= most / now;

    // Zero-flux wall boundary condition
    if (Solid(q + int2(1, 0))) v.x = min(v.x, 0.0);
    if (Solid(q - int2(1, 0))) v.x = max(v.x, 0.0);
    if (Solid(q + int2(0, 1))) v.y = min(v.y, 0.0);
    if (Solid(q - int2(0, 1))) v.y = max(v.y, 0.0);

    stateOut[q] = float4(clamp(v, -2000.0, 2000.0), s.z, 0.0);
}

// The state rendered between two fixed ticks: an exact blend by the fraction of the
// tick elapsed (mapScaleInfo.z), so any frame rate sees smooth, reproducible motion.
[numthreads(8, 8, 1)]
void Blend(uint3 id : SV_DispatchThreadID) {
    int2 q = int2(id.xy);
    if (Outside(q)) return;
    float4 st = lerp(stateIn.Load(int3(q, 0)), stateNext.Load(int3(q, 0)), mapScaleInfo.z);
    // Hero wakes, a pure function of this frame's hero positions: air steers around each
    // hero, goes calm behind them and stirs a little dust at their feet. Applied here
    // rather than in the ticks, so the same replay time always gives the same state.
    int bCount = min(int(bodyInfo.x), BODIES);
    if (bCount > 0) {
        float2 world = CellWorld(q);
        float2 v = st.xy;
        float ambient = length(st.xy);
        [loop] for (int i = 0; i < bCount; ++i) {
            float2 away = world - bodies[i].xy;
            float r = max(bodies[i].z, stepInfo.y * 1.25);
            float d = length(away);
            if (d >= r * 3.0 || bodies[i].w <= 0.001) continue;
            float2 n = d > 1e-3 ? away / d : float2(1.0, 0.0);
            float near = saturate(1.0 - (d - r) / (2.0 * r)) * bodies[i].w;
            // No flow into the hero: drop the inward part and send it round the sides.
            float inward = min(dot(v, n), 0.0);
            float2 side = float2(-n.y, n.x) * (dot(v, float2(-n.y, n.x)) >= 0.0 ? 1.0 : -1.0);
            v += (-n * inward + side * -inward * 0.6) * near;
            // Calm lee downwind.
            float sp = length(v);
            float lee = sp > 1e-3 ? saturate(dot(n, v / sp)) : 0.0;
            v *= 1.0 - 0.55 * lee * saturate(1.0 - d / (3.0 * r)) * bodies[i].w;
            // Still air at the hero, with dust kicked up by the wind hitting them.
            float core = saturate(1.0 - d / r) * bodies[i].w;
            v *= 1.0 - 0.8 * core;
            st.z += core * saturate(ambient / 300.0) * 0.25;
        }
        st.xy = v;
    }
    stateOut[q] = st;
}
)HLSL";

struct EmitterGPU {
    float pos_radius[4];
    float dir_speed[4];
    float color_density[4];
    float cone_height_active[4];
};

struct WindStepConstants {
    float step_info[4];
    float wind_info[4];
    float grid_info[4];
    float body_info[4];
    float style_info[4];
    float map_scale_info[4];
    float bodies[kMaxFluidBodies][4];
    float body_velocity[kMaxFluidBodies][4];
    EmitterGPU emitters[kMaxFluidEmitters];
};
static_assert(sizeof(WindStepConstants) % 16 == 0, "CB must be 16-byte aligned");

enum ShaderEntry {
    kCurl = 0,
    kForce,
    kAdvect,
    kDivergence,
    kJacobi,
    kProject,
    kBlend,
    kEntryCount
};
constexpr const char* kEntryNames[kEntryCount] = {"Curl",   "Force",   "Advect", "Divergence",
                                                  "Jacobi", "Project", "Blend"};

// Fixed simulation ticks on the absolute replay timeline. The wind at replay time t is
// defined by stepping these ticks from a canonical reset point, so every path that
// reaches t (playback, any export frame rate, a seek) computes the same state.
constexpr double kTickRate = 60.0;
constexpr int64_t kPrewarmTicks = 120;     // 2 s of wind before the reset point
constexpr int64_t kMaxCatchupTicks = 7200; // re-anchor beyond 2 min of forward catch-up

struct ComputeSurface {
    ComPtr<ID3D11Texture2D> texture;
    ComPtr<ID3D11ShaderResourceView> srv;
    ComPtr<ID3D11UnorderedAccessView> uav;

    void reset() {
        uav.Reset();
        srv.Reset();
        texture.Reset();
    }
};

// IEEE-754 float16 -> float32 helper for staging diagnostics readback
inline float half_to_float(uint16_t h) noexcept {
    const uint32_t sign = (static_cast<uint32_t>(h & 0x8000u)) << 16;
    const uint32_t exp = (h >> 10) & 0x1Fu;
    const uint32_t mant = h & 0x03FFu;
    uint32_t f = 0;
    if (exp == 0) {
        if (mant == 0) {
            f = sign;
        } else {
            uint32_t m = mant;
            uint32_t e = 127 - 14;
            while ((m & 0x0400u) == 0) {
                m <<= 1;
                --e;
            }
            m &= 0x03FFu;
            f = sign | (e << 23) | (m << 13);
        }
    } else if (exp == 31) {
        f = sign | 0x7F800000u | (mant << 13);
    } else {
        f = sign | ((exp + 112u) << 23) | (mant << 13);
    }
    float out = 0.0f;
    std::memcpy(&out, &f, sizeof(out));
    return out;
}

} // namespace

std::vector<uint8_t> generate_gust_noise_volume(int size) {
    std::vector<uint8_t> out;
    if (size < 4)
        return out;
    out.resize(static_cast<size_t>(size) * static_cast<size_t>(size) * static_cast<size_t>(size));
    const float inv_n = 1.0f / static_cast<float>(size);
    for (int z = 0; z < size; ++z) {
        const float pz = (static_cast<float>(z) + 0.5f) * inv_n;
        for (int y = 0; y < size; ++y) {
            const float py = (static_cast<float>(y) + 0.5f) * inv_n;
            for (int x = 0; x < size; ++x) {
                const float px = (static_cast<float>(x) + 0.5f) * inv_n;
                const float billow =
                    tiled_value(px, py, pz, 4, 1) * 0.53f + tiled_value(px, py, pz, 8, 2) * 0.27f +
                    tiled_value(px, py, pz, 16, 3) * 0.13f + tiled_value(px, py, pz, 32, 4) * 0.07f;
                const float cells = tiled_worley(px, py, pz, 4, 5) * 0.625f +
                                    tiled_worley(px, py, pz, 8, 6) * 0.25f +
                                    tiled_worley(px, py, pz, 16, 7) * 0.125f;
                float v = cells + billow * (1.0f - cells);
                v = std::clamp((v - 0.45f) / 0.50f, 0.0f, 1.0f);
                out[(static_cast<size_t>(z) * size + y) * size + x] =
                    static_cast<uint8_t>(v * 255.0f + 0.5f);
            }
        }
    }
    return out;
}

ReplayFluidClock::Advance ReplayFluidClock::tick(double replay_time) noexcept {
    Advance a{};
    if (!std::isfinite(replay_time))
        return a;
    if (!initialized) {
        initialized = true;
        last_time = replay_time;
        a.reset = true;
        return a;
    }
    const double delta = replay_time - last_time;
    if (delta == 0.0)
        return a; // paused: freeze storm cleanly
    if (delta < 0.0 || delta > seek_jump_threshold) {
        last_time = replay_time;
        a.reset = true;
        return a; // seek or discontinuity: reset & prewarm deterministically
    }
    last_time = replay_time;
    a.dt_total = static_cast<float>(delta);
    const int needed = static_cast<int>(std::ceil(a.dt_total / std::max(max_substep, 1e-4f)));
    a.steps = std::clamp(needed, 1, std::max(1, max_steps_per_frame));
    return a;
}

struct WindFluidSolverDX11::Impl {
    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11ComputeShader> shaders[kEntryCount];
    ComPtr<ID3D11Buffer> constants;
    ComPtr<ID3D11SamplerState> linear_clamp;

    ComputeSurface fluid[2];
    ComputeSurface pressure[2];
    ComputeSurface scalar;
    ComputeSurface previous; // state at the rendered tick; fluid[current] is the tick after
    ComputeSurface blended;  // what renderers sample between the two ticks

    ComPtr<ID3D11Texture2D> air_map_tex;
    ComPtr<ID3D11ShaderResourceView> air_map_srv;
    ComPtr<ID3D11Texture2D> tops_tex;
    ComPtr<ID3D11ShaderResourceView> tops_srv;
    ComPtr<ID3D11Texture2D> street_z_tex;
    ComPtr<ID3D11ShaderResourceView> street_z_srv;
    ComPtr<ID3D11Texture2D> floor_z_tex;
    ComPtr<ID3D11ShaderResourceView> floor_z_srv;
    ComPtr<ID3D11Texture3D> noise_3d_tex;
    ComPtr<ID3D11ShaderResourceView> noise_3d_srv;

    FluidGridInfo sim_grid;
    FluidGridInfo map_grid;
    std::vector<uint8_t> cached_air_map;
    int current_fluid = 0;
    int current_pressure = 0;
    uint32_t steps = 0;
    bool initialized = false;
    bool fluid_valid = false;
    bool blend_valid = false;
    int64_t tick = 0;       // tick index of fluid[current]
    int64_t reset_tick = 0; // tick the current history was reset at
    int64_t anchor_tick = -1;
    bool re_anchored = false; // history was cut short by the catch-up bound
    ReplayFluidClock clock;

    bool make_surface(uint32_t w, uint32_t h, DXGI_FORMAT fmt, ComputeSurface& out) {
        D3D11_TEXTURE2D_DESC d{};
        d.Width = w;
        d.Height = h;
        d.MipLevels = 1;
        d.ArraySize = 1;
        d.Format = fmt;
        d.SampleDesc.Count = 1;
        d.Usage = D3D11_USAGE_DEFAULT;
        d.BindFlags = D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_UNORDERED_ACCESS;
        return SUCCEEDED(device->CreateTexture2D(&d, nullptr, &out.texture)) &&
               SUCCEEDED(device->CreateShaderResourceView(out.texture.Get(), nullptr, &out.srv)) &&
               SUCCEEDED(device->CreateUnorderedAccessView(out.texture.Get(), nullptr, &out.uav));
    }

    void dispatch(ID3D11DeviceContext* ctx, ShaderEntry entry, ID3D11ShaderResourceView* in0,
                  ID3D11ShaderResourceView* in1, ID3D11ShaderResourceView* in2,
                  ID3D11UnorderedAccessView* out0, ID3D11UnorderedAccessView* out1) {
        ID3D11ShaderResourceView* srvs[4] = {in0, in1, in2, air_map_srv.Get()};
        ID3D11UnorderedAccessView* uavs[2] = {out0, out1};
        ctx->CSSetShader(shaders[entry].Get(), nullptr, 0);
        ctx->CSSetShaderResources(0, 4, srvs);
        ctx->CSSetUnorderedAccessViews(0, 2, uavs, nullptr);
        ctx->Dispatch((sim_grid.width + 7u) / 8u, (sim_grid.height + 7u) / 8u, 1u);

        ID3D11ShaderResourceView* null_srvs[4] = {};
        ID3D11UnorderedAccessView* null_uavs[2] = {};
        ctx->CSSetShaderResources(0, 4, null_srvs);
        ctx->CSSetUnorderedAccessViews(0, 2, null_uavs, nullptr);
    }

    void fill_constants(WindStepConstants& cb, float dt, double sim_time,
                        const WindFluidParams& params, const FluidBody* bodies, uint32_t body_count,
                        const DirectionalEmitter* emitters, uint32_t emitter_count) const {
        std::memset(&cb, 0, sizeof(cb));
        cb.step_info[0] = dt;
        cb.step_info[1] = sim_grid.cell_units;
        cb.step_info[2] = static_cast<float>(std::fmod(std::max(0.0, sim_time), 10000.0));
        cb.step_info[3] = params.swirl_strength;

        const float wind_spd =
            std::sqrt(params.wind_x * params.wind_x + params.wind_y * params.wind_y);
        cb.wind_info[0] = params.wind_x;
        cb.wind_info[1] = params.wind_y;
        cb.wind_info[2] = 220.0f + 1.5f * wind_spd;
        cb.wind_info[3] = 1.0f - std::exp(-dt / std::max(params.wind_relax_seconds, 0.05f));

        cb.grid_info[0] = sim_grid.origin_x;
        cb.grid_info[1] = sim_grid.origin_y;
        cb.grid_info[2] = static_cast<float>(sim_grid.width);
        cb.grid_info[3] = static_cast<float>(sim_grid.height);

        const uint32_t clamped_bodies =
            (params.enable_hero_wakes && bodies) ? std::min(body_count, kMaxFluidBodies) : 0u;
        const uint32_t clamped_emitters =
            emitters ? std::min(emitter_count, kMaxFluidEmitters) : 0u;

        cb.body_info[0] = static_cast<float>(clamped_bodies);
        cb.body_info[1] = (params.enable_world_collision && air_map_srv) ? 1.0f : 0.0f;
        cb.body_info[2] = std::exp(-dt / std::max(params.carry_decay_seconds, 0.1f));
        cb.body_info[3] = static_cast<float>(clamped_emitters);

        cb.style_info[0] = std::max(0.0f, params.gust_strength);
        cb.style_info[1] = std::max(0.0f, params.intensity);
        cb.style_info[2] = map_grid.origin_x;
        cb.style_info[3] = map_grid.origin_y;

        cb.map_scale_info[0] = 1.0f / (static_cast<float>(map_grid.width) * map_grid.cell_units);
        cb.map_scale_info[1] = 1.0f / (static_cast<float>(map_grid.height) * map_grid.cell_units);

        for (uint32_t i = 0; i < clamped_bodies; ++i) {
            cb.bodies[i][0] = bodies[i].world_pos[0];
            cb.bodies[i][1] = bodies[i].world_pos[1];
            cb.bodies[i][2] = std::max(bodies[i].radius, sim_grid.cell_units * 1.25f);
            cb.bodies[i][3] = bodies[i].active;
            cb.body_velocity[i][0] = bodies[i].velocity[0];
            cb.body_velocity[i][1] = bodies[i].velocity[1];
        }

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
    }

    void run_one_step(ID3D11DeviceContext* ctx, int jacobi_iters) {
        ID3D11Buffer* cbs[1] = {constants.Get()};
        ID3D11SamplerState* samps[1] = {linear_clamp.Get()};
        ctx->CSSetConstantBuffers(0, 1, cbs);
        ctx->CSSetSamplers(0, 1, samps);

        auto swap_fluid = [&] { current_fluid = 1 - current_fluid; };
        auto in_f = [&] { return fluid[current_fluid].srv.Get(); };
        auto out_f = [&] { return fluid[1 - current_fluid].uav.Get(); };

        dispatch(ctx, kCurl, in_f(), nullptr, nullptr, nullptr, scalar.uav.Get());
        dispatch(ctx, kForce, in_f(), scalar.srv.Get(), nullptr, out_f(), nullptr);
        swap_fluid();
        dispatch(ctx, kAdvect, in_f(), nullptr, nullptr, out_f(), nullptr);
        swap_fluid();
        dispatch(ctx, kDivergence, in_f(), nullptr, nullptr, nullptr, scalar.uav.Get());

        const int iters = std::clamp(jacobi_iters, 4, 60);
        for (int j = 0; j < iters; ++j) {
            const int next_p = 1 - current_pressure;
            dispatch(ctx, kJacobi, nullptr, scalar.srv.Get(), pressure[current_pressure].srv.Get(),
                     nullptr, pressure[next_p].uav.Get());
            current_pressure = next_p;
        }

        dispatch(ctx, kProject, in_f(), nullptr, pressure[current_pressure].srv.Get(), out_f(),
                 nullptr);
        swap_fluid();
        ++steps;

        // Clean up CS state
        ID3D11Buffer* null_cb[1] = {};
        ID3D11SamplerState* null_samp[1] = {};
        ctx->CSSetShader(nullptr, nullptr, 0);
        ctx->CSSetConstantBuffers(0, 1, null_cb);
        ctx->CSSetSamplers(0, 1, null_samp);
    }
};

WindFluidSolverDX11::WindFluidSolverDX11() : impl_(std::make_unique<Impl>()) {}
WindFluidSolverDX11::~WindFluidSolverDX11() = default;

bool WindFluidSolverDX11::initialize(ID3D11Device* device, std::string* out_error) {
    shutdown();
    if (!device) {
        if (out_error)
            *out_error = "null D3D11 device";
        return false;
    }
    impl_->device = device;

    const UINT compile_flags = D3DCOMPILE_ENABLE_STRICTNESS | D3DCOMPILE_OPTIMIZATION_LEVEL3;
    for (int i = 0; i < kEntryCount; ++i) {
        ComPtr<ID3DBlob> code, err;
        const HRESULT hr =
            D3DCompile(kWindFluidHlsl, sizeof(kWindFluidHlsl) - 1, "dolly_wind_fluid.hlsl", nullptr,
                       nullptr, kEntryNames[i], "cs_5_0", compile_flags, 0, &code, &err);
        if (FAILED(hr) || !code) {
            if (out_error) {
                *out_error = std::string("failed compiling CS ") + kEntryNames[i] + ": " +
                             (err ? static_cast<const char*>(err->GetBufferPointer()) : "unknown");
            }
            shutdown();
            return false;
        }
        if (FAILED(device->CreateComputeShader(code->GetBufferPointer(), code->GetBufferSize(),
                                               nullptr, &impl_->shaders[i]))) {
            if (out_error)
                *out_error = std::string("CreateComputeShader failed for ") + kEntryNames[i];
            shutdown();
            return false;
        }
    }

    D3D11_BUFFER_DESC cb{};
    cb.ByteWidth = sizeof(WindStepConstants);
    cb.Usage = D3D11_USAGE_DEFAULT;
    cb.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
    if (FAILED(device->CreateBuffer(&cb, nullptr, &impl_->constants))) {
        if (out_error)
            *out_error = "CreateBuffer failed for WindStepConstants";
        shutdown();
        return false;
    }

    D3D11_SAMPLER_DESC sd{};
    sd.Filter = D3D11_FILTER_MIN_MAG_MIP_LINEAR;
    sd.AddressU = sd.AddressV = sd.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
    sd.MaxLOD = D3D11_FLOAT32_MAX;
    if (FAILED(device->CreateSamplerState(&sd, &impl_->linear_clamp))) {
        if (out_error)
            *out_error = "CreateSamplerState failed";
        shutdown();
        return false;
    }

    // Generate & upload 64^3 seamless Perlin-Worley 3D noise volume
    constexpr int kNoiseSize = 64;
    const std::vector<uint8_t> voxels = generate_gust_noise_volume(kNoiseSize);
    D3D11_TEXTURE3D_DESC td3{};
    td3.Width = td3.Height = td3.Depth = static_cast<UINT>(kNoiseSize);
    td3.MipLevels = 1;
    td3.Format = DXGI_FORMAT_R8_UNORM;
    td3.Usage = D3D11_USAGE_IMMUTABLE;
    td3.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    D3D11_SUBRESOURCE_DATA init3{};
    init3.pSysMem = voxels.data();
    init3.SysMemPitch = static_cast<UINT>(kNoiseSize);
    init3.SysMemSlicePitch = static_cast<UINT>(kNoiseSize * kNoiseSize);
    if (FAILED(device->CreateTexture3D(&td3, &init3, &impl_->noise_3d_tex)) ||
        FAILED(device->CreateShaderResourceView(impl_->noise_3d_tex.Get(), nullptr,
                                                &impl_->noise_3d_srv))) {
        if (out_error)
            *out_error = "CreateTexture3D failed for 64^3 Perlin-Worley noise";
        shutdown();
        return false;
    }

    impl_->initialized = true;
    return true;
}

void WindFluidSolverDX11::shutdown() {
    impl_ = std::make_unique<Impl>();
}

bool WindFluidSolverDX11::upload_map_grid(const map_grid::MapGrid& grid, uint32_t sim_resolution,
                                          std::string* out_error) {
    if (!impl_->initialized || !impl_->device) {
        if (out_error)
            *out_error = "solver not initialized";
        return false;
    }
    if (!grid.valid()) {
        if (out_error)
            *out_error = "invalid MapGrid";
        return false;
    }

    const uint32_t mw = grid.width;
    const uint32_t mh = grid.height;
    const uint32_t sw = (sim_resolution >= 32u) ? std::min(sim_resolution, 1024u) : mw;
    const uint32_t sh = (sim_resolution >= 32u) ? std::min(sim_resolution, 1024u) : mh;

    const auto upload_2d = [&](DXGI_FORMAT fmt, const void* src_data, UINT pitch,
                               ComPtr<ID3D11Texture2D>& tex,
                               ComPtr<ID3D11ShaderResourceView>& srv) -> bool {
        tex.Reset();
        srv.Reset();
        D3D11_TEXTURE2D_DESC d{};
        d.Width = mw;
        d.Height = mh;
        d.MipLevels = 1;
        d.ArraySize = 1;
        d.Format = fmt;
        d.SampleDesc.Count = 1;
        d.Usage = D3D11_USAGE_IMMUTABLE;
        d.BindFlags = D3D11_BIND_SHADER_RESOURCE;
        D3D11_SUBRESOURCE_DATA init{src_data, pitch, 0};
        return SUCCEEDED(impl_->device->CreateTexture2D(&d, &init, &tex)) &&
               SUCCEEDED(impl_->device->CreateShaderResourceView(tex.Get(), nullptr, &srv));
    };

    // Floor heights travel as 16-bit UNORM over the map's own floor range (about 0.1 u per
    // step on dl_midtown), halving the bytes the march reads per step. Code 0 = no floor.
    float floor_lo = 1e30f, floor_hi = -1e30f;
    for (std::size_t i = 0; i < grid.street_z.size(); ++i) {
        floor_lo = std::min(floor_lo, grid.street_z[i]);
        floor_hi = std::max(floor_hi, grid.street_z[i]);
        for (float f : grid.floor_z[i])
            if (f > map_grid::kNoFloorZ * 0.5f) {
                floor_lo = std::min(floor_lo, f);
                floor_hi = std::max(floor_hi, f);
            }
    }
    if (!(floor_lo <= floor_hi))
        floor_lo = floor_hi = 0.0f;
    const float floor_span = std::max(floor_hi - floor_lo, 1e-3f);
    std::vector<uint16_t> floors(grid.floor_z.size() * map_grid::kFloorLayers, 0);
    for (std::size_t i = 0; i < grid.floor_z.size(); ++i)
        for (int k = 0; k < map_grid::kFloorLayers; ++k) {
            const float f = grid.floor_z[i][k];
            if (f > map_grid::kNoFloorZ * 0.5f)
                floors[i * map_grid::kFloorLayers + k] = static_cast<uint16_t>(
                    1 +
                    std::lround(std::clamp((f - floor_lo) / floor_span, 0.0f, 1.0f) * 65534.0f));
        }

    if (!upload_2d(DXGI_FORMAT_R8_UNORM, grid.air_map.data(), mw, impl_->air_map_tex,
                   impl_->air_map_srv) ||
        !upload_2d(DXGI_FORMAT_R32_FLOAT, grid.tops.data(), mw * sizeof(float), impl_->tops_tex,
                   impl_->tops_srv) ||
        !upload_2d(DXGI_FORMAT_R32_FLOAT, grid.street_z.data(), mw * sizeof(float),
                   impl_->street_z_tex, impl_->street_z_srv) ||
        !upload_2d(DXGI_FORMAT_R16G16B16A16_UNORM, floors.data(),
                   mw * sizeof(uint16_t) * map_grid::kFloorLayers, impl_->floor_z_tex,
                   impl_->floor_z_srv)) {
        if (out_error)
            *out_error = "failed uploading MapGrid DX11 textures";
        return false;
    }

    for (auto& s : impl_->fluid)
        s.reset();
    for (auto& p : impl_->pressure)
        p.reset();
    impl_->scalar.reset();
    impl_->previous.reset();
    impl_->blended.reset();

    if (!impl_->make_surface(sw, sh, DXGI_FORMAT_R16G16B16A16_FLOAT, impl_->fluid[0]) ||
        !impl_->make_surface(sw, sh, DXGI_FORMAT_R16G16B16A16_FLOAT, impl_->fluid[1]) ||
        !impl_->make_surface(sw, sh, DXGI_FORMAT_R32_FLOAT, impl_->pressure[0]) ||
        !impl_->make_surface(sw, sh, DXGI_FORMAT_R32_FLOAT, impl_->pressure[1]) ||
        !impl_->make_surface(sw, sh, DXGI_FORMAT_R32_FLOAT, impl_->scalar) ||
        !impl_->make_surface(sw, sh, DXGI_FORMAT_R16G16B16A16_FLOAT, impl_->previous) ||
        !impl_->make_surface(sw, sh, DXGI_FORMAT_R16G16B16A16_FLOAT, impl_->blended)) {
        if (out_error)
            *out_error = "failed allocating fluid compute surfaces";
        return false;
    }

    impl_->map_grid = {mw, mh, grid.origin_x, grid.origin_y, grid.cell_units};
    impl_->map_grid.floor_min = floor_lo;
    impl_->map_grid.floor_max = floor_lo + floor_span;
    impl_->sim_grid = {sw, sh, grid.origin_x, grid.origin_y,
                       grid.span_units / static_cast<float>(sw)};
    impl_->cached_air_map = grid.air_map;
    impl_->current_fluid = 0;
    impl_->current_pressure = 0;
    impl_->steps = 0;
    impl_->fluid_valid = false;
    impl_->blend_valid = false;
    impl_->clock.reset();
    return true;
}

void WindFluidSolverDX11::reset_and_prewarm(ID3D11DeviceContext* ctx, double replay_time,
                                            const WindFluidParams& params,
                                            const DirectionalEmitter* emitters,
                                            uint32_t emitter_count, int prewarm_steps,
                                            float prewarm_seconds) {
    if (!ctx || !impl_->initialized || !impl_->sim_grid.valid())
        return;

    const float zero[4] = {0.0f, 0.0f, 0.0f, 0.0f};
    for (auto& s : impl_->fluid)
        ctx->ClearUnorderedAccessViewFloat(s.uav.Get(), zero);
    for (auto& p : impl_->pressure)
        ctx->ClearUnorderedAccessViewFloat(p.uav.Get(), zero);
    ctx->ClearUnorderedAccessViewFloat(impl_->scalar.uav.Get(), zero);
    impl_->current_fluid = 0;
    impl_->current_pressure = 0;
    impl_->fluid_valid = true;
    impl_->blend_valid = false;
    impl_->tick = static_cast<int64_t>(std::floor(replay_time * kTickRate + 1e-9));
    impl_->reset_tick = impl_->tick;

    const int n_steps = std::max(0, prewarm_steps);
    if (n_steps == 0 || prewarm_seconds <= 0.0f)
        return;

    const float dt = prewarm_seconds / static_cast<float>(n_steps);
    WindStepConstants cb{};
    for (int s = 0; s < n_steps; ++s) {
        const double t = replay_time - static_cast<double>(prewarm_seconds) +
                         static_cast<double>(s + 1) * static_cast<double>(dt);
        impl_->fill_constants(cb, dt, t, params, nullptr, 0, emitters, emitter_count);
        ctx->UpdateSubresource(impl_->constants.Get(), 0, nullptr, &cb, 0, 0);
        impl_->run_one_step(ctx, params.jacobi_iterations);
    }
}

void WindFluidSolverDX11::step_dt(ID3D11DeviceContext* ctx, float dt, double sim_time,
                                  const WindFluidParams& params, const FluidBody* bodies,
                                  uint32_t body_count, const DirectionalEmitter* emitters,
                                  uint32_t emitter_count) {
    if (!ctx || !impl_->initialized || !impl_->sim_grid.valid() || dt <= 0.0f)
        return;
    if (!impl_->fluid_valid) {
        const float zero[4] = {0.0f, 0.0f, 0.0f, 0.0f};
        for (auto& s : impl_->fluid)
            ctx->ClearUnorderedAccessViewFloat(s.uav.Get(), zero);
        for (auto& p : impl_->pressure)
            ctx->ClearUnorderedAccessViewFloat(p.uav.Get(), zero);
        impl_->fluid_valid = true;
    }

    WindStepConstants cb{};
    impl_->fill_constants(cb, dt, sim_time, params, bodies, body_count, emitters, emitter_count);
    ctx->UpdateSubresource(impl_->constants.Get(), 0, nullptr, &cb, 0, 0);
    impl_->run_one_step(ctx, params.jacobi_iterations);
}

int WindFluidSolverDX11::step_replay_time(ID3D11DeviceContext* ctx, double replay_time,
                                          const WindFluidParams& params, const FluidBody* bodies,
                                          uint32_t body_count, const DirectionalEmitter* emitters,
                                          uint32_t emitter_count, double anchor_time) {
    if (!ctx || !impl_->initialized || !impl_->sim_grid.valid() || !std::isfinite(replay_time))
        return 0;
    Impl& m = *impl_;
    const int64_t target = static_cast<int64_t>(std::floor(replay_time * kTickRate + 1e-9));
    const float frac = static_cast<float>(
        std::clamp(replay_time * kTickRate - static_cast<double>(target), 0.0, 1.0));
    // Canonical history: from the shot start (the anchor) a reset 2 s before it, so every path
    // to a time inside the shot is the same. Before the anchor the wind simply runs on, and
    // resets 2 s before the target only when it must (start, backward seek, long jump).
    const bool anchored = std::isfinite(anchor_time) && anchor_time >= 0.0;
    const int64_t anchor_tick =
        anchored ? static_cast<int64_t>(std::floor(anchor_time * kTickRate + 1e-9)) : -1;
    if (anchor_tick != m.anchor_tick) {
        m.anchor_tick = anchor_tick;
        m.re_anchored = false;
    }
    const bool canonical = anchored && target >= anchor_tick;
    int64_t reset = (canonical ? anchor_tick : target) - kPrewarmTicks;
    // fluid[current] holds tick m.tick and `previous` tick m.tick - 1; this frame needs the
    // states at target and target + 1.
    // Off the canonical path a forward jump of more than 1.5 s starts fresh, as a seek.
    const int64_t forward_limit = canonical ? kMaxCatchupTicks : 90;
    bool must_reset = !m.fluid_valid || target + 1 < m.tick ||
                      target + 1 - m.tick > forward_limit ||
                      (canonical && !m.re_anchored && m.reset_tick != reset);
    if (must_reset) {
        m.re_anchored = false;
        if (target + 1 - reset > kMaxCatchupTicks) {
            // Bounded cost for a jump deep into a long shot; not on the canonical path.
            reset = target - kPrewarmTicks;
            m.re_anchored = canonical;
        }
    }

    int stepped = 0;
    if (must_reset) {
        const float zero[4] = {0.0f, 0.0f, 0.0f, 0.0f};
        for (auto& f : m.fluid)
            ctx->ClearUnorderedAccessViewFloat(f.uav.Get(), zero);
        for (auto& pr : m.pressure)
            ctx->ClearUnorderedAccessViewFloat(pr.uav.Get(), zero);
        ctx->ClearUnorderedAccessViewFloat(m.scalar.uav.Get(), zero);
        m.current_fluid = 0;
        m.current_pressure = 0;
        m.fluid_valid = true;
        m.tick = reset;
        m.reset_tick = reset;
    }
    const float dt = static_cast<float>(1.0 / kTickRate);
    while (m.tick < target + 1) {
        if (m.tick == target)
            ctx->CopyResource(m.previous.texture.Get(), m.fluid[m.current_fluid].texture.Get());
        const double t = static_cast<double>(m.tick + 1) / kTickRate;
        // Heroes are known only at the frame's replay time, not at past ticks, so the
        // ticks never see them (re-simulation after a seek must give the same state).
        step_dt(ctx, dt, t, params, nullptr, 0, emitters, emitter_count);
        ++m.tick;
        ++stepped;
    }

    // Blend the two tick states for this exact replay time.
    WindStepConstants cb{};
    m.fill_constants(cb, dt, replay_time, params, bodies, body_count, nullptr, 0);
    cb.map_scale_info[2] = frac;
    ctx->UpdateSubresource(m.constants.Get(), 0, nullptr, &cb, 0, 0);
    ID3D11Buffer* cbs[1] = {m.constants.Get()};
    ctx->CSSetConstantBuffers(0, 1, cbs);
    ID3D11ShaderResourceView* next[1] = {m.fluid[m.current_fluid].srv.Get()};
    ctx->CSSetShaderResources(4, 1, next);
    m.dispatch(ctx, kBlend, m.previous.srv.Get(), nullptr, nullptr, m.blended.uav.Get(), nullptr);
    ID3D11ShaderResourceView* none[1] = {};
    ctx->CSSetShaderResources(4, 1, none);
    m.blend_valid = true;
    return stepped;
}

bool WindFluidSolverDX11::readback_diagnostics(ID3D11DeviceContext* ctx, FluidDiagnostics& out_diag,
                                               std::vector<uint8_t>* out_topdown_rgba,
                                               const FluidBody* bodies, uint32_t body_count,
                                               const DirectionalEmitter* emitters,
                                               uint32_t emitter_count) const {
    if (!ctx || !impl_->initialized || !impl_->fluid_valid || !impl_->sim_grid.valid())
        return false;

    const uint32_t w = impl_->sim_grid.width;
    const uint32_t h = impl_->sim_grid.height;

    D3D11_TEXTURE2D_DESC sd{};
    sd.Width = w;
    sd.Height = h;
    sd.MipLevels = 1;
    sd.ArraySize = 1;
    sd.Format = DXGI_FORMAT_R16G16B16A16_FLOAT;
    sd.SampleDesc.Count = 1;
    sd.Usage = D3D11_USAGE_STAGING;
    sd.CPUAccessFlags = D3D11_CPU_ACCESS_READ;

    ComPtr<ID3D11Texture2D> staging;
    if (FAILED(impl_->device->CreateTexture2D(&sd, nullptr, &staging)))
        return false;

    // What the renderers sample: the blended state (with hero wakes) once there is one.
    ctx->CopyResource(staging.Get(), impl_->blend_valid
                                         ? impl_->blended.texture.Get()
                                         : impl_->fluid[impl_->current_fluid].texture.Get());
    D3D11_MAPPED_SUBRESOURCE mapped{};
    if (FAILED(ctx->Map(staging.Get(), 0, D3D11_MAP_READ, 0, &mapped)))
        return false;

    std::vector<float> vx(static_cast<size_t>(w) * h);
    std::vector<float> vy(static_cast<size_t>(w) * h);
    std::vector<float> dens(static_cast<size_t>(w) * h);
    std::vector<uint8_t> solid(static_cast<size_t>(w) * h);

    out_diag = FluidDiagnostics{};
    out_diag.width = w;
    out_diag.height = h;
    double open_speed_sum = 0.0;
    double open_dens_sum = 0.0;
    uint32_t open_cells = 0;

    for (uint32_t y = 0; y < h; ++y) {
        const auto* row = reinterpret_cast<const uint16_t*>(
            static_cast<const uint8_t*>(mapped.pData) + static_cast<size_t>(y) * mapped.RowPitch);
        for (uint32_t x = 0; x < w; ++x) {
            const size_t idx = static_cast<size_t>(y) * w + x;
            const float u = half_to_float(row[x * 4 + 0]);
            const float v = half_to_float(row[x * 4 + 1]);
            const float d = half_to_float(row[x * 4 + 2]);
            const float s = half_to_float(row[x * 4 + 3]);

            if (!std::isfinite(u) || !std::isfinite(v) || !std::isfinite(d) || !std::isfinite(s)) {
                ++out_diag.non_finite_cells;
                continue;
            }
            vx[idx] = u;
            vy[idx] = v;
            dens[idx] = d;
            solid[idx] = (s > 0.5f) ? 1u : 0u;
            const float spd = std::sqrt(u * u + v * v);
            if (solid[idx]) {
                ++out_diag.solid_cells;
                out_diag.max_solid_speed = std::max(out_diag.max_solid_speed, spd);
            } else {
                ++open_cells;
                open_speed_sum += spd;
                open_dens_sum += d;
                out_diag.max_speed = std::max(out_diag.max_speed, spd);
                out_diag.max_density = std::max(out_diag.max_density, d);
            }
        }
    }
    ctx->Unmap(staging.Get(), 0);

    if (open_cells > 0) {
        out_diag.mean_open_speed = static_cast<float>(open_speed_sum / open_cells);
        out_diag.mean_open_density = static_cast<float>(open_dens_sum / open_cells);
    }

    // Compute RMS divergence in interior open cells (cells/s normalized by cell_units)
    double div_sq_sum = 0.0;
    uint32_t div_cnt = 0;
    const float inv_2h = 1.0f / (2.0f * impl_->sim_grid.cell_units);
    for (uint32_t y = 1; y + 1 < h; ++y) {
        for (uint32_t x = 1; x + 1 < w; ++x) {
            const size_t idx = static_cast<size_t>(y) * w + x;
            if (solid[idx])
                continue;
            const float dvx = vx[idx + 1] - vx[idx - 1];
            const float dvy = vy[idx + w] - vy[idx - w];
            const float div = (dvx + dvy) * inv_2h;
            div_sq_sum += static_cast<double>(div) * static_cast<double>(div);
            ++div_cnt;
        }
    }
    if (div_cnt > 0)
        out_diag.rms_open_divergence = static_cast<float>(std::sqrt(div_sq_sum / div_cnt));

    if (out_topdown_rgba) {
        out_topdown_rgba->assign(static_cast<size_t>(w) * h * 4, 255u);
        for (uint32_t y = 0; y < h; ++y) {
            const uint32_t img_y = h - 1 - y; // North up
            for (uint32_t x = 0; x < w; ++x) {
                const size_t idx = static_cast<size_t>(y) * w + x;
                const size_t o = (static_cast<size_t>(img_y) * w + x) * 4;
                if (solid[idx]) {
                    (*out_topdown_rgba)[o + 0] = 44;
                    (*out_topdown_rgba)[o + 1] = 48;
                    (*out_topdown_rgba)[o + 2] = 62;
                } else {
                    const float s_vis = std::clamp(dens[idx] / 1.4f, 0.0f, 1.0f);
                    const float spd = std::sqrt(vx[idx] * vx[idx] + vy[idx] * vy[idx]);
                    const float v_vis = std::clamp(spd / 900.0f, 0.0f, 1.0f);
                    (*out_topdown_rgba)[o + 0] = static_cast<uint8_t>(
                        std::clamp(20.0f + s_vis * 218.0f + v_vis * 32.0f, 0.0f, 255.0f));
                    (*out_topdown_rgba)[o + 1] = static_cast<uint8_t>(
                        std::clamp(24.0f + s_vis * 172.0f + v_vis * 48.0f, 0.0f, 255.0f));
                    (*out_topdown_rgba)[o + 2] = static_cast<uint8_t>(
                        std::clamp(34.0f + s_vis * 88.0f + v_vis * 96.0f, 0.0f, 255.0f));
                }
                (*out_topdown_rgba)[o + 3] = 255u;
            }
        }

        // Draw crisp markers for emitters (teal) and hero bodies (coral)
        const auto draw_dot = [&](float wx, float wy, int rad, uint8_t r, uint8_t g, uint8_t b) {
            const int gx = static_cast<int>(
                std::floor((wx - impl_->sim_grid.origin_x) / impl_->sim_grid.cell_units));
            const int gy = static_cast<int>(
                std::floor((wy - impl_->sim_grid.origin_y) / impl_->sim_grid.cell_units));
            const int py = static_cast<int>(h) - 1 - gy;
            for (int dy = -rad; dy <= rad; ++dy) {
                for (int dx = -rad; dx <= rad; ++dx) {
                    if (dx * dx + dy * dy > rad * rad)
                        continue;
                    const int px = gx + dx, qy = py + dy;
                    if (px < 0 || px >= static_cast<int>(w) || qy < 0 || qy >= static_cast<int>(h))
                        continue;
                    const size_t o = (static_cast<size_t>(qy) * w + static_cast<size_t>(px)) * 4;
                    (*out_topdown_rgba)[o + 0] = r;
                    (*out_topdown_rgba)[o + 1] = g;
                    (*out_topdown_rgba)[o + 2] = b;
                    (*out_topdown_rgba)[o + 3] = 255;
                }
            }
        };

        if (emitters) {
            for (uint32_t i = 0; i < std::min(emitter_count, kMaxFluidEmitters); ++i) {
                if (emitters[i].active_weight > 0.01f)
                    draw_dot(emitters[i].world_pos[0], emitters[i].world_pos[1], 4, 72, 232, 206);
            }
        }
        if (bodies) {
            for (uint32_t i = 0; i < std::min(body_count, kMaxFluidBodies); ++i) {
                if (bodies[i].active > 0.01f)
                    draw_dot(bodies[i].world_pos[0], bodies[i].world_pos[1], 3, 255, 108, 118);
            }
        }
    }
    return true;
}

bool WindFluidSolverDX11::ready() const noexcept {
    return impl_->initialized;
}
bool WindFluidSolverDX11::has_grid() const noexcept {
    return impl_->sim_grid.valid();
}
FluidGridInfo WindFluidSolverDX11::sim_grid_info() const noexcept {
    return impl_->sim_grid;
}
FluidGridInfo WindFluidSolverDX11::map_grid_info() const noexcept {
    return impl_->map_grid;
}
uint32_t WindFluidSolverDX11::total_steps() const noexcept {
    return impl_->steps;
}

ID3D11ShaderResourceView* WindFluidSolverDX11::fluid_srv() const noexcept {
    if (!impl_->fluid_valid)
        return nullptr;
    return impl_->blend_valid ? impl_->blended.srv.Get()
                              : impl_->fluid[impl_->current_fluid].srv.Get();
}
ID3D11ShaderResourceView* WindFluidSolverDX11::air_map_srv() const noexcept {
    return impl_->air_map_srv.Get();
}
ID3D11ShaderResourceView* WindFluidSolverDX11::tops_srv() const noexcept {
    return impl_->tops_srv.Get();
}
ID3D11ShaderResourceView* WindFluidSolverDX11::street_z_srv() const noexcept {
    return impl_->street_z_srv.Get();
}
ID3D11ShaderResourceView* WindFluidSolverDX11::floor_z_srv() const noexcept {
    return impl_->floor_z_srv.Get();
}
ID3D11ShaderResourceView* WindFluidSolverDX11::noise_3d_srv() const noexcept {
    return impl_->noise_3d_srv.Get();
}

} // namespace dolly::env
