### Deadlock Dolly v0.7.23-beta

**0.7.23-beta**

- **More reliable Players exports.** *Before:* complex moments in a replay could exhaust capture slots and interrupt the player layer. *After:* Dolly reuses completed, verified draw slots so busy scenes can continue capturing safely.

- **Prompt capture failure recovery.** *Before:* a failed player layer could linger in Recording while waiting for a timeout. *After:* Dolly reports the failure promptly and begins cleanup as soon as the outstanding capture work finishes.

- **Accurate export results.** *Before:* finishing cleanup could label a failed player capture as completed. *After:* the export remains marked failed, incomplete output is rejected, and settings are restored before another capture starts.
