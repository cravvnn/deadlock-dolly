"""Coordinate native-editor publication, dispatch and ordered acknowledgements."""
import logging
import time
from .native_bridge import NativeBridgeError, NativeEditorStatusBusy, NativeStatusBusy
from . import bone_picker, editor_publication, editor_dispatch
from .editor_publication import _bridge

LOG = logging.getLogger(__name__)


def configure(app):
    return editor_publication.configure(app)


def dispatch(app, event, bridge):
    return editor_dispatch.dispatch(app, event, bridge, publish=lambda: configure(app))


def snap_is_current(app, bridge):
    context = getattr(app, "_attach_snap_context", None)
    if not context or context["bridge"] is not bridge or context["project"] is not app.project:
        return False
    index = context["index"]
    return (app._selection_index(app.camera_tree) == index and
            0 <= index < len(app.project.keyframes) and
            app.project.keyframes[index] == context["key"])


def snap_result_current(app, bridge, config_sequence):
    """Whether a snap result answers this request or a later republish of it.

    Roster refreshes may republish the attach block while native is still
    answering, so the latest sequence alone would reject a valid result.
    """
    latest = getattr(bridge, "_editor_attach_sequence", None)
    if latest is None or config_sequence is None:
        return False
    first = (getattr(app, "_attach_snap_context", None) or {}).get("sequence")
    if first is None:
        return config_sequence == latest
    return (latest - config_sequence) & 0xFFFFFFFF <= (latest - first) & 0xFFFFFFFF


SNAP_FAILURES = {
    1: "Attach here could not keep this view: the camera is too far from the player. Fly closer and try again.",
    2: "Attach here could not read the player at this moment. Try again.",
}


def cancel_snap(app, message):
    app._attach_snap_pending = False
    app._attach_snap_started = None
    app._attach_snap_context = None
    if message:
        app.status_text.set(message)


def poll(app):
    if getattr(app, "closed", False) or getattr(app, "_editor_closed", False):
        return
    bridge = _bridge(app)
    if bridge is None:
        app.native_editor_active = False
        cancel_snap(app, None)
        bone_picker.discard(app)
        app._native_editor_bridge = None
        app._native_visualization_bridge = None
        app._native_visualization_cache = None
        return
    try:
        if getattr(app, "_attach_snap_pending", False) and not snap_is_current(app, bridge):
            cancel_snap(app, "Attach cancelled because the selected camera changed.")
        # Flight can fail before the controller marks the editor active even
        # though DX11/input finish initializing afterwards. Keep that live
        # panel's console/stop/retry actions connected to the launcher. Do not
        # interfere with the startup worker while it is still arming flight.
        if (not getattr(app, "native_editor_active", False) and not app.busy
                and app.controller.status().get("connected")):
            recovery = bridge.editor_status()
            if recovery.get("enabled"):
                app.native_editor_active = True
                if hasattr(app, "_disable_external_input"):
                    app._disable_external_input()
        configure(app)
        if not getattr(app, "native_editor_active", False):
            app._native_editor_error = None
            return
        # The attach target picker refreshes from the native roster at a low
        # rate; a stale or unavailable block must never break camera control.
        roster_now = time.monotonic()
        if roster_now - getattr(app, "_attach_roster_at", 0.0) >= 2.0:
            app._attach_roster_at = roster_now
            roster = None
            busy = False
            reader = getattr(bridge, "editor_roster", None)
            if callable(reader):
                try:
                    roster = reader()
                except NativeStatusBusy:
                    busy = True  # Keep the current picker list until the next refresh.
                except (NativeBridgeError, ValueError, OSError):
                    roster = None
            if not busy:
                app.attach_roster = roster
                handler = getattr(app, "_attach_roster_changed", None)
                if callable(handler):
                    handler(roster)
            bones = None
            busy = False
            reader = getattr(bridge, "editor_bones", None)
            if callable(reader):
                try:
                    bones = reader()
                except NativeStatusBusy:
                    busy = True  # Keep the current bone list until the next refresh.
                except (NativeBridgeError, ValueError, OSError):
                    pass
            if not busy:
                app.attach_bones = bones
                handler = getattr(app, "_attach_bones_changed", None)
                if callable(handler):
                    handler(bones)
        # Native snap / attached-fly offsets feed the selected attach key.
        result_reader = getattr(bridge, "editor_attach_result", None)
        if callable(result_reader):
            attach_result = None
            try:
                attach_result = result_reader()
            except (NativeBridgeError, ValueError, OSError):
                attach_result = None
            if attach_result and attach_result.get("valid") and not getattr(app, "_bone_picker_context", None):
                sequence = attach_result.get("sequence")
                identity = (bridge, sequence)
                if sequence and identity != getattr(app, "_attach_result_sequence", None):
                    app._attach_result_sequence = identity
                    handler = getattr(app, "_attach_result_changed", None)
                    if callable(handler):
                        handler(attach_result)
            elif attach_result and attach_result.get("snap_failure"):
                identity = (bridge, attach_result.get("sequence"))
                if identity != getattr(app, "_attach_result_sequence", None):
                    app._attach_result_sequence = identity
                    if (getattr(app, "_attach_snap_pending", False) and
                            snap_result_current(app, bridge, attach_result.get("config_sequence"))):
                        cancel_snap(app, SNAP_FAILURES.get(attach_result["snap_failure"], SNAP_FAILURES[2]))
        snap_started = getattr(app, "_attach_snap_started", None)
        if (getattr(app, "_attach_snap_pending", False) and snap_started is not None
                and time.monotonic() - snap_started > 5):
            cancel_snap(app, "Attach here did not finish. Pause, check the player, and try again.")
        status = bridge.editor_status()
        app._native_editor_status = status
        # The native counter only grows within one game process. A new bridge
        # reporting fewer drops means the game restarted with its own count.
        dropped = status.get("dropped_events", 0)
        seen_bridge, seen = getattr(app, "_native_editor_dropped", (None, 0))
        app._native_editor_dropped = (bridge, dropped)
        if dropped > seen or (seen_bridge is not bridge and 0 < dropped < seen):
            app.status_text.set("The editor was busy; an in-game action was ignored. Retry the action.")
        for event in status.get("events", ()):
            if app.busy and event.get("action") != "save_shot":
                break
            try:
                accepted = dispatch(app, event, bridge)
            except NativeStatusBusy:
                # A torn native read happens before the handler changes
                # anything. Keep the event queued and retry on the next tick.
                break
            except (RuntimeError, ValueError, OSError) as exc:
                LOG.exception("Native editor action failed: %s", event["action"])
                app._error("In-game editor", exc)
                accepted = True  # Do not replay a failed action on every tick.
            if not accepted:
                break
            bridge.acknowledge_editor_event(event["sequence"])
        app._native_editor_error = None
    except NativeEditorStatusBusy:
        return
    except NativeBridgeError as exc:
        if str(exc) != getattr(app, "_native_editor_error", None):
            app._native_editor_error = str(exc)
            LOG.error("Native editor: %s", exc)
            app.status_text.set(str(exc))
    except (ValueError, OSError) as exc:
        if str(exc) != getattr(app, "_native_editor_error", None):
            app._native_editor_error = str(exc)
            LOG.exception("Native editor configuration failed")
            app.status_text.set(str(exc))


def close(app):
    app._editor_closed = True  # The window closes after this; see configure.
    app._bone_picker_context = None
    from . import curve_preview
    curve_preview.clear(app)
    bridge = _bridge(app)
    if bridge is not None:
        try:
            bridge.configure_editor(enabled=False, owner="disabled")
        except (NativeBridgeError, OSError, ValueError):
            pass
    app.native_editor_active = False
    app._native_editor_config_cache = None
    app._native_visualization_bridge = None
    app._native_visualization_cache = None
