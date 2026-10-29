# Credits

Deadlock Dolly was created by **Cravvnn** and has grown through code,
testing, feedback and the work of other open-source projects. This page records
the people and projects behind it, from the original camera editor onward.

## People

| Contributor | Contribution |
| --- | --- |
| [Cravvnn](https://github.com/cravvnn) | Project creation, direction, ongoing development, game-update compatibility research, testing and releases. |
| [**@Moelandblue**](https://github.com/Moelandblue) | Reported missing DLSS options and identified redirected game stdout as the trigger, informing Dolly's optional DLSS-compatible startup and retained diagnostic logs. See [issue #18](https://github.com/cravvnn/deadlock-dolly/issues/18). |
| [**@ExoCalibra**](https://github.com/ExoCalibra) | Bone and Weapon vertical-aim lock and camera-relative movement during attached preview. See [PR #29](https://github.com/cravvnn/deadlock-dolly/pull/29) and the [original aim-lock contribution](https://github.com/cravvnn/deadlock-dolly/commit/f8e23dbdb811ccf76e51f88f72e8ff6086d317ed). |
| [**@SamuelCurrid**](https://github.com/SamuelCurrid) | After Effects and USD camera exports, recorded camera tracks for path, attached and POV recordings, export controls, and forward-seek undershoot recovery. See [PR #25](https://github.com/cravvnn/deadlock-dolly/pull/25) and [PR #28](https://github.com/cravvnn/deadlock-dolly/pull/28). |
| [**@paralin**](https://github.com/paralin) | Linux/Proton compatibility: Wine mount-point handling, Steam-library discovery and recovery from interrupted plugin cleanup. See merged [PR #10](https://github.com/cravvnn/deadlock-dolly/pull/10), [#11](https://github.com/cravvnn/deadlock-dolly/pull/11) and [#12](https://github.com/cravvnn/deadlock-dolly/pull/12). |
| [**@tbhtho**](https://github.com/tbhtho) | The in-game UI rework and saved panel layouts that later editor updates build on. See the [original contribution](https://github.com/cravvnn/deadlock-dolly/commit/8521b341d78b26bbb1a3897d23eefb89d15b8363). |
| [**@s34532**](https://github.com/s34532) | Custom native particle effects, beginning with confetti rain, plus game-only audio capture and reconstructed clip audio. See the [particle contribution](https://github.com/cravvnn/deadlock-dolly/commit/dee932e24411656ea0f13c624e189797ae80b5e8) and [audio PR #9](https://github.com/cravvnn/deadlock-dolly/pull/9). |
| [**@Hesykhia**](https://github.com/Hesykhia) | High-resolution screenshots and players-only mattes, Players compatibility fixes, hidden FFmpeg launches for still-image assembly with a clean DLL environment, and feedback on update controls. See merged [PR #16](https://github.com/cravvnn/deadlock-dolly/pull/16) and [#17](https://github.com/cravvnn/deadlock-dolly/pull/17). |

Thanks also to the players who have tested replays, reported bugs, shared
diagnostics and helped check how Dolly behaves on different systems.

## Inspiration and research references

| Project or source | How it helped |
| --- | --- |
| [HLAE / advancedfx](https://github.com/advancedfx/advancedfx) | A major reference for the capture, edit and replay camera-path workflow, paused camera input, and research into Source 2 camera timing and recording. Dolly's Deadlock integration uses its own reviewed hooks; HLAE is credited here as a workflow and technical reference. |
| Epic Games' [Curve Editor](https://dev.epicgames.com/documentation/en-us/unreal-engine/animation-curve-editor-in-unreal-engine) and [Cinematic Sequence](https://dev.epicgames.com/documentation/en-us/fortnite/using-cinematic-sequence-device-in-unreal-editor-for-fortnite) documentation | References for graph-editor organization and keeping shot authoring separate from playback. |
| [SteamDB's Deadlock tracking](https://github.com/SteamDatabase/GameTracking-Deadlock) | Publicly extracted command information used during console research. |
| [Valve](https://www.valvesoftware.com/) | Deadlock, Source 2, and the game and replay formats Dolly works with. |

The early workflow research is recorded in
[Console research](https://github.com/cravvnn/deadlock-dolly/blob/main/docs/internal/CONSOLE_RESEARCH.md).

## Libraries and components

| Project or people | Role in Dolly |
| --- | --- |
| [Dear ImGui](https://github.com/ocornut/imgui), Omar Cornut and contributors, including the bundled [stb](https://github.com/nothings/stb) components | The native in-game editor, font rendering and supporting UI code. Dolly retains upstream licenses and records its local vendor patches. |
| [MinHook](https://github.com/TsudaKageyu/minhook), Tsuda Kageyu and contributors | Native function hooking. |
| [cvar-unhide-s2-citadel](https://github.com/Artemon121/cvar-unhide-s2-citadel), Artemon121 and upstream contributors | The console unlocker. Dolly bundles a locally modified build with pinned source and patch records; its retained MIT notice credits Saul Rennison. |
| [DemoFile.Net](https://github.com/saul/demofile-net), Saul Rennison and contributors | Deadlock replay parsing for Dolly's player and kill-event index, through the `DemoFile.Game.Deadlock` package and DollyReplayIndex helper. |
| [FFmpeg](https://ffmpeg.org/) and the [BtbN FFmpeg builds](https://github.com/BtbN/FFmpeg-Builds) | Video and audio encoding, decoding, depth-master and preview encoding, and other media operations. The Windows package uses a pinned LGPL shared build. |
| [ReShade](https://github.com/crosire/reshade), Patrick Mours and contributors | Public API headers used for Dolly's ReShade integration. Users supply the ReShade runtime separately. |
| [crosire's ReShade shaders](https://github.com/crosire/reshade-shaders) and [prod80](https://github.com/prod80/prod80-ReShade-Repository) | The redistributable shader library supplied with Dolly, under CC0/public-domain and MIT terms respectively. |
| [Python](https://www.python.org/) and [Tcl/Tk](https://www.tcl-lang.org/) | Application runtime and desktop interface. |
| [PyInstaller](https://pyinstaller.org/) and its contributors | Packaging the portable Windows application. |
| [NumPy](https://numpy.org/), [SciPy](https://scipy.org/), [SoundFile](https://github.com/bastibe/python-soundfile) and [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) | Numerical processing, reconstructed audio and supporting media workflows. |
| [.NET](https://dotnet.microsoft.com/) and its contributors | Runtime and build tools for DollyReplayIndex. |

## Optional and development tools

| Project or tool | Role in Dolly |
| --- | --- |
| [Source 2 Viewer / ValveResourceFormat](https://github.com/ValveResourceFormat/ValveResourceFormat) | An optional, separately installed CLI used to extract assets for reconstructed clip audio. |
| [Capstone](https://www.capstone-engine.org/) and [pefile](https://github.com/erocarrera/pefile) | Compatibility research, disassembly and inspection of Windows executables and libraries. |
| [CMake](https://cmake.org/), [Visual Studio C++ tools](https://visualstudio.microsoft.com/vs/features/cplusplus/) and [clang-format](https://clang.llvm.org/docs/ClangFormat.html) | Building and maintaining the native helper. |
| [OpenAI ChatGPT](https://openai.com/chatgpt/) and [Codex](https://openai.com/codex/) | Reverse-engineering assistance for analyzing Deadlock binaries and researching camera, rendering and game-update compatibility. |

## Interface design

**Kara** designed the desktop interface, artwork treatments and in-game top rail.
The bundled [**Inter**](https://github.com/rsms/inter) typefaces are by The Inter Project Authors, under the
[SIL Open Font License 1.1](assets/fonts/inter/LICENSE.txt).

## Licenses, provenance and artwork

Dolly's code uses the [MIT license](https://github.com/cravvnn/deadlock-dolly/blob/main/LICENSE.txt).
This page is a credit record; components retain their own license terms and notices.
The detailed records live in:

- [Runtime notices](https://github.com/cravvnn/deadlock-dolly/tree/main/third_party/notices).
- [Native vendor licenses and pinned upstream records](https://github.com/cravvnn/deadlock-dolly/tree/main/native/vendor).
- [Unlocker provenance](https://github.com/cravvnn/deadlock-dolly/blob/main/third_party/cvar_unlocker/THIRD_PARTY.json) and [local modifications](https://github.com/cravvnn/deadlock-dolly/blob/main/third_party/cvar_unlocker/MODIFICATIONS.md).
- [Replay parser notice](https://github.com/cravvnn/deadlock-dolly/blob/main/native/replay_index/THIRD_PARTY_NOTICES.txt).
- [Shader library notices](https://github.com/cravvnn/deadlock-dolly/tree/main/third_party/reshade_shaders).
- [FFmpeg build and notice handling](https://github.com/cravvnn/deadlock-dolly/blob/main/tools/fetch_ffmpeg.py).
- [Artwork and supplied icon terms](https://github.com/cravvnn/deadlock-dolly/blob/main/assets/README.md), which are separate from the source-code license.

Git history cannot capture every early suggestion or testing contribution. If a
name or role is missing or inaccurate, please
[open an issue](https://github.com/cravvnn/deadlock-dolly/issues) or contact
**@Cravvnn on Discord** so this record can be corrected.
