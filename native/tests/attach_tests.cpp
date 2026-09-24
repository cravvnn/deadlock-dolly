#include "dolly_attach.hpp"
#include "dolly_effects.hpp"
#include "dolly_hide.hpp"

#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <iterator>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

#ifndef DOLLY_ATTACH_FIXTURE
#define DOLLY_ATTACH_FIXTURE ""
#endif

namespace {

void require(bool value, const char* message) {
    if (!value)
        throw std::runtime_error(message);
}

bool near(double a, double b, double tolerance = 1e-9) {
    return std::abs(a - b) <= tolerance;
}

void u32(std::vector<std::uint8_t>& data, std::uint32_t value) {
    for (unsigned i = 0; i < 4; ++i)
        data.push_back(std::uint8_t(value >> (i * 8)));
}

void u64(std::vector<std::uint8_t>& data, std::uint64_t value) {
    for (unsigned i = 0; i < 8; ++i)
        data.push_back(std::uint8_t(value >> (i * 8)));
}

void number(std::vector<std::uint8_t>& data, double value) {
    std::uint64_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    u64(data, bits);
}

void raw(std::vector<std::uint8_t>& data, const char* text, std::size_t bytes) {
    data.insert(data.end(), text, text + bytes);
}

void set_u32(std::vector<std::uint8_t>& data, std::size_t offset, std::uint32_t value) {
    for (unsigned i = 0; i < 4; ++i)
        data.at(offset + i) = std::uint8_t(value >> (i * 8));
}

void set_u64(std::vector<std::uint8_t>& data, std::size_t offset, std::uint64_t value) {
    for (unsigned i = 0; i < 8; ++i)
        data.at(offset + i) = std::uint8_t(value >> (i * 8));
}

void set_number(std::vector<std::uint8_t>& data, std::size_t offset, double value) {
    std::vector<std::uint8_t> encoded;
    number(encoded, value);
    for (std::size_t i = 0; i < encoded.size(); ++i)
        data.at(offset + i) = encoded[i];
}

std::vector<std::uint8_t> camera_block() {
    // One free camera key: a header-only DLYPATH with identical endpoints.
    std::vector<std::uint8_t> data;
    raw(data, "DLYPATH", 8);
    u32(data, 1);
    u32(data, 0);
    u32(data, 7);
    u32(data, 0);
    number(data, 1); // duration
    number(data, 0); // first_time
    number(data, 0); // last_time
    const double pose[7] = {100, 200, 300, 0, 0, 0, 16.0 / 9};
    for (double value : pose)
        number(data, value);
    for (double value : pose)
        number(data, value);
    return data;
}

std::vector<std::uint8_t> effects_block() {
    std::vector<std::uint8_t> data;
    raw(data, "DLYEFX02", 8);
    u32(data, 0);
    u32(data, 0);
    return data;
}

void push_segment(std::vector<std::uint8_t>& data, const dolly::AttachSegment& segment) {
    number(data, segment.begin);
    number(data, segment.end);
    u32(data, segment.flags);
    u32(data, static_cast<std::uint32_t>(segment.point));
    u32(data, segment.target.handle);
    u32(data, segment.target.entity_id);
    u64(data, segment.target.model);
    for (double value : segment.offset)
        number(data, value);
    number(data, segment.smoothing);
}

void push_segment2(std::vector<std::uint8_t>& data, const dolly::AttachSegment& segment) {
    push_segment(data, segment);
    u64(data, segment.bone_hash);
}

std::vector<std::uint8_t> attach_block(const std::vector<dolly::AttachSegment>& segments) {
    std::vector<std::uint8_t> data;
    raw(data, "DLYATT01", 8);
    u32(data, 1);
    u32(data, 0);
    u32(data, static_cast<std::uint32_t>(segments.size()));
    for (const auto& segment : segments)
        push_segment(data, segment);
    return data;
}

std::vector<std::uint8_t> attach_block2(const std::vector<dolly::AttachSegment>& segments) {
    std::vector<std::uint8_t> data;
    raw(data, "DLYATT01", 8);
    u32(data, 2);
    u32(data, 0);
    u32(data, static_cast<std::uint32_t>(segments.size()));
    for (const auto& segment : segments)
        push_segment2(data, segment);
    return data;
}

void push_block(std::vector<std::uint8_t>& data, const std::vector<std::uint8_t>& block) {
    data.insert(data.end(), block.begin(), block.end());
}

std::vector<std::uint8_t> shot3_block(const std::vector<std::uint8_t>& camera,
                                      const std::vector<std::uint8_t>& effects,
                                      const std::vector<std::uint8_t>& attach) {
    std::vector<std::uint8_t> data;
    raw(data, "DLYSHOT3", 8);
    u32(data, 1);
    u32(data, static_cast<std::uint32_t>(camera.size()));
    u32(data, static_cast<std::uint32_t>(effects.size()));
    u32(data, static_cast<std::uint32_t>(attach.size()));
    u32(data, 0);
    push_block(data, camera);
    push_block(data, effects);
    push_block(data, attach);
    return data;
}

std::vector<std::uint8_t> shot2_block(const std::vector<std::uint8_t>& camera,
                                      const std::vector<std::uint8_t>& effects) {
    std::vector<std::uint8_t> data;
    raw(data, "DLYSHOT2", 8);
    u32(data, 1);
    u32(data, static_cast<std::uint32_t>(camera.size()));
    u32(data, static_cast<std::uint32_t>(effects.size()));
    u32(data, 0);
    push_block(data, camera);
    push_block(data, effects);
    return data;
}

dolly::AttachSegment enabled_segment() {
    dolly::AttachSegment segment;
    segment.begin = 0;
    segment.end = 1;
    segment.flags = 1;
    segment.point = dolly::AttachPoint::eyes;
    segment.target = {19464268u, 76u, 0x1122334455667788ull};
    segment.offset = {0, 0, 0, 0, 0, 0};
    segment.smoothing = 0.1;
    return segment;
}

void view_offset_validation() {
    std::array<double, 3> out{};
    require(dolly::attach_view_offset({0.0f, 0.0f, 85.0f}, out), "A standing eye offset is valid");
    require(near(out[2], 85.0), "The decoded eye height is preserved");
    require(!dolly::attach_view_offset({0.0f, 0.0f, std::numeric_limits<float>::quiet_NaN()}, out),
            "A nonfinite eye offset is refused");
    require(!dolly::attach_view_offset({0.0f, 0.0f, 500.0f}, out),
            "An implausible eye offset is refused");
}

void source_blending() {
    dolly::AttachSegment free;
    free.end = 1;
    auto attached = enabled_segment();
    attached.begin = 1;
    attached.end = 2;
    attached.source_blend = 0.5;
    dolly::AttachSegment terminal;
    terminal.begin = terminal.end = 2;
    terminal.source_blend = 0.25;
    const auto encode = [](const std::vector<dolly::AttachSegment>& segments) {
        std::vector<std::uint8_t> data;
        raw(data, "DLYATT01", 8);
        u32(data, 3);
        u32(data, 0);
        u32(data, static_cast<std::uint32_t>(segments.size()));
        for (const auto& segment : segments) {
            push_segment2(data, segment);
            number(data, segment.source_blend);
        }
        return data;
    };
    dolly::AttachTrack track;
    std::string error;
    auto data = encode({free, attached, terminal});
    require(track.load(data.data(), data.size(), 2, error), error.c_str());
    double weight = 123;
    require(!track.arriving(0.49, weight) && weight == 0, "Blend does not start early");
    auto next = track.arriving(0.75, weight);
    require(next && next->flags == 1 && near(weight, 0.5), "Free to attach eased midpoint");
    require(track.at(1)->flags == 1 && !track.arriving(1, weight),
            "Arrival owns its exact key time");
    next = track.arriving(1.875, weight);
    require(next && next->flags == 0 && near(weight, 0.5), "Attach to final free eased midpoint");
    require(track.at(2)->flags == 0 && !track.arriving(2, weight),
            "Final free arrival is retained");
    attached.source_blend = 10;
    data = encode({free, attached, terminal});
    require(track.load(data.data(), data.size(), 2, error), error.c_str());
    require(track.arriving(0.5, weight) && near(weight, 0.5), "Long blend clamps to previous key");
    for (double invalid : {-1.0, 10.1, std::numeric_limits<double>::quiet_NaN()}) {
        attached.source_blend = invalid;
        data = encode({free, attached, terminal});
        require(!track.load(data.data(), data.size(), 2, error), "Invalid blend duration refused");
    }
    attached.source_blend = 0.5;
    free.end = 0;
    data = encode({free, attached, terminal});
    require(!track.load(data.data(), data.size(), 2, error), "Nonterminal empty interval refused");
    require(near(dolly::attach_blend_weight(-1, 0, 1), 0) &&
                near(dolly::attach_blend_weight(2, 0, 1), 1),
            "Blend clamps to endpoints");
    require(dolly::attach_blend_weight(0.001, 0, 1) < 1e-8 &&
                1 - dolly::attach_blend_weight(0.999, 0, 1) < 1e-8,
            "Easing has flat endpoint velocity and acceleration");
    dolly::CameraPose from{0, 0, 0, 0, 350, 0, 1};
    dolly::CameraPose to{20, 40, 60, 0, 10, 0, 2};
    const auto mid = dolly::blend_attach_poses(from, to, 0.5);
    require(near(mid[0], 10) && near(mid[1], 20) && near(mid[2], 30) && near(mid[4], 360) &&
                near(mid[6], 2),
            "Pose blending takes shortest angles and preserves destination lens");
    std::vector<dolly::AttachSegment> maximum;
    for (unsigned i = 0; i < dolly::AttachTrack::max_segments; ++i) {
        auto segment = enabled_segment();
        segment.begin = double(i) / dolly::AttachTrack::max_segments;
        segment.end = double(i + 1) / dolly::AttachTrack::max_segments;
        segment.source_blend = 0.5;
        maximum.push_back(segment);
    }
    const auto envelope = shot3_block(camera_block(), effects_block(), encode(maximum));
    dolly::NativeShot shot;
    require(shot.load(envelope.data(), envelope.size(), error),
            "Shot envelope accepts the full version 3 segment budget");
}

void eye_resolution() {
    dolly::AttachSample sample;
    sample.valid = true;
    sample.point = dolly::AttachPoint::eyes;
    sample.target = {19464268u, 76u, 0x1122334455667788ull};
    sample.origin = {100, 200, 300};
    sample.angles = {0, 90, 0};
    sample.aim = {10, 90, 0};
    sample.eye_local = {10, 0, 64};
    dolly::AttachSegment segment = enabled_segment();
    dolly::CameraPose pose{};
    pose[6] = 2.0;
    require(dolly::resolve_attach_pose(sample, segment, pose), "An eye sample resolves");
    // Yaw 90: forward is +Y and up is +Z, so the local eye offset is (0,10,64).
    require(near(pose[0], 100) && near(pose[1], 210) && near(pose[2], 364),
            "The eye point composes origin and local offset");
    require(near(pose[3], 10) && near(pose[4], 90) && near(pose[5], 0),
            "The eyes point uses the aim angles");
    require(near(pose[6], 2.0), "Resolving preserves the authored aspect");
}

void offset_application() {
    dolly::AttachSample sample;
    sample.valid = true;
    sample.point = dolly::AttachPoint::eyes;
    sample.origin = {100, 200, 300};
    sample.angles = {0, 0, 0};
    sample.aim = {0, 0, 0};
    sample.eye_local = {0, 0, 64};
    dolly::AttachSegment segment = enabled_segment();
    segment.target = {0, 0, 0};
    segment.offset = {5, 0, 0, 10, 20, 30};
    dolly::CameraPose pose{};
    require(dolly::resolve_attach_pose(sample, segment, pose), "Offsets resolve");
    require(near(pose[0], 105) && near(pose[1], 200) && near(pose[2], 364),
            "A local position offset is applied");
    require(near(pose[3], 10) && near(pose[4], 20) && near(pose[5], 30),
            "Angle offsets are applied");
    // The same offset rotates with the view: yaw 90 turns +X into +Y.
    sample.aim = {0, 90, 0};
    require(dolly::resolve_attach_pose(sample, segment, pose), "Rotated offsets resolve");
    require(near(pose[0], 100) && near(pose[1], 205),
            "A position offset follows the resolved frame");
}

void clearance_behavior() {
    dolly::AttachSample sample{};
    sample.valid = true;
    sample.point = dolly::AttachPoint::bone;
    sample.origin = {100, 200, 300};
    sample.angles = sample.aim = {0, 0, 0};
    dolly::AttachSegment segment{};
    segment.point = dolly::AttachPoint::bone;
    segment.offset = {0, 0, 6, 0, 180, 0};
    dolly::CameraPose pose{};
    require(dolly::resolve_attach_pose(sample, segment, pose), "Bone pose resolves");
    dolly::enforce_attach_clearance(sample, segment, pose);
    require(near(pose[0], 100) && near(pose[2], 306), "Exact offset remains exact");
    segment.flags = 4;
    dolly::enforce_attach_clearance(sample, segment, pose);
    require(pose[0] > 147 && near(pose[2], 306) && near(pose[4], 180),
            "Auto clearance moves forward without changing height or look");
    segment.offset[0] = -6;
    require(dolly::resolve_attach_pose(sample, segment, pose), "Back-facing bone pose resolves");
    dolly::enforce_attach_clearance(sample, segment, pose);
    require(pose[0] < 53, "Negative forward offset clears behind the point");

    segment.bone_hash = dolly::attach_bone_hash("head");
    require(segment.bone_hash == 0x0a8f12cc5f9a0c03ull,
            "Native head hash matches the Python attach compiler");
    segment.offset[0] = 0;
    require(dolly::resolve_attach_pose(sample, segment, pose), "Head pose resolves");
    dolly::enforce_attach_clearance(sample, segment, pose);
    require(near(pose[0], 104) && near(pose[2], 306),
            "Automatic head clearance stays near the first-person pose");
    segment.offset[0] = 6;
    require(dolly::resolve_attach_pose(sample, segment, pose), "Forward head pose resolves");
    dolly::enforce_attach_clearance(sample, segment, pose);
    require(near(pose[0], 106), "Automatic head clearance preserves a larger exact offset");
    segment.offset = {0, 0, 24, 0, 0, 0};
    require(dolly::resolve_attach_pose(sample, segment, pose), "Head clearance boundary resolves");
    pose[2] += 0.1;
    dolly::enforce_attach_clearance(sample, segment, pose);
    require(near(pose[0], 104),
            "Small smoothing drift cannot switch close head clearance to the wide sphere");
    segment.offset = {-6, 0, 6, 0, 180, 0};
    segment.offset[0] = -6;
    require(dolly::resolve_attach_pose(sample, segment, pose), "Rear head pose resolves");
    dolly::enforce_attach_clearance(sample, segment, pose);
    require(pose[0] < 53, "Rear-facing head shot retains wide automatic clearance");
}

void identity_gates() {
    dolly::AttachSample sample;
    sample.valid = true;
    sample.point = dolly::AttachPoint::eyes;
    sample.target = {1111u, 0, 0};
    sample.origin = {100, 200, 300};
    sample.angles = {0, 0, 0};
    sample.aim = {0, 0, 0};
    sample.eye_local = {0, 0, 64};
    dolly::AttachSegment segment = enabled_segment();
    dolly::CameraPose pose{};
    require(!dolly::resolve_attach_pose(sample, segment, pose),
            "A mismatched target handle is refused");
    sample.target = segment.target;
    sample.point = dolly::AttachPoint::weapon;
    require(!dolly::resolve_attach_pose(sample, segment, pose),
            "A mismatched attach point is refused");
    sample.point = dolly::AttachPoint::eyes;
    sample.valid = false;
    require(!dolly::resolve_attach_pose(sample, segment, pose), "An invalid sample is refused");
}

void smoothing_behavior() {
    // Picker overview may retain Head in the editor while resolving Eyes and
    // collecting a skeleton whose default index is root_motion (zero). Cancel
    // and Use Bone must resolve Head again instead of reusing that root index.
    dolly::AttachSegment head_request{};
    head_request.point = dolly::AttachPoint::bone;
    head_request.bone_hash = dolly::attach_bone_hash("head");
    dolly::AttachResolution overview{dolly::AttachPoint::eyes, head_request.bone_hash};
    require(!overview.matches(head_request),
            "Leaving picker cannot reuse overview root as the highlighted Head bone");
    dolly::AttachResolution resolved_head{head_request.point, head_request.bone_hash};
    head_request.offset[0] = 6;
    require(resolved_head.matches(head_request),
            "Offset edits can reuse an actually resolved Head bone");
    auto hand_request = head_request;
    hand_request.bone_hash = dolly::attach_bone_hash("hand_R");
    require(!resolved_head.matches(hand_request), "Changing bone requires a new resolution");
    hand_request.point = dolly::AttachPoint::weapon;
    require(!overview.matches(hand_request), "Picker overview cannot stand in for Weapon either");
    dolly::PausedAttachSample<dolly::AttachSample> paused;
    dolly::AttachSample sampled{};
    sampled.origin = {10, 20, 30};
    paused.apply(sampled, true, 42);
    sampled.origin = {12, 23, 34};
    paused.apply(sampled, true, 42);
    require(sampled.origin == std::array<double, 3>{10, 20, 30},
            "Repeated paused tick holds the validated bone pose");
    sampled.origin = {15, 25, 35};
    paused.apply(sampled, true, 43);
    require(sampled.origin[0] == 15, "Single-step refreshes paused pose");
    sampled.origin[0] = 18;
    paused.apply(sampled, false, 43);
    require(sampled.origin[0] == 18, "Playback resumes immediately even within the same tick");
    paused.apply(sampled, true, 43);
    paused.reset();
    sampled.origin[0] = 24;
    paused.apply(sampled, true, 43);
    require(sampled.origin[0] == 24, "Changing target discards paused pose");
    dolly::AttachSmoothing smoothing;
    dolly::CameraPose pose{100, 200, 300, 0, 0, 0, 16.0 / 9};
    smoothing.apply(pose, 0.016, 0);
    require(smoothing.primed() && near(pose[0], 100), "Zero smoothing snaps and primes");
    const dolly::CameraPose target{200, 200, 300, 0, 0, 0, 16.0 / 9};
    for (int i = 0; i < 600; ++i) {
        dolly::CameraPose step = target;
        smoothing.apply(step, 0.016, 0.2);
        pose = step;
    }
    require(near(pose[0], 200, 1e-3), "Exponential smoothing converges on its target");
    dolly::AttachSmoothing wrap;
    dolly::CameraPose start{};
    start[4] = 350;
    wrap.apply(start, 0, 0);
    dolly::CameraPose step{};
    step[4] = 10;
    wrap.apply(step, 0.016, 0.5);
    require(step[4] > 350 && step[4] < 360, "Angle smoothing takes the short way across the wrap");

    dolly::AttachSmoothing anchored;
    dolly::CameraPose follow{0, 0, 70, 0, 0, 0, 16.0 / 9};
    anchored.apply_anchored(follow, {0, 0, 0}, 0.016, 0.4, 2.0);
    // A jump and dash are whole-player motion: the camera travels with them
    // on the first frame instead of spending the smoothing interval inside
    // the moving character.
    follow = {180, 0, 150, 0, 0, 0, 16.0 / 9};
    anchored.apply_anchored(follow, {180, 0, 80}, 0.016, 0.4, 2.0);
    require(near(follow[0], 180) && near(follow[2], 150),
            "Anchored smoothing tracks a jump and dash without world-space lag");
    for (double root : {182.0, 179.0, 183.0, 180.0}) {
        follow = {root, 0, 150, 0, 0, 0, 16.0 / 9};
        anchored.apply_anchored(follow, {root, 0, 80}, 0.016, 0.4, 2.0);
        require(near(follow[0], root) && near(follow[2], 150),
                "Camera stays fixed relative to the render skeleton through root interpolation");
    }
    follow = {180, 15, 150, 0, 90, 0, 16.0 / 9};
    anchored.apply_anchored(follow, {180, 0, 80}, 0.016, 0.4, 2.0);
    require(follow[1] >= 13 && follow[1] < 15,
            "Relative motion remains smoothed within a strict position bound");
    require(follow[4] > 0 && follow[4] < 90, "Attached rotation remains smoothed");
    follow = {180, 15, 150, 0, 90, 0, 16.0 / 9};
    anchored.apply_anchored(follow, {180, 0, 80}, 0.016, 0, 2.0);
    require(near(follow[1], 15) && near(follow[4], 90), "Zero smoothing remains exact");

    dolly::AttachSegment offset{};
    require(near(dolly::attach_smoothing_error_limit(offset), 0.5),
            "A zero-offset bone cannot drift far from its point");
    offset.offset[0] = 100;
    require(near(dolly::attach_smoothing_error_limit(offset), 3.0),
            "An authored offset still has a finite drift limit");
}

void track_loading_and_cuts() {
    const std::vector<dolly::AttachSegment> segments = {
        enabled_segment(),
        {1, 2, 0, dolly::AttachPoint::eyes, {}, {0, 0, 0, 0, 0, 0}, 0},
    };
    dolly::AttachTrack track;
    std::string error;
    const auto block = attach_block(segments);
    require(track.load(block.data(), block.size(), 2.0, error), error.c_str());
    require(!track.empty() && near(track.duration(), 2.0), "A contiguous track loads");
    require(track.at(-1) == nullptr, "A phase before the track releases");
    const dolly::AttachSegment* active = track.at(0.5);
    require(active && active->flags == 1 && active->target.handle == 19464268u,
            "The enabled segment covers its range");
    active = track.at(1.0);
    require(active && active->flags == 0, "The disabled segment is a hard cut");
    active = track.at(9.0);
    require(active && active->flags == 0, "A phase past the end holds the last segment");
    require(track.load(nullptr, 0, 2.0, error) && track.empty(),
            "An empty block loads an empty track");
}

void track_rejections() {
    const std::vector<dolly::AttachSegment> segments = {enabled_segment()};
    const auto good = attach_block(segments);
    dolly::AttachTrack track;
    std::string error;
    auto reject = [&](std::vector<std::uint8_t> data, const char* message) {
        require(!track.load(data.data(), data.size(), 1.0, error), message);
    };
    auto bad = good;
    bad[0] = 'X';
    reject(bad, "Bad attach magic is refused");
    bad = good;
    set_u32(bad, 8, 4);
    reject(bad, "An unsupported attach version is refused");
    bad = good;
    set_u32(bad, 16, 2);
    reject(bad, "A segment count that disagrees with the length is refused");
    bad = good;
    set_number(bad, 28, 0.0);
    reject(bad, "An empty segment range is refused");
    bad = good;
    set_u32(bad, 36, 8);
    reject(bad, "Unknown segment flags are refused");
    bad = good;
    set_u32(bad, 40, 2);
    reject(bad, "An unknown attach point is refused");
    bad = good;
    set_number(bad, 60, std::numeric_limits<double>::quiet_NaN());
    reject(bad, "A nonfinite offset is refused");
    bad = good;
    set_number(bad, 108, 6.0);
    reject(bad, "Excessive smoothing is refused");
    bad = good;
    set_u32(bad, 44, 0);
    set_u32(bad, 48, 0);
    set_u64(bad, 52, 0);
    reject(bad, "An enabled segment without identity is refused");
    const auto pair = attach_block({enabled_segment(), enabled_segment()});
    reject(pair, "A duplicate segment range is refused");
    require(track.load(good.data(), good.size(), 1.0, error),
            "A valid track still loads after rejected blocks");
    require(!track.empty(), "The replacement track is live");
}

void bone_segments() {
    dolly::AttachSegment bone = enabled_segment();
    bone.point = dolly::AttachPoint::bone;
    bone.bone_hash = 0x1122334455667788ull;
    const auto block = attach_block2({bone});
    dolly::AttachTrack track;
    std::string error;
    require(track.load(block.data(), block.size(), 1.0, error), error.c_str());
    const dolly::AttachSegment* loaded = track.at(0.5);
    require(loaded && loaded->point == dolly::AttachPoint::bone,
            "A version-two bone segment loads with its point");
    require(loaded->bone_hash == 0x1122334455667788ull, "The bone hash is preserved");
    dolly::AttachSample sample;
    sample.valid = true;
    sample.point = dolly::AttachPoint::bone;
    sample.target = loaded->target;
    sample.origin = {10, 20, 30};
    sample.angles = {5, 90, 0};
    dolly::CameraPose pose{};
    require(dolly::resolve_attach_pose(sample, *loaded, pose), "A bone sample resolves");
    require(near(pose[0], 10.0) && near(pose[1], 20.0) && near(pose[2], 30.0),
            "The bone point composes the bone origin");
    require(near(pose[3], 5.0) && near(pose[4], 90.0), "The bone point uses the aim angles");
    auto zero_hash = block;
    set_u64(zero_hash, 116, 0);
    require(!track.load(zero_hash.data(), zero_hash.size(), 1.0, error),
            "A bone segment without a hash is refused");
    auto bad_point = attach_block2({bone});
    set_u32(bad_point, 40, 3);
    require(!track.load(bad_point.data(), bad_point.size(), 1.0, error),
            "An unknown point in a version-two block is refused");
}

void python_fixture_loads() {
    // Generated by tests/test_attach_shot.fixture_project; both sides assert
    // the same bytes so the Python compiler and native loader cannot drift.
    std::ifstream input(DOLLY_ATTACH_FIXTURE, std::ios::binary);
    require(input.good(), "Python attach fixture is missing");
    std::vector<unsigned char> data((std::istreambuf_iterator<char>(input)), {});
    dolly::NativeShot shot;
    std::string error;
    require(shot.load(data.data(), data.size(), error), error.c_str());
    require(!shot.attach.empty(), "Fixture carries an attach track");
    const dolly::AttachSegment* free_segment = shot.attach.at(0.5);
    require(free_segment && free_segment->flags == 0, "First fixture segment is free");
    const dolly::AttachSegment* attached = shot.attach.at(1.5);
    require(attached && attached->flags == 3, "Second fixture segment is attached and hidden");
    require(attached->point == dolly::AttachPoint::eyes, "Fixture point is eyes");
    require(attached->target.handle == 19464268u && attached->target.entity_id == 76u,
            "Fixture target identity differs");
    require(attached->target.model == 0x4F5B17F42C61561Dull, "Fixture model token differs");
    require(near(attached->offset[0], 1.0) && near(attached->offset[5], 6.0),
            "Fixture offsets differ");
    require(near(attached->smoothing, 0.25), "Fixture smoothing differs");
    require(shot.attach.first_enabled() == attached, "First enabled segment differs");
    dolly::CameraPose pose{};
    require(shot.camera.evaluate(0.5, pose) && near(pose[0], 50.0), "Fixture camera pose differs");
}

void snap_round_trip() {
    dolly::AttachSample sample;
    sample.valid = true;
    sample.point = dolly::AttachPoint::eyes;
    sample.target = {19464268u, 76u, 0x1122334455667788ull};
    sample.origin = {100, 200, 300};
    sample.angles = {0, 90, 0};
    sample.aim = {10, 90, 0};
    sample.eye_local = {0, 0, 64};
    const dolly::CameraPose camera{110, 205, 380, -5, 80, 3, 16.0 / 9.0};
    dolly::AttachSegment segment = enabled_segment();
    std::array<double, 6> offsets{};
    require(dolly::attach_snap_offsets(sample, camera, offsets), "Snap computes offsets");
    segment.offset = offsets;
    dolly::CameraPose resolved{};
    require(dolly::resolve_attach_pose(sample, segment, resolved), "Snapped offsets resolve");
    for (int i = 0; i < 3; ++i)
        require(near(resolved[i], camera[i], 1e-6), "Snap reproduces the camera position");
    for (int i = 0; i < 3; ++i)
        require(near(std::remainder(resolved[3 + i] - camera[3 + i], 360.0), 0.0, 1e-6),
                "Snap reproduces the camera angles");
    dolly::CameraPose broken = camera;
    broken[0] = std::numeric_limits<double>::quiet_NaN();
    require(!dolly::attach_snap_offsets(sample, broken, offsets), "A nonfinite camera is refused");
}

void envelope_loading() {
    const auto camera = camera_block();
    const auto effects = effects_block();
    const auto attach = attach_block({enabled_segment()});
    std::string error;
    dolly::NativeShot shot;
    const auto modern = shot3_block(camera, effects, attach);
    require(shot.load(modern.data(), modern.size(), error), error.c_str());
    require(!shot.attach.empty(), "A DLYSHOT3 envelope carries the attach track");
    const dolly::AttachSegment* active = shot.attach.at(0.5);
    require(active && active->target.handle == 19464268u, "The envelope track resolves");
    dolly::AttachSample sample;
    sample.valid = true;
    sample.point = dolly::AttachPoint::eyes;
    sample.target = active->target;
    sample.eye_local = {0, 0, 64};
    sample.origin = {100, 200, 300};
    dolly::CameraPose pose{};
    require(dolly::resolve_attach_pose(sample, *active, pose),
            "A resolved sample applies to the loaded track");
    dolly::NativeShot legacy;
    const auto previous = shot2_block(camera, effects);
    require(legacy.load(previous.data(), previous.size(), error), error.c_str());
    require(legacy.attach.empty() && legacy.attach.at(0.5) == nullptr,
            "A DLYSHOT2 envelope stays attach-free");
    dolly::NativeShot empty;
    const auto none = shot3_block(camera, effects, {});
    require(empty.load(none.data(), none.size(), error), error.c_str());
    require(empty.attach.empty(), "A zero-length attach block is empty");
    auto truncated = modern;
    truncated.pop_back();
    dolly::NativeShot broken;
    require(!broken.load(truncated.data(), truncated.size(), error),
            "An envelope length mismatch is refused");
}

} // namespace

void rendered_pose_history() {
    dolly::AttachRenderHistory history;
    dolly::AttachSample sample{};
    sample.valid = true;
    sample.motion_anchor = {100, 200, 300};
    sample.origin = {100, 200, 380};
    sample.aim = {2, 30, 0};
    sample.angles = sample.aim;
    history.record(sample, 1.0, 10);
    sample.origin[2] = 380.1;
    history.record(sample, 1.01, 11);
    // The current readable animation may step to an older network pose while
    // scene translation is already current. Follow completed animation locally.
    sample.motion_anchor = {125, 180, 310};
    sample.origin = {125, 180, 389.5};
    require(history.apply(sample, 1.025, 12), "Completed pose not available at next view");
    require(std::abs(sample.origin[2] - 390.25) < 1e-6, "Irregular view cadence not predicted");
    require(sample.origin[0] == 125 && sample.origin[1] == 180, "Scene translation lagged");
    require(sample.aim[1] == 30 && sample.angles[0] == 2, "Completed pose changed aim");
    const auto unchanged = sample.origin;
    require(!history.apply(sample, 1.026, 13) && sample.origin == unchanged, "Old frame reused");
    require(!history.apply(sample, 1.2, 12), "Prediction survived a long stall");
    require(!history.apply(sample, 1.009, 12), "Backward clock predicted");
    history.reset();
    require(!history.apply(sample, 1.025, 12), "Target reset reused history");
    sample.motion_anchor = {0, 0, 0};
    sample.origin = {0, 0, 80};
    history.record(sample, 2, 20);
    sample.origin[2] = 100;
    history.record(sample, 2.01, 21);
    require(history.apply(sample, 2.02, 22) && sample.origin[2] <= 102.000001,
            "Animation cut caused unbounded prediction");
    sample.origin[2] = 200;
    require(!history.apply(sample, 2.02, 22), "Large local discontinuity reused old pose");
}

int main() {
    try {
        unsigned hide_index = 999;
        require(dolly::hide_instance_index(1, 4, 8, 4, 2, 64, hide_index) && hide_index == 5,
                "Hide identity offset must include stream, element and first instance");
        require(!dolly::hide_instance_index(2, 4, 0, 0, 0, 64, hide_index),
                "Never hide a mixed instance batch");
        require(!dolly::hide_instance_index(1, 8, 0, 0, 0, 64, hide_index),
                "Unknown identity stride must remain visible");
        require(!dolly::hide_instance_index(1, 4, 1, 0, 0, 64, hide_index),
                "Unaligned identity must remain visible");
        require(!dolly::hide_instance_index(1, 4, 0, 0xffffffffULL, 1, 64, hide_index),
                "Overflowing identity must remain visible");
        require(!dolly::hide_instance_index(1, 4, 0, 0, 64, 64, hide_index),
                "Out of range identity must remain visible");
        const auto owner = (std::uint64_t(20) << 32) | 123;
        require(dolly::hide_owner_matches(owner, 123, 20) &&
                    dolly::hide_owner_matches(owner, 123, 21),
                "Current owner accepted");
        require(!dolly::hide_owner_matches(owner, 124, 20) &&
                    !dolly::hide_owner_matches(owner, 123, 22) &&
                    !dolly::hide_owner_matches(owner, 123, 19),
                "Wrong owner and stale or future generations remain visible");
        rendered_pose_history();
        view_offset_validation();
        source_blending();
        eye_resolution();
        offset_application();
        clearance_behavior();
        identity_gates();
        smoothing_behavior();
        track_loading_and_cuts();
        track_rejections();
        bone_segments();
        envelope_loading();
        snap_round_trip();
        python_fixture_loads();
        std::cout << "Native attach camera tests passed.\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
