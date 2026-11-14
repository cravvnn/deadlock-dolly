# Formal accounting and remaining obligations

This review draft accounts for309implementation/tool units. All source exports
have explicit roles in FORMAL_COVERAGE.json. This is accounting, not full-file
semantic review or whole-program proof. Test-reference leads are navigation,
not execution coverage. No row has full formal signoff.

The draft has82bounded model scopes/96configurations, with244expected failing
mutants (0.7.35 added CameraOnlyCapture/CravCapture; the 2026-11-13 native sweep
added 17 across VideoCompleteness, NativeSnapshotClose, MediaDetach,
StatusSnapshot, ReleaseJoin, BridgeClose and CameraOnlyCapture). The seven additions cover prepared recording, release/retry, worker
retirement, capture restoration, heartbeat mapping lifetime, shared native
snapshot residence and coherent status readback. SPECS.md states their bounds.
The main-view callback is no longer assumed serialized: on_view admits one
callback at a time, and NativeSnapshotClose checks two callback threads. Other
native models (DrawProof, CaptureFailure) still assume serialized immediate-context
callbacks. VideoCompleteness models x86-TSO store buffering for the encoder stop
handshake only. None of them prove the C++ memory model or game/GPU behavior. The camera worker model excludes
self-halt and arbitrary concurrent callers. These assumptions are open work.

The ledger distinguishes39modules without independent lifecycles,10generated
inputs,27offline tools,4entrypoints and1child job from120Python/UI and108native/
delegated partial/open owner records. These are dispositions, not pass rates.

L01camera/session still needs manual/hold/play, self-halt/reentrancy and native
publication composition. L02media still needs GPU/drain/device-removal and native
buffer lifetime. L03storage needs full caller-lock/crash composition. L04UI needs
complete queued/modal/timer interfaces. L05process integration needs reconnect/
shutdown composition. L06tools retains actual generation/build/package evidence.
L07root end-to-end refinement is open: no whole-program proof exists.

The keeper checks implementation/export/model accounting and normalized owner
fingerprints. Source changes require semantic review of dispositions, caller
assumptions, models and tests before refreshing affected hashes. Never bulk-refresh
hashes as a substitute for this review. UTF8 optional BOM is stripped and CRLF
normalized to LF; checkout EOL differences do not change source identity.

The heartbeat fix adds synchronization, not an equivalent-state reduction.
An in-flight store may extend close beyond its timed join. Normal model deadlock
checks/terminal stuttering remain enabled. Fairness of returning abstract steps
does not prove real callback/I/O termination. Held/user/live obligations and
disabled Object Picker work remain separate from this accounting.
