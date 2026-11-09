### Deadlock Dolly v0.7.21-beta

**0.7.21-beta**

- **Frame-accurate export by default.** *Before:* recording could run in real time and miss frames when capture could not keep up. *After:* fixed-step export is enabled by default, allowing the shot to render more slowly while preserving its frames.

- **Clear failures for incomplete takes.** *Before:* missed captures could produce a shortened video that appeared successfully saved. *After:* fixed-step capture stops on queue timeouts, and fixed-step or FFmpeg takes with detected frame loss are rejected instead of saved as complete.

- **Accurate finishing duration.** *Before:* the displayed duration could omit frames written during final cleanup. *After:* the duration includes the final frames written by FFmpeg.
