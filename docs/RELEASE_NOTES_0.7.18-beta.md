### Deadlock Dolly v0.7.18-beta

- **Safer reconstructed audio.** *Before:* a silent stereo channel could spoil voice fitting. *After:* either channel can stay silent while fitting produces finite audio, and invalid samples are rejected.

- **More reliable audio conversion.** *Before:* bundled library settings or an inaccessible custom encoder path could interrupt audio preparation. *After:* asset converters use the external-program environment and encoder lookup can fall back to an available copy.

- **Clearer extraction failures.** *Before:* cleanup of a failed sound could hide its original error and stop later assets. *After:* extraction retains the original failure, attempts the remaining sounds and reports partial results. Filtered voice logs also preserve a competing output file.

- **Lean export preparation.** *Before:* voice-log parsing, WAV hashing and camera marker lookup made avoidable copies. *After:* these steps read incrementally or iterate adjacent frames without those extra copies.
