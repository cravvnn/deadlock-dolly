### Deadlock Dolly v0.6.83-alpha

**0.6.83-alpha**

- **Optional NVIDIA DLSS.** *Before:* Launching Deadlock through Dolly could hide NVIDIA DLSS from the game's Video settings. *After:* Enable NVIDIA DLSS support in Settings, beneath Replay Graphics Profiles, then choose DLSS and its scaling mode in the game's Video settings. The switch applies to the next launch and supports both previews and recorded Color video on compatible NVIDIA GPUs.

- **Retained launch diagnostics.** *Before:* Disabling redirected game output to make DLSS available could lose useful error details. *After:* Dolly keeps stderr, engine console output, received console text and native-hook diagnostics with DLSS enabled. Export diagnostics includes the new logs. Turn DLSS support off before a new launch to restore standard output capture when troubleshooting; very early stdout-only messages can still be unavailable with it on.

- **Quiet local builds.** *Before:* Automated UI checks could open windows over the desktop during a build. *After:* Windows builds keep the checks running on a private desktop, retaining their output and failure reporting.

Thanks to [@Moelandblue](https://github.com/Moelandblue) for identifying the stdout-redirection trigger in [issue #18](https://github.com/cravvnn/deadlock-dolly/issues/18).
