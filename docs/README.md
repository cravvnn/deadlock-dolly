# Dolly documentation

These guides describe the current source and its desktop and in-game editors.
Start with the user guide, then choose the guide for the task you are doing.
If your installed version differs, check its release notes or update Dolly;
older screenshots and per-version notes may show controls that have changed.

| Task | Current guide |
| --- | --- |
| Find controls in Kara's desktop redesign and the in-game top rail | [Redesigned interface](UI_REDESIGN.md) |
| Install, open a replay, capture cameras, use shortcuts and recover a session | [User guide](USER_GUIDE.md) |
| Use Camera, Follow, Bone Picker, Look, Export, Campaths and the timelines | [In-game editor](UI_REWORK.md) |
| Attach a camera to a replay participant's bone | [Bone Picker](BONE_PICKER.md) |
| Record video, choose an encoder, capture audio and use ReShade | [Video and ReShade](VIDEO_AND_RESHADE.md) |
| Export the recorded camera to After Effects, Blender or other USD apps | [Export the camera](VIDEO_AND_RESHADE.md#export-the-camera) |
| Export a still, players-only matte or cut-out hero | [Screenshots](SCREENSHOTS.md) |
| Export Depth, World, Players or Effects, and understand their limits | [Layer export](LAYER_EXPORT.md) |
| Use paired camera metadata and float depth in compositing | [Camera bundle](CAMERA_BUNDLE.md) |
| Check the framing and depth-of-field controls Dolly exposes | [Camera control reference](SUPPORTED_CAMERA_CVARS.md) |
| Check for updates, install an update or recover an interrupted update | [Updating and recovery](UPDATING.md) |
| Understand, publish or diagnose game-build definitions | [Published build definitions](DEFINITIONS.md) |

The [repository README](../README.md) is the quick start. The Windows ZIP also
includes `Start_Here.txt` and selected export and update guides. Settings,
custom shortcuts and saved layouts can differ from the defaults in these guides.

## Development and credits

- [Building](BUILDING.md) covers source setup and the Windows toolchain.
- [Contributing](../CONTRIBUTING.md) covers changes and verification.
- [Credits](../CREDITS.md) records contributors, references and bundled libraries.
- [Maintainer references](internal/README.md) cover implementation details,
  compatibility work, validation records and archived plans.

## Release history

[Published releases](https://github.com/cravvnn/deadlock-dolly/releases) and the
[changelog](CHANGELOG.md) describe what changed in each version. The
`RELEASE_NOTES_<version>.md` files and versioned video walkthroughs are historical
records, rather than current setup instructions. To inspect documentation and
code for a particular release, select its matching Git tag in the repository.
