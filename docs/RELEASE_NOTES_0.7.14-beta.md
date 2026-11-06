### Deadlock Dolly v0.7.14-beta

**0.7.14-beta**

- **Clear scene controls.** *Before:* glow, health bars and near-player opacity used action buttons, and the opacity fix had no off switch. *After:* matching checkboxes show Dolly's choices, the opacity fix restores the original fade values when switched off, and Clear stuck effects remains a button.

- **Safer still exports.** *Before:* cleanup could remove unexpected intermediate-folder content, and selected Players frames had incomplete format checks. *After:* Dolly preserves unknown files and checks the selected frame before writing the still; terminal cleanup failures report leftovers without hiding a saved output.

- **More reliable replay editing.** *Before:* a replacement bridge could lose its first attach result, free-camera shots built unused attach intervals, and destroyed scroll widgets could retain callbacks. *After:* attach results follow their bridge, free-camera shots skip unused work, and scroll callbacks follow widget ownership.

- **Stronger failure handling.** *Before:* early bridge cleanup errors could strand other cleanup, provisional media initialization could retain a mapping, huge numeric preferences could bypass normal fallback, and unreadable mod entries could be overlooked. *After:* remaining bridge cleanup still runs, provisional mappings close explicitly, invalid preferences use normal fallback, and unchecked mod entries are refused in the default mode.
