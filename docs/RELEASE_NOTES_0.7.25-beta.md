### Deadlock Dolly v0.7.25-beta

**0.7.25-beta**

- **Safer audio cancellation.** *Before:* an unconfirmed audio stop could discard captured WAV files, and repeated cancellation could send another stop command. *After:* Dolly keeps the files until stopping is acknowledged and avoids repeated stop commands after confirmation.

- **Resilient audio timing checks.** *Before:* oversized or deeply nested timing maps could escape normal validation. *After:* Dolly bounds the read and reports malformed timing data through the normal alignment error path.

- **Reliable capture results.** *Before:* an optional preview check or Players status-file error could obscure a completed recording or its original failure. *After:* Dolly preserves the completed result or primary failure and cancellation outcome when those secondary operations fail.

- **Recoverable layer submission.** *Before:* a rejected queue submission could lose pending layer work. *After:* Dolly retains the work for retry when rejection is confirmed, while holding uncertain submissions to avoid duplicate work.
