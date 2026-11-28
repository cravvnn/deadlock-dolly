### Deadlock Dolly v0.7.70-beta

**0.7.70-beta**

- **Support for Deadlock build 6883 (Nov 28 2026 update).** *Before:* opening a replay after the build 6883 Deadlock update stopped with an unsupported game build message and the 6882 cvar unlocker rejected the updated server binary. *After:* Dolly supports build 6883 with a reviewed client profile and an updated cvar unlocker (`v0.5.2-dolly-build-6883`) while keeping support for earlier reviewed builds.

- **Fixed Native DOF and Citadel DOF on Deadlock builds 6866–6883.** *Before:* applying Native DOF or Citadel DOF in Native camera mode failed immediately with a setting unavailable/clamped error after build 6866 moved `tier0.dll`'s cvar setter address, and rain-density seek relief was silently skipped. *After:* Dolly binds Season-2 cvar types directly from the reviewed game profile so Native DOF, Citadel DOF, and seek relief work across builds 6866–6883.

- **Clearer DOF fault diagnostics and retail shader logging.** *Before:* native DOF faults collapsed every cvar failure into one generic message, dropped the fault error on follow-up frames, and logged missing retail `slang.dll` as a warning right before the error. *After:* native faults name the exact setting and reason, preserve the fault snapshot in exported diagnostics, and log missing retail shader compilers at info level as expected behavior.
