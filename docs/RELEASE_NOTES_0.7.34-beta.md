### Deadlock Dolly v0.7.34-beta

**0.7.34-beta**

- **Safer shutdown.** *Before:* a background connection heartbeat could try to write after its shared connection had closed. *After:* closing waits for an active heartbeat write to finish before releasing the connection.

- **Stronger regression coverage.** *Before:* several camera, capture and connection handoffs lacked dedicated automated checks. *After:* added checks cover those handoffs and guard against the shutdown race returning.
