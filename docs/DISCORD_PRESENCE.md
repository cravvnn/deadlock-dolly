# Discord Rich Presence

Dolly can show what you are doing on your Discord profile while the Windows
editor is open. Keep the Discord desktop app running and enable activity sharing
in Discord. In Dolly, use **Settings → Discord Rich Presence → Show Dolly
activity on Discord** to turn it on or off. It is enabled by default.

The activity describes the current task: ready to create, preparing a replay,
editing or previewing a campath, exporting video, saving an export, or processing
the Players layer. Editing can include the number of cameras in the shot. A
timer shows how long Dolly has been open. **Get Dolly** and **Editing community**
buttons help friends find the app and the community.

Replay names, project names, file paths, player names, console output and export
destinations are never included. Dolly sends only these fixed activity phrases,
the camera count, session start time, public branding and public links. It
communicates locally with Discord; it does not request a Discord login or token.

Closing Dolly or turning the setting off clears its presence. If Discord is
closed or restarts, Dolly keeps editing normally and retries in the background.
Routine activity changes are combined and sent at most once every 15 seconds. Allow a
little time after changing tasks. Discord controls which activity it displays
when several applications are running, including Deadlock.

Discord does not show the custom buttons to their owner. Ask a friend to view
your profile when checking the buttons. See Discord's
[Rich Presence guide](https://github.com/discord/discord-api-docs/blob/main/developers/discord-social-sdk/development-guides/setting-rich-presence.mdx).

## Maintainer setup

The public Application ID in `dolly/discord_presence.py` belongs to the
**Deadlock Dolly** application registered in the
[Discord Developer Portal](https://discord.com/developers/applications).
The portal's application name controls the displayed app name. Set its icon to
Dolly's artwork. The large presence image uses the existing public Dolly logo.
No bot, OAuth redirect, account token, client secret, join or spectate permission
is required for this presence-only IPC integration.

The Windows worker uses the documented local `discord-ipc-0` through
`discord-ipc-9` named pipes. Requests require READY and matching acknowledgements;
reads, writes, responses, reconnects and shutdown are bounded. Presence is owned
by the desktop editor, independently of the native game DLL, heartbeat, camera,
render callbacks and export writer. Bundle self-tests do not publish a presence.

Protocol reference: [Discord RPC over IPC](https://github.com/discord/discord-api-docs/blob/main/developers/topics/rpc.mdx).
