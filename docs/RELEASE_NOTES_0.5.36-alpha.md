# Deadlock Dolly 0.5.36-alpha

## Orbit the Bone Picker

Hold middle mouse over the hero overview and drag to inspect the character from
any horizontal angle. Vertical dragging changes elevation without flipping over
the poles. The opening overview distance stays fixed; releasing stops rotation.
Attached preview and saved camera offsets are unchanged by this inspection orbit.
Returning from attached preview preserves the overview angle.

Bone markers retain the validated paused pose. Their screen projection updates
with the applied camera view so smoothing does not make dots trail the rotation.
Drags started over the side panel do not orbit; losing focus cancels a held drag.

## Hero icons from your game

The top-right portrait now uses the selected hero's small player icon from the
installed game instead of a scene crop. Dolly reads artwork locally when the
picker opens; no game artwork is bundled and no extra scene capture is needed.
Updated image contents are picked up automatically. If a future update changes
an unsupported asset format or path, the portrait may be unavailable until Dolly
is updated; the player name and bone controls remain usable.

## Clearer offset label

The lateral offset is now **Sideways**, at the same font size. Axis signs, saved
offsets and slider behavior are unchanged.

## Validation

A bounded local Warden replay was tested with physical middle-mouse dragging.
Andrew confirmed stable aligned markers, fixed distance and the correct game
portrait. Telemetry confirmed constant orbit radius and replay tick, no native
errors, successful head/right-hand/right-shoulder selection, and unchanged saved
camera settings. This is not an all-hero zero-jitter guarantee.

Automated checks cover full-circle radius preservation, paused pose holding,
rotated marker selection, middle-button input ownership, focus loss, and local
portrait decoding. Icons for 35 installed heroes were checked. The release build
runs Python regressions, native checks, packaged startup and updater tests.
Hardware encoder and depth-scene graphics smoke tests remain deliberately omitted
under the existing watchdog safeguards; native metadata records those omissions.

## Short publish description

**0.5.36-alpha**
- **Orbit the Bone Picker.** *Before:* The hero overview stayed at one angle. *After:* Hold middle mouse and drag around the character while keeping the opening distance.
- **Stable bone markers while orbiting.** *Before:* Marker smoothing assumed a stationary overview. *After:* Markers follow the applied camera view while retaining the held paused pose.
- **Game-native hero icons.** *Before:* The picker showed a cropped scene image. *After:* It reads the hero icon from your installed game, including updated artwork, without bundling it.
- **Clearer Sideways control.** *Before:* Left/Right could crowd the offset slider. *After:* Sideways uses the same font size with a shorter label.
