---- MODULE CameraLeaseApi ----
\* Caller-visible native camera ownership: the controller lease
\* (Controller._native_active / _release_native_camera), whether Dolly's native
\* callback still writes the game camera (bridge_win.cpp on_view), and whether
\* the game's effects/lens were restored (gEffects.restore in finish()).
\* Refined by ReleaseJoin (controller/NativeBridge.release/on_view join).
\* Composed by DollyRoot. No invariant lives here: callers state promises.
\* Acquire: a new shot begins writing; it does NOT require a retired lease, so
\*   the caller's own admission guard is what prevents an early new writer.
\* NativeRelease: a Release (or heartbeat-expiry fault) callback stops writing;
\*   restoration may fail (Fault 36) and is then retried every frame (Restore).
\* Retire: the lease ends only after writing stopped and effects are restored.
\* Exit/Lost: the game process exits; only then may the lease end without
\*   restoration (recover_dead_session checks the process, not a timeout).
VARIABLES lease, writer, restored, alive
vars == <<lease, writer, restored, alive>>
Init == /\ lease \in BOOLEAN /\ writer \in {"none", "shot"} /\ restored \in BOOLEAN
        /\ alive = TRUE
        /\ (~lease => (writer = "none" /\ restored))
Acquire == /\ alive /\ lease' = TRUE /\ writer' = "shot" /\ restored' = FALSE /\ UNCHANGED alive
NativeRelease == /\ lease /\ writer' = "none" /\ restored' \in BOOLEAN
                 /\ UNCHANGED <<lease, alive>>
Restore == /\ alive /\ writer = "none" /\ ~restored /\ restored' = TRUE
           /\ UNCHANGED <<lease, writer, alive>>
Retire == /\ lease /\ writer = "none" /\ restored /\ lease' = FALSE
          /\ UNCHANGED <<writer, restored, alive>>
Exit == /\ alive /\ alive' = FALSE /\ writer' = "none" /\ UNCHANGED <<lease, restored>>
Lost == /\ ~alive /\ lease /\ lease' = FALSE /\ UNCHANGED <<writer, restored, alive>>
Spec == Init /\ [][Acquire \/ NativeRelease \/ Restore \/ Retire \/ Exit \/ Lost]_vars
====
