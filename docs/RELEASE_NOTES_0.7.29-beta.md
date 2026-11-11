### Deadlock Dolly v0.7.29-beta

**0.7.29-beta**

- **Reliable optional metadata.** *Before:* corrupt update completion metadata could block reuse, and nested UI-art JSON could escape fallback. *After:* Dolly bounds both reads and contains parser failures through normal skip or flat-colour paths.

- **Safer inspection and diagnostics.** *Before:* mixed-skin allowance could hide incomplete inspection, and growing dumps could exceed their read budget. *After:* failed inspections remain refusals and dump reads enforce their cap.

- **Honest source-launch results.** *Before:* an informational log failure could report failure after the editor started. *After:* the source launcher preserves the admitted PID while retaining pre-launch errors.
