"""Replay-only camera controller with console and native view playback.

The camera command backend is experimental until checked on the installed game.
Playback follows acknowledged demo ticks with a bounded one-tick interpolation
window. Console commands are not synchronized with renderer frames.
"""
from __future__ import annotations

from contextlib import contextmanager
from collections import deque
from copy import deepcopy
from dataclasses import asdict
import json
import logging
import math
from pathlib import Path
import re
import threading
import time
import zipfile

from . import __version__
from .path import Project, Keyframe, validate_cvar_name, STANDARD_ASPECT, ASPECT_MIN, ASPECT_MAX
from .console import ConsoleClient, error_text, parse_demo_info, parse_demo_tick
from .playback import ReplayClock
from .smoothing import PhaseSmoother, smoothing_window
from .display import client_aspect_ratio
from .navigation import CameraMotion, move_camera
from .pacing import FrameWait
from .runtime import application_root
from . import launcher
from .native_effects import compile_effects

ROOT = application_root(Path(__file__).resolve().parents[1])
LOG = logging.getLogger("dolly")
NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
ASPECT_CVAR = "r_aspectratio"
LEGACY_FOV_CONTROLS = {"citadel_camera_fov", "citadel_camera_spectator_fov"}
DOF_RANGES = {
    "r_citadel_depthoffield_enable": (0, 1),
    "r_citadel_depthoffield_aperture_diameter": (0, 3),
    "r_citadel_depthoffield_focus_distance": (0, 10000),
    "r_citadel_depthoffield_mode": (0, 2),
    "r_citadel_depthoffield_sensor_size": (0.5, 3),
}
DISCRETE = {"r_citadel_depthoffield_enable", "r_citadel_depthoffield_mode",
            "r_depth_of_field", "r_dof_override", "cl_lock_camera"}
CAMERA_AXES = ("x", "y", "z")
CAMERA_SETTLE_INTERVAL = .05
CAMERA_SETTLE_SAMPLES = 3
CAMERA_SETTLE_LIMIT = 18
CAMERA_POSITION_TOLERANCE = .5
CAMERA_STABILITY_TOLERANCE = .2
CAMERA_MAX_OFFSET = 256.0
CAMERA_HEIGHT_PROBE = 16.0
CAMERA_CORRECTION_LIMIT = 12
SEEK_SETTLE_INTERVAL = .04
SEEK_SETTLE_SAMPLES = 3
CAMERA_READBACK_INTERVAL = .25
CAMERA_DRIFT_LIMIT = 64.0
CAMERA_DRIFT_SAMPLES = 3
NATIVE_PAUSE_TIMEOUT = 3.0


class CameraPositionError(RuntimeError):
    """A measured position response failed, eligible for one paused refresh."""


def motion_clock_info():
    # Python 3.12's Windows monotonic clock can advance in 15/16 ms steps.
    # All motion timestamps use the same high-resolution QPC-backed domain.
    info = time.get_clock_info("perf_counter")
    return {"name": "perf_counter", "implementation": info.implementation,
            "resolution_seconds": info.resolution, "monotonic": info.monotonic}


def numeric(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("A camera value is not finite.")
    return format(value, ".9g")


def _tail_file(path, limit=2_000_000):
    with Path(path).open("rb") as stream:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - limit))
        return stream.read(limit)


def read_cvar_value(name, output):
    """Parse the current value, never a value from the default/range description."""
    escaped = re.escape(name)
    for line in output.splitlines():
        match = re.search(r'(?<![\w])"?' + escaped + r'"?\s*(?:=|:)\s*"?(' + NUMBER + r'|true|false)(?=["\s,)\]]|$)', line, re.I)
        if match:
            raw = match.group(1).lower()
            value = 1.0 if raw == "true" else 0.0 if raw == "false" else float(raw)
            if math.isfinite(value):
                return value
    raise ValueError(f"Could not read the current value of {name}. Export diagnostics.")


def parse_camera(output, fov=90.0, roll=0.0, *, aspect_ratio=STANDARD_ASPECT):
    """spec_pos produces spec_goto X Y Z pitch yaw; getpos has a second form."""
    match = re.search(r"\bspec_goto\s+" + r"\s+".join([f"({NUMBER})"] * 5), output)
    if match:
        xyzpy = [float(v) for v in match.groups()]
        frame = Keyframe(0.0, *xyzpy, float(roll), float(fov), aspect_ratio=float(aspect_ratio))
        Project(keyframes=[frame]).validate()
        return frame
    match = re.search(r"\bsetpos(?:_exact)?\s+" + r"\s+".join([f"({NUMBER})"] * 3)
                      + r"\s*;\s*setang(?:_exact)?\s+" + r"\s+".join([f"({NUMBER})"] * 3), output)
    if match:
        frame = Keyframe(0.0, *[float(v) for v in match.groups()], float(fov), aspect_ratio=float(aspect_ratio))
        Project(keyframes=[frame]).validate()
        return frame
    raise ValueError("The spectator camera position could not be read. Enter replay freecam, then export diagnostics if Capture still fails.")


def parse_camera_readback(output, *, roll, aspect_ratio):
    """Accept a printed position, never the outgoing multi-command echo."""
    clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
    pattern = (r"^[ \t]*(?:\[[^\]\r\n]*\][ \t]*)*spec_goto[ \t]+"
               + r"[ \t]+".join([f"({NUMBER})"] * 5) + r"[ \t\r]*$")
    matches = list(re.finditer(pattern, clean, re.M))
    if not matches:
        raise ValueError("The camera readback did not contain a printed spec_pos position. Movement stopped; export diagnostics.")
    return parse_camera("spec_goto " + " ".join(matches[-1].groups()),
                        roll=roll, aspect_ratio=aspect_ratio)


def frame_commands(frame, lens_cvar=ASPECT_CVAR):
    if lens_cvar != ASPECT_CVAR:
        raise ValueError("Framing uses r_aspectratio. Degree-based FOV controls are no longer supported.")
    aspect = float(frame["aspect_ratio"])
    if not math.isfinite(aspect) or not ASPECT_MIN <= aspect <= ASPECT_MAX:
        raise ValueError(f"Aspect ratio must be between {ASPECT_MIN:g} and {ASPECT_MAX:g}. These are the editor's framing limits.")
    commands = ["spec_goto " + " ".join(numeric(frame[k]) for k in ("x", "y", "z", "pitch", "yaw")),
                "cl_citadel_forceangles " + " ".join(numeric(frame[k]) for k in ("pitch", "yaw", "roll")),
                ASPECT_CVAR + " " + numeric(aspect)]
    for name, value in sorted(frame.get("cvars", {}).items()):
        validate_cvar_name(name)
        if name == ASPECT_CVAR or name in LEGACY_FOV_CONTROLS:
            raise ValueError("Use the Framing curve for aspect ratio. Legacy FOV and duplicate aspect tracks are disabled.")
        if name in DOF_RANGES:
            lo, hi = DOF_RANGES[name]
            if not lo <= float(value) <= hi:
                raise ValueError(f"{name} must be between {lo:g} and {hi:g}.")
        if name in DISCRETE and float(value) != int(float(value)):
            raise ValueError(f"{name} needs whole-number values and a Step track.")
        commands.append(name + " " + numeric(value))
    return "; ".join(commands)


class Controller:
    def __init__(self, log_callback=None):
        self._log_callback = log_callback
        self._session = None
        self._native_active = False
        self._native_manual = False
        self._game_ui_visible = False
        self._game_ui_restore = {}
        self._console_open = None
        self._native_last_status = {}
        self._native_handoff_details = {}
        self._console = None
        self._protocol = "netcon"
        self._port = 29090
        self._demo = None
        self.lens_cvar = ASPECT_CVAR
        self.standard_aspect = STANDARD_ASPECT
        self._aspect_capture = {}
        self._restore = {}
        self._demo_speed_changed = False
        self._playback_restore = {}
        self._playback_details = None
        self._playback_metrics = {}
        self._playback_samples = deque(maxlen=256)
        self._paused_samples = deque(maxlen=256)
        self._recent_camera_targets = deque(maxlen=64)
        self._next_camera_readback = 0.0
        self._camera_drift_samples = 0
        self._last_seek_details = {}
        self._applied_pose = None
        self._camera_offset = dict.fromkeys(CAMERA_AXES, 0.0)
        self._camera_calibration = {}
        self._paused_pose = None
        self._paused_tick = None
        self._paused_details = {}
        self._paused_cancelled = None
        self._probe_result = {}
        self._last_output = {}
        self._launch_attempt = None
        self._unlocker_pid = None
        self._startup_evidence = {}
        self._replay_requested = False
        self._stop_event = threading.Event()
        self._thread = None
        self._state_lock = threading.RLock()
        self._op_lock = threading.RLock()
        self._state = dict(connected=False, playing=False, time=0.0, tick=None,
                           paused_camera=False, paused_flight=False, paused_tick=None,
                           startup_stage="not_launched",
                           message="Launch the hideout, connect and initialize the unlocker before loading your replay.")

    def _message(self, message, **changes):
        with self._state_lock:
            self._state.update(message=message, **changes)
        LOG.info(message)
        if self._log_callback:
            self._log_callback(message)

    def status(self):
        with self._state_lock:
            state = dict(self._state)
        state["connected"] = bool(self._console and self._console.is_connected and self._alive())
        state["unlocker_ready"] = bool(self._alive() and self._unlocker_pid == self._session.pid)
        state["camera_backend"] = "native" if self._native_bridge() is not None else "console"
        state["native_holding"] = self._native_active and not state.get("playing", False)
        state["native_editor_active"] = self._native_manual
        state["game_ui_visible"] = self._game_ui_visible
        state["game_running"] = self._alive()
        if self._session and not self._alive():
            exit_code = self._session.process.poll()
            state["startup_stage"] = "game_closed" if exit_code == 0 else "failed"
            if (self._launch_attempt or {}).get("status") != "failed":
                state["message"] = ("Deadlock closed. Launch another editing session to continue."
                                    if exit_code == 0 else
                                    f"Deadlock exited (code {exit_code}). Export diagnostics if this was unexpected.")
        return state

    def _alive(self):
        return bool(self._session and self._session.process.poll() is None)

    def game_pid(self):
        """The only process eligible for an optional capture hotkey."""
        return self._session.pid if self._alive() else None

    def _require_connection(self):
        if not self._alive():
            raise RuntimeError("Launch the local replay from Dolly first. Connections to separately launched games are disabled.")
        if not self._console or not self._console.is_connected:
            raise RuntimeError("The console is disconnected. Use Connect after Deadlock finishes loading.")

    def _request(self, command, timeout=3.0, allow_error=False, completion_patterns=None):
        self._require_connection()
        if completion_patterns is None:
            output = self._console.request(command, timeout=timeout)
        else:
            output = self._console.request(command, timeout=timeout, completion_patterns=completion_patterns)
        camera_frame = command.startswith("spec_goto ")
        key = "camera_frame" if camera_frame else command.split(";", 1)[0][:160]
        self._last_output[key] = output[-32000:]
        if camera_frame:
            self._last_output["last_camera_command"] = command
        else:
            LOG.debug("%s\n%s", command, output)
        error = error_text(output)
        if error and not allow_error:
            raise RuntimeError(error)
        return output

    def _require_demo(self, require_tick=True):
        # No arguments: demo_goto reports current playback position; it does
        # not seek. demo_info in the user's game prints file metadata instead.
        output = self._request("demo_goto", allow_error=True)
        return self._resolve_demo(output, require_tick)

    def _resolve_demo(self, output, require_tick=True):
        """Validate fresh status, including status returned after a camera batch."""
        try:
            current = parse_demo_tick(output)
        except ValueError:
            current = None
        if current is not None and not current.get("playing"):
            raise RuntimeError("A local demo must be playing. Load the selected replay, then check camera support.")
        if current and current.get("name"):
            result = current
        else:
            result = parse_demo_info(self._request("demo_info", allow_error=True))
            if current and result.get("playing"):
                # A legacy live-tick line without File needs a fresh identity
                # query. Total playback_ticks in metadata is never used here.
                result.update(tick=current["tick"], total_ticks=current.get("total_ticks"))
        if not result.get("playing"):
            raise RuntimeError("A local demo must be playing. Load the selected replay, then reconnect/probe.")
        name = str(result.get("name") or "").replace("\\", "/").rsplit("/", 1)[-1]
        if not self._demo or Path(name).stem.casefold() != self._demo.stem.casefold():
            raise RuntimeError("The open replay is different from the local .dem chosen for this launch. Restart through Dolly with the intended file.")
        with self._state_lock:
            self._state["tick"] = int(result["tick"]) if result.get("tick") is not None else None
        if require_tick and result.get("tick") is None:
            raise RuntimeError("The replay is recognized, but its current tick was not reported. Export diagnostics before normal playback, or explicitly select Frozen preview.")
        return result

    def launch(self, game_path, demo_path, protocol="netcon", *, native=False, launch_options=""):
        with self._op_lock:
            self._launch_attempt = {
                "game_path": str(game_path),
                "demo_path": str(demo_path or ""),
                "protocol": str(protocol),
                "status": "requested",
            }
            if launch_options:
                self._launch_attempt["launch_options"] = str(launch_options)
            if native:
                self._launch_attempt["camera_backend"] = "native"
            LOG.info("Launch requested: %s", self._launch_attempt)
            try:
                result = self._launch(game_path, demo_path, protocol, native=native, launch_options=launch_options)
            except Exception as exc:
                self._launch_attempt.update(status="failed", error=str(exc), error_type=type(exc).__name__)
                LOG.exception("Launch failed")
                self._message("Launch failed: " + str(exc), startup_stage="failed")
                raise
            self._launch_attempt.update(status="started", pid=result["pid"], session_dir=result["session_dir"])
            return result

    def _launch(self, game_path, demo_path, protocol, *, native=False, launch_options=""):
        with self._op_lock:
            if protocol not in ("vconsole", "netcon"):
                raise ValueError("Unknown console protocol.")
            if not demo_path:
                raise ValueError("Choose a local .dem file before launching.")
            path = Path(demo_path).resolve()
            if not path.is_file() or path.suffix.lower() != ".dem":
                raise ValueError("Choose an existing, decompressed .dem replay file.")
            if self._alive():
                raise RuntimeError("Close the existing Dolly game session before launching again.")
            self.disconnect()
            options = {"native": True} if native else {}
            if launch_options:
                options["launch_options"] = launch_options
            self._session = launcher.launch(game_path, str(path), port=self._port, protocol=protocol, **options)
            self._native_active = False
            self._native_manual = False
            self._game_ui_visible = False
            self._game_ui_restore.clear()
            self._console_open = None
            self._native_last_status = {}
            self._native_handoff_details = {}
            self._demo = path
            self._protocol = protocol
            self._probe_result = {}
            self._restore.clear()
            self._playback_restore.clear()
            self._playback_details = None
            self._playback_metrics = {}
            self._applied_pose = None
            self._aspect_capture = {}
            self._demo_speed_changed = False
            self._last_output.clear()
            self._unlocker_pid = None
            self._startup_evidence = {}
            self._replay_requested = False
            with self._state_lock:
                self._state.update(time=0.0, tick=None)
            self._message("Deadlock launched with -dev -insecure. Wait until the hideout is fully loaded, then Connect and Initialize unlocker. The replay has not been loaded.", startup_stage="waiting_hideout")
            return {"pid": self._session.pid, "session_dir": str(self._session.session_dir),
                    "camera_backend": "native" if native else "console"}

    def connect(self):
        with self._op_lock:
            if not self._alive():
                raise RuntimeError("Use Launch first. Dolly only connects to its own running development session.")
            self.stop()
            self._reset_camera_position()
            if self._console:
                self._console.close()
            if not self._session.owns_console_port():
                self._console = None
                raise RuntimeError("The launched Deadlock process does not yet own the console port. Wait for loading to finish and retry. If it persists, export diagnostics and test the other console protocol in a new launch.")
            console = ConsoleClient(protocol=self._protocol)
            try:
                console.connect(host="127.0.0.1", port=self._port, timeout=3)
                self._console = console
                self._request("echo DOLLY_CONNECTION_READY")
            except Exception:
                console.close()
                self._console = None
                raise RuntimeError("Could not establish the game console connection. Wait for loading to finish and retry. If it still fails, export diagnostics; this build's console launch flags need checking.") from None
            ready = self._unlocker_pid == self._session.pid
            self._message("Connected. The unlocker was initialized in this game process; load your replay or check camera support." if ready else "Connected. Once you are in the fully loaded hideout, click Initialize unlocker before loading the replay.",
                          connected=True, startup_stage=("loading_replay" if self._replay_requested else "unlocker_ready") if ready else "connected")
            return self.status()

    @staticmethod
    def _check_startup_cancelled(cancel_event):
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Editing startup cancelled. The launched game remains open; close it before starting again.")

    def _startup_wait(self, check, label, cancel_event, timeout=120):
        """Poll observed state, with a deadline; elapsed time is not readiness."""
        deadline = time.perf_counter() + timeout
        waiter = cancel_event if cancel_event is not None else threading.Event()
        while True:
            self._check_startup_cancelled(cancel_event)
            if not self._alive():
                raise RuntimeError("Deadlock closed while " + label + ". Export diagnostics to inspect startup.")
            result = check()
            self._check_startup_cancelled(cancel_event)
            if result is not None and result is not False:
                return result
            if time.perf_counter() >= deadline:
                raise RuntimeError("Timed out " + label + ". The replay was not marked ready. Use the manual startup controls or export diagnostics.")
            waiter.wait(.25)

    def _automatic_hideout_check(self, initial_frame):
        """Require a rendered pre-replay scene and loaded command registration.

        Native frame progression proves that view setup is running, not merely
        that the console socket opened. The launcher forbids replay/map startup
        commands; an explicit no-demo response therefore bounds this to its
        initial pre-replay scene. Console-only mode needs named hideout evidence.
        """
        output = self._request("demo_info", allow_error=True)
        try:
            demo = parse_demo_info(output)
        except ValueError:
            return None
        if demo.get("playing") is True:
            raise RuntimeError("A replay opened before unlocker initialization. Close the game and launch again through Dolly.")
        if demo.get("playing") is not False:
            return None
        bridge = self._native_bridge()
        if bridge is not None:
            native = bridge.status()
            self._native_last_status = native
            if native.get("state") in ("fault", "unsupported"):
                raise RuntimeError("Native startup failed: " + str(native.get("message") or native["state"]))
            if native.get("demo_name"):
                return None
            rendered = int(native.get("frame_count", 0))
            if rendered <= max(0, initial_frame):
                return None
            evidence = {"method": "rendered_pre_replay_scene", "initial_frame": initial_frame,
                        "rendered_frame": rendered, "demo": demo}
        else:
            status = self._request("status", allow_error=True)
            maps = re.findall(r"(?im)^\s*(?:\[[^]\r\n]+\]\s*)*(?:map|mapname)\s*[:=]\s*[\"']?([^\s\"']+)", status)
            hideouts = [name for name in maps if "hideout" in re.split(r"[/\\_.-]", name.casefold())]
            if not hideouts:
                return None
            evidence = {"method": "named_hideout_status", "map": hideouts[0], "demo": demo}
        if not self._console.supports("cvar_unhide"):
            return None
        evidence["unlocker_command_registered"] = True
        return evidence

    def start_editing(self, game_path, demo_path, protocol="netcon", native=True,
                      launch_options="", cancel_event=None):
        """Launch, initialize once before the demo, and enter paused editing.

        Every transition uses process, console or rendered-view evidence.
        This runs on the caller's worker; cancellation does not kill the game.
        The existing manual launch/initialize/load/probe actions remain usable.
        """
        with self._op_lock:
            try:
                self._check_startup_cancelled(cancel_event)
                self.launch(game_path, demo_path, protocol, native=native, launch_options=launch_options)
                self._message("Starting Deadlock. Waiting for its console connection…", startup_stage="waiting_console")

                def connected():
                    if not self._session.owns_console_port():
                        return None
                    try:
                        return self.connect()
                    except (RuntimeError, OSError) as exc:
                        self._startup_evidence["connection_wait"] = str(exc)
                        return None

                self._startup_wait(connected, "waiting for the game console", cancel_event)
                bridge = self._native_bridge()
                initial_frame = int(bridge.status().get("frame_count", 0)) if bridge is not None else 0
                self._message("Connected. Waiting for the rendered hideout and unlocker command…", startup_stage="waiting_hideout")
                readiness = self._startup_wait(lambda: self._automatic_hideout_check(initial_frame),
                                               "waiting for hideout readiness", cancel_event)
                self._message("Hideout ready. Initializing the camera CVAR unlocker…", startup_stage="initializing_unlocker")
                self._check_startup_cancelled(cancel_event)
                # Exactly one invocation; missing completion never starts a demo.
                self.initialize_unlocker()
                self._startup_evidence["automatic_readiness"] = readiness
                self._check_startup_cancelled(cancel_event)
                self.load_replay()
                self._message("Unlocker confirmed. Loading the selected replay…", startup_stage="loading_replay")

                def selected_replay():
                    try:
                        info = self._require_demo()
                    except (RuntimeError, ValueError):
                        return None
                    if bridge is not None:
                        current = bridge.status()
                        if current.get("state") in ("fault", "unsupported"):
                            raise RuntimeError("Native camera failed while loading: " + str(current.get("message")))
                        name = str(current.get("demo_name") or "").replace("\\", "/").rsplit("/", 1)[-1]
                        if not name or Path(name).stem.casefold() != self._demo.stem.casefold():
                            return None
                        if int(current.get("frame_count", 0)) <= initial_frame:
                            return None
                    return info

                self._startup_wait(selected_replay, "waiting for the selected replay", cancel_event)
                self._request("demo_pause")
                self._message("Replay loaded and paused. Checking camera controls…", startup_stage="checking_camera")
                self.probe()
                self._check_startup_cancelled(cancel_event)
                self._message("Opening the paused camera editor…", startup_stage="entering_editor")
                if bridge is not None:
                    # -console preserves access but can leave the console
                    # visible at startup. Close it explicitly before enabling
                    # native flight, so its text box cannot share movement keys.
                    self.toggle_console(False)
                    self.enter_native_flight(cancelled=cancel_event.is_set if cancel_event is not None else None)
                else:
                    self.begin_paused_camera(cancelled=cancel_event.is_set if cancel_event is not None else None)
                self._message("Replay ready. Move the camera and capture your first view. F7 opens the console.",
                              startup_stage="editing_ready")
                return self.status()
            except Exception as exc:
                self._message(str(exc), startup_stage="cancelled" if cancel_event is not None and cancel_event.is_set() else "failed")
                raise

    def initialize_unlocker(self):
        """Explicit hideout step; no replay is dispatched by this method.

        The user invokes this from the loaded hideout. No map-name guess is used
        as a readiness signal. A recognized active demo is rejected; unavailable
        demo_info output is recorded, not treated as proof of a missing plugin.
        Camera control still requires a recognized selected demo and Probe.
        """
        with self._op_lock:
            self._halt()
            self._require_connection()
            if self._unlocker_pid == self._session.pid:
                return dict(self._startup_evidence)
            if self._replay_requested:
                raise RuntimeError("This session already requested a replay. Restart through Dolly and initialize the unlocker in the hideout first.")
            self._startup_evidence = {"initialized": False, "pid": self._session.pid}
            try:
                self._request("version", allow_error=True)
                self._request("status", allow_error=True)
                before = self._request("demo_info", allow_error=True)
                try:
                    info = parse_demo_info(before)
                except ValueError:
                    info = {"playing": None, "note": "demo_info was not recognized; hideout readiness is selected explicitly in the UI."}
                self._startup_evidence["before_unlocker"] = info
                if info.get("playing") is True:
                    raise RuntimeError("A replay is already loaded. Restart through Dolly, stay in the hideout, and initialize the unlocker before loading the replay.")
                output = self._request("cvar_unhide", timeout=12, completion_patterns=(
                    r"Removed hidden flags from \d+ concommands\b",
                    r"Removed hidden flags from \d+ cvars\b"))
                counts = {kind.lower(): int(count) for count, kind in re.findall(
                    r"Removed hidden flags from (\d+) (concommands|cvars)\b", output, re.I)}
                self._startup_evidence["confirmation_counts"] = counts
                if not {"concommands", "cvars"} <= counts.keys():
                    raise RuntimeError("Dolly could not confirm cvar_unhide completion. Keep the game in the hideout and retry Initialize unlocker. Empty console output alone does not prove the DLL failed to load. Export diagnostics if it persists; the replay has not been loaded by Dolly.")
                after = self._request("demo_info", allow_error=True)
                try:
                    after_info = parse_demo_info(after)
                except ValueError:
                    after_info = {"playing": None}
                self._startup_evidence["after_unlocker"] = after_info
                if after_info.get("playing") is True:
                    raise RuntimeError("The replay opened while the unlocker was initializing. Restart and complete initialization in the hideout before loading it.")
                self._session.restore_gameinfo()
                self._unlocker_pid = self._session.pid
                self._startup_evidence["initialized"] = True
                self._message("Unlocker confirmed in the hideout startup step. Original game configuration restored. You can now Load replay.", startup_stage="unlocker_ready")
                return dict(self._startup_evidence)
            except Exception as exc:
                self._startup_evidence["error"] = str(exc)
                self._message(str(exc), startup_stage="failed")
                raise

    def _require_unlocker(self):
        self._require_connection()
        if self._unlocker_pid != self._session.pid:
            raise RuntimeError("Initialize the unlocker in the hideout before loading a replay or checking camera support.")

    def load_replay(self):
        with self._op_lock:
            self._require_unlocker()
            self.stop()
            path = launcher._validate_demo(self._demo)
            if path is None:
                raise RuntimeError("No replay was selected for this launch.")
            # Source commands use forward slashes; _validate_demo rejects quotes,
            # separators and line breaks before this trusted quoted argument.
            command = 'playdemo "' + path.as_posix() + '"'
            self._probe_result = {}
            self._replay_requested = True
            self._startup_evidence["replay_requested"] = str(path)
            try:
                # Map loading may outlast a console request deadline. Queue the
                # command once; Probe later verifies the actual selected demo.
                self._console.send(command)
                self._last_output["playdemo"] = "Sent: " + command + "\nWaiting for Check camera support to verify the loaded replay."
                LOG.info("Replay requested after confirmed unlocker initialization: %s", path)
                self._message("Replay load requested. Wait until it is visible in the game, then Check camera support.", startup_stage="loading_replay", tick=None)
                return {"requested": str(path), "confirmed_loaded": False}
            except Exception as exc:
                self._message("Replay load request failed: " + str(exc), startup_stage="failed")
                raise

    def probe(self):
        with self._op_lock:
            try:
                result = self._probe()
            except Exception as exc:
                self._message(str(exc), startup_stage="failed")
                raise
            return result

    def _probe(self):
        with self._op_lock:
            self.stop()
            self._require_unlocker()
            self._request("version", allow_error=True)
            info = self._require_demo(require_tick=False)
            names = ["spec_goto", "spec_pos", "cl_citadel_forceangles", "demo_info", "demo_pause", "demo_resume", "demo_gototick", "demo_timescale", ASPECT_CVAR,
                     "r_citadel_depthoffield_enable", "r_citadel_depthoffield_aperture_diameter", "r_citadel_depthoffield_focus_distance", "r_depth_of_field"]
            capabilities = {}
            for name in names:
                if name.startswith("r_"):
                    response = self._request(name, allow_error=True)
                    try:
                        read_cvar_value(name, response)
                        capabilities[name] = True
                    except ValueError:
                        capabilities[name] = False
                else:
                    capabilities[name] = self._console.supports(name)
            self._probe_result = {"version": __version__, "capabilities": capabilities, "demo": info,
                                  "live_tick_available": info.get("tick") is not None,
                                  "camera_effect_verified": False,
                                  "note": "Command availability only. Visual rotation, framing and DOF must be tested in the installed game."}
            native = self._native_bridge()
            if native is not None:
                native_status = native.status()
                self._native_last_status = native_status
                self._probe_result["native_camera"] = native_status
                if native_status.get("state") in ("starting", "fault", "unsupported"):
                    raise RuntimeError("Native camera is not ready: " + str(native_status.get("message") or native_status.get("state")))
                if int(native_status.get("frame_count", 0)) <= 0:
                    raise RuntimeError("Native camera has not seen a rendered replay view. Enter freecam and check camera support again.")
            needed = ("spec_goto", "spec_pos", "cl_citadel_forceangles", "demo_info", "demo_pause", "demo_resume", "demo_gototick", "demo_timescale", self.lens_cvar)
            missing = [name for name in needed if not capabilities.get(name)]
            if missing:
                raise RuntimeError("Required controls were not confirmed: " + ", ".join(missing) + ". Export diagnostics.")
            message = "Camera/replay commands found. Start a path from your current freecam view, move to another view, and Add camera here."
            if info.get("tick") is None:
                message += " Current replay tick is unavailable; export diagnostics or select Frozen preview explicitly."
            self._message(message, startup_stage="replay_ready")
            return self._probe_result

    def _require_probe(self):
        capabilities = self._probe_result.get("capabilities", {})
        for name in ("spec_goto", "cl_citadel_forceangles", self.lens_cvar):
            if not capabilities.get(name):
                raise RuntimeError("Complete 5 Check camera support before previewing or playing a path.")

    def _capture_aspect_ratio(self):
        raw = read_cvar_value(ASPECT_CVAR, self._request(ASPECT_CVAR))
        if raw == 0:
            # Zero is the engine's automatic mode, never a curve endpoint.
            # Resolve it to the launched game's client dimensions when possible.
            native = client_aspect_ratio(self.game_pid())
            aspect = native if native is not None else float(self.standard_aspect)
            source = "game_client_area" if native is not None else "shot_standard"
        else:
            aspect, source = raw, "r_aspectratio"
        if not math.isfinite(aspect) or not ASPECT_MIN <= aspect <= ASPECT_MAX:
            raise ValueError(f"Captured aspect ratio {aspect:g} is outside the editor range {ASPECT_MIN:g}–{ASPECT_MAX:g}. Check the framing standard or game override.")
        self._aspect_capture = {"raw": raw, "resolved": aspect, "source": source}
        return aspect

    def capture(self, shot_time):
        with self._op_lock:
            native = self._native_bridge()
            if native is not None and hasattr(native, "start_flight"):
                snapshot = self._sample_paused_native_view()
                frame, _ = self._record_native_capture(snapshot, shot_time, pause=False)
                return frame
            self._halt()
            paused_tick = self._paused_tick
            self._require_demo(require_tick=False)
            self._request("demo_pause")
            info = self._require_demo(require_tick=False)
            capture_tick = info.get("tick")
            if paused_tick is not None and capture_tick != paused_tick:
                # Capture is a fresh measurement, not a continuation of the
                # previous manual writer. An external seek retires that
                # preparation without making the first new capture fail.
                self._invalidate_paused_camera()
                self._reset_camera_position()
                paused_tick = None
            if paused_tick is not None:
                self._check_paused_tick()
            aspect = self._capture_aspect_ratio()
            roll = self._applied_pose["roll"] if self._applied_pose else 0.0
            frame = parse_camera(self._request("spec_pos"), roll=roll, aspect_ratio=aspect)
            frame.time = float(shot_time)
            if not math.isfinite(frame.time) or frame.time < 0:
                raise ValueError("Shot time must be a finite, nonnegative number.")
            if capture_tick is not None and self._require_demo().get("tick") != capture_tick:
                self._invalidate_paused_camera()
                self._reset_camera_position()
                raise RuntimeError("The replay moved during Capture. Pause it and capture again.")
            if paused_tick is not None:
                self._check_paused_tick()
                # Capture measures the actual view, including manual movement
                # outside Dolly. Keep cvars and calibration, but rebase flight
                # on this measurement so resuming cannot jump to its old pose.
                refreshed = dict(self._paused_pose)
                refreshed.update(asdict(frame))
                self._set_paused_pose(refreshed, paused_tick)
            self._message("Captured the current freecam view and aspect ratio; replay paused. Roll keeps Dolly's last applied value, or zero; edit Bank if needed.")
            return frame

    def _wait_paused_native_view(self, bridge, after_frame, *, timeout=None):
        """Wait for fresh, settled render telemetry after a pause command."""
        deadline = time.perf_counter() + (NATIVE_PAUSE_TIMEOUT if timeout is None else timeout)
        previous_tick = None
        previous_frame = int(after_frame)
        while True:
            self._require_connection()
            self._check_paused_cancelled()
            current = bridge.status()
            self._require_native_demo(current, allow_idle=True)
            frame = int(current.get("frame_count", 0))
            if frame > previous_frame:
                tick = int(current["tick"])
                if current.get("paused") and tick >= 0:
                    if previous_tick == tick:
                        return current
                    previous_tick = tick
                else:
                    previous_tick = None
                previous_frame = frame
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise RuntimeError("The renderer has not confirmed a paused replay view. Return to Deadlock and retry Capture; no camera was added.")
            time.sleep(min(.01, remaining))

    def _sample_paused_native_view(self):
        """Desktop capture takes its pose and timestamp from one rendered frame."""
        if not self._native_manual:
            self._halt(native_action="native_hold")
        self._require_demo(require_tick=False)
        bridge = self._native_bridge()
        before = bridge.status()
        self._require_native_demo(before, allow_idle=True)
        self._request("demo_pause")
        current = self._wait_paused_native_view(bridge, before["frame_count"])
        field = "applied_pose" if self._native_active else "original_pose"
        return {"pose": list(current[field]), "tick": int(current["tick"]), "paused": True}

    def capture_native_snapshot(self, snapshot, time=0.0):
        """Keep the input event's pose/tick even if its worker runs a little later."""
        return self._record_native_capture(snapshot, time)

    def _record_native_capture(self, snapshot, time=0.0, *, pause=True):
        with self._op_lock:
            if not isinstance(snapshot, dict):
                raise ValueError("Native capture requires a camera snapshot.")
            pose = self._native_pose({"applied_pose": snapshot.get("pose")})
            tick = snapshot.get("tick")
            if isinstance(tick, bool) or not isinstance(tick, int) or tick < 0:
                raise ValueError("Native capture has no valid replay tick.")
            if not isinstance(snapshot.get("paused"), bool):
                raise ValueError("Native capture has no valid replay pause state.")
            shot_time = float(time)
            if not math.isfinite(shot_time) or shot_time < 0:
                raise ValueError("Shot time must be a finite, nonnegative number.")
            if not ASPECT_MIN <= pose["aspect_ratio"] <= ASPECT_MAX:
                raise ValueError("Native camera framing is outside the supported aspect range.")
            bridge = self._native_bridge()
            if bridge is None:
                raise RuntimeError("The native camera session is no longer available.")
            self._require_demo()
            current = bridge.status()
            self._require_native_demo(current, allow_idle=True)
            if snapshot["paused"] and int(current["tick"]) != tick:
                raise RuntimeError("The replay moved after this camera capture. Capture the current view again.")
            if not snapshot["paused"] and int(current["tick"]) < tick:
                raise RuntimeError("The replay rewound after this camera capture. Capture the current view again.")
            # Taking a key must not turn off native flight. The pose belongs
            # to the input event; later render updates cannot mutate this key.
            # An authored path still stops before becoming an editing capture.
            if not self._native_manual:
                self._halt(native_action="native_hold")
            if pause:
                self._request("demo_pause")
                current = self._wait_paused_native_view(bridge, current["frame_count"])
            if not current.get("paused"):
                raise RuntimeError("The replay resumed during Capture. Capture the current view again.")
            if (snapshot["paused"] and int(current["tick"]) != tick) or int(current["tick"]) < tick:
                raise RuntimeError("The replay moved after this camera capture. Capture the current view again.")
            frame = Keyframe(time=shot_time, **pose)
            # P temporarily changes Manual into HoldCurrent. A capture pauses
            # the replay too, so re-arm native movement without changing its
            # visible seed or taking focus from the open editor panel.
            if self._native_manual and not self.status().get("paused_flight"):
                owner = "panel"
                if callable(getattr(bridge, "editor_status", None)):
                    editor = bridge.editor_status()
                    owner = editor.get("owner", editor.get("input_mode", "panel"))
                    if owner not in ("panel", "flight", "console", "game_ui"):
                        owner = "panel"
                armed = bridge.start_flight(self._demo.name)
                bridge.configure_editor(owner=owner)
                self._require_native_demo(armed)
                if not armed.get("paused") or int(armed["tick"]) != int(current["tick"]):
                    self._release_native_camera()
                    raise RuntimeError("The replay moved while returning to paused camera movement. Capture again.")
                paused_pose = self._native_pose(armed)
                paused_pose.update(time=float(armed.get("phase", 0)), cvars={})
                self._set_paused_pose(paused_pose, int(armed["tick"]))
                with self._state_lock:
                    self._state["paused_flight"] = True
            self._message("Captured the rendered camera view. Replay paused.",
                          tick=int(current["tick"]), captured_tick=tick, replay_paused=True)
            return frame, tick

    def current_tick(self):
        with self._op_lock:
            return int(self._require_demo()["tick"])

    def capture_at_replay(self, start_tick, tick_rate):
        """Pause and capture a pose with a timestamp from one acknowledged tick."""
        with self._op_lock:
            tick_rate = float(tick_rate)
            if not math.isfinite(tick_rate) or tick_rate <= 0:
                raise ValueError("Replay tick rate must be a positive finite number.")
            if start_tick is not None:
                start_tick = float(start_tick)
                if not math.isfinite(start_tick) or start_tick < 0 or not start_tick.is_integer():
                    raise ValueError("Shot start tick must be a nonnegative integer.")
            if self._supports_native_flight():
                snapshot = self._sample_paused_native_view()
                tick = snapshot["tick"]
                shot_time = 0.0 if start_tick is None else (tick - start_tick) / tick_rate
                if shot_time < 0:
                    raise ValueError("Replay is before this shot's start. Start a new path here or choose Timed shot.")
                frame, _ = self._record_native_capture(snapshot, shot_time, pause=False)
                return frame
            if not self._native_manual:
                self._halt()
            self._require_demo()
            self._request("demo_pause")
            info = self._require_demo()
            tick = int(info["tick"])
            shot_time = 0.0 if start_tick is None else (tick - start_tick) / tick_rate
            if shot_time < 0:
                raise ValueError("Replay is before this shot's start. Start a new path here or choose Timed shot.")
            frame = self.capture(shot_time)
            if int(self._require_demo()["tick"]) != tick:
                raise RuntimeError("The replay moved while capturing. Pause it and capture again.")
            # The replay remains paused during capture; expose the sampled tick
            # to the UI so its first key and start_tick describe the same instant.
            with self._state_lock:
                self._state["tick"] = tick
            return frame

    def _validate_project(self, project):
        project.validate()
        if not project.keyframes:
            raise ValueError("Add at least one camera keyframe.")
        for key in project.keyframes:
            frame_commands(project.evaluate(key.time), self.lens_cvar)
        names = set(project.setup_values)
        for track in project.tracks:
            names.add(track.name)
            if track.name in DISCRETE and track.interpolation != "step":
                raise ValueError(f"Use Step interpolation for {track.name}.")
            for key in track.keys:
                frame_commands(project.evaluate(key.time), self.lens_cvar)
            if track.restore_value is not None:
                restore_frame = project.evaluate(project.keyframes[0].time)
                restore_frame["cvars"] = {track.name: track.restore_value}
                frame_commands(restore_frame, self.lens_cvar)
        if names.intersection(LEGACY_FOV_CONTROLS | {ASPECT_CVAR}):
            raise ValueError("Use the Framing curve for aspect ratio. Legacy FOV and duplicate aspect tracks are disabled.")
        return names | {self.lens_cvar}

    def _snapshot(self, project):
        names = self._validate_project(project)
        self.standard_aspect = project.standard_aspect
        overrides = {track.name: track.restore_value for track in project.tracks if track.restore_value is not None}
        for name in sorted(names):
            if name not in self._restore:
                current = read_cvar_value(name, self._request(name))
                self._restore[name] = current if name not in overrides else overrides[name]

    def _prepare_playback_settings(self, hide_hud):
        if hide_hud:
            # The requested automatic mode explicitly turns HUD back on when
            # the shot ends, even if it was manually hidden before the shot.
            read_cvar_value("citadel_hud_visible", self._request("citadel_hud_visible"))
            self._playback_restore["citadel_hud_visible"] = 1.0
            self._request("citadel_hud_visible 0")
            self._temporary_playback_value("citadel_hide_replay_hud", 1.0)
        # Prevent background-window throttling while operating the companion.
        # This optional optimization is skipped if the build cannot expose it.
        self._temporary_playback_value("engine_no_focus_sleep", 0.0)

    def _temporary_playback_value(self, name, target):
        try:
            value = read_cvar_value(name, self._request(name))
        except (ValueError, RuntimeError) as exc:
            LOG.info("Optional playback setting %s unavailable: %s", name, exc)
            return
        if value != target:
            self._playback_restore[name] = value
            self._request(name + " " + numeric(target))

    def _restore_playback_settings(self):
        """Undo playback-only settings in our process, even after a demo ends."""
        if not self._playback_restore:
            return None
        try:
            self._require_connection()
            self._request("; ".join(name + " " + numeric(value)
                                    for name, value in sorted(self._playback_restore.items())))
            self._playback_restore.clear()
        except Exception as exc:
            LOG.warning("Playback setting restoration remains pending: %s", exc)
            return "HUD/background settings could not be restored; reconnect and use Stop / restore. " + str(exc)
        return None

    def _reset_camera_position(self):
        self._camera_offset = dict.fromkeys(CAMERA_AXES, 0.0)
        self._camera_calibration = {}

    def _position_commands(self, frame):
        # Keys describe the view reported by spec_pos. Only the outgoing
        # spec_goto origin is adjusted; captures and saved projects stay intact.
        command_frame = dict(frame)
        for axis in CAMERA_AXES:
            command_frame[axis] -= self._camera_offset[axis]
        return frame_commands(command_frame, self.lens_cvar)

    def _check_position_cancelled(self):
        self._check_paused_cancelled()
        if self._stop_event.is_set():
            raise RuntimeError("Camera position check cancelled.")

    def _settled_camera(self, frame):
        # A console acknowledgment can precede the camera's next update.
        # Require a short settling window and several consistent readbacks.
        samples = self._camera_calibration["attempts"][-1]["samples"]
        if self._stop_event.wait(2 * CAMERA_SETTLE_INTERVAL):
            self._check_position_cancelled()
        for _ in range(CAMERA_SETTLE_LIMIT):
            self._check_position_cancelled()
            pose = parse_camera(self._request("spec_pos", timeout=1), roll=frame["roll"], aspect_ratio=frame["aspect_ratio"])
            samples.append({axis: getattr(pose, axis) for axis in CAMERA_AXES})
            recent = samples[-CAMERA_SETTLE_SAMPLES:]
            if len(recent) == CAMERA_SETTLE_SAMPLES and all(
                max(p[axis] for p in recent) - min(p[axis] for p in recent)
                <= CAMERA_STABILITY_TOLERANCE + 1e-8 for axis in CAMERA_AXES
            ):
                return samples[-1]
            if self._stop_event.wait(CAMERA_SETTLE_INTERVAL):
                self._check_position_cancelled()
        raise RuntimeError("Camera position did not settle. Release movement keys and enter replay freecam, then retry. Export diagnostics if it persists.")

    def _measure_position(self, frame, stage, position_only=False):
        self._check_position_cancelled()
        attempt = {"stage": stage, "requested": {a: frame[a] for a in CAMERA_AXES},
                   "commanded": {a: frame[a] - self._camera_offset[a] for a in CAMERA_AXES},
                   "offset": dict(self._camera_offset), "samples": []}
        self._camera_calibration["attempts"].append(attempt)
        command = self._position_commands(frame)
        self._request(command.split(";", 1)[0] if position_only else command)
        observed = self._settled_camera(frame)
        attempt["observed"] = observed
        residual = {a: observed[a] - frame[a] for a in CAMERA_AXES}
        attempt["residual"] = residual
        return residual

    @staticmethod
    def _position_matches(residual):
        return all(abs(value) <= CAMERA_POSITION_TOLERANCE for value in residual.values())

    def _converge_position(self, frame, stage):
        # The paused game can move only partway toward a command, even when
        # subsequent readbacks are identical. The user's 40-degree preview
        # had shrinking residuals after all three old correction attempts.
        previous_error = None
        stalled = 0
        for _ in range(CAMERA_CORRECTION_LIMIT):
            residual = self._measure_position(frame, stage)
            if self._position_matches(residual):
                # Do not accept a transient crossing of the target. The same
                # outgoing origin must reproduce the view on a second write.
                residual = self._measure_position(frame, stage + "_hold")
                if self._position_matches(residual):
                    return residual
            error = max(abs(v) for v in residual.values())
            stalled = stalled + 1 if previous_error is not None and error >= previous_error - .05 else 0
            if stalled >= 3:
                raise CameraPositionError("Camera position stopped converging. The current camera mode or collision may be limiting movement. Export diagnostics if it persists.")
            previous_error = error
            offset = {a: self._camera_offset[a] + residual[a] for a in CAMERA_AXES}
            if math.sqrt(sum(value * value for value in offset.values())) > CAMERA_MAX_OFFSET:
                raise CameraPositionError("Camera position correction exceeded its measurement limit. Enter replay freecam and retry; export diagnostics if it persists.")
            self._camera_offset = offset
            self._camera_calibration["offset"] = dict(offset)
        raise CameraPositionError("Camera position was still outside tolerance after the correction limit. Export diagnostics so the remaining response can be checked.")

    def _position_frame(self, frame, *, direct=False):
        """Converge to the requested view; verify before any resume.

        A matching initial position alone can hide an ignored/clamped height.
        A small, distinct-height witness checks that Z responds, followed by a
        verified return to the requested view. Never run this in the frame loop.
        """
        previous_offset = dict(self._camera_offset)
        self._camera_calibration = {"verified": False, "height_response_verified": False,
                                    "offset": dict(self._camera_offset), "attempts": [],
                                    "frame": dict(frame),
                                    "position_tolerance": CAMERA_POSITION_TOLERANCE,
                                    "correction_limit": CAMERA_CORRECTION_LIMIT}
        self._message("Checking camera position and height while paused. Release movement keys.")
        try:
            self._check_position_cancelled()
            # Resolve and check the authored aspect before camera positioning.
            # Degree FOV values in older shots are inert migration metadata.
            self._request(frame_commands(frame, self.lens_cvar).split("; ", 1)[1])
            aspect = read_cvar_value(ASPECT_CVAR, self._request(ASPECT_CVAR))
            self._camera_calibration["lens"] = {"control": ASPECT_CVAR,
                "requested": frame["aspect_ratio"], "observed": aspect,
                "verified": abs(aspect - frame["aspect_ratio"]) <= .001}
            if not self._camera_calibration["lens"]["verified"]:
                raise RuntimeError(f"The game reported aspect ratio {aspect:g} after requesting {frame['aspect_ratio']:g}. Export diagnostics; the framing override may be limited in this mode.")
            target_residual = self._converge_position(frame, "target")

            probe = dict(frame, **{a: frame[a] + CAMERA_HEIGHT_PROBE
                                    for a in (CAMERA_AXES if direct else ("z",))})
            try:
                probe_residual = self._measure_position(probe, "translation_probe" if direct else "height_probe")
            except Exception:
                # A failed readback may follow a successful probe write. Try
                # to leave the requested view in place, unless Stop cancelled
                # the check or the selected demo is no longer active.
                if not self._stop_event.is_set():
                    try:
                        self._require_demo(require_tick=False)
                        self._measure_position(frame, "return_after_probe_error", position_only=True)
                    except Exception as exc:
                        self._camera_calibration["return_error"] = str(exc)
                raise
            # Private convergence can inspect a partial paused response, but
            # public movement needs approximately unit gain on every axis.
            # Never convert weak response into a guessed native camera scale.
            response = {a: probe[a] + probe_residual[a] - frame[a] - target_residual[a]
                        for a in CAMERA_AXES}
            self._camera_calibration["height_response"] = {
                "commanded_delta_z": CAMERA_HEIGHT_PROBE, "observed_delta": response,
                "gain_z": response["z"] / CAMERA_HEIGHT_PROBE}
            if direct:
                returned = self._measure_position(frame, "translation_return")
                gains = {a: response[a] / CAMERA_HEIGHT_PROBE for a in CAMERA_AXES}
                self._camera_calibration["translation_response"] = {
                    "commanded_delta": dict.fromkeys(CAMERA_AXES, CAMERA_HEIGHT_PROBE),
                    "observed_delta": response, "gain": gains, "return_residual": returned}
                if not all(.9 <= gain <= 1.1 for gain in gains.values()) or not self._position_matches(returned):
                    raise CameraPositionError("The paused camera still applies only part of a position change. Camera movement was not started because native Resume could jump to a hidden position. Enter replay freecam, release movement keys and retry; export diagnostics if it persists.")
                self._camera_calibration["direct_response_verified"] = True
            else:
                self._converge_position(frame, "final")
                if (not .1 * CAMERA_HEIGHT_PROBE <= response["z"] <= 4 * CAMERA_HEIGHT_PROBE or
                    any(abs(response[a]) > .1 * CAMERA_HEIGHT_PROBE for a in ("x", "y"))):
                    raise RuntimeError("Camera height did not follow the position command consistently. Enter replay freecam and retry; export diagnostics if it persists.")
            self._camera_calibration["height_response_verified"] = True
            self._check_position_cancelled()
            self._camera_calibration["verified"] = True
            self._camera_calibration["offset"] = dict(self._camera_offset)
            self._applied_pose = frame
            LOG.info("Camera position verified; measured spec_goto offset: %s", self._camera_offset)
        except Exception as exc:
            self._camera_calibration["error"] = str(exc)
            # Failed measurements must not replace the previous starting
            # estimate. Every retry still runs fresh verification before use.
            self._camera_offset = previous_offset
            self._camera_calibration["restored_offset"] = dict(previous_offset)
            self._message("Camera position check failed: " + str(exc), playing=False)
            raise

    def _apply_frame(self, project, shot_time):
        frame = project.evaluate(shot_time)
        self._request(self._position_commands(frame))
        self._applied_pose = frame
        with self._state_lock:
            self._state["time"] = max(0.0, min(project.duration, shot_time))

    def _position_direct_frame(self, frame, tick=None, *, refresh=False):
        """Verify a usable camera origin, rather than a local paused fit.

        Some paused spectator states expose only a fraction of spec_goto's
        displacement. Fitting an additive offset in that state can place the
        hidden origin far away; native Resume then reveals it. Refreshing the
        exact same tick can be a no-op. A bounded adjacent-tick round trip is
        a recovery attempt, not an assumption about the engine. The final
        tick and a direct XYZ displacement and return must still pass.
        """
        refresh_details = None
        failed_calibration = None
        # A seek invalidates the previous local correction, including any
        # correction measured while the game had a weak paused response.
        self._reset_camera_position()
        try:
            if refresh:
                if tick is None:
                    raise RuntimeError("A readable replay tick is needed to refresh the paused camera.")
                refresh_details = self._refresh_camera_tick(tick)
            try:
                self._position_frame(frame, direct=True)
            except CameraPositionError:
                if refresh or tick is None:
                    raise
                # A Play/Seek targeting its already-current tick may also
                # leave a weak camera. Retry once, only for measured position
                # failures; cancellation, identity and lens errors propagate.
                failed_calibration = deepcopy(self._camera_calibration)
                self._check_position_cancelled()
                refresh_details = self._refresh_camera_tick(tick)
                self._reset_camera_position()
                self._position_frame(frame, direct=True)
            if tick is not None and int(self._require_demo()["tick"]) != int(tick):
                raise RuntimeError("The replay moved during the camera position check. Keep it paused and retry.")
            self._check_position_cancelled()
        except Exception as exc:
            self._camera_calibration["verified"] = False
            self._camera_calibration["error"] = str(exc)
            self._message("Camera position check failed: " + str(exc), playing=False)
            raise
        finally:
            self._camera_calibration["same_tick_refresh"] = False
            self._camera_calibration["refresh"] = refresh_details or self._camera_calibration.get("refresh")
            if failed_calibration is not None:
                self._camera_calibration["before_refresh"] = failed_calibration
            self._camera_calibration["prepared_tick"] = tick

    def _refresh_camera_tick(self, tick):
        """Actually leave and return to a paused tick before touching the camera."""
        tick = int(tick)
        self._check_position_cancelled()
        info = self._require_demo()
        if int(info["tick"]) != tick:
            raise RuntimeError("The replay moved before camera refresh. Pause it and retry.")
        neighbour = tick - 1 if tick > 0 else 1
        total = info.get("total_ticks")
        if tick < 0 or (tick == 0 and (total is None or int(total) <= neighbour)):
            raise RuntimeError("A neighbouring replay tick is unavailable for camera refresh. Seek slightly into the replay and retry.")
        details = {"method": "adjacent_tick_round_trip", "target_tick": tick,
                   "neighbour_tick": neighbour, "verified": False, "seeks": []}
        # Keep an incomplete sequence available if a seek is cancelled or
        # the demo changes. Never issue another seek after such a failure.
        self._camera_calibration["refresh"] = details
        self._message("Refreshing the paused camera, then returning to this replay tick. Release movement keys.")
        try:
            expected_tick = tick
            for target in (neighbour, tick):
                self._check_position_cancelled()
                if int(self._require_demo()["tick"]) != expected_tick:
                    raise RuntimeError("The replay moved during camera refresh. Pause it and retry.")
                try:
                    self._seek_tick(target)
                finally:
                    details["seeks"].append(deepcopy(self._last_seek_details))
                expected_tick = target
            details["verified"] = True
            return details
        except Exception as exc:
            details["error"] = str(exc)
            raise

    def _reset_motion_observations(self, frame):
        self._recent_camera_targets.clear()
        self._recent_camera_targets.append((time.perf_counter(), dict(frame)))
        self._next_camera_readback = 0.0
        self._camera_drift_samples = 0

    @staticmethod
    def _segment_distance(point, start, end):
        delta = [end[a] - start[a] for a in CAMERA_AXES]
        squared = sum(v * v for v in delta)
        fraction = (sum((point[a] - start[a]) * delta[i]
                        for i, a in enumerate(CAMERA_AXES)) / squared) if squared else 0
        fraction = max(0.0, min(1.0, fraction))
        return math.sqrt(sum((point[a] - start[a] - fraction * delta[i]) ** 2
                             for i, a in enumerate(CAMERA_AXES)))

    def _observe_motion_frame(self, frame, output, sent_at, received_at, tick, *, sampled, manual=False):
        """Keep bounded numeric traces and stop sustained visible drift.

        A console reply can precede rendering, so compare readbacks with the
        last 120 ms of commanded segments, not just this batch's endpoint.
        Readbacks never train the additive camera offset during movement.
        """
        recent = [pose for stamp, pose in self._recent_camera_targets if stamp >= sent_at - .12]
        if not recent and self._recent_camera_targets:
            recent = [self._recent_camera_targets[-1][1]]
        recent.append(frame)
        self._recent_camera_targets.append((sent_at, dict(frame)))
        sample = {"sent_at": sent_at, "received_at": received_at, "tick": tick,
                  "time": frame.get("time"),
                  "pose": {a: frame[a] for a in (*CAMERA_AXES, "pitch", "yaw", "roll")},
                  "aspect_ratio": frame["aspect_ratio"]}
        failure = None
        if sampled:
            observed = asdict(parse_camera_readback(output, roll=frame["roll"], aspect_ratio=frame["aspect_ratio"]))
            sample["observed_pose"] = {a: observed[a] for a in (*CAMERA_AXES, "pitch", "yaw")}
            pairs = list(zip(recent, recent[1:])) or [(frame, frame)]
            distance = min(self._segment_distance(observed, first, last) for first, last in pairs)
            sample["visible_distance_from_recent_path"] = distance
            self._camera_drift_samples = self._camera_drift_samples + 1 if distance > CAMERA_DRIFT_LIMIT else 0
            sample["consecutive_large_drift"] = self._camera_drift_samples
            if self._camera_drift_samples >= CAMERA_DRIFT_SAMPLES:
                measured = dict(frame)
                measured.update({a: observed[a] for a in (*CAMERA_AXES, "pitch", "yaw")})
                self._applied_pose = measured
                if manual:
                    self._paused_details["last_visible_pose"] = deepcopy(measured)
                failure = "The visible camera stopped following its commands. Movement stopped before further camera writes. Start Paused camera controls again to refresh the current view, or retry Play shot; export diagnostics if it persists."
        with self._state_lock:
            (self._paused_samples if manual else self._playback_samples).append(sample)
        if failure:
            raise RuntimeError(failure)

    def _invalidate_paused_camera(self):
        self._paused_pose = None
        self._paused_tick = None
        with self._state_lock:
            self._state.update(paused_camera=False, paused_flight=False, paused_tick=None)

    def _set_paused_pose(self, frame, tick):
        self._paused_pose = deepcopy(frame)
        self._paused_tick = int(tick)
        self._applied_pose = deepcopy(frame)
        with self._state_lock:
            self._state.update(paused_camera=True, paused_tick=int(tick), tick=int(tick))
            self._paused_details.update(tick=int(tick), pose=deepcopy(frame))

    def _check_paused_tick(self, output=None):
        """Read fresh identity and tick before/after each manual camera write."""
        if self._paused_pose is None or self._paused_tick is None:
            raise RuntimeError("Start Paused camera controls before moving the camera.")
        try:
            info = self._require_demo() if output is None else self._resolve_demo(output)
            if int(info["tick"]) != self._paused_tick:
                raise RuntimeError("The replay moved while Paused camera was active. Camera controls stopped; pause the replay and start controls again.")
        except Exception:
            self._invalidate_paused_camera()
            raise
        return info

    def _prepare_paused_camera(self):
        # Stop the single writer and clean up playback-only HUD settings, but
        # retain original camera cvar snapshots until the main Stop / restore.
        self._halt()
        self._check_paused_cancelled()
        self._invalidate_paused_camera()
        self._stop_event.clear()
        self._require_probe()
        self._require_demo()
        restoration_error = self._restore_playback_settings()
        if restoration_error:
            raise RuntimeError(restoration_error)
        command = "demo_pause"
        if self._demo_speed_changed:
            command += "; demo_timescale 1"
        self._request(command)
        self._demo_speed_changed = False
        return int(self._require_demo()["tick"])

    def _check_paused_cancelled(self):
        if self._paused_cancelled is not None and self._paused_cancelled():
            raise RuntimeError("Paused camera operation cancelled.")

    @contextmanager
    def _paused_preparation(self, cancelled):
        """Scope a dialog's cancellation token to this operation only.

        A closed panel must cancel calibration even if its operation started
        just after closing. Clearing the playback stop event cannot clear this
        independent token. Other previews/playback never inherit the callback.
        """
        if cancelled is not None and not callable(cancelled):
            raise ValueError("Paused camera cancellation must be callable.")
        previous = self._paused_cancelled
        self._paused_cancelled = cancelled
        try:
            self._check_paused_cancelled()
            yield
        finally:
            self._paused_cancelled = previous

    def _verify_paused_camera(self, frame, tick, source):
        self._paused_details = {"source": source, "tick": tick, "updates": 0,
                                "elapsed_seconds": 0, "runtime_verified_in_Deadlock": False}
        self._paused_samples.clear()
        try:
            self._check_position_cancelled()
            if int(self._require_demo()["tick"]) != tick:
                raise RuntimeError("The replay moved before the camera check. Pause it and start Paused camera again.")
            self._position_direct_frame(frame, tick, refresh=True)
            if int(self._require_demo()["tick"]) != tick:
                raise RuntimeError("The replay moved during the camera check. Pause it and start Paused camera again.")
            self._check_position_cancelled()
            self._set_paused_pose(frame, tick)
            self._paused_details["startup_calibration"] = deepcopy(self._camera_calibration)
            self._reset_motion_observations(frame)
        except Exception as exc:
            self._paused_details["error"] = str(exc)
            self._invalidate_paused_camera()
            raise
        return deepcopy(frame)

    def begin_paused_camera(self, *, cancelled=None):
        """Prepare the current freecam view for movement at a fixed demo tick."""
        if self._supports_native_flight():
            return self.enter_native_flight(cancelled=cancelled)
        with self._op_lock, self._paused_preparation(cancelled):
            tick = self._prepare_paused_camera()
            aspect = self._capture_aspect_ratio()
            self._restore.setdefault(ASPECT_CVAR, self._aspect_capture["raw"])
            roll = self._applied_pose["roll"] if self._applied_pose else 0.0
            key = parse_camera(self._request("spec_pos"), roll=roll, aspect_ratio=aspect)
            frame = asdict(key)
            # Leave the game's current DOF/other variables in place. Replaying
            # _applied_pose's old values could undo a prior Stop / restore.
            frame["cvars"] = {}
            result = self._verify_paused_camera(frame, tick, "current_freecam")
            self._message("Paused camera ready. Move or switch saved views without advancing the replay.")
            return result

    def select_paused_camera(self, project, shot_time, *, cancelled=None):
        """Switch the authored camera and lens, keeping the current demo tick."""
        if self._supports_native_flight():
            with self._op_lock, self._paused_preparation(cancelled):
                self._validate_project(project)
                shot_time = self._shot_time(project, shot_time)
                self._halt(native_action="native_hold")
                self._require_probe()
                self._require_demo()
                self._request("demo_pause")
                self._snapshot(project)
                bridge = self._native_bridge()
                bridge.prepare(project, shot_time, 1.0, True, self._demo.name)
                self._native_active = True
                self._native_manual = True
                status = bridge.status()
                self._require_native_demo(status)
                frame = self._native_pose(status)
                frame.update(time=shot_time, cvars=dict(project.evaluate(shot_time)["cvars"]))
                self._set_paused_pose(frame, int(status["tick"]))
                self._message("Saved camera applied at the current paused replay moment.", time=shot_time,
                              paused_flight=False)
                return deepcopy(frame)
        with self._op_lock, self._paused_preparation(cancelled):
            tick = self._prepare_paused_camera()
            self._snapshot(project)
            shot_time = self._shot_time(project, shot_time)
            frame = dict(project.evaluate(shot_time), time=shot_time)
            result = self._verify_paused_camera(frame, tick, "saved_camera")
            self._message("Saved camera applied at the current paused replay moment.", time=shot_time)
            return result

    def _supports_native_flight(self):
        bridge = self._native_bridge()
        return bridge is not None and callable(getattr(bridge, "start_flight", None))

    def enter_native_flight(self, pose=None, *, cancelled=None):
        """Let the in-game view callback own input and manual camera movement."""
        with self._op_lock, self._paused_preparation(cancelled):
            if not self._supports_native_flight():
                raise RuntimeError("Native paused movement requires the matching native editor build.")
            self._halt(native_action="native_hold")
            self._require_probe()
            self._require_demo()
            restoration_error = self._restore_playback_settings()
            if restoration_error:
                raise RuntimeError(restoration_error)
            if self._game_ui_visible:
                if self._console_open:
                    self.toggle_console(enabled=False)
                self._request("hud_free_cursor 0; citadel_hide_replay_hud 1")
                self._verify_game_ui_values({"hud_free_cursor": 0, "citadel_hide_replay_hud": 1})
                self._game_ui_visible = False
            self._request("demo_pause; demo_timescale 1" if self._demo_speed_changed else "demo_pause")
            self._demo_speed_changed = False
            self._check_paused_cancelled()
            bridge = self._native_bridge()
            # With no supplied pose, the callback seeds from the currently
            # rendered view, including a held path endpoint. No spec_goto or
            # paused player-eye calibration can move that seed vertically.
            if isinstance(pose, dict):
                pose = tuple(pose[name] for name in ("x", "y", "z", "pitch", "yaw", "roll", "aspect_ratio"))
            armed = bridge.start_flight(self._demo.name, pose=pose, cancelled=cancelled)
            self._native_active = True
            self._native_manual = True
            current = bridge.status()
            self._require_native_demo(current)
            # The acknowledgement is sampled by the renderer after it pauses.
            # Console telemetry may still describe the frame before that pause.
            if not armed.get("paused") or not current.get("paused") or int(current["tick"]) != int(armed["tick"]):
                self._release_native_camera()
                raise RuntimeError("The replay moved while opening the paused camera. Pause it and try again.")
            frame = self._native_pose(current)
            frame.update(time=float(current.get("phase", 0)), cvars={})
            self._set_paused_pose(frame, int(current["tick"]))
            self._paused_details.update(source="native_render_input", runtime_verified_in_Deadlock=False)
            self._stop_event.clear()
            self._game_ui_visible = False
            self._message("Paused camera ready. Use the in-game movement controls; F7 opens the console.",
                          playing=False, paused_flight=True, replay_paused=True)
            return deepcopy(frame)

    def toggle_console(self, enabled=None):
        """Native input is suspended before its console-toggle event arrives."""
        with self._op_lock:
            self._require_connection()
            if enabled is not None and not isinstance(enabled, bool):
                raise ValueError("Console visibility must be a boolean.")
            if enabled is None:
                enabled = not self._console_open if self._console_open is not None else True
            bridge = self._native_bridge()
            editor_configure = getattr(bridge, "configure_editor", None)
            if callable(editor_configure):
                # A seek already in progress may have overwritten the optimistic
                # F7 owner before this queued event runs. Suspend input again
                # for the actual command, including a delayed close operation.
                editor_configure(owner="console")
            command = "showconsole" if enabled else "hideconsole"
            if self._console.supports(command):
                self._request(command)
            elif self._console_open == enabled:
                if not enabled and callable(editor_configure):
                    editor_configure(owner="game_ui" if self._game_ui_visible else "panel")
                return self.status()
            elif self._console_open is not None and self._console.supports("toggleconsole"):
                self._request("toggleconsole")
            else:
                raise RuntimeError("This game build did not confirm " + command + "; the console's current visibility is unknown. Console access was not changed.")
            self._console_open = enabled
            if not enabled and callable(editor_configure):
                editor_configure(owner="game_ui" if self._game_ui_visible else "panel")
            self._message("Console open. Editor movement is suspended while typing." if enabled else "Console closed. Return to the editor to continue.")
            return self.status()

    def toggle_game_ui(self, enabled=None):
        """Hand camera/mouse ownership to the game's replay UI, or return."""
        with self._op_lock:
            enabled = not self._game_ui_visible if enabled is None else enabled
            if not isinstance(enabled, bool):
                raise ValueError("Game UI visibility must be a boolean.")
            self._require_demo(require_tick=False)
            # Deadlock has no registered `demoui` command in the reviewed build.
            # These readable cvars control the replay HUD and its real cursor;
            # explicit values also survive manual UI changes and delayed events.
            names = ("citadel_hud_visible", "citadel_hide_replay_hud", "hud_free_cursor")
            current = {name: read_cvar_value(name, self._request(name)) for name in names}
            for name, value in current.items():
                # A playing shot may temporarily hide the HUD. Restore the
                # pre-shot value on Stop, not that transient hidden state.
                self._game_ui_restore.setdefault(name, self._playback_restore.get(name, value))
            bridge = self._native_bridge()
            configure = getattr(bridge, "configure_editor", None)
            if callable(configure):
                # Keep ordinary game input available until native flight is
                # actually ready on return; never optimistically enable flight.
                configure(owner="game_ui")
            if self._console_open:
                self.toggle_console(enabled=False)
                if callable(configure):
                    configure(owner="game_ui")
            if enabled:
                self._halt(native_action="release")
                self._invalidate_paused_camera()
                restoration_error = self._restore_playback_settings()
                if restoration_error:
                    raise RuntimeError(restoration_error)
                self._request("citadel_hud_visible 1; citadel_hide_replay_hud 0; hud_free_cursor 1")
                self._verify_game_ui_values({"citadel_hud_visible": 1, "citadel_hide_replay_hud": 0, "hud_free_cursor": 1})
                self._game_ui_visible = True
                self._message("Deadlock owns the camera and replay UI. Select a hero, then return to Dolly editing.")
                return self.status()
            self._request("hud_free_cursor 0; citadel_hide_replay_hud 1")
            self._verify_game_ui_values({"hud_free_cursor": 0, "citadel_hide_replay_hud": 1})
            if self._game_ui_visible or not (self._native_active and self._native_manual):
                self.enter_native_flight()
                # Stopping a running shot restores its temporary HUD settings.
                # Reassert the explicit editor view after that cleanup.
                self._request("hud_free_cursor 0; citadel_hide_replay_hud 1")
                self._verify_game_ui_values({"hud_free_cursor": 0, "citadel_hide_replay_hud": 1})
            elif callable(configure):
                configure(owner="flight")
            self._game_ui_visible = False
            return self.status()

    def _verify_game_ui_values(self, values):
        output = self._request("; ".join(values))
        for name, expected in values.items():
            if read_cvar_value(name, output) != expected:
                raise RuntimeError("The game did not apply " + name + ". Retry the replay UI control.")

    def _restore_game_ui_settings(self):
        """Restore the original automatic cursor/HUD on Stop or disconnect."""
        if not self._game_ui_restore:
            return None
        try:
            self._require_connection()
            self._request("; ".join(name + " " + numeric(value) for name, value in self._game_ui_restore.items()))
            self._verify_game_ui_values(self._game_ui_restore)
            self._game_ui_restore.clear()
            self._game_ui_visible = False
        except (RuntimeError, ValueError, OSError) as exc:
            LOG.warning("Replay UI restoration remains pending: %s", exc)
            return "Replay UI settings could not be restored; reconnect and use Stop / restore. " + str(exc)
        return None

    def toggle_replay(self):
        """Pause/resume replay time without restarting an authored native path."""
        with self._op_lock:
            self._require_demo()
            bridge = self._native_bridge()
            if bridge is None:
                raise RuntimeError("In-game replay controls require the native editor.")
            current = bridge.status()
            self._require_native_demo(current, allow_idle=True)
            paused = bool(current["paused"])
            if self._thread and self._thread.is_alive() and (self._playback_details or {}).get("frozen"):
                raise RuntimeError("Stop the frozen preview before resuming replay time.")
            if self._native_manual:
                if paused:
                    # Hold the last rendered view while replay time advances.
                    # Movement returns through native flight when paused again.
                    self._halt(native_action="native_hold")
                    bridge.configure_editor(owner="panel")
                else:
                    self._request("demo_pause")
                    return self.enter_native_flight()
            self._request("demo_resume" if paused else "demo_pause")
            self._message("Replay playing." if paused else "Replay paused.", replay_paused=not paused)
            return self.status()

    def seek_relative(self, seconds, tick_rate=None):
        """Seek from the observed demo tick, then retain the displayed camera."""
        with self._op_lock:
            seconds = float(seconds)
            if not math.isfinite(seconds) or abs(seconds) > 3600:
                raise ValueError("Replay seek must be a finite number of seconds within one hour.")
            info = self._require_demo()
            bridge = self._native_bridge()
            if bridge is None:
                raise RuntimeError("In-game replay seeking requires the native editor.")
            status = bridge.status()
            self._require_native_demo(status, allow_idle=True)
            pose = self._native_pose(status, "applied_pose" if self._native_active else "original_pose")
            if tick_rate is None:
                metadata = parse_demo_info(self._request("demo_info"))
                tick_rate = metadata.get("tick_rate")
            if tick_rate is None:
                tick_rate = (self._probe_result.get("demo") or {}).get("tick_rate")
            if isinstance(tick_rate, bool) or not isinstance(tick_rate, (int, float)) or not math.isfinite(tick_rate) or tick_rate <= 0:
                raise RuntimeError("Replay tick rate is unavailable. Set the shot's ticks/second before using relative seek.")
            target = max(0, int(round(int(info["tick"]) + seconds * tick_rate)))
            if info.get("total_ticks") is not None:
                target = min(target, max(0, int(info["total_ticks"]) - 1))
            # A held native view must release before a seek, including replay
            # rewind. Preserve its visual pose explicitly for the return trip.
            self._halt(native_action="release")
            self._invalidate_paused_camera()
            self._stop_event.clear()
            self._seek_tick(target)
            result = self.enter_native_flight(pose=pose)
            self._message("Replay paused at the new time; camera position retained.")
            return result

    @staticmethod
    def _flight_options(move_speed, turn_speed, rate):
        # Reuse the movement model's bounds before launching a worker or
        # issuing any camera command. The frame is inert at zero elapsed time.
        move_camera({"x": 0, "y": 0, "z": 0, "pitch": 0, "yaw": 0},
                    CameraMotion(), 0, move_speed=move_speed, turn_speed=turn_speed)
        if isinstance(rate, bool) or rate not in (30, 60, 120):
            raise ValueError("Choose a command rate of 30, 60, or 120.")
        return float(move_speed), float(turn_speed), float(rate)

    def start_paused_flight(self, input_source=None, move_speed=240, turn_speed=60, rate=60):
        """Run one bounded console writer using wall time, never replay time.

        input_source is called on the worker and must be thread-safe. It must
        return CameraMotion and must not call GUI functions.
        """
        if self._supports_native_flight():
            self._flight_options(move_speed, turn_speed, rate)
            return self.enter_native_flight()
        if not callable(input_source):
            raise ValueError("Paused camera input must be callable.")
        move_speed, turn_speed, rate = self._flight_options(move_speed, turn_speed, rate)
        with self._op_lock:
            self._halt()
            self._check_paused_tick()
            self._stop_event.clear()
            self._reset_motion_observations(self._paused_pose)
            with self._state_lock:
                self._paused_details.update(move_speed=move_speed, turn_speed=turn_speed,
                                            requested_updates_per_second=rate, updates=0)
                self._paused_details.pop("error", None)
            self._message("Paused camera movement active. Escape stops movement; the replay stays paused.",
                          playing=False, paused_flight=True)
            self._thread = threading.Thread(target=self._run_paused_flight,
                args=(input_source, move_speed, turn_speed, rate), daemon=True,
                name="DollyPausedCamera")
            try:
                self._thread.start()
            except Exception:
                self._thread = None
                with self._state_lock:
                    self._state["paused_flight"] = False
                raise

    def _write_paused_pose(self, frame):
        self._check_paused_tick()
        if self._stop_event.is_set():
            return False
        sent_at = time.perf_counter()
        sampled = sent_at >= self._next_camera_readback
        command = self._position_commands(frame) + ("; spec_pos" if sampled else "") + "; demo_goto"
        output = self._request(command)
        received_at = time.perf_counter()
        self._check_paused_tick(output)
        if sampled:
            self._next_camera_readback = received_at + CAMERA_READBACK_INTERVAL
        self._observe_motion_frame(frame, output, sent_at, received_at, self._paused_tick,
                                   sampled=sampled, manual=True)
        self._set_paused_pose(frame, self._paused_tick)
        return True

    def _run_paused_flight(self, input_source, move_speed, turn_speed, rate):
        began = previous = time.perf_counter()
        next_frame = began
        failure = None
        escape = False
        updates = 0
        waiter = FrameWait()
        try:
            while not self._stop_event.is_set():
                now = time.perf_counter()
                motion = input_source()
                if not isinstance(motion, CameraMotion):
                    raise ValueError("Paused camera input must return CameraMotion.")
                if motion.stop:
                    escape = True
                    break
                # Delta includes command latency. move_camera caps long stalls
                # so a delayed response cannot create a large camera jump.
                frame = move_camera(self._paused_pose, motion, max(0.0, now - previous),
                                    move_speed=move_speed, turn_speed=turn_speed)
                previous = now
                if frame != self._paused_pose:
                    if not self._write_paused_pose(frame):
                        break
                    updates += 1
                else:
                    # Check external resume/seek even when no input is held.
                    self._check_paused_tick()
                elapsed = max(0.0, time.perf_counter() - began)
                with self._state_lock:
                    self._paused_details.update(updates=updates, elapsed_seconds=elapsed,
                        achieved_updates_per_second=updates / elapsed if elapsed else 0)
                next_frame += 1 / rate
                now = time.perf_counter()
                if next_frame < now:
                    next_frame = now
                waiter.wait(max(0, next_frame - now), self._stop_event)
        except Exception as exc:
            LOG.exception("Paused camera movement stopped")
            failure = str(exc)
            self._paused_details["error"] = failure
            self._invalidate_paused_camera()
        finally:
            with self._state_lock:
                self._paused_details.update(frame_wait_backend=waiter.backend,
                                             frame_wait_error=waiter.error)
            waiter.close()
            with self._state_lock:
                self._state["paused_flight"] = False
            if failure:
                self._message(failure)
            elif escape:
                self._message("Paused camera movement stopped. Replay and current view remain paused.")

    def stop_paused_flight(self):
        """End manual movement without restoring the current lens or seeking."""
        with self._op_lock:
            if self.status().get("paused_flight"):
                self._halt()
            self._message("Paused camera movement stopped. Current camera settings are held.", paused_flight=False)

    def nudge_paused_camera(self, motion, seconds=.1, move_speed=240, turn_speed=60):
        if self._supports_native_flight():
            with self._op_lock:
                self._halt(native_action="native_hold")
                self._check_paused_tick()
                native = self._native_bridge()
                frame = self._native_pose(native.status())
                frame = move_camera(frame, motion, seconds, move_speed=move_speed, turn_speed=turn_speed)
                self.enter_native_flight(pose=frame)
                self._halt(native_action="native_hold")
                return deepcopy(self._paused_pose)
        with self._op_lock:
            self._halt()
            self._check_paused_tick()
            frame = move_camera(self._paused_pose, motion, seconds,
                                move_speed=move_speed, turn_speed=turn_speed)
            self._stop_event.clear()
            if frame != self._paused_pose:
                self._write_paused_pose(frame)
            return deepcopy(self._paused_pose)

    def apply(self, project, shot_time):
        if self._supports_native_flight():
            return self.select_paused_camera(project, shot_time)
        with self._op_lock:
            self.stop()
            self._stop_event.clear()
            self._require_probe()
            self._require_demo(require_tick=False)
            self._snapshot(project)
            shot_time = self._shot_time(project, shot_time)
            frame = project.evaluate(shot_time)
            self._request("demo_pause")
            tick = self._require_demo(require_tick=False).get("tick")
            self._position_direct_frame(frame, tick, refresh=tick is not None)
            self._message("Camera position verified and lens settings applied. Replay paused at this view.", time=float(shot_time))

    @staticmethod
    def _shot_time(project, value):
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("Shot time must be finite.")
        return max(0.0, min(project.duration, value))

    def _seek(self, project, shot_time):
        target = int(round(project.start_tick + shot_time * project.tick_rate))
        return self._seek_tick(target, allow_start_boundary=True)

    def _seek_shot_time(self, project, shot_time, info):
        """Keep authored keys aligned when the engine cannot reconstruct tick 0."""
        if not info.get("seek_boundary"):
            return shot_time
        actual_time = (info["tick"] - project.start_tick) / project.tick_rate
        if actual_time > project.duration:
            raise RuntimeError("This replay's first seekable tick is 1, after this shot ends. "
                               "Extend the shot past that tick or preview the camera without seeking.")
        self._message("Replay tick 0 precedes its first seekable packet. "
                      f"Starting at tick 1 ({actual_time:.6f} shot seconds); saved camera times are unchanged.")
        return actual_time

    def _seek_tick(self, target, *, allow_start_boundary=False):
        """Verify a paused tick; optionally recognize the replay's packet-1 floor."""
        target = int(target)
        self._check_position_cancelled()
        # Current engine help: demo_goto <tick> [relative] [pause].
        # demo_gototick follows the same command in the reference command list.
        self._request("demo_pause")
        self._check_position_cancelled()
        seek_output = self._request(f"demo_gototick {target} 0 1", timeout=6)
        # Some replays initially expose tick 0, but their first reconstructible
        # full packet is tick 1. Recognize only that explicitly reported floor;
        # a nearby tick alone is never sufficient evidence to change a target.
        boundary_pattern = (r"Demo Skipping:\s*skipping to demo tick 0\s+"
                            r"\(game tick \d+\)\s+from full packet 1\s+\(")
        boundary_reported = bool(allow_start_boundary and target == 0 and
                                 re.search(boundary_pattern, seek_output, re.IGNORECASE))
        boundary_samples = 0
        boundary_pause_confirmed = False
        self._check_position_cancelled()
        end = time.perf_counter() + 15
        samples = deque(maxlen=32)
        stable = 0
        overshoot_tick = None
        overshoot_samples = 0
        pause_confirmed = False
        corrections = []
        self._last_seek_details = {"target_tick": target, "verified": False,
                                   "corrections": corrections}
        while time.perf_counter() < end:
            self._check_position_cancelled()
            info = self._require_demo()
            self._check_position_cancelled()
            observed = int(info["tick"])
            samples.append({"at": time.perf_counter(), "tick": observed})
            self._last_seek_details["samples"] = list(samples)
            if boundary_reported and corrections and observed == 1:
                boundary_samples += 1
                if boundary_samples >= SEEK_SETTLE_SAMPLES:
                    if boundary_pause_confirmed:
                        self._last_seek_details.update(verified=True,
                            actual_tick=1, boundary="first_full_packet",
                            boundary_delta_ticks=1)
                        # Return the real tick. Playback must evaluate the path
                        # at that tick rather than silently move every keyframe.
                        return dict(info, seek_boundary={"requested_tick": 0,
                            "actual_tick": 1, "reason": "first_full_packet"})
                    self._request("demo_pause")
                    boundary_pause_confirmed = True
                    boundary_samples = 0
            else:
                boundary_samples = 0
                boundary_pause_confirmed = False
            stable = stable + 1 if observed == target else 0
            if stable >= SEEK_SETTLE_SAMPLES:
                if pause_confirmed:
                    self._last_seek_details["verified"] = True
                    return info
                self._request("demo_pause")
                pause_confirmed = True
                stable = 0
            if target < observed <= target + 2:
                overshoot_samples = overshoot_samples + 1 if observed == overshoot_tick else 1
                overshoot_tick = observed
            else:
                overshoot_tick = None
                overshoot_samples = 0
            if overshoot_samples >= SEEK_SETTLE_SAMPLES and not corrections:
                # Native forward seeks can stop two ticks late. Reissuing the
                # SAME target from there takes the backward-seek route. Do not
                # react to a transient ahead sample or chase an unrelated seek.
                self._check_position_cancelled()
                self._request("demo_pause")
                self._check_position_cancelled()
                confirmed = int(self._require_demo()["tick"])
                self._check_position_cancelled()
                if confirmed != observed:
                    if confirmed != target:
                        raise RuntimeError("The replay moved during seek correction. Pause it and retry.")
                    # It may have settled on its own while pause was processed.
                    # Restart observation without issuing a corrective seek.
                    stable = 0
                    overshoot_tick = None
                    overshoot_samples = 0
                    pause_confirmed = False
                elif time.perf_counter() < end:
                    corrections.append({"from_tick": confirmed, "target_tick": target,
                                        "at": time.perf_counter(),
                                        "samples_before": list(samples)})
                    correction_output = self._request(f"demo_gototick {target} 0 1", timeout=6)
                    boundary_reported = bool(boundary_reported and
                        re.search(boundary_pattern, correction_output, re.IGNORECASE))
                    self._check_position_cancelled()
                    stable = 0
                    overshoot_tick = None
                    overshoot_samples = 0
                    pause_confirmed = False
            if self._stop_event.wait(SEEK_SETTLE_INTERVAL):
                raise RuntimeError("Seek cancelled.")
        raise RuntimeError(f"Replay did not reach tick {target}. Export diagnostics; the demo may have ended or seeking may differ in this build.")

    def seek(self, project, shot_time):
        with self._op_lock:
            self.stop()
            self._stop_event.clear()
            self._require_probe()
            self._require_demo()
            self._snapshot(project)
            shot_time = self._shot_time(project, shot_time)
            positioned_demo = self._seek(project, shot_time)
            shot_time = self._seek_shot_time(project, shot_time, positioned_demo)
            self._position_direct_frame(project.evaluate(shot_time), positioned_demo["tick"])
            self._message("Replay paused at the requested path time.", time=shot_time)

    def play(self, project, time=0, speed=1, rate=60, frozen=False, hide_hud=True, smoothing="off"):
        # The main UI starts at zero; explicit API callers may choose a start.
        # Reject invalid experimental modes before stopping a working camera.
        window = smoothing_window(smoothing)
        if self._native_bridge() is not None:
            compile_effects(project)  # Reject unsupported tracks before stopping a working shot.
        with self._op_lock:
            self.stop()
            if self._playback_restore or self._restore or self._game_ui_restore or self._demo_speed_changed:
                raise RuntimeError("Previous settings still need restoration. Reconnect and use Stop / restore before playing again.")
            self._stop_event.clear()
            self._require_probe()
            self._require_demo(require_tick=not frozen)
            project = Project.from_dict(project.to_dict())
            speed, rate = float(speed), float(rate)
            if not math.isfinite(speed) or not .05 <= speed <= 4:
                raise ValueError("Playback speed must be between 0.05 and 4.")
            if rate not in (30, 60, 120):
                raise ValueError("Choose a command rate of 30, 60, or 120.")
            if hide_hud and ("citadel_hud_visible" in project.setup_values or
                             any(track.name == "citadel_hud_visible" for track in project.tracks)):
                raise ValueError("Remove the citadel_hud_visible track/fixed value or turn off Hide HUD during playback.")
            start = self._shot_time(project, time)
            self._snapshot(project)
            self._playback_details = {"project": project.to_dict(), "start_time": start,
                                      "speed": speed, "rate": rate, "frozen": bool(frozen),
                                      "hide_hud": bool(hide_hud),
                                      "smoothing": {"mode": smoothing, "window_seconds": window,
                                                    "nominal_delay_seconds": window / 2,
                                                    "kind": "shared_phase_finite_window"},
                                      "clock_max_lead_ticks": 0 if frozen else 1}
            native = self._native_bridge()
            self._playback_details["camera_backend"] = "native" if native is not None else "console"
            if native is not None:
                self._playback_details["smoothing"] = {"mode": "off", "requested_mode": smoothing,
                    "kind": "native_view_time", "window_seconds": 0, "nominal_delay_seconds": 0}
                self._playback_details["camera_rate"] = "main view callbacks"
                self._playback_details["cvar_timing"] = "Verified DOF curves apply at native main-view phase, with typed setter readback."
            self._playback_metrics = {"updates": 0, "requested_updates_per_second": rate,
                                      "mean_round_trip_ms": 0, "max_round_trip_ms": 0,
                                      "max_update_interval_ms": 0, "elapsed_seconds": 0,
                                      "achieved_updates_per_second": 0}
            self._playback_samples.clear()
            try:
                self._prepare_playback_settings(hide_hud)
                if not frozen:
                    positioned_demo = self._seek(project, start)
                    actual_start = self._seek_shot_time(project, start, positioned_demo)
                    if positioned_demo.get("seek_boundary"):
                        self._playback_details.update(requested_start_time=start,
                            start_time=actual_start,
                            seek_boundary=deepcopy(positioned_demo["seek_boundary"]))
                        start = actual_start
                else:
                    self._request("demo_pause")
                    positioned_demo = self._require_demo(require_tick=False)
                    if positioned_demo.get("tick") is not None:
                        positioned_demo = self._seek_tick(positioned_demo["tick"])
                frame = project.evaluate(start)
                if native is not None:
                    frame["cvars"] = {}  # Native preparation snapshots originals before the first effect write.
                try:
                    self._position_direct_frame(frame, positioned_demo.get("tick"))
                finally:
                    self._playback_details["startup_calibration"] = deepcopy(self._camera_calibration)
                    self._playback_details["startup_seek"] = (deepcopy(self._last_seek_details)
                        if positioned_demo.get("tick") is not None else None)
                self._reset_motion_observations(frame)
                checked_demo = self._require_demo(require_tick=not frozen)
                if (positioned_demo.get("tick") is not None and
                    checked_demo.get("tick") != positioned_demo["tick"]):
                    raise RuntimeError("The replay moved during the camera position check. Keep it paused until Dolly starts the shot, then retry.")
                self._check_position_cancelled()
                command = self._position_commands(frame)
                if native is not None:
                    # The whole shot is published once. HOLD must be acknowledged
                    # while paused, then PLAY is armed before demo_resume.
                    self._request(command)
                    self._native_active = True
                    native.prepare(project, start, speed, bool(frozen), self._demo.name)
                    self._native_last_status = native.status()
                    self._require_native_demo(self._native_last_status)
                    self._check_position_cancelled()
                    native.play()
                    if not frozen:
                        self._demo_speed_changed = True
                        self._request("demo_timescale " + numeric(speed) + "; demo_resume")
                    self._playback_details["initial_tick"] = positioned_demo.get("tick")
                    self._applied_pose = frame
                    self._message("Playing native frozen preview." if frozen else
                                  "Playing native camera path at render time.", playing=True, time=start)
                    self._thread = threading.Thread(target=self._run_native,
                        args=(project, start, speed, rate, bool(frozen)), daemon=True, name="DollyNativePlayback")
                    self._thread.start()
                    return
                if not frozen:
                    # Apply the initial camera/lens/cvars before resuming in
                    # one ordered command batch, while the replay is paused.
                    self._demo_speed_changed = True
                    command += "; demo_timescale " + numeric(speed) + "; demo_resume"
                self._playback_details["initial_tick"] = positioned_demo.get("tick")
                self._playback_details["camera_prepared_at"] = self._recent_camera_targets[-1][0]
                self._request(command)
                self._applied_pose = frame
                self._message("Playing frozen preview." if frozen else "Playing shot with the replay.",
                              playing=True, time=start)
                self._thread = threading.Thread(target=self._run, args=(project, start, speed, rate, frozen, smoothing),
                                                daemon=True, name="DollyPlayback")
                self._thread.start()
            except Exception as exc:
                self._playback_details["error"] = str(exc)
                if native is not None:
                    cleanup = self._finish_native_playback()
                    if cleanup:
                        LOG.warning("Native startup cleanup: %s", cleanup)
                else:
                    self._finish_playback()
                raise

    def _native_bridge(self):
        return getattr(self._session, "native", None) if self._session is not None else None

    def _require_native_demo(self, status, *, allow_idle=False):
        name = str(status.get("demo_name") or "").replace("\\", "/").rsplit("/", 1)[-1]
        if not name or self._demo is None or Path(name).stem.casefold() != self._demo.stem.casefold():
            raise RuntimeError("Native camera replay identity changed. Camera playback stopped; restart the intended replay through Dolly.")
        disallowed = ("fault", "unsupported", "starting") if allow_idle else ("fault", "unsupported", "starting", "probe", "stopped")
        if status.get("state") in disallowed:
            raise RuntimeError("Native camera is unavailable: " + str(status.get("message") or status.get("state")))

    @staticmethod
    def _native_pose(status, field="applied_pose"):
        pose = status.get(field)
        if not isinstance(pose, (tuple, list)) or len(pose) != 7:
            raise RuntimeError("Native camera telemetry has no valid view pose.")
        values = [float(value) for value in pose]
        if not all(math.isfinite(value) for value in values):
            raise RuntimeError("Native camera telemetry contains a non-finite view pose.")
        return dict(zip(("x", "y", "z", "pitch", "yaw", "roll", "aspect_ratio"), values))

    def _handoff_native_camera(self, *, allow_hold=False):
        """Keep the native view held until the underlying spectator catches up.

        spec_pos can report the overridden camera. Only the unmodified view
        supplied to the native callback can verify this handoff. If the game
        cannot settle while paused, leave the view held and block competing
        camera writers. Explicit Stop releases ownership without requiring a
        position match; Play uses that release before its normal seek.
        """
        bridge = self._native_bridge()
        if bridge is None or not self._native_active:
            return
        if not self._alive():
            self._native_active = False
            return
        status = bridge.status()
        self._native_last_status = status
        try:
            self._require_native_demo(status)
            self._require_demo(require_tick=False)
        except Exception:
            # Never reposition another replay or keep overriding an invalid one.
            bridge.release()
            self._native_active = False
            return
        if status.get("state") == "playing":
            bridge.hold()
            status = bridge.status()
        if status.get("effect_count", 0):
            # Keep the shot's final DOF and camera together until explicit Stop.
            # Releasing would restore native effect snapshots for one frame.
            self._native_handoff_details = {"verified": False, "pending": True,
                "reason": "Final native camera and DOF held until Play or Stop"}
            if allow_hold:
                return
            raise RuntimeError("Native camera and DOF are held together. Use Stop / restore before manual camera controls, or Play shot to restart.")
        self._request("demo_pause")
        target = self._native_pose(status)
        target["cvars"] = {}
        target["time"] = float(status["phase"])
        self._native_handoff_details = {"verified": False, "target": dict(target), "samples": []}
        self._request(self._position_commands(target))
        deadline = time.perf_counter() + 2.0
        previous_frame = int(status["frame_count"])
        stable = 0
        tick = int(status["tick"])
        # A fresh event is deliberate: Stop has already set _stop_event, but a
        # bounded held-camera handoff must still complete before another writer.
        waiter = threading.Event()
        while time.perf_counter() < deadline:
            waiter.wait(.04)
            current = bridge.status()
            self._native_last_status = current
            try:
                self._require_native_demo(current)
            except Exception:
                bridge.release()
                self._native_active = False
                raise
            if int(current["tick"]) != tick or not current["paused"]:
                raise RuntimeError("The replay moved during native camera handoff. The view remains held; pause the replay and use Stop / restore.")
            frame_count = int(current["frame_count"])
            if frame_count == previous_frame:
                continue
            previous_frame = frame_count
            original = self._native_pose(current, "original_pose")
            position_error = math.dist([original[k] for k in CAMERA_AXES], [target[k] for k in CAMERA_AXES])
            angle_error = max(abs((original[k] - target[k] + 180) % 360 - 180)
                              for k in ("pitch", "yaw", "roll"))
            self._native_handoff_details["samples"].append({"frame_count": frame_count,
                "position_error": position_error, "angle_error": angle_error})
            stable = stable + 1 if position_error <= CAMERA_POSITION_TOLERANCE and angle_error <= .2 else 0
            if stable >= 3:
                bridge.release()
                self._native_active = False
                self._native_handoff_details["verified"] = True
                self._applied_pose = target
                return
        self._native_handoff_details["pending"] = True
        if allow_hold:
            return
        raise RuntimeError("Native camera is holding the final view because the underlying free camera did not settle. Use Play shot to restart, or Stop / restore to return control to the game before using paused movement.")

    def _release_native_camera(self):
        """Abandon a held view on explicit Stop, before any new seek or writer."""
        bridge = self._native_bridge()
        if not self._native_active:
            self._native_manual = False
            return
        if self._alive():
            if bridge is None:
                raise RuntimeError("Cannot release native camera: its bridge is unavailable.")
            # release waits for acknowledgement. Keep ownership on failure so
            # a new camera writer cannot race an override that is still active.
            bridge.release()
        self._native_active = False
        self._native_manual = False
        self._reset_camera_position()
        if self._native_handoff_details is not None:
            self._native_handoff_details.update(pending=False, released=True,
                release_reason="explicit_stop")


    def _finish_native_playback(self):
        errors = []
        bridge = self._native_bridge()
        if bridge is not None and self._native_active:
            try:
                status = bridge.status()
                if status.get("state") == "playing":
                    bridge.hold()
            except Exception as exc:
                errors.append("Could not hold native camera: " + str(exc))
        restoration = self._finish_playback()
        if restoration:
            errors.append(restoration)
        try:
            self._handoff_native_camera(allow_hold=True)
        except Exception as exc:
            errors.append(str(exc))
        return " ".join(errors) or None

    def _run_native(self, project, start, speed, rate, frozen):
        """Monitor native camera and DOF samples; no animated console writes."""
        bridge = self._native_bridge()
        began = time.perf_counter()
        last_fresh = began
        next_identity = began
        previous_frame = None
        first_frame = None
        previous_tick = None
        previous_phase = start
        previous_poll = began
        expected_effects = len(project.evaluate(start)["cvars"])
        updates = 0
        finished = False
        failure = None
        waiter = FrameWait()
        try:
            if bridge is None:
                raise RuntimeError("This session has no native camera bridge. Relaunch with the Native camera driver.")
            while not self._stop_event.is_set():
                self._require_connection()
                now = time.perf_counter()
                status = bridge.status()
                self._native_last_status = status
                self._require_native_demo(status)
                if status.get("state") not in ("playing", "completed"):
                    raise RuntimeError("Native playback was interrupted. The camera remains held for a verified handoff.")
                phase = float(status["phase"])
                if not math.isfinite(phase) or phase < previous_phase - 1e-5 or phase > project.duration + 1e-5:
                    raise RuntimeError("Native camera time changed unexpectedly. Playback stopped before following that seek.")
                tick = int(status["tick"])
                if previous_tick is not None:
                    allowed = max(128, project.tick_rate * 2,
                                  max(0, now - previous_poll) * project.tick_rate * speed * 2 + 4)
                    if tick < previous_tick or tick - previous_tick > allowed:
                        raise RuntimeError("The replay jumped during the native shot. Camera playback stopped.")
                    if frozen and tick != previous_tick:
                        raise RuntimeError("The scene resumed during native Frozen preview. Camera playback stopped.")
                frame_count = int(status["frame_count"])
                if previous_frame is not None and frame_count < previous_frame:
                    raise RuntimeError("The native view callback restarted unexpectedly. Relaunch the editing session.")
                if frame_count != previous_frame:
                    last_fresh = now
                    if first_frame is None:
                        first_frame = frame_count
                elif now - last_fresh > 3:
                    raise RuntimeError("The native camera stopped receiving rendered views. Restore the game window and export diagnostics.")
                if now >= next_identity:
                    self._require_demo(require_tick=not frozen)
                    next_identity = time.perf_counter() + .5
                evaluated = project.evaluate(min(project.duration, max(0, phase)))
                if status.get("effect_count", 0) != expected_effects:
                    raise RuntimeError("Native effect count does not match the shot. Rebuild the complete Windows package.")
                if expected_effects and (status.get("effect_error", 0) or
                        abs(float(status.get("effect_phase", -1)) - phase) > 1e-9):
                    raise RuntimeError("Native effects did not acknowledge the camera phase. Playback stopped.")
                pose = self._native_pose(status)
                pose.update(time=phase, cvars=dict(evaluated.get("cvars", {})))
                self._applied_pose = pose
                updates += 1
                elapsed = max(0, time.perf_counter() - began)
                with self._state_lock:
                    self._state.update(time=phase, tick=tick)
                    self._playback_samples.append(dict(status, time=phase, camera_backend="native"))
                    self._playback_metrics = {"camera_backend": "native", "updates": updates,
                        "monitor_updates_per_second": rate, "elapsed_seconds": elapsed,
                        "rendered_view_updates": frame_count - first_frame,
                        "rendered_views_per_second": (frame_count - first_frame) / elapsed if elapsed else 0,
                        "effect_frames": status.get("effect_frames", 0),
                        "effect_phase": status.get("effect_phase"),
                        "native_frame_interval_ms": status.get("frame_interval_ms"),
                        "native_max_frame_interval_ms": status.get("max_frame_interval_ms")}
                if status.get("state") == "completed":
                    if abs(phase - project.duration) > 1e-5:
                        raise RuntimeError("Native completion did not reach the authored endpoint.")
                    finished = True
                    break
                previous_frame, previous_tick, previous_phase, previous_poll = frame_count, tick, phase, now
                waiter.wait(max(0, 1 / rate - (time.perf_counter() - now)), self._stop_event)
        except Exception as exc:
            LOG.exception("Native camera playback stopped")
            failure = str(exc)
            if self._playback_details is not None:
                self._playback_details["error"] = failure
        finally:
            waiter.close()
            restoration = self._finish_native_playback()
            if failure or restoration:
                self._message(" ".join(part for part in (failure, restoration) if part), playing=False)
            elif finished:
                self._message("Native shot finished. Replay paused and final camera held. Play shot restarts; Stop / restore returns control to the game." if self._native_active else "Native shot finished. Replay paused, free-camera handoff verified and HUD restored.", playing=False)

    def _finish_playback(self):
        # Camera/cvar framing remains available for inspection until Stop.
        # HUD and background throttling are restored on every exit path.
        try:
            self._require_demo(require_tick=False)
            commands = "demo_pause"
            if self._demo_speed_changed:
                commands += "; demo_timescale 1"
            self._request(commands)
            self._demo_speed_changed = False
        except Exception as exc:
            LOG.info("Could not pause/reset replay during playback cleanup: %s", exc)
        restoration_error = self._restore_playback_settings()
        with self._state_lock:
            self._state["playing"] = False
            self._state["paused_flight"] = False
        return restoration_error

    def _run(self, project, start, speed, rate, frozen, smoothing="off"):
        began = time.perf_counter()
        next_frame = began
        idle_since = began
        finished = False
        failure = None
        updates = 0
        total_round_trip = 0.0
        max_round_trip = 0.0
        max_interval = 0.0
        previous_update = None
        last_observed_at = began
        endpoint_started_at = None
        endpoint_settle_limit = 2.0
        waiter = FrameWait()
        try:
            window = smoothing_window(smoothing)
            phase = PhaseSmoother(window, began, start) if window else None
            if self._playback_details is not None:
                self._playback_details["smoothing"] = {
                    "mode": smoothing, "window_seconds": window,
                    "nominal_delay_seconds": window / 2,
                    "kind": "shared_phase_finite_window"}
            # One identity check before the first frame. Each subsequent frame
            # returns its own fresh demo status, avoiding a second round trip.
            info = self._require_demo(require_tick=not frozen)
            tick = int(info["tick"]) if info.get("tick") is not None else None
            frozen_tick = tick
            prepared = self._playback_details or {}
            initial_tick = prepared.get("initial_tick")
            if frozen and initial_tick is not None:
                frozen_tick = initial_tick
            if not frozen and initial_tick is not None:
                elapsed = max(0.0, time.perf_counter() - prepared.get("camera_prepared_at", began))
                allowed = max(128.0, project.tick_rate * 2, elapsed * project.tick_rate * speed * 2 + 4)
                if tick - initial_tick > allowed:
                    raise RuntimeError("The replay jumped forward before camera playback started. Use Play shot to restart.")
            clock = ReplayClock(project.tick_rate, speed) if not frozen else None
            if clock:
                # Allow two replay-tick periods with a .25–2 second real-time
                # budget. Compute only after ReplayClock validates its rate.
                endpoint_settle_limit = max(.25, 2 / max(1.0, project.tick_rate * speed)) + window
                clock.observe(tick, time.perf_counter())
            elif phase:
                endpoint_settle_limit = .25 + window
            while not self._stop_event.is_set():
                self._require_connection()
                now = time.perf_counter()
                if frozen:
                    if frozen_tick is not None and tick is not None and tick != frozen_tick:
                        raise RuntimeError("The scene resumed during Frozen preview. Camera playback stopped.")
                    raw_time = start + (now - began) * speed
                    source_at_end = raw_time >= project.duration
                else:
                    acknowledged_time = (tick - project.start_tick) / project.tick_rate
                    if acknowledged_time < 0:
                        raise RuntimeError("Replay moved before this path's start tick. Use Play shot to restart.")
                    raw_time = (clock.position(now) - project.start_tick) / project.tick_rate
                    # The camera may lead by at most one demo tick, but the HUD
                    # is kept hidden until the actual replay reaches the end.
                    source_at_end = acknowledged_time >= project.duration
                    if now - idle_since > 1.0:
                        if self.status().get("message") != "Replay is paused or waiting; the path is holding its camera.":
                            self._message("Replay is paused or waiting; the path is holding its camera.", playing=True)
                # Average one shared phase, then evaluate the original path.
                # Do not independently lag the axes, cut corners, blend across
                # discrete cvar values, or extrapolate past the existing clock.
                bounded_time = min(project.duration, max(0.0, raw_time))
                shot_time = phase.update(now, bounded_time) if phase else raw_time
                reached_end = source_at_end and shot_time >= project.duration
                if source_at_end and endpoint_started_at is None and (not frozen or phase):
                    endpoint_started_at = now
                    if self._playback_details is not None:
                        self._playback_details["endpoint_settle"] = {
                            "started_at": now, "budget_seconds": endpoint_settle_limit,
                            "initial_lag_ticks": max(0.0, (project.duration - shot_time) * project.tick_rate),
                            "elapsed_seconds": 0.0, "verified": False}
                if endpoint_started_at is not None:
                    settling = max(0.0, now - endpoint_started_at)
                    if self._playback_details is not None:
                        self._playback_details["endpoint_settle"].update(
                            elapsed_seconds=settling, clock_ready=raw_time >= project.duration,
                            filter_ready=shot_time >= project.duration)
                    if not reached_end and settling >= endpoint_settle_limit:
                        raise RuntimeError("The camera clock did not settle at the shot endpoint. Playback stopped without jumping to the final view. Use Play shot to restart; export diagnostics if it persists.")
                # Keep the same fractional camera clock through the final pan.
                # An integer end-tick acknowledgement must not snap a lagging
                # camera ahead. Once both have arrived, send the exact last key.
                if reached_end:
                    shot_time = project.duration
                frame = dict(project.evaluate(min(project.duration, shot_time)), time=shot_time)
                command = self._position_commands(frame)
                if frozen and tick is None:
                    command += "; demo_pause"
                sent_at = time.perf_counter()
                sampled = sent_at >= self._next_camera_readback
                if sampled:
                    command += "; spec_pos"
                command += "; demo_goto"
                output = self._request(command)
                received_at = time.perf_counter()
                # Never queue an unbounded set of camera commands. There is
                # one outstanding batch, acknowledged before the next frame.
                next_info = self._resolve_demo(output, require_tick=not frozen)
                next_tick = int(next_info["tick"]) if next_info.get("tick") is not None else None
                if not frozen:
                    expected = max(0.0, received_at - last_observed_at) * project.tick_rate * speed
                    allowed_jump = max(128.0, project.tick_rate * 2, expected * 2 + 4)
                    if next_tick - tick > allowed_jump:
                        raise RuntimeError("The replay jumped forward during the shot. Camera playback stopped before following that seek. Use Play shot to restart.")
                last_observed_at = received_at
                if sampled:
                    self._next_camera_readback = received_at + CAMERA_READBACK_INTERVAL
                self._observe_motion_frame(frame, output, sent_at, received_at, next_tick, sampled=sampled)
                with self._state_lock:
                    self._playback_samples[-1].update(
                        raw_time=bounded_time, smoothing_mode=smoothing,
                        smoothing_delay_shot_seconds=max(0.0, bounded_time - shot_time),
                        smoothing_delay_ms=1000 * max(0.0, bounded_time - shot_time) / speed)
                if clock:
                    clock.observe(next_tick, received_at)
                    with self._state_lock:
                        self._playback_samples[-1].update(
                            clock_estimate_tick=raw_time * project.tick_rate + project.start_tick,
                            camera_estimate_tick=shot_time * project.tick_rate + project.start_tick,
                            clock_lag_ticks=clock.lag_ticks,
                            clock_phase_error_ticks=clock.phase_error_ticks)
                self._applied_pose = frame
                updates += 1
                round_trip = received_at - sent_at
                total_round_trip += round_trip
                max_round_trip = max(max_round_trip, round_trip)
                if previous_update is not None:
                    max_interval = max(max_interval, received_at - previous_update)
                previous_update = received_at
                elapsed = max(0.0, received_at - began)
                with self._state_lock:
                    self._state["time"] = max(0.0, min(project.duration, shot_time))
                    self._playback_metrics = {
                        "updates": updates, "requested_updates_per_second": rate,
                        "elapsed_seconds": elapsed,
                        "achieved_updates_per_second": updates / elapsed if elapsed else 0,
                        "mean_round_trip_ms": total_round_trip * 1000 / updates,
                        "max_round_trip_ms": max_round_trip * 1000,
                        "max_update_interval_ms": max_interval * 1000}
                if reached_end:
                    if (not frozen or phase) and self._playback_details is not None:
                        self._playback_details["endpoint_settle"].update(
                            verified=True, camera_completed_at=received_at)
                    finished = True
                    break
                if next_tick != tick:
                    idle_since = received_at
                    if not frozen and self.status().get("message") == "Replay is paused or waiting; the path is holding its camera.":
                        self._message("Playing shot with the replay.", playing=True)
                tick = next_tick
                next_frame += 1 / rate
                now = time.perf_counter()
                if next_frame < now:
                    next_frame = now
                waiter.wait(max(0, next_frame - now), self._stop_event)
        except Exception as exc:
            LOG.exception("Camera playback stopped")
            failure = str(exc)
        finally:
            with self._state_lock:
                self._playback_metrics.update(frame_wait_backend=waiter.backend,
                                               frame_wait_error=waiter.error)
            waiter.close()
            restoration_error = self._finish_playback()
            if failure or restoration_error:
                self._message(" ".join(part for part in (failure, restoration_error) if part), playing=False)
            elif finished:
                self._message("Shot finished. Replay paused and playback settings restored; Stop restores lens/cvar values.", playing=False)

    def _halt(self, *, native_action="handoff"):
        self._stop_event.set()
        worker = self._thread
        if worker and worker is not threading.current_thread():
            worker.join(timeout=10)
            if worker.is_alive():
                raise RuntimeError("Playback is still waiting for the game console. Wait a moment before retrying.")
        self._thread = None
        if native_action == "release":
            self._release_native_camera()
        elif self._native_active and (self._native_manual or native_action == "native_hold"):
            bridge = self._native_bridge()
            if bridge is None:
                raise RuntimeError("The native camera bridge is unavailable; camera ownership could not be changed.")
            if self._alive():
                bridge.hold()
                status = bridge.status()
                self._native_last_status = status
                self._require_native_demo(status)
                frame = self._native_pose(status)
                frame.update(time=float(status.get("phase", 0)), cvars={})
                self._set_paused_pose(frame, int(status["tick"]))
            else:
                self._native_active = self._native_manual = False
        else:
            self._handoff_native_camera(allow_hold=native_action == "hold")
        with self._state_lock:
            self._state["playing"] = False
            self._state["paused_flight"] = False

    def pause(self):
        self._halt(native_action="hold")
        restoration_error = self._restore_playback_settings()
        if self._console and self._console.is_connected and self._alive():
            self._require_demo(require_tick=False)
            self._request("demo_pause; demo_timescale 1" if self._demo_speed_changed else "demo_pause")
            self._demo_speed_changed = False
        self._message(restoration_error or "Paused. HUD restored; the current camera and lens values are held.", playing=False)

    def stop(self):
        self._halt(native_action="release")
        bridge = self._native_bridge()
        if bridge is not None and callable(getattr(bridge, "editor_status", None)) and self._alive():
            try:
                editor = bridge.editor_status()
                if editor.get("enabled"):
                    # Release leaves the camera with the game. Show controls
                    # instead of leaving an apparently frozen flight mode.
                    bridge.configure_editor(owner="console" if editor.get("console_open") else "panel")
            except (RuntimeError, ValueError, OSError):
                # UI recovery must never prevent the remaining cvar cleanup.
                LOG.exception("Could not open the editor panel after Stop")
        self._invalidate_paused_camera()
        restoration_error = self._restore_playback_settings()
        ui_error = self._restore_game_ui_settings()
        restoration_error = " ".join(part for part in (restoration_error, ui_error) if part) or None
        if (self._restore or self._demo_speed_changed) and self._console and self._console.is_connected and self._alive():
            try:
                self._require_demo(require_tick=False)
                commands = []
                for name, value in sorted(self._restore.items()):
                    validate_cvar_name(name)
                    commands.append(name + " " + numeric(value))
                if self._demo_speed_changed:
                    # demo_timescale is a command, not a readable cvar. Stop
                    # explicitly returns speed to 1x rather than guessing a
                    # prior speed. Playback cleanup and Pause also reset it.
                    commands.append("demo_timescale 1")
                self._request("; ".join(commands))
                self._restore.clear()
                self._demo_speed_changed = False
            except Exception as exc:
                self._message("Could not restore all cvars: " + str(exc))
                return
        with self._state_lock:
            self._state["playing"] = False
        if restoration_error:
            self._message(restoration_error, playing=False)

    def disconnect(self):
        self.stop()
        if self._console:
            self._console.close()
        self._console = None
        self._probe_result = {}
        self._reset_camera_position()
        self._message("Disconnected.", connected=False, playing=False)

    def export_diagnostics(self, destination):
        destination = Path(destination)
        if destination.suffix.lower() != ".zip":
            destination = destination / ("Dolly_diagnostics_" + time.strftime("%Y%m%d_%H%M%S") + ".zip")
        destination.parent.mkdir(parents=True, exist_ok=True)
        report = {"version": __version__, "state": self.status(), "protocol": self._protocol,
                  "motion_clock": motion_clock_info(),
                  "launch_attempt": self._launch_attempt,
                  "startup_evidence": self._startup_evidence,
                  "game_exit_code": self._session.process.poll() if self._session else None,
                  "lens_control": self.lens_cvar, "standard_aspect": self.standard_aspect,
                  "aspect_capture": dict(self._aspect_capture), "probe": self._probe_result,
                  "recent_console_responses": self._last_output,
                  "pending_cvar_restoration": self._restore,
                  "pending_playback_restoration": dict(self._playback_restore),
                  "pending_game_ui_restoration": dict(self._game_ui_restore),
                  "last_playback": self._playback_details,
                  "native_camera": deepcopy(self._native_last_status),
                  "native_handoff": deepcopy(self._native_handoff_details),
                  "playback_metrics": dict(self._playback_metrics),
                  "playback_frame_samples": list(self._playback_samples),
                  "paused_camera_samples": list(self._paused_samples),
                  "last_seek": deepcopy(self._last_seek_details),
                  "camera_position_offset": dict(self._camera_offset),
                  "camera_calibration": self._camera_calibration,
                  "paused_camera": deepcopy(self._paused_details),
                  "local_demo_name": self._demo.name if self._demo else None,
                  "runtime_verified_in_Deadlock": False}
        bridge = self._native_bridge()
        if bridge is not None and callable(getattr(bridge, "diagnostics", None)):
            # Include current input owner, event queue and overlay availability,
            # even when the last authored-path sample predates manual editing.
            report["native_editor_runtime"] = bridge.diagnostics()
        history = None
        if self._console and hasattr(self._console, "recent_output"):
            try:
                history = self._console.recent_output()
            except Exception as exc:
                report["console_history_error"] = str(exc)
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("diagnostics.json", json.dumps(report, indent=2, ensure_ascii=False))
            if history is not None:
                archive.writestr("logs/recent_console.txt", history)
            for path in (ROOT / "logs").glob("*.log"):
                if path.is_file():
                    archive.writestr("logs/" + path.name, _tail_file(path))
            if self._session:
                for name in ("session.json", "launch.log", "game_stdout.log"):
                    file = self._session.session_dir / name
                    if file.is_file():
                        archive.writestr("session/" + name, _tail_file(file))
            # A successful retry must not hide the previous crashed launch.
            journals = sorted((ROOT / "logs").glob("*/session.json"), key=lambda p: p.parent.name, reverse=True)[:8]
            for journal in journals:
                folder = journal.parent
                if self._session and folder.resolve() == self._session.session_dir.resolve():
                    continue
                for name in ("session.json", "launch.log", "game_stdout.log"):
                    file = folder / name
                    if file.is_file():
                        archive.writestr("previous_sessions/" + folder.name + "/" + name, _tail_file(file, 512_000))
        self._message("Diagnostics exported to " + str(destination))
        return destination

    def close(self):
        try:
            self.disconnect()
        finally:
            if self._session:
                self._session.restore_gameinfo()
                if self._session.process.poll() is not None:
                    self._session.close()
