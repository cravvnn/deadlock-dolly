# Camera integration evidence

Research snapshot: 8 September 2026. This is a command-driven prototype. Console
framing and path calculations can be tested without the game; visible camera
behavior, game launching, and unlocker ABI compatibility require a Windows
Deadlock replay test. No Deadlock process was run during package production.

## Paused resume and command timing (0.3.2)

The latest diagnostics and clips distinguish a native-resume camera jump from
small path jitter. Fresh-seek position checks had unit displacement response;
later paused checks had gains near 0.25 and 0.194. Manual movement then retained
a commanded pose far from the displayed view. The inferred latent-origin mismatch
is addressed by refreshing the exact current tick and requiring a near-unit XYZ
probe and verified return before movement. No fixed eye-height offset or inverse
weak-gain multiplier is used. Whether the same-tick refresh restores the current
Deadlock build's underlying state remains a native validation item.

Periodic spec_pos samples now accompany existing movement batches. They detect
large sustained separation from recent commands without learning a new offset
from a weak response. Playback startup calibration and recent frame samples are
retained independently, so later manual checks cannot overwrite their evidence.

Replay timing no longer snaps its fractional phase to each integer observation.
The continuous clock corrects gradually and drives all camera/framing curves
together, with a one-tick extrapolation cap. This addresses a demonstrated
algorithmic source of uneven increments; it does not establish sole causation
for the small rendered shake in the clip.

Windows command pacing uses an unnamed high-resolution waitable timer. Microsoft
documents the high-resolution creation flag for Windows 10 version 1803 onward
in [CreateWaitableTimerExW](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createwaitabletimerexw).
Negative due times are relative and expressed in 100 ns units; a zero period
makes the timer one-shot, per [SetWaitableTimer](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-setwaitabletimer).
The frame worker checks cancellation between waits of at most 20 ms, closes its
handle and falls back to normal event waits on unavailable APIs or failures.
These API properties do not guarantee a particular frame cadence under load.

The global entity-interpolation controls were not changed. The legacy Valve
SDK's [GetPredictionErrorSmoothingVector](https://github.com/ValveSoftware/source-sdk-2013/blob/master/src/game/client/c_baseplayer.cpp)
skips its cl_smooth prediction-error correction during demos and pause. That
Source 1 implementation does not establish Deadlock behavior, so cl_smooth is
not treated as a verified cure for this spectator issue. Native renderer camera
hooks remain separate work if console timing is insufficient after local testing.

## Paused camera input (0.3.0)

The existing Frozen preview demonstrates the useful separation: replay time
can remain fixed while Dolly sends different camera poses. The new manual mode
uses that command path, with one input-driven worker instead of a path-time
sampler. Saved-view switching pauses at the current replay moment and evaluates
the selected key's pose/lens/cvar values without issuing a seek or resume.

HLAE was inspected at commit `03f972b4710ab9a8b1601fc0c34ce33c9c0268cc`:

- The [`mirv_input` documentation](https://github.com/advancedfx/advancedfx/wiki/Source:mirv_input)
  describes camera override input, keyboard movement, arrow and mouse look,
  console suspension and ending input mode.
- The [campath workflow](https://github.com/advancedfx/advancedfx/wiki/Source:mirv_campath)
  includes authoring camera positions while a demo is paused.
- [Source 2 main.cpp](https://github.com/advancedfx/advancedfx/blob/03f972b4710ab9a8b1601fc0c34ce33c9c0268cc/AfxHookSource2/main.cpp)
  obtains game time separately from absolute frame time, then calls the input
  override with `LastFrameTime` and stores absolute frame time for that input
  update. Campath evaluation uses its own game-time argument.
- [MirvInput.cpp](https://github.com/advancedfx/advancedfx/blob/03f972b4710ab9a8b1601fc0c34ce33c9c0268cc/shared/MirvInput.cpp)
  integrates translation and rotation using the supplied delta and replaces
  the camera pose. Window/input and cursor hooks give it mouse ownership.
  The older pause/client-time workaround near the input tail is commented
  out; it is not treated as an active requirement for Source 2.

This supports separating camera movement from replay time. It does not make
HLAE's CS2 signatures, interfaces or hooks directly usable in Deadlock. Dolly
ships new Python movement/input code, no copied game offsets or native hooks.
It uses world-Z elevation, camera-relative translation, a real-time delta
capped at 100 ms and the existing measured outgoing XYZ correction. A single
console worker verifies the selected replay and fixed current tick around
updates, and stops on unexpected advancement. Slow command responses can still
reduce visible smoothness; this is not render-frame synchronization.

Input is optional, session-only and scoped to the foreground PID of the launched
game. The native boundary reuses the tested high-bit `GetAsyncKeyState` query.
The GUI supplies held movement controls under a lock; the worker never calls
Tk. Focus changes clear or re-arm input. This code does not consume game keys,
take over the mouse, or detect the in-game console. Keyboard look is provided
through the arrow keys. No assumption is made that `sv_noclipduringpause` or
other generic freecamera cvars restore Deadlock's native paused input.

## Current game command inventory

Primary technical evidence is the directly extracted game data maintained by
[SteamDatabase/GameTracking-Deadlock](https://github.com/SteamDatabase/GameTracking-Deadlock),
commit `70a264134418d4546776e3a993245fdb12138200` (7 September 2026).

| Command/control | Evidence in extracted game data | Remaining compatibility question |
| --- | --- | --- |
| `spec_goto` | Switch spectator to roaming and move to location | Effect timing and per-frame smoothness |
| `spec_pos` | Dump camera position and angles | Permission and output in user's build |
| `cl_citadel_forceangles` | Force third-person camera angles | Pitch/yaw/roll application to roaming camera, accepted argument format |
| `r_aspectratio` | Default 0; development-only and defensive flags | User confirms visible freecam effect; full animated transition requires local validation |
| `citadel_camera_fov` | Default 75, clamp 40–170 | Retired in 0.2.0 after user reports no visible freecam effect |
| `citadel_camera_spectator_fov` | Default 90, clamp 65–100 | Retired in 0.2.0; no longer probed or written |
| `r_citadel_depthoffield_enable` | Boolean, default false | Actual effect at current graphics settings |
| `r_citadel_depthoffield_focus_distance` | Inches, default 200, clamp 0–10000 | Actual effect in replay scene |
| `r_citadel_depthoffield_aperture_diameter` | Inches, default 0, clamp 0–3 | Actual effect in replay scene |
| `r_citadel_depthoffield_sensor_size` | Inches, default 1, clamp 0.5–3 | Actual effect in replay scene |
| `r_citadel_depthoffield_mode` | 0 normal, 1 near only, 2 far only | Actual effect in replay scene |
| `r_depth_of_field` | Boolean, default true | Relation to Citadel's DOF pass |
| `demo_info` | User's 0.1.2 log contains replay file metadata | Metadata gives identity and duration, not current tick |
| Bare `demo_goto` | User's 0.1.3 log confirms current/total ticks and filename | Integer query cadence is separate from render cadence |
| `demo_gototick` / `demo_goto` with arguments | Seek in demo | Successful seek completion must be checked |
| `demo_step_tick` | Play N ticks then pause | Render frame timing is independent of console scheduling |
| `demo_pause` / `demo_resume` | Pause and resume | No inferred fractional ticks or synchronization guarantee |

Sources:
[commands.txt](https://github.com/SteamDatabase/GameTracking-Deadlock/blob/70a264134418d4546776e3a993245fdb12138200/DumpSource2/commands.txt),
[convars.txt](https://github.com/SteamDatabase/GameTracking-Deadlock/blob/70a264134418d4546776e3a993245fdb12138200/DumpSource2/convars.txt).

Generic `r_dof_override`, `r_dof_override_near_blurry`,
`r_dof_override_near_crisp`, `r_dof_override_far_crisp`,
`r_dof_override_far_blurry`, and `r_dof_override_tilt_to_ground` also appear,
but these are cheat-flagged; removing hidden flags does not remove cheat checks.
The Citadel DOF family is development-only and defensive; the supplied unlocker
removes those flags.

The observer guide explicitly reports `spec_goto` ignoring pitch/yaw. Therefore
this prototype does not treat its optional angle fields as verified rotation:
[Deadlock observer guide](https://forums.playdeadlock.com/resources/guide-to-deadlock-esports-and-content-creation-observing-and-camera-controls.28/).
An older direct report confirms `cl_citadel_forceangles` could set roll in 2024,
which motivates testing it rather than copying CS2 camera offsets:
[Deadlock forceangles report](https://forums.playdeadlock.com/threads/cl_citadel_forceangles-allows-changing-roll-and-shooting-left-handed.5737/).

## Console transport

VConsole2 is a framed TCP protocol, distinct from a newline-based netconsole.
This package implements both explicitly and never guesses by sending protocol
garbage to the other endpoint.

The VConsole header is 12 bytes: four-byte packet tag, four-byte version,
two-byte total length, two-byte handle. Multibyte header numbers are big-endian.
The commonly used current command version is `0x00D40000`. A CMND payload is
the UTF-8 command plus NUL. PRNT text follows a 28-byte payload metadata prefix.

Protocol references inspected:

- [theokyr/CS2RemoteConsole](https://github.com/theokyr/CS2RemoteConsole/blob/260746d06e13cd7d4a92ad0bb6cfb8b703eb6884/libvconsole/src/vconsole.cpp), MIT.
- [uilton-oliveira/VConsoleLib.python](https://github.com/uilton-oliveira/VConsoleLib.python/blob/21d962bead0f5f38c17068477b4bf1df6bd8a555/libs/vconsole2_lib.py), MIT, older version `0x00D20000`.

The implementation is new Python code with correct buffering of fragmented TCP
reads, bounded packet/response lengths, loopback-only connection targets,
serial command requests, and random echo delimiters. Reference license notices
are retained in `third_party/notices`.

The CS2 reference requires tools mode. Availability with only Deadlock's `-dev`
and `-vconport` has not been demonstrated. The current Deadlock engine string
dump contains `CNetConsoleMgr` and a netconsole listener failure message, which
supported investigating `-netconport`. The user's subsequent Windows logs
confirmed Netconsole connectivity, including full unlocker output in 0.1.2.

## Replay identity and current tick

Literal strings extracted from the current `engine2` binary include:

```text
Playing back demo: '%s' at tick %u
Error - Not currently playing back a demo.
Demo paused at engine time %g, demo tick %d
Syntax: demo_goto <tick> [relative] [pause]
  Currently playing %d of %d ticks. Minutes:%.2f File:%s
```

See [engine2_strings.txt](https://github.com/SteamDatabase/GameTracking-Deadlock/blob/70a264134418d4546776e3a993245fdb12138200/game/bin/win64/engine2_strings.txt).
The user's 0.1.2 diagnostic gives direct evidence that `demo_info` emits a
different format in build 10854: `Demo contents for <path>:` followed by a
`DemoFileHeader` and `DemoFileInfo`. The latter reports `playback_time` and
`playback_ticks`: duration values, not the current position. The observed totals
were 2522.15625 seconds and 161418 ticks (a ratio of 64 ticks/second).

Version 0.1.3 recognizes that metadata as replay identity while leaving the
current tick unknown. It separately sends `demo_goto` with no arguments and
parses the current/total status format above. This query was originally inferred
from the engine's syntax/status strings. The user's subsequent 0.1.3 diagnostics
confirm the exact format, including live tick 112615, total 161418 and the
selected replay filename.
It supplies no numeric seek argument. If its response lacks a filename, a fresh
`demo_info` query establishes the selected replay's identity before using the tick.

Timed camera capture, camera preview and frozen playback can proceed with
recognized metadata alone. Frozen playback explicitly pauses the scene and uses
shot seconds, reasserting pause when no live tick is available. Normal replay
motion, replay-timed capture and seeking require an actual readable current tick;
they never substitute file duration or a cached tick. Unknown identity still
blocks camera control. A stopped tick does not establish pause state or tick rate.
Version 0.1.4 adds a fractional camera clock capped at one tick ahead of the
latest live sample. This is an explicit bounded estimate for slow motion, not
substitution of a wall clock for replay progress. A stopped tick eventually holds
the estimate at that limit; a reported rewind resets it immediately.

## Why this package does not transplant HLAE hooks

[HLAE source](https://github.com/advancedfx/advancedfx/blob/03f972b4710ab9a8b1601fc0c34ce33c9c0268cc/AfxHookSource2/main.cpp)
implements paths by overriding CS2's view structure during camera setup. It
uses CS2-specific view offsets and engine interfaces. Its time module also
assumes CS2 timing. Those details must not be reused as Deadlock offsets.
This package uses HLAE as a workflow/design reference; it does not include
HLAE hook code or claim HLAE binary compatibility.

The user's animation markdown concerns CS2 character animation and contains
explicitly build-specific offsets. It supplies no verified Deadlock spectator
camera interface. Those offsets are not used by this package.

## Unlocker behavior

[Artemon121/cvar-unhide-s2-citadel](https://github.com/Artemon121/cvar-unhide-s2-citadel/tree/505547d58e4b66001406a8fa618548364f7cdb4d)
is a server.dll proxy mounted before the real server through a game search
path. It checks `-insecure`, forwards CreateInterface to the real Citadel
server, and patches the server config interface to register its console
commands. It is therefore itself dependent on Source 2 interface compatibility.
The upstream code does not check `-dev`; the dolly launcher must require that
flag. `cvar_unhide` removes hidden, development-only, and defensive flags;
it does not remove cheat flags. `cvarlist_md` writes an inventory of commands
and variables useful for diagnosing update compatibility.

Launching a renamed alternate game directory is not established by the upstream
unlocker instructions. The current engine contains a recorded-game/current-game
mismatch diagnostic, and `demo_allow_game_mismatch` defaults to false. The
available SDK merely declares `Plat_GetGameDirectory(int unknown=0)` without
documenting its relationship to `-game`. Thus preserving the display `game`
field in a cloned gameinfo file does not prove either normal demo compatibility
or the proxy's hardcoded real-server path remains correct. The known upstream
mount location is the original `game/citadel/gameinfo.gi`; any alternate loading
scheme requires explicit in-game confirmation.

This alpha therefore uses a temporary mount in the original gameinfo, with a
durable byte-for-byte backup, recovery journal, and content-hash checks before
restoration. It restores after confirmed unlocker initialization, editor close,
or game exit, and includes a recovery command for interrupted sessions. It does
not enable `demo_allow_game_mismatch`. The unique generated folder contains the
plugin only; it is not the `-game` directory.

## 0.1.2 runtime feedback and startup order

The user's 0.1.1 diagnostic export confirms a successful Netconsole echo over
the launched process's local port. Both version and cvar_unhide returned empty
responses. The source shows cvar_unhide confirmation using ConColorMsg, while
individual unlocked names use Msg. A working echo does not guarantee the same
visibility or timing for every logging channel. This is an uncertainty, not a
diagnosis of the plugin DLL or an established transport parsing bug.

The user identified the required lifecycle: initialize cvar_unhide in the
pre-lobby/hideout before loading a demo. Version 0.1.1 instead passed +playdemo
at launch and attempted unhide in the later camera probe. Version 0.1.2 removes
that command-line replay load, makes Netconsole the default and introduces
explicit hideout initialization followed by a separately gated replay load.
Both upstream completion summaries are required. The camera support probe no
longer executes cvar_unhide inside a demo.

Raw console history is retained for diagnostics, including text arriving after
request end markers. Delimiter acknowledgment does not guarantee asynchronous
engine work or logging has completed. Initialization therefore uses an explicit
completion wait: after the echo, keep the request serialized and collect output
until both unhide summary patterns appear, a rejection appears, or the 12-second
deadline expires. Normal per-frame requests retain their existing behavior.
No blanket logging-channel resets, speculative sleeps, or unverified logfile
response fallback were introduced.

## 0.1.3 runtime feedback

The user's 0.1.2 export confirms both unlocker summaries, for 626 concommands and
2896 cvars, while `demo_info` reports no active demo before and after initialization.
The replay is loaded afterward. Step 5 then fails because the old parser rejects
the metadata format described above. That is a Dolly parsing defect; these logs
do not indicate an unlocker failure. They stop before testing camera support or
visible camera effects. The 0.1.3 tests cover the observed metadata shape, but the
new live status query and actual camera application required a local game test
at that release. The following 0.1.3 feedback provides that additional evidence.

## 0.1.4 motion and playback feedback

The user's 0.1.3 export confirms every existing camera capability check and the
live `demo_goto` status format. Every retained Play operation uses Frozen mode.
One run holds tick 112615 through 3063 status queries. Its elapsed time is
39.802 seconds for a final shot time of 3.96875 seconds, consistent with 0.1x
speed; this is an inference because that export did not retain the project or
selected settings. A prior short run started from a middle camera pose, matching
the old UI's use of the selected preview time when starting playback.

Frame inspection of the supplied 150-second, 60-FPS clip confirms the pause
overlay and HUD stay visible during travel. In a 300-frame sample from 120 to
125 seconds, the camera visibly advances about every 9-10 video frames, with
nearly held views between. This is evidence of stepping, not proof of a single
engine or transport cause. Command cadence in the logs also varies sharply,
from around 87 to 21-22 updates per second, while the controller waits for two
serial console round trips per frame.

Version 0.1.4 makes normal resumed-demo playback the default, always starts the
main Play shot action at zero, batches frame application with the next live
status query, and writes request delimiters/commands together. One batch remains
outstanding at a time to avoid building a delayed command backlog. Continuous
yaw/roll output removes numeric +/-180-degree seams; no full-turn spin was
confirmed in the sampled clip.

The [observer guide](https://forums.playdeadlock.com/resources/guide-to-deadlock-esports-and-content-creation-observing-and-camera-controls.28/)
documents `citadel_hud_visible` for the HUD, `citadel_hide_replay_hud` for replay
controls, and `engine_no_focus_sleep 0` to prevent background FPS drops. Playback
uses the requested HUD toggle and, when readable, temporary replay-bar and
background-sleep settings. Cleanup turns the HUD on and restores the other prior
values. This guide supports those commands, but the new automated lifecycle and
visible smoothness still require the user's game test. Repeated native
`spec_goto` commands do not provide a renderer camera hook.


## 0.2.0 aspect-ratio framing and editor workflow

After 0.1.6, the user reported that direct `r_aspectratio` changes visibly affect
their freecam while the prior FOV controls do not. The native inventory lists
`r_aspectratio` with default 0 and development-only/defensive flags, but supplies
no description establishing a degree-based projection conversion or an editing
range. This release uses that reported effect directly, with no native FOV writes
and no conversion from the old FOV angles. The 0.5–4.0 curve bounds are explicit
Dolly editor limits, not a claim about the engine's allowed values.

Automatic 0 is a native mode sentinel, not a meaningful interpolation endpoint.
Capture resolves it to the launched process's largest visible, non-minimized
Windows client area's width/height, falling back to the shot's standard aspect
if unavailable. The default standard is 16:9, consistent with the user's supplied
game screenshots. Client dimensions are a fallback and do not establish an
internal render viewport or letterboxing. Explicit positive values are retained;
the raw value, resolved value and source are included in diagnostics. Stop restores
the original cvar exactly, including automatic 0. No per-frame window query or
position calibration round trip is added to the playback loop.

The framing graph plots shot time against aspect ratio and uses the same evaluated
values as playback. Its Smooth mode is a shape-preserving cubic curve, independent
of the camera position spline, with Linear and Step alternatives. Vertical graph
dragging edits a selected camera's aspect only; arrival times remain explicit
inspector edits. Old version-1 projects preserve camera poses, timing, cvar tracks
and inactive FOV metadata, but begin with standard 16:9 framing keys. That migration
requires reauthoring framing rather than assuming equivalence between controls.

The interface references established camera-editor workflows:

- Epic's [Curve Editor documentation](https://dev.epicgames.com/documentation/unreal-engine/animation-curve-editor-in-unreal-engine?lang=en-US)
  separates the item list, editing graph and toolbar, with time on the horizontal
  axis and values on the vertical axis. Dolly adopts that organization for its
  smaller framing editor; it does not implement Epic's tangent handles or retiming
  tool set.
- Epic's [Fortnite Cinematic Sequence device documentation](https://dev.epicgames.com/documentation/fortnite/using-cinematic-sequence-device-in-unreal-editor-for-fortnite?lang=en-US)
  treats authored sequences and their playback as separate operations. Dolly keeps
  editing beside an always-visible playback bar so a graph edit does not move the
  live game camera by itself.
- HLAE's [mirv_campath documentation](https://github.com/advancedfx/advancedfx/wiki/Source:mirv_campath)
  remains the reference for capturing camera states, editing paths and playing
  them against a demo. This is workflow inspiration, not shared Deadlock hooks.

These sources support the editor organization. They do not establish Deadlock's
`r_aspectratio` rendering behavior; that evidence comes from the user's game test.
This release's complete animated transition and actual Windows interface remain
local validation items.


## 0.2.1 desktop input and identity

The capture preference is local desktop input configuration, separate from the
replay and from Deadlock's own key bindings. The implementation observes the
configured virtual key and modifier states through the high bit of
GetAsyncKeyState, with an 8 ms cancellable poll interval and explicit press
edges. The unreliable low bit is ignored. This avoids consuming a global
hotkey or installing an input hook, and leaves the game's action intact.
Very short taps between polls can be missed; hardware/vendor remapping can
change the key Windows reports. A mouse button held while returning focus
must be released before a new capture. Windows-key chords are ignored.

Primary API references:

- [GetAsyncKeyState](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getasynckeystate)
- [Windows virtual-key codes](https://learn.microsoft.com/en-us/windows/win32/inputdev/virtual-key-codes)
- [SetCurrentProcessExplicitAppUserModelID](https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nf-shobjidl_core-setcurrentprocessexplicitappusermodelid)

The taskbar identity is set before creating the desktop UI, and Tk applies the
bundled icon to the window and future dialogs. Native Windows taskbar behavior
still needs a local check; the Linux Tk smoke test verifies asset loading only.


## 0.2.2 Windows icon compatibility

The previous ICO contained nine PNG-compressed frames. Tk 8.6.12–8.6.15's
Windows reader treats the frame bytes as BITMAPINFOHEADER in
AdjustIconImagePointers and passes the resulting dimensions onward in
MakeIconOrCursorFromResource. For these PNGs it reads width 169478669 and
height 109051904. Current Tk branch code obtains dimensions from the native
icon instead. The old behavior establishes an icon-loader incompatibility;
it does not reproduce the supplied screenshot on a native Windows desktop.

This release uses DIB-backed ICO images with valid bitmap dimensions and
explicit AND masks. The main root and default dialog icon are set explicitly.
The PNG fallback is not followed by an ICO call that could override it.
Normal startup uses CREATE_NO_WINDOW for the Python GUI process, preserving
redirected logs and showing startup failures through MessageBoxW. This also
removes the separate console taskbar entry; the supplied crop alone cannot
establish whether its Python icon belonged to the console or GUI window.

Primary references:

- [Tk 8.6.13 Windows window-manager source](https://github.com/tcltk/tk/blob/core-8-6-13/win/tkWinWm.c)
- [Tk window-manager iconbitmap/iconphoto documentation](https://www.tcl-lang.org/man/tcl8.6/TkCmd/wm.htm)
- [Windows process creation flags](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags)
- [Windows application identity](https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nf-shobjidl_core-setcurrentprocessexplicitappusermodelid)
