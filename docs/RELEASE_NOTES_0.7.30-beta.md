### Deadlock Dolly v0.7.30-beta

- **Reliable dropdown choices.** *Before:* a roster refresh or disabled control could accept an old popup row. *After:* selections require the same visible list and an enabled control.

- **Smoother camera deletion.** *Before:* deleting the first camera could jump interpolated effects to an earlier value. *After:* effects retain their evaluated value at the new start.

- **Consistent transition timing.** *Before:* replay-rate correction moved camera keys without scaling arrival blends. *After:* both scale together, and invalid changes leave the shot intact.

- **Clearer paired take audits.** *Before:* conflicting color and depth frame counts could pass validation. *After:* Dolly reports disagreements while preserving the finished recording and its frames.
