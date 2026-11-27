#include "dolly_precip_dx11.hpp"

#define NOMINMAX
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3d11.h>
#include <d3dcompiler.h>
#include <wrl/client.h>

#include <algorithm>
#include <cmath>
#include <cstring>

namespace dolly::env {
using Microsoft::WRL::ComPtr;

namespace {

// Flake model. Each flake falls through world-anchored layers of height H; on
// crossing a layer boundary it reappears at a new random spot. Boundaries are
// offset per flake so no common plane of popping flakes is visible. Within a
// layer the flake drifts with the wind field from its spawn point, integrated
// through the current fluid state. The shown copy is the one inside a box that
// follows the camera, and alpha fades out toward the box faces.
//
// LOOK (a compile-time define, one shader pair per look) selects the particle look:
//   0  the first look. Its code is everything outside the `#if LOOK >= 1` blocks and
//      must stay exactly as it is, so its frames never move.
//   1  varied sizes (a few big flakes among many small), per-flake speed and opacity,
//      streaks that dim as they lengthen and taper to a point, particles that dissolve
//      into the haze with distance, soft out-of-focus flakes near the lens.
//   2  look 1 with most particles turned into fine near-round specks and fewer big ones.
// Rain is drawn the same in every look.
constexpr char kPrecipHlsl[] = R"HLSL(
static const uint KIND_SNOW = 1, KIND_RAIN = 2, KIND_SAND = 3, KIND_ASH = 4;

cbuffer PrecipConstants : register(b0) {
    row_major float4x4 viewProj;
    row_major float4x4 invViewProj;
    float4 cameraPos;    // xyz world, w 1 if reversed-Z
    float4 cameraRight;  // xyz, w kind
    float4 cameraUp;     // xyz, w seed (as uint bits)
    float4 screenInfo;   // x width, y height, z 1/width, w 1/height
    float4 gridWindow;   // xy origin, zw 1/span
    float4 timeWind;     // x time (s, wrapped), y 1 = camera from gpuView, zw steady wind
    float4 volume;       // x box size, y box height, z height ceiling, w sand life (s)
    float4 kindA;        // x fall speed, y speed jitter, z size, w streak exposure (s)
    float4 kindB;        // x wind follow, y sway (u), z opacity, w ember fraction
    float4 albedo;       // rgb, w near fade (u)
    float4 sunDir;
    float4 sunColor;
    float4 ambientColor;
    float4 lookA;        // x haze extinction (1/u), y exposure scale, z streak aspect cap, w fine share
    float4 lookFog;      // rgb haze albedo far particles dissolve into, w fine size (of kindA.z)
    float4 lookB;        // x fine opacity, y big opacity, z faintest streak, w opacity
    float4 qualityInfo;  // x 1 = high quality, y 1 = sceneColor bound for lighting
};

Texture2D<float> sceneDepth : register(t0);
Texture2D<float4> fluidState : register(t1); // xy velocity (u/s), z carried density, w solid
Texture2D<float> topsMap : register(t2);
Texture2D<float> streetZMap : register(t3);
struct ViewData {
    row_major float4x4 viewProj;    // without TAA jitter, for drawing
    row_major float4x4 invViewProj; // with the frame's jitter, matching its depth
    float4 cameraPos;               // xyz world, w 1 when a valid view was found
    float4 right;
    float4 up;
};
StructuredBuffer<ViewData> gpuView : register(t4);
Texture2D<float4> sceneColor : register(t5);

static float4x4 gViewProj, gInvViewProj;
static float3 gCamPos, gRight, gUp;

// The camera comes from the constant buffer, or (timeWind.y) from a view the GPU built
// for this exact frame. Returns false when that frame had no valid view.
bool LoadView() {
    if (timeWind.y > 0.5) {
        ViewData v = gpuView[0];
        gViewProj = v.viewProj;
        gInvViewProj = v.invViewProj;
        gCamPos = v.cameraPos.xyz;
        gRight = v.right.xyz;
        gUp = v.up.xyz;
        return v.cameraPos.w > 0.5;
    }
    gViewProj = viewProj;
    gInvViewProj = invViewProj;
    gCamPos = cameraPos.xyz;
    gRight = cameraRight.xyz;
    gUp = cameraUp.xyz;
    return true;
}
SamplerState linearClamp : register(s0);

uint Pcg(uint v) {
    uint state = v * 747796405u + 2891336453u;
    uint word = ((state >> ((state >> 28u) + 4u)) ^ state) * 277803737u;
    return (word >> 22u) ^ word;
}

float Rand(uint a, uint b, uint c) {
    return float(Pcg(a ^ Pcg(b ^ Pcg(c)))) * (1.0 / 4294967296.0);
}

float2 MapUV(float2 xy) { return (xy - gridWindow.xy) * gridWindow.zw; }
bool OnMap(float2 uv) { return all(uv >= 0.0) && all(uv <= 1.0); }

float LoadMap(Texture2D<float> tex, float2 uv) {
    uint w, h;
    tex.GetDimensions(w, h);
    int2 q = clamp(int2(floor(uv * float2(w, h))), int2(0, 0), int2(w - 1, h - 1));
    return tex.Load(int3(q, 0));
}

// Horizontal wind at a point: the fluid near the street, the steady wind higher up.
float2 WindAt(float3 p, out float carried) {
    float2 uv = MapUV(p.xy);
    carried = 0.3;
    if (!OnMap(uv)) return timeWind.zw;
    float4 f = fluidState.SampleLevel(linearClamp, uv, 0);
    carried = f.z;
    float street = streetZMap.SampleLevel(linearClamp, uv, 0);
    float high = saturate((p.z - street - volume.z) / 400.0);
    return lerp(f.xy, timeWind.zw, high);
}

float Wrap(float v, float center, float size) {
    float lo = center - 0.5 * size;
    return lo + (v - lo) - size * floor((v - lo) / size);
}

struct VSOut {
    float4 pos : SV_Position;
    float2 uv : TEXCOORD0;     // x along streak in flake radii, y across (-1..1)
    float3 shape : TEXCOORD1;  // x streak length in radii, y view distance, z alpha
    float3 color : COLOR0;
#if LOOK >= 1
    float2 fx : TEXCOORD2;     // x near-lens softness, y 1 for the styled (non-rain) shape
#endif
};

)HLSL"
                              R"HLSL(
VSOut PrecipVS(uint vid : SV_VertexID, uint iid : SV_InstanceID) {
    VSOut o;
    o.pos = float4(0.0, 0.0, -2.0, 1.0); // outside the clip volume
    o.uv = float2(0.0, 0.0);
    o.shape = float3(0.0, 0.0, 0.0);
    o.color = float3(0.0, 0.0, 0.0);
#if LOOK >= 1
    o.fx = float2(0.0, 0.0);
#endif
    if (!LoadView())
        return o;

    uint kind = uint(cameraRight.w + 0.5);
    uint seed = asuint(cameraUp.w);
    float B = volume.x, H = volume.y;
    float t = timeWind.x;
    float r0 = Rand(iid, seed, 11u), r1 = Rand(iid, seed, 23u);
    float r2 = Rand(iid, seed, 37u), r3 = Rand(iid, seed, 53u);
#if LOOK >= 1
    bool isSand = kind == KIND_SAND;
    bool styled = kind != KIND_RAIN;
    float r4 = Rand(iid, seed, 71u), r5 = Rand(iid, seed, 89u);
    float r6 = Rand(iid, seed, 149u), r7 = Rand(iid, seed, 163u);
    float sizeMul = lerp(0.7, 1.3, r3), speedMul = 1.0, fallMul = 1.0, alphaMul = 1.0;
    float sandVz = 0.0;
    bool fine = false;
    if (styled) {
        // Sizes: the sum of three hashes is close to a bell curve, so its exponential is a
        // log-normal spread: most flakes small, a few several times larger.
        float g = clamp((r3 + r4 + Rand(iid, seed, 97u)) * 2.0 - 3.0, -2.0, 2.2);
        sizeMul = exp2(0.75 * g - 0.25);
        alphaMul = lookB.w * lerp(0.55, 1.0, Rand(iid, seed, 131u));
#if LOOK >= 2
        fine = Rand(iid, seed, 113u) < lookA.w;
        if (fine) {
            sizeMul = lookFog.w * lerp(isSand ? 0.55 : 0.7, isSand ? 1.45 : 1.4, r4);
            alphaMul *= lookB.x;
        } else {
            if (isSand) sizeMul *= lerp(0.75, 1.35, r6);
            alphaMul *= lookB.y;
        }
#endif
        speedMul = lerp(0.72, 1.28, r5);
        fallMul = lerp(0.8, 1.25, saturate((sizeMul - 0.3) / 1.7)); // big flakes fall faster
    }
#endif

    float3 p;
    float age, lifeFrac, fall = 0.0;
    uint cycle;
    if (kind == KIND_SAND) {
        float life = volume.w * lerp(0.7, 1.3, r0);
        float u = t / life + r1;
        cycle = uint(floor(u));
        lifeFrac = frac(u);
        age = lifeFrac * life;
        p = float3(0.0, 0.0, -1.0e5); // below any roof: drift with the street-level fluid
    } else {
        fall = kindA.x * lerp(1.0 - kindA.y, 1.0 + kindA.y, r0);
#if LOOK >= 1
        fall *= fallMul;
#endif
        float offset = r1 * H;
        float z = Wrap(r2 * H * 64.0 - t * fall, gCamPos.z, H);
        float layer = (z - offset) / H;
        cycle = uint(int(floor(layer)) + 1048576);
        lifeFrac = 1.0 - frac(layer);
        age = lifeFrac * H / fall;
        p = float3(0.0, 0.0, z);
    }

    // World-anchored spawn for this cycle, then drift through the current wind field.
    float2 spawn = float2(Rand(iid, cycle, seed ^ 0x68E31DA4u), Rand(iid, cycle, seed ^ 0xB5297A4Du)) * B;
    p.xy = float2(Wrap(spawn.x, gCamPos.x, B), Wrap(spawn.y, gCamPos.y, B));
    float carried = 0.3;
    float2 vel = float2(0.0, 0.0);
    float sz = p.z;
    const int STEPS = 4;
    float dt = age / STEPS;
    [unroll] for (int s = 0; s < STEPS; ++s) {
        if (kind != KIND_SAND) p.z = sz + fall * (age - dt * (s + 0.5));
        vel = WindAt(p, carried) * kindB.x;
#if LOOK >= 1
        vel *= speedMul;
#endif
        p.xy += vel * dt;
    }
    p.z = sz;
    float sway = kindB.y * sin(t * lerp(0.6, 1.4, r3) * 2.4 + r2 * 6.2831);
#if LOOK >= 1
    if (isSand) sway *= 0.45;
#endif
    p.xy += float2(sway, sway * 0.6 * (r0 - 0.5));
#if LOOK >= 1
    if (styled) {
        // The streak follows the flutter too, so neighbours are not combed parallel.
        float swayRate = lerp(0.6, 1.4, r3) * 2.4;
        float swayVel = kindB.y * (isSand ? 0.45 : 1.0) * swayRate * cos(t * swayRate + r2 * 6.2831);
        vel += float2(swayVel, swayVel * 0.6 * (r0 - 0.5)) * (isSand ? 0.35 : 0.75);
    }
#endif
    p.xy = float2(Wrap(p.x, gCamPos.x, B), Wrap(p.y, gCamPos.y, B));

    float alpha = kindB.z;
#if LOOK >= 1
    alpha *= alphaMul;
#endif
    float2 uv = MapUV(p.xy);
    if (kind == KIND_SAND) {
#if LOOK >= 1
        // Saltation near the street plus suspended wind-driven grains in the camera band.
        sandVz = volume.z * 0.11 * cos(t * 2.8 + r3 * 6.2831) + (r7 - 0.5) * 26.0;
        float street = OnMap(uv) ? streetZMap.SampleLevel(linearClamp, uv, 0) : gCamPos.z - 64.0;
        if (r5 < 0.45) {
            float bandH = min(H * 0.65, 900.0);
            p.z = Wrap(r2 * bandH * 32.0 + t * 8.0 * (r7 - 0.5), gCamPos.z, bandH);
            if (p.z < street + 4.0) alpha = 0.0;
            if (OnMap(uv) && LoadMap(topsMap, uv) > p.z + 8.0) alpha = 0.0;
            alpha *= 1.0 - smoothstep(volume.z * 0.9, volume.z * 3.4, p.z - street);
            alpha *= 1.0 - smoothstep(0.36 * bandH, 0.5 * bandH, abs(p.z - gCamPos.z));
        } else {
            float heightPow = fine ? pow(max(r2, 1e-4), 1.35) : pow(max(r2, 1e-4), 1.65);
            p.z = street + 4.0 + volume.z * (heightPow * 1.15 + 0.05 * sin(t * 2.8 + r3 * 6.2831) + 0.04);
        }
        alpha *= sin(3.14159265 * lifeFrac) * saturate(0.45 + carried * 0.75);
#else
        float street = OnMap(uv) ? streetZMap.SampleLevel(linearClamp, uv, 0) : gCamPos.z - 64.0;
        p.z = street + 4.0 + volume.z * (r2 * r2 * 0.85 + 0.1 * sin(t * 3.1 + r3 * 6.2831) + 0.05);
        alpha *= sin(3.14159265 * lifeFrac) * saturate(0.2 + carried * 0.9);
#endif
        if (OnMap(uv) && fluidState.SampleLevel(linearClamp, uv, 0).w > 0.65) alpha = 0.0;
    } else {
        // Sheltered under a roof or deck, or already landed: solid lies above the flake.
        if (OnMap(uv) && LoadMap(topsMap, uv) > p.z + 8.0) alpha = 0.0;
        alpha *= smoothstep(0.0, 0.08, lifeFrac) * smoothstep(1.0, 0.9, lifeFrac);
        alpha *= 1.0 - smoothstep(0.36 * H, 0.5 * H, abs(p.z - gCamPos.z));
    }
    float3 rel = p - gCamPos;
    alpha *= 1.0 - smoothstep(0.36 * B, 0.5 * B, max(abs(rel.x), abs(rel.y)));
    float dist = length(rel);
    alpha *= smoothstep(albedo.w, albedo.w * 3.0, dist);
    if (alpha <= 0.002) return o;

    // Motion-stretched capsule along the flake's velocity.
#if LOOK >= 1
    float3 v3 = float3(vel, kind == KIND_SAND ? sandVz : -fall);
    float radius = kindA.z * sizeMul;
    float fogT = 1.0, nearSoft = 0.0, exposure = kindA.w;
    float4 centre = float4(0.0, 0.0, 0.0, 1.0);
    if (styled) {
        centre = mul(gViewProj, float4(p, 1.0));
        if (centre.w <= 1.0) return o; // behind the lens
        // Far particles dissolve into the haze; near the lens they go large, soft and faint.
        fogT = exp(-dist * lookA.x);
        nearSoft = 1.0 - smoothstep(albedo.w * 1.5, albedo.w * 5.0, dist);
        radius *= 1.0 + 0.6 * nearSoft;
        alpha *= fogT * lerp(1.0, 0.55, nearSoft);
        // Never thinner than about a pixel and a half across: a smaller particle keeps that
        // footprint and fades instead, so fine grain does not sparkle/alias.
        float pxPerUnit = abs(mul(gViewProj, float4(gUp, 0.0)).y) / centre.w * 0.5 * screenInfo.y;
        float minRadius = 0.72 / max(pxPerUnit, 1e-4);
        if (radius < minRadius) {
            float q = radius / minRadius;
            if (isSand) alpha *= max(pow(q, 1.15), 0.22);
            else alpha *= q * q;
            radius = minRadius;
        }
        if (isSand) {
            // Blowing sand streaks along the wind even for fine grains.
            float windBoost = lerp(0.65, 1.25, saturate(length(vel) / 360.0));
            exposure *= lookA.y * (fine ? 0.78 : 1.05) * windBoost;
        } else {
            exposure *= lookA.y * (fine ? 0.3 : 1.0);
        }
    }
    float3 tail = -v3 * exposure;
    float len = length(tail);
    float3 axis = len > 1e-3 ? tail / len : -gUp;
    if (styled) {
        // A streak spreads the flake's light over its length: the longer, the fainter.
        len = min(len, radius * lookA.z);
        alpha *= max(sqrt(2.0 * radius / (len + 2.0 * radius)), lookB.z);
        if (alpha <= 0.002) return o;
    }
#else
    float3 v3 = float3(vel, kind == KIND_SAND ? 0.0 : -fall);
    float radius = kindA.z * lerp(0.7, 1.3, r3);
    float3 tail = -v3 * kindA.w;
    float len = length(tail);
    float3 axis = len > 1e-3 ? tail / len : -gUp;
#endif
    float3 toCam = normalize(gCamPos - p);
    float3 side = cross(axis, toCam);
    side = dot(side, side) > 1e-6 ? normalize(side) : gRight;

    static const float2 corners[6] = {float2(0, -1), float2(1, -1), float2(1, 1),
                                      float2(0, -1), float2(1, 1),  float2(0, 1)};
    float2 c = corners[vid];
    float extent = len + 2.0 * radius;
    float3 world = p - axis * radius + axis * (c.x * extent) + side * (c.y * radius);
    o.pos = mul(gViewProj, float4(world, 1.0));
    o.uv = float2(c.x * extent / radius, c.y);
    o.shape = float3(len / radius, dist, alpha);
#if LOOK >= 1
    o.fx = float2(nearSoft, styled ? 1.0 : 0.0);
#endif

    float3 sun = normalize(sunDir.xyz);
    float3 light = ambientColor.rgb + sunColor.rgb * (0.35 + 0.65 * saturate(sun.z));
    float3 col = albedo.rgb * light;
#if LOOK >= 1
    if (styled) {
        float3 veil = lookFog.rgb * light;
        if (isSand) {
            // Mineral grain colour and brightness variation: pale quartz, golden sand, darker silt.
            float3 paleQuartz = float3(1.16, 1.04, 0.84);
            float3 goldenSand = float3(0.96, 0.76, 0.48);
            float3 darkSilt   = float3(0.58, 0.44, 0.30);
            float3 grainAlb = r6 < 0.5 ? lerp(darkSilt, goldenSand, r6 * 2.0)
                                       : lerp(goldenSand, paleQuartz, (r6 - 0.5) * 2.0);
            float glint = (r6 > 0.75 ? 1.0 : 0.0) * pow(saturate(dot(-toCam, sun)), 8.0) * 0.65;
            col = grainAlb * light * lerp(0.72, 1.30, r7) + sunColor.rgb * glint;
            if (fine) col = lerp(col, veil, 0.10);
        } else {
            col *= lerp(0.84, 1.08, r4); // grain-to-grain tone
            if (fine) col = lerp(col, veil, 0.12);
        }
        col = lerp(col, veil, (1.0 - fogT) * (isSand ? 0.42 : 0.6));
    }
#endif
    if (kind == KIND_ASH && r1 > 1.0 - kindB.w) col = float3(1.6, 0.55, 0.16) * lerp(0.6, 1.2, r2);
    o.color = col;
    return o;
}

)HLSL"
                              R"HLSL(
float4 PrecipPS(VSOut i) : SV_Target {
    LoadView();
    // Distance to the streak's core segment, in flake radii.
    float x = i.uv.x - 1.0;
    float along = clamp(x, 0.0, i.shape.x);
    float d = length(float2(x - along, i.uv.y));
#if LOOK >= 1
    float a;
    if (i.fx.y > 0.5) {
        // A soft head and a tail that narrows and fades to nothing. Both terms are smooth as
        // the streak shortens to a round flake.
        float narrow = lerp(1.0, 0.45, saturate(along / max(i.shape.x, 0.5)));
        float dd = length(float2(x - along, i.uv.y / narrow));
        float fade = pow(saturate(1.0 - along / (i.shape.x + 0.75)), 1.5);
        a = min(i.shape.z, 1.0) * (1.0 - smoothstep(lerp(0.3, 0.0, i.fx.x), 1.0, dd)) * fade;
    } else {
        a = i.shape.z * (1.0 - smoothstep(0.35, 1.0, d));
        a *= lerp(1.0, 0.35, saturate(along / max(i.shape.x, 1e-3)));
    }
#else
    float a = i.shape.z * (1.0 - smoothstep(0.35, 1.0, d));
    // Thin the tail so long streaks read as motion, not as rods.
    a *= lerp(1.0, 0.35, saturate(along / max(i.shape.x, 1e-3)));
#endif

    int2 px = clamp(int2(i.pos.xy), int2(0, 0), int2(screenInfo.xy) - 1);
    uint dw, dh;
    sceneDepth.GetDimensions(dw, dh);
    int2 dq = clamp(int2((float2(px) + 0.5) * float2(dw, dh) * screenInfo.zw), int2(0, 0),
                    int2(dw, dh) - 1);
    float depth = sceneDepth.Load(int3(dq, 0));
    bool sky = cameraPos.w > 0.5 ? (depth <= 1e-6) : (depth >= 1.0 - 1e-6);
    if (!sky) {
        float2 suv = (float2(px) + 0.5) * screenInfo.zw;
        float4 w = mul(gInvViewProj, float4(suv.x * 2.0 - 1.0, 1.0 - suv.y * 2.0, depth, 1.0));
        float sceneDist = length(w.xyz / max(abs(w.w), 1e-6) * (w.w < 0.0 ? -1.0 : 1.0) - gCamPos);
        a *= saturate((sceneDist - i.shape.y) / 24.0);
    }
    if (a <= 0.002) discard;
    return float4(i.color, a);
}
)HLSL";

struct PrecipConstants {
    float view_proj[16];
    float inv_view_proj[16];
    float camera_pos[4];
    float camera_right[4];
    float camera_up[4];
    float screen_info[4];
    float grid_window[4];
    float time_wind[4];
    float volume[4];
    float kind_a[4];
    float kind_b[4];
    float albedo[4];
    float sun_dir[4];
    float sun_color[4];
    float ambient_color[4];
    float look_a[4];
    float look_fog[4];
    float look_b[4];
    float quality_info[4];
};
static_assert(sizeof(PrecipConstants) % 16 == 0, "PrecipConstants must be 16-byte aligned");

struct KindStyle {
    float base_flakes;
    float box_size, box_height, sand_life;
    float fall, jitter, size, exposure;
    float follow, sway, opacity, ember;
    float albedo[3];
    float near_fade;
};

KindStyle kind_style(PrecipitationKind kind) noexcept {
    switch (kind) {
    case PrecipitationKind::Snow:
        return {28000.0f,
                1800.0f,
                1400.0f,
                0.0f,
                95.0f,
                0.35f,
                2.6f,
                0.03f,
                0.95f,
                14.0f,
                0.85f,
                0.0f,
                {0.95f, 0.96f, 0.98f},
                40.0f};
    case PrecipitationKind::Rain:
        return {26000.0f,
                1800.0f,
                1600.0f,
                0.0f,
                1050.0f,
                0.15f,
                0.55f,
                0.045f,
                0.55f,
                0.0f,
                0.32f,
                0.0f,
                {0.70f, 0.74f, 0.80f},
                70.0f};
    case PrecipitationKind::Sand:
        return {22000.0f,
                1800.0f,
                1400.0f,
                2.2f,
                0.0f,
                0.0f,
                1.1f,
                0.035f,
                1.0f,
                22.0f,
                0.6f,
                0.0f,
                {0.86f, 0.70f, 0.48f},
                16.0f};
    case PrecipitationKind::Ash:
        return {24000.0f,
                1800.0f,
                1400.0f,
                0.0f,
                60.0f,
                0.4f,
                2.0f,
                0.02f,
                0.9f,
                18.0f,
                0.8f,
                0.06f,
                {0.46f, 0.43f, 0.41f},
                30.0f};
    default:
        return {};
    }
}

// What looks 1 and 2 change per kind. Look 0 reads none of it.
struct LookStyle {
    float count;      // flakes, times the look-0 count
    float exposure;   // streak exposure, times the kind's
    float aspect;     // longest streak, in flake radii
    float extinction; // 1/u: how fast particles dissolve into the haze with distance
    float fog[3];     // the haze albedo they dissolve into
    float fine_share; // look 2: share of particles that are fine specks
    float fine_size;  // their radius, times the kind's size
    float fine_alpha, big_alpha;
    float blur_floor; // a streak never dims below this share of its flake's opacity
    float opacity;    // times the kind's opacity
    float size;       // times the kind's size
};

LookStyle look_style(PrecipitationKind kind, uint32_t look, float rate,
                     bool high_quality = false) noexcept {
    LookStyle s{1.0f, 1.0f, 64.0f, 0.0f, {0.0f, 0.0f, 0.0f}, 0.0f, 1.0f, 1.0f, 1.0f, 1.0f, 1.0f, 1.0f};
    if (look == 0)
        return s;
    const bool two = look >= 2;
    switch (kind) {
    case PrecipitationKind::Snow:
        // A heavier fall has a thicker veil behind it.
        s = {two ? 3.4f : 1.8f, 0.9f, 5.6f, 0.00028f + 0.00014f * std::clamp(rate, 0.0f, 2.0f),
             {0.80f, 0.84f, 0.90f}, 0.70f, 0.45f, 0.98f, 1.0f, 0.65f, 1.8f, 1.15f};
        break;
    case PrecipitationKind::Sand:
        // Readable, varied, wind-streaked sand grains; High adds a finer micro-silt layer.
        s = {(two ? 5.0f : 2.4f) * (high_quality ? 1.3f : 1.0f), 1.25f, 11.5f, 0.00038f,
             {0.76f, 0.62f, 0.42f}, high_quality ? 0.74f : 0.68f, 0.58f, 0.92f, 0.96f, 0.66f,
             high_quality ? 3.9f : 3.6f, 1.18f};
        break;
    case PrecipitationKind::Ash:
        s = {two ? 3.1f : 1.7f, 0.9f, 4.0f, 0.00045f,
             {0.36f, 0.35f, 0.36f}, 0.65f, 0.48f, 0.98f, 1.0f, 0.65f, 1.7f, 1.15f};
        break;
    default:
        break;
    }
    return s;
}

bool compile(const char* entry, const char* target, uint32_t look, ComPtr<ID3DBlob>& code,
             std::string* out_error) {
    ComPtr<ID3DBlob> err;
    const UINT flags = D3DCOMPILE_ENABLE_STRICTNESS | D3DCOMPILE_OPTIMIZATION_LEVEL3;
    const char look_text[2] = {static_cast<char>('0' + look), '\0'};
    const D3D_SHADER_MACRO macros[] = {{"LOOK", look_text}, {nullptr, nullptr}};
    if (SUCCEEDED(D3DCompile(kPrecipHlsl, sizeof(kPrecipHlsl) - 1, "dolly_precip.hlsl", macros,
                             nullptr, entry, target, flags, 0, &code, &err)))
        return true;
    if (out_error)
        *out_error = std::string(entry) + " compile failed: " +
                     (err ? static_cast<const char*>(err->GetBufferPointer()) : "unknown");
    return false;
}

void unproject(const float* m, float x, float y, float z, float out[3]) noexcept {
    const float rx = m[0] * x + m[1] * y + m[2] * z + m[3];
    const float ry = m[4] * x + m[5] * y + m[6] * z + m[7];
    const float rz = m[8] * x + m[9] * y + m[10] * z + m[11];
    const float rw = m[12] * x + m[13] * y + m[14] * z + m[15];
    out[0] = rx / rw;
    out[1] = ry / rw;
    out[2] = rz / rw;
}

void normalized_delta(const float a[3], const float b[3], float out[4]) noexcept {
    const float d[3] = {b[0] - a[0], b[1] - a[1], b[2] - a[2]};
    const float l = std::sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]);
    for (int i = 0; i < 3; ++i)
        out[i] = l > 1e-9f ? d[i] / l : 0.0f;
}

} // namespace

uint32_t precipitation_flake_count(const PrecipitationParams& params) noexcept {
    if (params.kind == PrecipitationKind::None || !(params.rate > 0.0f))
        return 0;
    const double n = static_cast<double>(kind_style(params.kind).base_flakes) * params.rate;
    const uint32_t look = std::min(params.look, kPrecipitationLooks - 1);
    if (look == 0)
        return static_cast<uint32_t>(std::min<double>(n, kMaxPrecipitationFlakes));
    return static_cast<uint32_t>(std::min<double>(
        n * look_style(params.kind, look, params.rate, params.high_quality).count,
        kMaxPrecipitationFlakesStyled));
}

struct PrecipitationRendererDX11::Impl {
    ComPtr<ID3D11VertexShader> vs[kPrecipitationLooks];
    ComPtr<ID3D11PixelShader> ps[kPrecipitationLooks];
    ComPtr<ID3D11Buffer> constants;
    ComPtr<ID3D11SamplerState> linear_clamp;
    ComPtr<ID3D11RasterizerState> rs_state;
    ComPtr<ID3D11DepthStencilState> ds_state;
    ComPtr<ID3D11BlendState> blend_state;
    bool initialized = false;
};

PrecipitationRendererDX11::PrecipitationRendererDX11() : impl_(std::make_unique<Impl>()) {}
PrecipitationRendererDX11::~PrecipitationRendererDX11() = default;

bool PrecipitationRendererDX11::initialize(ID3D11Device* device, std::string* out_error) {
    shutdown();
    if (!device) {
        if (out_error)
            *out_error = "null D3D11 device";
        return false;
    }
    bool ok = true;
    for (uint32_t look = 0; look < kPrecipitationLooks; ++look) {
        ComPtr<ID3DBlob> vs_code, ps_code;
        if (!compile("PrecipVS", "vs_5_0", look, vs_code, out_error) ||
            !compile("PrecipPS", "ps_5_0", look, ps_code, out_error))
            return false;
        ok = ok &&
             SUCCEEDED(device->CreateVertexShader(vs_code->GetBufferPointer(),
                                                  vs_code->GetBufferSize(), nullptr,
                                                  &impl_->vs[look])) &&
             SUCCEEDED(device->CreatePixelShader(ps_code->GetBufferPointer(),
                                                 ps_code->GetBufferSize(), nullptr,
                                                 &impl_->ps[look]));
    }

    D3D11_BUFFER_DESC cb{};
    cb.ByteWidth = sizeof(PrecipConstants);
    cb.Usage = D3D11_USAGE_DEFAULT;
    cb.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
    ok = ok && SUCCEEDED(device->CreateBuffer(&cb, nullptr, &impl_->constants));

    D3D11_SAMPLER_DESC sd{};
    sd.Filter = D3D11_FILTER_MIN_MAG_MIP_LINEAR;
    sd.AddressU = sd.AddressV = sd.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
    sd.MaxLOD = D3D11_FLOAT32_MAX;
    ok = ok && SUCCEEDED(device->CreateSamplerState(&sd, &impl_->linear_clamp));

    D3D11_RASTERIZER_DESC rd{};
    rd.FillMode = D3D11_FILL_SOLID;
    rd.CullMode = D3D11_CULL_NONE;
    rd.DepthClipEnable = TRUE;
    ok = ok && SUCCEEDED(device->CreateRasterizerState(&rd, &impl_->rs_state));

    D3D11_DEPTH_STENCIL_DESC dd{};
    dd.DepthEnable = FALSE;
    dd.DepthWriteMask = D3D11_DEPTH_WRITE_MASK_ZERO;
    ok = ok && SUCCEEDED(device->CreateDepthStencilState(&dd, &impl_->ds_state));

    D3D11_BLEND_DESC bd{};
    auto& rt = bd.RenderTarget[0];
    rt.BlendEnable = TRUE;
    rt.SrcBlend = D3D11_BLEND_SRC_ALPHA;
    rt.DestBlend = D3D11_BLEND_INV_SRC_ALPHA;
    rt.BlendOp = D3D11_BLEND_OP_ADD;
    rt.SrcBlendAlpha = D3D11_BLEND_ZERO;
    rt.DestBlendAlpha = D3D11_BLEND_ONE;
    rt.BlendOpAlpha = D3D11_BLEND_OP_ADD;
    rt.RenderTargetWriteMask = D3D11_COLOR_WRITE_ENABLE_ALL;
    ok = ok && SUCCEEDED(device->CreateBlendState(&bd, &impl_->blend_state));

    if (!ok) {
        if (out_error)
            *out_error = "failed to create precipitation pipeline objects";
        shutdown();
        return false;
    }
    impl_->initialized = true;
    return true;
}

void PrecipitationRendererDX11::shutdown() {
    impl_ = std::make_unique<Impl>();
}

bool PrecipitationRendererDX11::ready() const noexcept {
    return impl_->initialized;
}

bool PrecipitationRendererDX11::render(ID3D11DeviceContext* ctx,
                                       ID3D11ShaderResourceView* scene_depth_srv,
                                       ID3D11RenderTargetView* out_rtv, const EnvCamera& camera,
                                       const PrecipitationParams& params,
                                       const WindFluidSolverDX11& fluid,
                                       ID3D11ShaderResourceView* scene_color_srv) {
    if (!ctx || !impl_->initialized || !scene_depth_srv || !out_rtv || camera.width == 0 ||
        camera.height == 0 || !fluid.has_grid() || !fluid.fluid_srv())
        return false;
    const uint32_t flakes = precipitation_flake_count(params);
    if (flakes == 0)
        return true;

    const KindStyle k = kind_style(params.kind);
    const uint32_t look = std::min(params.look, kPrecipitationLooks - 1);
    const LookStyle ls = look_style(params.kind, look, params.rate, params.high_quality);
    const FluidGridInfo mg = fluid.map_grid_info();
    PrecipConstants cb{};
    std::memcpy(cb.view_proj, camera.view_proj, sizeof(cb.view_proj));
    std::memcpy(cb.inv_view_proj, camera.inv_view_proj, sizeof(cb.inv_view_proj));
    cb.camera_pos[0] = camera.position[0];
    cb.camera_pos[1] = camera.position[1];
    cb.camera_pos[2] = camera.position[2];
    cb.camera_pos[3] = camera.reversed_z ? 1.0f : 0.0f;

    // Camera basis from the inverse view-projection, so it matches the projection exactly.
    float c0[3], cx[3], cy[3];
    // With a GPU-built view the shader derives its own basis; the CPU matrices are unset.
    if (!camera.gpu_view) {
        unproject(camera.inv_view_proj, 0.0f, 0.0f, 0.5f, c0);
        unproject(camera.inv_view_proj, 1.0f, 0.0f, 0.5f, cx);
        unproject(camera.inv_view_proj, 0.0f, 1.0f, 0.5f, cy);
        normalized_delta(c0, cx, cb.camera_right);
        normalized_delta(c0, cy, cb.camera_up);
    }
    cb.camera_right[3] = static_cast<float>(static_cast<uint32_t>(params.kind));
    std::memcpy(&cb.camera_up[3], &params.seed, sizeof(float));

    cb.screen_info[0] = static_cast<float>(camera.width);
    cb.screen_info[1] = static_cast<float>(camera.height);
    cb.screen_info[2] = 1.0f / static_cast<float>(camera.width);
    cb.screen_info[3] = 1.0f / static_cast<float>(camera.height);
    cb.grid_window[0] = mg.origin_x;
    cb.grid_window[1] = mg.origin_y;
    cb.grid_window[2] = 1.0f / (static_cast<float>(mg.width) * mg.cell_units);
    cb.grid_window[3] = 1.0f / (static_cast<float>(mg.height) * mg.cell_units);

    // The shader works in float, so time is wrapped to keep flake motion precise. The
    // wrap (about 2.3 hours) is longer than any replay, so its jump is never reached.
    cb.time_wind[0] = static_cast<float>(std::fmod(std::max(params.time, 0.0), 8192.0));
    cb.time_wind[1] = camera.gpu_view ? 1.0f : 0.0f;
    cb.time_wind[2] = params.wind_xy[0];
    cb.time_wind[3] = params.wind_xy[1];
    cb.volume[0] = k.box_size;
    cb.volume[1] = k.box_height;
    cb.volume[2] = std::max(params.height_ceiling, 24.0f);
    cb.volume[3] = k.sand_life;
    cb.kind_a[0] = k.fall;
    cb.kind_a[1] = k.jitter;
    cb.kind_a[2] = k.size * std::clamp(params.size_scale, 0.25f, 3.0f) * ls.size;
    cb.kind_a[3] = k.exposure;
    cb.kind_b[0] = k.follow;
    cb.kind_b[1] = k.sway;
    cb.kind_b[2] = k.opacity;
    cb.kind_b[3] = k.ember;
    cb.albedo[0] = k.albedo[0];
    cb.albedo[1] = k.albedo[1];
    cb.albedo[2] = k.albedo[2];
    cb.albedo[3] = std::max(k.near_fade, camera.near_clip);
    for (int i = 0; i < 3; ++i) {
        cb.sun_dir[i] = params.sun_dir[i];
        cb.sun_color[i] = params.sun_color[i];
        cb.ambient_color[i] = params.ambient_color[i];
        cb.look_fog[i] = ls.fog[i];
    }
    cb.look_a[0] = ls.extinction;
    cb.look_a[1] = ls.exposure;
    cb.look_a[2] = ls.aspect;
    cb.look_a[3] = ls.fine_share;
    cb.look_fog[3] = ls.fine_size;
    cb.look_b[0] = ls.fine_alpha;
    cb.look_b[1] = ls.big_alpha;
    cb.look_b[2] = ls.blur_floor;
    cb.look_b[3] = ls.opacity;
    cb.quality_info[0] = params.high_quality ? 1.0f : 0.0f;
    cb.quality_info[1] = scene_color_srv ? 1.0f : 0.0f;

    ctx->UpdateSubresource(impl_->constants.Get(), 0, nullptr, &cb, 0, 0);

    D3D11_VIEWPORT vp{
        0.0f, 0.0f, static_cast<float>(camera.width), static_cast<float>(camera.height),
        0.0f, 1.0f};
    ID3D11RenderTargetView* rtvs[1] = {out_rtv};
    ID3D11ShaderResourceView* vs_srvs[6] = {scene_depth_srv, fluid.fluid_srv(), fluid.tops_srv(),
                                            fluid.street_z_srv(), camera.gpu_view,
                                            scene_color_srv};
    ID3D11SamplerState* samps[1] = {impl_->linear_clamp.Get()};
    ID3D11Buffer* cbs[1] = {impl_->constants.Get()};

    ctx->OMSetRenderTargets(1, rtvs, nullptr);
    const float blend_factor[4] = {0.0f, 0.0f, 0.0f, 0.0f};
    ctx->OMSetBlendState(impl_->blend_state.Get(), blend_factor, 0xFFFFFFFFu);
    ctx->OMSetDepthStencilState(impl_->ds_state.Get(), 0);
    ctx->RSSetState(impl_->rs_state.Get());
    ctx->RSSetViewports(1, &vp);
    ctx->IASetInputLayout(nullptr);
    ctx->IASetVertexBuffers(0, 0, nullptr, nullptr, nullptr);
    ctx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    ctx->VSSetShader(impl_->vs[look].Get(), nullptr, 0);
    ctx->VSSetConstantBuffers(0, 1, cbs);
    ctx->VSSetSamplers(0, 1, samps);
    ctx->VSSetShaderResources(0, 6, vs_srvs);
    ctx->PSSetShader(impl_->ps[look].Get(), nullptr, 0);
    ctx->PSSetConstantBuffers(0, 1, cbs);
    ctx->PSSetSamplers(0, 1, samps);
    ctx->PSSetShaderResources(0, 6, vs_srvs);

    ctx->DrawInstanced(6, flakes, 0, 0);

    ID3D11ShaderResourceView* null_srvs[6] = {};
    ID3D11RenderTargetView* null_rtvs[1] = {};
    ctx->VSSetShaderResources(0, 6, null_srvs);
    ctx->PSSetShaderResources(0, 6, null_srvs);
    ctx->OMSetRenderTargets(1, null_rtvs, nullptr);
    return true;
}

} // namespace dolly::env
