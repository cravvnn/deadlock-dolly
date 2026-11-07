### Deadlock Dolly v0.7.16-beta

**0.7.16-beta**

- **Capture shortcuts recover cleanly.** *Before:* a capture shortcut could remain unavailable after its worker failed to start. *After:* refused starts restore the controls while accepted workers retain ownership until they finish.

- **More reliable movie audio.** *Before:* bundled runtime library settings could interfere with audio conversion. *After:* both movie-audio conversion steps use the external-program environment.

- **Safer fades and previews.** *Before:* failed Windows drawing setup could retain resources, and invalid preview coordinates or fade buffers could reach drawing. *After:* failed setup cleans up its resources, fade rows stay within their viewport, and invalid drawing inputs are rejected.

- **More robust asset discovery.** *Before:* duplicate matching archive resources could be accepted and an inaccessible viewer candidate could stop discovery. *After:* ambiguous resources are rejected and viewer discovery continues past inaccessible candidates.
