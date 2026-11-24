### Deadlock Dolly v0.7.54-beta

**0.7.54-beta**

- **Weather drawn by Dolly in a new Scene tab.** *Before:* snow and rain were game particle presets that only followed the camera. *After:* the new **Scene** tab (in game and on the desktop) adds rain storms, snow, blizzards, sandstorms, dust haze, ground fog and ash, with wind that flows through the streets and around heroes, drifting haze and wet or snowy ground. It is saved with the shot and looks the same in playback, scrubbing and export.

- **Rain on the lens.** *Before:* there was no way to put water on the camera. *After:* turn on **Rain on the lens** under **Scene → Lens** during a rain storm: drops land on the glass, run down and leave streaks, stop landing under roofs, and come out the same every time you render the shot. Amount, drop size, slide, trails, refraction and softness are adjustable.

- **Effects layer stays clean with ReShade.** *Before:* with ReShade effects on, the Effects layer's transparency could come out wrong. *After:* the Effects layer is recorded without ReShade so its transparency is exact, while the Color take keeps your ReShade look.
