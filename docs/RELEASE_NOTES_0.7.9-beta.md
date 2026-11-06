### Deadlock Dolly v0.7.9-beta

**0.7.9-beta**

- **Safer replay repair and helper setup.** *Before:* interrupted replay copies could expose partial output, and concurrent audio-helper setup attempts shared temporary files. *After:* replay repairs are verified before publication, and helper setup uses a separate workspace for each attempt.

- **Retryable Players status updates.** *Before:* an interrupted status write could leave a temporary file blocking the next update. *After:* each write owns its temporary file and preserves existing capture data when retrying.
