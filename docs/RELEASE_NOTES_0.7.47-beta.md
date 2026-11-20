### Deadlock Dolly v0.7.47-beta

**0.7.47-beta**

- **Bone cameras hold their framing.** *Before:* a camera attached to a bone or weapon could lock straight down and rewrite its own pitch offset whenever the player looked above the horizon, the scene jumped about twice a second while the replay played, and turning came in small jerks. *After:* offsets change only when you move the camera, the view stops at straight up or down instead of tipping over, the scene stays steady, and turns are smooth.

- **Recordings are not lost by accident.** *Before:* closing Dolly or pressing Disconnect during a take left it half finished, pressing Finish early on a layered take still ran every remaining pass, and a real-time take that lost frames only showed an error. *After:* Dolly asks whether to finish, discard or keep recording, an early Finish skips the remaining passes, and Dolly offers to turn on fixed-step and record again.

- **Sessions recover on their own.** *Before:* after the game closed or a recorder failed mid-take, every later Record was refused until Dolly restarted, pressing Play replay in a running session disabled Record, Connect could fail until the game was closed, and one failed restore could raise an endless run of error boxes. *After:* Record, Connect and the session state come back by themselves and the error is shown once.

- **In-game editor and timeline.** *Before:* some shots made every in-game button stop responding, a click or the end of a drag was occasionally ignored, a new camera could be missing from the Shot timeline, Replace selected turned an attached camera into a free one and dropped its blend, and Undo after an attached flight stepped back a tiny amount. *After:* the buttons keep working, clicks register, new cameras show at once, Replace keeps the attachment and blend, and one flight is one Undo.
