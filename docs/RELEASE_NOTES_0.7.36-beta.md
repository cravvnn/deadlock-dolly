### Deadlock Dolly v0.7.36-beta

**0.7.36-beta**

- **Recording stop is consistent.** *Before:* stopping or cancelling right as a recording finished could leave the video on disk while Dolly said it was discarded, or delete colour but keep depth. *After:* Dolly makes the stop decision once, so the reported result always matches the files.

- **Steadier camera handoff.** *Before:* a momentarily busy camera status could fail a shot or a release, and a rare overlapping game callback could write the camera after release. *After:* Dolly keeps waiting through busy reads, and only one game callback drives the camera at a time.
