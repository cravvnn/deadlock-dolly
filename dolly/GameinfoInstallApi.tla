---- MODULE GameinfoInstallApi ----
\* Caller-visible state of Dolly's game-install transaction: which gameinfo.gi
\* the install holds (cfg), whether Dolly's temporary plugin folder exists
\* (plugin) and whether the launched game runs (alive).
\* cfg: "original" (the user's file), "dolly" (Dolly's base file mounting the
\* plugin), "other" (changed outside Dolly) and "otherRef" (changed outside
\* Dolly but still mounting the plugin).
\* Refined by GameinfoTransaction (launch, restore, exit watcher, helper and
\* reopen recovery). Composed by DollyRoot. No promise lives here: the steps
\* only say what can happen; callers state what must.
\* Mount: Dolly's file and folder go in before the game starts.
\* Start/Exit: the launched game starts (with the folder in place) and exits.
\* Restore: Dolly's own file goes back to the original; an outside change is
\*   never touched (no step turns other/otherRef into original except Resolve).
\* Edit: a mod manager or Steam check rewrites Dolly's file.
\* Remove: the folder goes only with no game running and no file mounting it.
\* Resolve: the user puts the original back in an open Dolly (no game running).
VARIABLES cfg, plugin, alive
vars == <<cfg, plugin, alive>>
Init == /\ cfg \in {"original", "dolly"} /\ plugin \in BOOLEAN /\ alive \in BOOLEAN
        /\ (cfg = "dolly" => plugin) /\ (alive => plugin)
Mount == /\ ~alive /\ cfg = "original" /\ cfg' = "dolly" /\ plugin' = TRUE /\ UNCHANGED alive
Start == /\ ~alive /\ plugin /\ alive' = TRUE /\ UNCHANGED <<cfg, plugin>>
Exit == /\ alive /\ alive' = FALSE /\ UNCHANGED <<cfg, plugin>>
Restore == /\ cfg = "dolly" /\ cfg' = "original" /\ UNCHANGED <<plugin, alive>>
Edit == /\ cfg = "dolly" /\ cfg' \in {"other", "otherRef"} /\ UNCHANGED <<plugin, alive>>
Remove == /\ ~alive /\ plugin /\ cfg \in {"original", "other"} /\ plugin' = FALSE
          /\ UNCHANGED <<cfg, alive>>
Resolve == /\ ~alive /\ cfg \in {"other", "otherRef"} /\ cfg' = "original"
           /\ UNCHANGED <<plugin, alive>>
Spec == Init /\ [][Mount \/ Start \/ Exit \/ Restore \/ Edit \/ Remove \/ Resolve]_vars
====
