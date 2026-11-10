### Deadlock Dolly v0.7.24-beta

**0.7.24-beta**

- **Independent camera edits.** *Before:* saved camera keys could share mutable values with earlier edits. *After:* committing an edit keeps its camera keys independent.

- **Safer recording cleanup.** *Before:* a connection loss could leave source audio running, and recording startup or cleanup errors could hide the original failure. *After:* Dolly requests audio stop after the video grace period, retires refused recording workers, and preserves the original error and earlier completed video.

- **More resilient Players exports.** *Before:* valid timing data from longer takes could be rejected, and an unavailable audit folder could disrupt completed status. *After:* Dolly accepts larger timing records within a bounded read budget and retains completed status when the optional folder audit fails.

- **Reliable definitions fallback.** *Before:* deeply nested definitions could escape normal validation and interrupt startup or refresh. *After:* Dolly reports the validation error, falls back to bundled definitions on first load, and retains active definitions after a rejected refresh.
