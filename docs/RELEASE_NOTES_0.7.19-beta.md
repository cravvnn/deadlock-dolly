### Deadlock Dolly v0.7.19-beta

**0.7.19-beta**

- **Clearer recording errors.** *Before:* a recording failure could show only FFmpeg's final summary and hide the earlier diagnostic. *After:* Dolly keeps the first diagnostic and labels paired color/depth failures more clearly. This improves reporting; it does not fix the underlying NVIDIA encoder failure.

- **Safer saves and support reports.** *Before:* temporary-file cleanup could hide a save error, and a competing support report could be overwritten. *After:* saves retain their original result and support reports preserve existing files. Saving an unchanged shot also avoids an extra history copy.

- **More reliable recording preparation.** *Before:* ignored game timing commands or a failed recording worker start could leave preparation in an incorrect state. *After:* timing changes require readback and worker startup failures release their provisional files for retry.

- **Stronger startup checks.** *Before:* replaced game files could reuse stale cached data, and deeply nested configuration files could escape normal error handling. *After:* cache entries track file identity and stay within their retention budget, while nesting failures use the normal recovery and discovery paths.
