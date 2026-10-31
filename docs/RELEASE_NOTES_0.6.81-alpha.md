### Deadlock Dolly v0.6.81-alpha

**0.6.81-alpha**

- **Bone camera framing.** *Before:* A snapped camera could slide or jump as its target moved. *After:* Newly snapped attachments keep their framing in the target's coordinate frame, and exact position offsets can extend beyond the sliders.

- **Attached camera controls.** *Before:* Framing could be disrupted by a target looking up or down, and preview movement was difficult to steer. *After:* Enable **Lock vertical aim** per Bone or Weapon camera and move relative to the displayed view. Thanks to @ExoCalibra for the contribution.

- **Players capture.** *Before:* Some exports could stop while seeking to the start, and Players stills could exceed the capture clock limit. *After:* Dolly can correct a verified small seek overshoot and uses a supported clock for Players stills.

- **Replay seeking and console output.** *Before:* A forward seek could settle one tick short, or repeated replay warnings could flood a command response. *After:* Dolly can make one guarded correction and filters the known warnings while keeping raw diagnostics. Thanks to @SamuelCurrid for the seek fix.
