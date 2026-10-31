### Deadlock Dolly v0.6.80-alpha

**0.6.80-alpha**

- **Experimental replay repair.** *Before:* some locally recorded demos crashed Deadlock while loading. *After:* use **Try replay repair...** in Replay to create a separate copy for a specific resource-loading issue. One previously crashing replay was tested successfully at both affected areas. The original stays untouched; test each repaired copy because results may vary.

- **More accurate video color.** *Before:* compressed exports could appear with shifted colors in editing software. *After:* SDR color recordings and alpha layers use Rec. 709 conversion and color information.

- **QuickTime recording.** *Before:* choosing MOV could be rejected when recording through FFmpeg. *After:* compressed MOV exports are accepted and carry the correct color information.
