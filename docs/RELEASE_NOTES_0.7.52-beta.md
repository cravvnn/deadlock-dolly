### Deadlock Dolly v0.7.52-beta

**0.7.52-beta**

- **Escape closes the picker first.** *Before:* pressing Escape in the in-game Bone Picker or Object Picker closed the picker and also left the Dolly panel. *After:* the first Escape only closes the picker, and the next one closes the panel as usual.

- **Slow playback speeds in every speed menu.** *Before:* the curve editor dropdown and the compact panel only offered 0.25x to 4x. *After:* they offer 0.05x and 0.1x too, like the other speed menus.

- **Closing Dolly while it is busy.** *Before:* closing while other work was running could stack two "Save changes?" prompts, pressing Cancel could still close Dolly, and a close could be ignored. *After:* there is one prompt, Cancel keeps Dolly open, and the close finishes once the other work is done.

- **In-game actions are no longer dropped.** *Before:* an action used in the game while Dolly was reading from it could show an error and be lost, and a Bone Picker left open when the connection dropped blocked Connect and Stop until the game closed. *After:* the action runs on the next tick and the Bone Picker is cancelled when the connection is lost.
