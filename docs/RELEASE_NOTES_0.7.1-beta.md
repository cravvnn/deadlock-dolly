### Deadlock Dolly v0.7.1-beta

**0.7.1-beta**

- **Patched packaging dependency.** *Before:* the Windows bundle included setuptools 84.0.0, affected by a low-severity legacy installer path-escape issue. *After:* Dolly includes setuptools 84.1.0, which rejects invalid namespace package paths.

- **Fresh beta download.** *Before:* existing downloads still carried the affected dependency. *After:* the rebuilt package includes the fix and retains build 6849 compatibility and the existing camera, recording and audio behavior.
