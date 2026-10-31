### Deadlock Dolly v0.6.78-alpha

**0.6.78-alpha**

- **Discord Rich Presence.** *Before:* Discord could not show whether you were editing a shot or exporting from Dolly. *After:* your activity can show campath editing, previewing, video exporting and Players processing, with a session timer and links to Dolly and the editing community.

- **Control what you share.** *Before:* Dolly had no Discord activity control. *After:* Settings includes a Rich Presence switch. Replay names, project names and file paths stay private; Discord can close or restart without interrupting editing, and closing Dolly clears its presence.

- **Clean campath death effects.** *Before:* selected-player death effects could tint a normal campath export red and change its saturation on the current game build. *After:* Dolly uses the verified current effect controller and checks its identity before enabling camera ownership. Player POV retains its normal effects.

- **Players exports wait for the scratch drive.** *Before:* a brief scratch-write stall could stop a Players export with a misleading missed-frames error. *After:* fixed-step capture waits for the writer, preserves every frame, and names the scratch drive if a genuine timeout occurs.
