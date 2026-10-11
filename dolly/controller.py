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
# Compatibility exports shared with graphics runtime diagnostics.
from .convar_response import NUMBER, read_cvar_value
from .path import (Project, Keyframe, validate_cvar_name, STANDARD_ASPECT, ASPECT_MIN, ASPECT_MAX,
                   validate_cvar_value, format_cvar_value)
# Camera command/parse layer, re-exported for existing callers and tests.
from .camera_commands import (ASPECT_CVAR, DISCRETE, DOF_RANGES, LEGACY_FOV_CONTROLS, frame_commands,
                              motion_clock_info, numeric, parse_camera, parse_camera_readback,
                              _tail_file, tick_rate_advice, tick_rates_match)
from .export_timing import ExportTimingMixin
from .layer_modes import LayerModesMixin
from .game_ui_handoff import GameUiHandoffMixin, OWN_HEALTH_HUD
from .paused_flight import PausedFlightMixin, CAMERA_READBACK_INTERVAL
from .console import ConsoleClient, ConsoleError, ConsoleTimeout, error_text, parse_demo_info, parse_demo_tick
from .replays import same_replay_name
from .demo_packets import packet_index, replay_header
from .playback import ReplayClock
from .smoothing import PhaseSmoother, smoothing_window
from .display import client_aspect_ratio
from .pacing import FrameWait
from .runtime import application_root
from . import launcher
from .native_effects import compile_effects
from .preload import PreloadMonitor
from .replay_camera import ReplayCameraMonitor

ROOT = application_root(Path(__file__).resolve().parents[1])
LOG = logging.getLogger("dolly")
# The engine's Native DOF material (materials/dev/dof1_general.vmat) is built
# with DynamicShaderCompile 1. A session that disables dynamic shader
# compilation makes the engine substitute its error material for the
# full-screen DOF pass (the reported magenta/black checkerboard), so Dolly
# enables it for the session and forces one reload of the DOF shaders.
DOF_SHADER_SETTINGS = ("mat_disable_dynamic_shader_compile",)
DOF_SHADER_RELOAD = "mat_forcereloadshaders dof"
# Game-bin DLLs the engine needs to compile the Native DOF material at runtime.
# ``vfx_dx11.dll`` is the DX11 dynamic shader compiler and it statically imports
# ``slang.dll``; when either is absent the engine cannot compile the developer
# DOF material and substitutes its error material (the magenta/black
# checkerboard). Testing for them lets Dolly refuse the pass without forcing a
# shader reload that would itself swap a working precompiled shader for the
# error material.
DOF_COMPILER_FILES = ("vfx_dx11.dll", "slang.dll")
# Engine text observed when the game install cannot compile the Native DOF
# material because its vfx shader compiler files are missing: the DOF pass
# falls back to the engine error material, the magenta/black checkerboard. The
# markers are matched against a separator-normalized copy of the reload output,
# so the exact punctuation ("vfx_dx", "InitDynamicShaderCompileDLL") is
# irrelevant.
DOF_SHADER_COMPILE_ERRORS = ("dynamic shader compile unavailable", "can't load vfx",
                             "initdynamicshadercompiledll")
# Raised when Native DOF cannot be rendered safely. Without the vfx compiler the
# engine substitutes its error material for the full-screen pass (the magenta/
# black checkerboard) and the corrupt pass has crashed Deadlock, so Dolly refuses
# the override instead of warning and continuing.
NATIVE_DOF_UNAVAILABLE = (
    "Native Depth of Field cannot run on this game install: its vfx shader compiler "
    "is missing, so the engine shows the magenta/black checkerboard and can crash "
    "Deadlock. Dolly left Native DOF off. Repair the game files (Steam > Deadlock > "
    "Properties > Installed Files > Verify integrity of game files) to re-enable it, "
    "or use Citadel Depth of Field instead.")


def _dof_shader_compile_failed(text):
    """True when the engine's shader compiler could not load, ignoring punctuation."""
    compact = re.sub(r"[^a-z0-9]+", "", str(text or "").casefold())
    return any(re.sub(r"[^a-z0-9]+", "", marker) in compact
               for marker in DOF_SHADER_COMPILE_ERRORS)


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
CAMERA_DRIFT_LIMIT = 64.0
CAMERA_DRIFT_SAMPLES = 3
NATIVE_PAUSE_TIMEOUT = 8.0
# Automatic startup normally advances Deadlock's hideout intro, and finishing
# that intro starts the verified map/shader preload. A session that never runs
# the intro (coherent preload manager, unstarted, intro phase 0) can never pass
# the preload gate, so waiting the full timeout only delays the manual path.
# After this window the controller stops early and offers that manual load.
PRELOAD_INTRO_STALL_SECONDS = 45.0
# Live-verified switches for the in-game health-bar toggle. Disabling
# citadel_unit_status_enabled or citadel_hud_objective_health_enabled while a
# replay renders hangs the game with a DX11 device error, so those master
# switches are never written. Prior values are snapshotted before hiding and
# restored exactly on re-enable; bar glow stays owned by toggle_citadel_glow.
HEALTHBAR_SWITCH_CVARS = ("citadel_healthbars_enabled", "citadel_unit_status_use_new")
HEALTHBAR_SWITCH_ON = {"citadel_healthbars_enabled": 1.0, "citadel_unit_status_use_new": 1.0}
HEALTHBAR_SCALE_CVARS = ("citadel_unit_status_min_distance_scale", "citadel_unit_status_max_distance_scale")


class CameraPositionError(RuntimeError):
    """A measured position response failed, eligible for one paused refresh."""


class PreloadUnavailableError(RuntimeError):
    """Deadlock never started its hideout intro/preload.

    Verified automatic startup fails closed in that state; the game session
    stays open so the replay can be loaded through the explicit manual path.
    """


class Controller(LayerModesMixin, ExportTimingMixin, PausedFlightMixin, GameUiHandoffMixin):
    def __init__(self, log_callback=None):
        self._log_callback = log_callback
        self._session = None
        self._native_active = False
        self._native_manual = False
        self._seek_relief = True
        self._game_ui_visible = False
        self._game_ui_restore = {}
        self._game_hero_restore = None
        self._replay_hud_restore = {}
        self._replay_hud_session = None
        self._show_replay_hud = False
        self._follow_transaction = None
        self._follow_monitor = None
        self._follow_session = None
        self._follow_demo = None
        self._follow_active = False
        self._follow_mode_original = None
        self._follow_mode_session = None
        self._follow_settings = None
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
        self._dof_shader_restore = {}
        self._dof_shader_checked = False
        self._native_dof_unavailable = None
        self._layer_hidden = None
        self._healthbar_restore = {}
        self._healthbars_hidden = False
        self._look_restore = {}
        self._glow_disabled = False
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
        self._probe_failure = None
        self._last_output = {}
        self._launch_attempt = None
        self._unlocker_pid = None
        self._startup_evidence = {}
        self._replay_requested = False
        self._replay_recovery_active = False
        self._replay_recovery = {}
        self._replay_header = {}
        self._recording_replay = None
        self._recording_pending = False
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
        state["health_panel_restore_pending"] = self._game_ui_restore.get(OWN_HEALTH_HUD) == 0
        state["native_dof_unavailable"] = bool(self._native_dof_unavailable)
        state["game_follow_active"] = self._follow_active
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

    def deployment_directory(self):
        """Directory that receives native capture marker files, or None.

        The player layer writes its capture marker beside the deployed native
        DLL; the launcher owns that folder and removes it with the session.
        """
        overlay = getattr(self._session, "overlay_dir", None) if self._session else None
        if overlay is None:
            return None
        return Path(overlay) / "cvar_unlocker" / "bin" / "win64"

    def _require_connection(self):
        if not self._alive():
            raise RuntimeError("Launch the local replay from Dolly first. Connections to separately launched games are disabled.")
        if not self._console or not self._console.is_connected:
            raise RuntimeError("The console is disconnected. Use Connect after Deadlock finishes loading.")

    def _game_death_message(self, context):
        """Reason and likely cause for a game process that is no longer alive.

        The console socket is closed by the operating system when Deadlock
        crashes, so a raw WinError 10054 hides the real event. A replay that
        cannot be reconstructed by the installed build fatals the game during
        demo load; report the crash and the likely cause instead. The recorded
        map/build from the replay header is included when it was read.
        """
        exit_code = self._session.process.poll() if self._session else None
        if exit_code is None:
            reason = "Deadlock stopped responding"
        elif exit_code == 0:
            reason = "Deadlock closed"
        else:
            reason = f"Deadlock crashed (exit code {exit_code}, 0x{exit_code & 0xFFFFFFFF:08X})"
        if context == "waiting for the game console" and not self._replay_requested:
            return (f"{reason} while {context}. The game exited before the selected replay "
                    "was loaded. Inspect the startup error or game dump before retrying.")
        message = (f"{reason} while {context}. A replay that this game build cannot "
                   "reconstruct (for example an incompatible or incomplete .dem) can fatal Deadlock as soon as "
                   "it starts playing. Try the latest replay recorded on this build, then launch again through Dolly.")
        header = self._replay_header
        details = ", ".join(str(part) for part in (
            header.get("name"),
            f"recorded on game build {header['build_num']}" if header.get("build_num") is not None else None,
            f"map {header['map_name']}" if header.get("map_name") else None) if part)
        if details:
            message += f" Selected replay: {details}."
        return message

    def _game_exit_message(self, command, detail):
        cleaned = str(command).strip().splitlines()[0][:80] if str(command).strip() else "a console command"
        return self._game_death_message(f"Dolly was running '{cleaned}'") + f" Console reported: {detail}"

    def _request(self, command, timeout=3.0, allow_error=False, completion_patterns=None,
                 allow_truncated=False):
        self._require_connection()
        extra = {"allow_truncated": True} if allow_truncated else {}
        try:
            if completion_patterns is None:
                output = self._console.request(command, timeout=timeout, **extra)
            else:
                output = self._console.request(command, timeout=timeout,
                                               completion_patterns=completion_patterns,
                                               **extra)
        except ConsoleError as exc:
            if not self._alive():
                raise RuntimeError(self._game_exit_message(command, exc)) from exc
            raise
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

    def _require_demo(self, require_tick=True, *, timeout=3):
        # No arguments: demo_goto reports current playback position; it does
        # not seek. demo_info in the user's game prints file metadata instead.
        output = self._request("demo_goto", allow_error=True, timeout=timeout)
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
        if not self._demo or not same_replay_name(self._demo, result.get("name")):
            raise RuntimeError("The open replay is different from the local .dem chosen for this launch. Restart through Dolly with the intended file.")
        with self._state_lock:
            self._state["tick"] = int(result["tick"]) if result.get("tick") is not None else None
        if require_tick and result.get("tick") is None:
            raise RuntimeError("The replay is recognized, but its current tick was not reported. Export diagnostics before normal playback, or explicitly select Frozen preview.")
        return result

    def replay_tick_rate(self):
        """Ticks per second of the loaded replay, or None when unknown.

        The replay file's own CDemoFileInfo (playback_ticks / playback_time)
        is authoritative: matchmaking replays are recorded at 32 ticks/second
        as well as 64, so the project's ticks/second must adopt this value or
        every replay-timed camera arrives at the wrong replay moment. Engine
        status metadata is a fallback when the file index is unavailable; an
        unknown rate is never guessed.
        """
        if self._demo:
            index = packet_index(self._demo)
            if index is not None and index.tick_rate:
                return float(index.tick_rate)
        demo = self._probe_result.get("demo") if isinstance(self._probe_result, dict) else None
        if isinstance(demo, dict):
            rate = demo.get("tick_rate")
            if (isinstance(rate, (int, float)) and not isinstance(rate, bool)
                    and math.isfinite(rate) and rate > 0):
                return float(rate)
        return None

    def _replay_tick_rate_warning(self, project):
        """Text for a project/replay tick-rate mismatch, or None to stay quiet."""
        detected = self.replay_tick_rate()
        if detected is None or tick_rates_match(detected, project.tick_rate):
            return None
        LOG.warning("Replay tick rate %.3f does not match the shot's %.3f", detected, project.tick_rate)
        return (f"This replay runs at {detected:.3g} ticks/second but the shot uses "
                f"{project.tick_rate:g}; replay-timed camera arrival times will not line up. "
                "Retime the shot to this replay in Shot settings.")

    def _replay_begun(self, minimum_tick=2):
        """Require the replay to apply its first full packet before pausing.

        A freshly loaded replay exposes tick 0 while it is still reconstructing
        its initial full update. Seeking back across that boundary in that state
        is what fataled Deadlock on replays this build could not reconstruct.
        Real playback has started once the demo tick advances past the first
        packet. Returns the demo status, or None while it is still starting.
        """
        try:
            info = self._require_demo()
        except (RuntimeError, ValueError):
            if not self._alive():
                raise
            return None
        tick = info.get("tick")
        if tick is None or int(tick) < int(minimum_tick):
            return None
        return info

    def launch(self, game_path, demo_path, protocol="netcon", *, native=False, launch_options="", graphics_profile=""):
        with self._op_lock:
            self._launch_attempt = {
                "game_path": str(game_path),
                "demo_path": str(demo_path or ""),
                "protocol": str(protocol),
                "status": "requested",
            }
            if launch_options:
                self._launch_attempt["launch_options"] = str(launch_options)
            if graphics_profile:
                self._launch_attempt["graphics_profile"] = str(graphics_profile)
            if native:
                self._launch_attempt["camera_backend"] = "native"
            LOG.info("Launch requested: %s", self._launch_attempt)
            try:
                result = self._launch(game_path, demo_path, protocol, native=native, launch_options=launch_options,
                                      graphics_profile=graphics_profile)
            except Exception as exc:
                self._launch_attempt.update(status="failed", error=str(exc), error_type=type(exc).__name__)
                LOG.exception("Launch failed")
                self._message("Launch failed: " + str(exc), startup_stage="failed")
                raise
            self._launch_attempt.update(status="started", pid=result["pid"], session_dir=result["session_dir"])
            return result

    def _launch(self, game_path, demo_path, protocol, *, native=False, launch_options="", graphics_profile=""):
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
            if graphics_profile:
                options["graphics_profile"] = graphics_profile
            self._recording_replay = None
            self._session = launcher.launch(game_path, str(path), port=self._port, protocol=protocol, **options)
            self._native_active = False
            self._native_manual = False
            self._game_ui_visible = False
            self._game_ui_restore.clear()
            self._game_hero_restore = None
            self._healthbar_restore.clear()
            self._healthbars_hidden = False
            self._look_restore.clear()
            self._console_open = None
            self._native_last_status = {}
            self._native_handoff_details = {}
            self._demo = path
            self._protocol = protocol
            self._invalidate_probe()
            self._restore.clear()
            self._playback_restore.clear()
            self._dof_shader_restore.clear()
            self._dof_shader_checked = False
            self._native_dof_unavailable = None
            self._playback_details = None
            self._playback_metrics = {}
            self._applied_pose = None
            self._aspect_capture = {}
            self._demo_speed_changed = False
            self._last_output.clear()
            self._unlocker_pid = None
            self._startup_evidence = {}
            self._replay_requested = False
            self._replay_header = {}
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
            bridge = self._native_bridge()
            if bridge is not None:
                try:
                    # F8/F9 must work before the first camera exists so the
                    # replay can be scrubbed and a hero selected from the
                    # start. "disabled" keeps ordinary controls with the game.
                    bridge.configure_editor(enabled=True, owner="disabled")
                except (RuntimeError, ValueError, OSError):
                    LOG.warning("Could not enable in-game editor input before camera setup")
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
                exit_code = self._session.process.poll() if self._session else None
                if exit_code not in (None, 0):
                    raise RuntimeError(self._game_death_message(label) + " Export diagnostics to inspect startup.")
                raise RuntimeError("Deadlock closed while " + label + ". Export diagnostics to inspect startup.")
            result = check()
            self._check_startup_cancelled(cancel_event)
            if result is not None and result is not False:
                return result
            if time.perf_counter() >= deadline:
                raise RuntimeError("Timed out " + label + ". The replay was not marked ready. Use the manual startup controls or export diagnostics.")
            waiter.wait(.25)

    def _console_command_available(self, name, timeout=2.0):
        """Tri-state availability of a console name: True, False, or None.

        None means the console could not confirm the name (a -dev data dump
        delays the echo, or the log overflowed). That is not evidence the name
        is missing, so startup-critical callers must treat None as usable and
        let the command's own completion/ack decide, instead of aborting.
        """
        try:
            return self._console.supports(name, timeout)
        except TypeError:
            # A legacy/stub console without the timeout parameter; treat an
            # explicit rejection as False and any other result as confirmed.
            return self._console.supports(name)

    def _automatic_hideout_check(self, initial_frame):
        """Require a rendered pre-replay scene and loaded command registration.

        Native view callbacks can start while hideout prerequisites are still
        loading. Require settled engine status as well as rendered frames;
        neither an open socket nor an early view callback proves readiness.
        """
        output = self._request("demo_info", allow_error=True, allow_truncated=True)
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
        status = self._request("status", allow_error=True, allow_truncated=True)
        self._startup_evidence["console_status"] = str(status or "")[:4000]
        settled = self._console_hideout_evidence(status, demo)
        if settled is None:
            return None
        if bridge is None:
            evidence = settled
        else:
            evidence["settled_status"] = settled
        available = self._console_command_available("cvar_unhide")
        if available is False:
            return None
        # None means the flood hid the confirmation; the unlocker step still
        # verifies completion patterns before the replay is allowed to load.
        evidence["unlocker_command_registered"] = available is True
        return evidence

    @staticmethod
    def _console_hideout_evidence(status, demo):
        """Prove the console-only pre-replay scene before loading the replay.

        A named hideout map is the strongest signal. Many builds do not expose
        the map name through ``status``; any non-empty status that is not mid
        level-load is the same settled pre-replay state the rendered-view path
        waits for. A replay already playing is rejected earlier, so this stays
        bounded to the pre-replay scene.
        """
        text = str(status or "")
        folded = text.casefold()
        # Still loading a level: the engine queues the map and reports startup
        # prerequisites. Any of these means the hideout is not ready yet.
        loading = ("levelload", "prerequisite", "waiting for isserverrunning",
                   "waiting for startup resource", "waiting for first spawn")
        if any(marker in folded for marker in loading):
            return None
        if not folded.strip():
            return None
        maps = re.findall(r"(?im)^\s*(?:\[[^]\r\n]+\]\s*)*(?:map|mapname)\s*[:=]\s*[\"']?([^\s\"']+)", text)
        hideouts = [name for name in maps if "hideout" in re.split(r"[/\\_.-]", name.casefold())]
        if hideouts:
            return {"method": "named_hideout_status", "map": hideouts[0], "demo": demo}
        return {"method": "settled_status", "demo": demo}

    def _wait_dashboard_preload(self, initial_frame, cancel_event):
        """Verify a started preload, not just an idle counter or elapsed time.

        If Deadlock never shows its hideout intro and never starts the preload,
        automatic startup stops after ``PRELOAD_INTRO_STALL_SECONDS`` instead of
        waiting for a state that cannot change; the game stays open and the
        GUI offers the explicit manual replay load.
        """
        monitor = PreloadMonitor(self._session)
        restore_hud = None
        try:
            self._startup_evidence["preload_identity"] = monitor.identity
            self.toggle_console(False)
            # The intro phase is cached by a HUD panel. With the HUD hidden its
            # update never advances, even while the hideout continues rendering.
            # Show it only for startup; restore the user's value before loading.
            hud = read_cvar_value("citadel_hud_visible", self._request("citadel_hud_visible"))
            self._startup_evidence["preload_hud"] = {
                "original": hud, "temporarily_enabled": hud == 0, "restored": hud != 0}
            if hud == 0:
                restore_hud = hud
                self._playback_restore["citadel_hud_visible"] = hud
                self._request("citadel_hud_visible 1")
            consecutive = 0
            last_message = None
            observed_at = time.monotonic()
            trace = {"first": None, "changes": [], "dropped_changes": 0,
                     "observed_started": False, "observed_ready": False,
                     "observed_intro": False}
            self._startup_evidence["preload_trace"] = trace
            previous_sample = None

            def observe():
                nonlocal previous_sample
                sample = monitor.sample()
                self._startup_evidence["preload"] = sample
                trace["observed_started"] |= bool(sample.get("coherent") and sample.get("started"))
                trace["observed_ready"] |= bool(sample.get("coherent") and sample.get("ready"))
                trace["observed_intro"] |= bool(sample.get("coherent")
                                                and sample.get("intro_phase") in (1, 2, 3))
                if sample != previous_sample:
                    row = {"elapsed_seconds": round(time.monotonic() - observed_at, 3),
                           "sample": deepcopy(sample)}
                    if trace["first"] is None:
                        trace["first"] = deepcopy(row)
                    trace["changes"].append(row)
                    if len(trace["changes"]) > 64:
                        del trace["changes"][0]
                        trace["dropped_changes"] += 1
                    previous_sample = deepcopy(sample)
                return sample

            def ready():
                nonlocal consecutive, last_message
                sample = observe()
                if not sample.get("coherent"):
                    consecutive = 0
                    return None
                if not trace["observed_started"] and not trace["observed_intro"]:
                    elapsed = time.monotonic() - observed_at
                    if elapsed >= PRELOAD_INTRO_STALL_SECONDS:
                        message = ("Deadlock never showed its hideout intro or started the map and "
                                   "shader preload, so Dolly stopped the automatic check. The game "
                                   "session is still open: load the selected replay from Startup "
                                   "controls to continue without preload verification.")
                        self._startup_evidence["preload_unavailable"] = {
                            "reason": "intro_never_started",
                            "waited_seconds": round(elapsed, 3),
                            "last_sample": deepcopy(sample)}
                        self._message(message, startup_stage="preload_unavailable")
                        raise PreloadUnavailableError(message)
                if not sample["started"]:
                    message = ("waiting_preload_intro", "Preparing Deadlock's intro and map preload. "
                               "Your replay will open automatically when ready.")
                    if sample.get("intro_phase") == 2 and "intro_action" not in self._startup_evidence:
                        if self._automatic_hideout_check(initial_frame):
                            self._check_startup_cancelled(cancel_event)
                            if monitor.advance_intro():
                                self._startup_evidence["intro_action"] = "queued_owned_window_escape"
                else:
                    message = ("preloading", "Preloading map and shaders: "
                               f'{sample["completed"]} of {sample["total"]} in the current batch. '
                               "Your replay will open automatically when ready.")
                if message != last_message:
                    self._message(message[1], startup_stage=message[0])
                    last_message = message
                consecutive = consecutive + 1 if sample["ready"] else 0
                if consecutive < 3:
                    return None
                # A completed background job does not replace hideout/demo guards.
                settled = self._automatic_hideout_check(initial_frame)
                if not settled:
                    consecutive = 0
                    return None
                final = observe()
                if not final.get("coherent") or not final.get("ready"):
                    consecutive = 0
                    return None
                self._startup_evidence["preload"] = final
                self._startup_evidence["post_preload_hideout"] = settled
                return final

            self._startup_wait(ready, "waiting for verified map and shader preload completion", cancel_event)
        finally:
            try:
                if restore_hud is not None:
                    self._request("citadel_hud_visible " + numeric(restore_hud))
                    self._playback_restore.pop("citadel_hud_visible", None)
                    self._startup_evidence["preload_hud"]["restored"] = True
            finally:
                monitor.close()
        self._message("Map and shader preload complete. Opening your selected replay…",
                      startup_stage="preload_ready")

    def _wait_replay_camera(self, bridge, cancel_event):
        """Wait for the stock camera and HUD after the first full replay packet."""
        monitor = ReplayCameraMonitor(self._session)
        previous_ready = None
        try:
            self._startup_evidence['replay_camera_identity'] = monitor.identity
            self._message("Waiting for the replay intro and HUD to finish…",
                          startup_stage="waiting_replay_camera")

            def ready():
                nonlocal previous_ready
                info = self._require_demo()
                native = bridge.status() if bridge is not None else None
                if native is not None:
                    self._require_native_demo(native, allow_idle=True)
                sample = monitor.sample_startup()
                self._startup_evidence['replay_camera'] = dict(sample, tick=info.get('tick'))
                progress = int(native['frame_count']) if native is not None else int(info['tick'])
                if sample.get('coherent') and sample.get('ready') and int(info.get('tick') or 0) >= 2:
                    if previous_ready is not None and progress > previous_ready:
                        return sample
                    previous_ready = progress
                else:
                    previous_ready = None
                return None

            self._startup_wait(ready, "waiting for the replay intro and HUD to finish",
                               cancel_event, timeout=90)
        finally:
            monitor.close()

    def start_editing(self, game_path, demo_path, protocol="netcon", native=True,
                      launch_options="", cancel_event=None, graphics_profile=""):
        """Launch, initialize once before the demo, and enter paused editing.

        Every transition uses process, console or rendered-view evidence.
        This runs on the caller's worker; cancellation does not kill the game.
        The existing manual launch/initialize/load/probe actions remain usable.
        """
        with self._op_lock:
            try:
                self._check_startup_cancelled(cancel_event)
                profile_options = {"graphics_profile": graphics_profile} if graphics_profile else {}
                self.launch(game_path, demo_path, protocol, native=native, launch_options=launch_options,
                            **profile_options)
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
                # Honour the requested camera driver. The native bridge only
                # drives startup when Native was selected; otherwise the console
                # path must run even if a native build is present.
                bridge = self._native_bridge() if native else None
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
                self._wait_dashboard_preload(initial_frame, cancel_event)
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
                        if self._demo is None or not same_replay_name(self._demo, current.get("demo_name")):
                            return None
                        if int(current.get("frame_count", 0)) <= initial_frame:
                            return None
                    return info

                self._startup_wait(selected_replay, "waiting for the selected replay", cancel_event)
                # Both backends can render views at tick 0 while the first full
                # entity update is still being reconstructed. A native view is
                # camera readiness, not replay simulation readiness. Let that
                # initial update advance before handing users a paused editor.
                self._message("Replay detected. Waiting for it to begin playing before pausing…",
                              startup_stage="loading_replay")
                self._startup_wait(self._replay_begun, "waiting for the replay to begin playing",
                                   cancel_event, timeout=45)
                self._wait_replay_camera(bridge, cancel_event)
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
                if bridge is not None:
                    self._record_native_renderer()
                return self.status()
            except Exception as exc:
                if not self._probe_result:
                    self._probe_failure = str(exc)
                self._message(str(exc), startup_stage="cancelled" if cancel_event is not None and cancel_event.is_set() else "failed")
                raise

    def _record_native_renderer(self):
        """One read-only confirmation that a native session really runs DX11.

        The launcher requests ``-dx11`` explicitly, and the native panel and
        recorder depend on DX11 Present. Record what the engine reports and warn
        once when an explicit different renderer won. Unknown or rejected
        answers are recorded but never treated as a renderer failure, and this
        diagnostic must never be able to fail the startup itself.
        """
        try:
            used = str(self._request("engine_rendersystem_used", allow_error=True) or "").strip()
            initialized = str(self._request("engine_rendersystem_init", allow_error=True) or "").strip()
        except Exception as exc:  # noqa: BLE001 - best-effort diagnostics only
            self._startup_evidence["renderer"] = {"error": str(exc)}
            return None
        self._startup_evidence["renderer"] = {"used": used, "init": initialized}
        combined = (used + " " + initialized).casefold()
        if "dx11" in combined:
            return None
        if not any(token in combined for token in ("vulkan", "d3d12", "dx12", "opengl", "null")):
            return None
        warning = ("This session is not rendering with DirectX 11 ("
                   + (used or initialized or "no renderer answer")
                   + "). The native camera and recorder need DX11; close the game, keep Dolly's graphics "
                     "settings unchanged, and launch again.")
        LOG.warning("Native renderer check found a different renderer: %s", warning)
        self._message(warning)
        return warning

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

    def _remember_replay_header(self, path):
        """Record the selected replay's recorded map/build for support context."""
        header = replay_header(path)
        if header:
            self._replay_header = header
            self._startup_evidence["replay_header"] = header
            LOG.info("Replay header: %s", header)
        return header

    def load_replay(self):
        with self._op_lock:
            self._require_unlocker()
            self._recording_replay = None
            self.stop()
            path = launcher._validate_demo(self._demo)
            if path is None:
                raise RuntimeError("No replay was selected for this launch.")
            self._remember_replay_header(path)
            # Source commands use forward slashes; _validate_demo rejects quotes,
            # separators and line breaks before this trusted quoted argument.
            command = 'playdemo "' + path.as_posix() + '"'
            self._invalidate_probe()
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

    def cancel_replay_recovery(self):
        """Signal the existing worker; never launch, reconnect or queue a retry."""
        if not self._replay_recovery_active:
            return False
        self._stop_event.set()
        return True

    def mark_recording_pending(self, pending, *, player_layer=False):
        self._player_capture_pending = bool(pending and player_layer)
        was_pending = self._recording_pending
        self._recording_pending = bool(pending)
        if was_pending and not pending:
            self._recording_replay = None

    def _recorder_active(self):
        if self._recording_pending:
            return True
        bridge = self._native_bridge()
        read = getattr(bridge, "video_status", None)
        if not callable(read):
            return False
        status = read()
        if not isinstance(status, dict):
            raise RuntimeError("Recorder state is unavailable; replay reload was not attempted.")
        return status.get("state") in ("starting", "recording", "finalizing")

    def _recover_replay_for_shot(self):
        """One in-process reset, only on a user's playback/recording request.

        Confirm an inactive replay before the single playdemo command. Old ticks
        from the same file cannot satisfy readiness. A failed attempt invalidates
        probing and never launches a process or retries the load command.
        """
        self._require_unlocker()
        self._require_probe()
        self._require_demo()
        path = launcher._validate_demo(self._demo)
        if path is None:
            raise RuntimeError("No replay was selected for this launch.")
        self._remember_replay_header(path)
        self._remember_spectator_hero()
        if self._recorder_active():
            raise RuntimeError("Finish recording before reloading the replay.")
        # A held own-health panel only needs to stay hidden; it must not block an
        # export/recovery reload (mirrors play()). Other pending restorations, and
        # the replay-HUD override, still block recovery as before.
        if (self._playback_restore or self._restore or
                (self._game_ui_restore and not self._health_panel_held(require_full_hud=False)) or
                self._replay_hud_restore or self._demo_speed_changed):
            raise RuntimeError("Use Stop / restore to restore pending settings before replay recovery.")
        if self._console_command_available("disconnect") is False:
            raise RuntimeError("This game does not expose replay disconnect; recovery was not attempted.")
        previous_probe = deepcopy(self._probe_result)
        session = self._session
        self._recording_replay = None
        self._replay_recovery = {"pid": session.pid, "replay": str(path), "stage": "disconnecting"}
        self._replay_recovery_active = True
        self._invalidate_probe()
        try:
            self._check_position_cancelled()
            self._message("Preparing replay for the shot…", startup_stage="recovering_replay")
            self._request("disconnect", timeout=10)
            def inactive():
                try:
                    info = parse_demo_info(self._request("demo_info", allow_error=True))
                except ConsoleTimeout:
                    return None
                return info if not info["playing"] else None
            stopped = self._startup_wait(inactive, "waiting for the previous replay to stop",
                                         self._stop_event, timeout=15)
            self._replay_recovery.update(stage="loading", inactive=stopped)
            self._check_position_cancelled()
            if self._session is not session or not self._alive():
                raise RuntimeError("The game session ended during replay preparation.")
            command = 'playdemo "' + path.as_posix() + '"'
            self._console.send(command)  # Queue once; never resend on timeout.
            self._replay_requested = True
            self._last_output["playdemo"] = "Sent: " + command
            def begun():
                try:
                    output = self._request("demo_goto", allow_error=True)
                    try:
                        current = parse_demo_tick(output)
                    except ValueError:
                        output = self._request("demo_info", allow_error=True)
                        current = parse_demo_info(output)
                    if not current["playing"]:
                        return None
                    info = self._resolve_demo(output)
                    return info if info["tick"] >= 2 else None
                except ConsoleTimeout:
                    return None
            ready = self._startup_wait(begun, "waiting for the fresh replay's initial update",
                                       self._stop_event, timeout=45)
            self._check_position_cancelled()
            # A fresh playdemo restarts the scripted opening even inside an
            # existing process. Apply the same camera handoff guard as initial
            # startup before pausing or seeking to the authored shot.
            self._wait_replay_camera(self._native_bridge(), self._stop_event)
            self._replay_recovery["camera_handoff"] = deepcopy(
                self._startup_evidence.get("replay_camera", {}))
            before = self._native_bridge().status()
            self._require_native_demo(before, allow_idle=True)
            self._request("demo_pause")
            paused = self._wait_paused_native_view(self._native_bridge(), before["frame_count"])
            self._check_position_cancelled()
            checked = self._require_demo()
            if self._session is not session or checked["tick"] != paused["tick"]:
                raise RuntimeError("Replay changed while confirming the recovered paused view.")
            self._probe_result = dict(previous_probe, demo=checked, native_camera=paused)
            self._replay_recovery.update(stage="ready", initial_update=ready, paused_tick=checked["tick"])
            if self._healthbars_hidden:
                self._write_look_values({name: 0 for name in self._healthbar_restore})
            if self._glow_disabled:
                # Reloading the replay restores the game's default glow. A take
                # must keep the user's choice, so re-assert it before playback.
                try:
                    self._apply_citadel_glow()
                except Exception:
                    LOG.exception("Could not re-apply the Citadel glow choice after recovery")
            self._message("Replay prepared. Starting the shot…", startup_stage="replay_ready")
            return checked
        except Exception as exc:
            self._invalidate_probe(str(exc))
            self._replay_recovery.update(stage="failed", error=str(exc))
            message = "Replay preparation stopped: " + str(exc) + " Dolly will not restart the game or retry automatically."
            self._message(message, startup_stage="failed", playing=False)
            raise RuntimeError(message) from exc
        finally:
            self._replay_recovery_active = False

    def open_pov_panel(self):
        """Show Dolly controls without replacing the selected spectator view."""
        with self._op_lock:
            self._require_demo(require_tick=False)
            if self._console_open:
                self.toggle_console(enabled=False)
            self._native_bridge().configure_editor(owner="panel")

    def _follow_context(self):
        """Keep restoration tied to the original replay, independent of camera type."""
        if self._session is not self._follow_session:
            raise RuntimeError("Game Follow restoration belongs to a different replay session.")
        restoring = self._follow_transaction is not None and self._follow_transaction.restoring
        if restoring:
            self._require_connection()
        else:
            if self._demo != self._follow_demo:
                raise RuntimeError("Game Follow preview belongs to a different replay.")
            self._require_demo(require_tick=False)
        return self._follow_monitor.sample()

    def start_selected_game_follow(self, settings, player):
        """Select a fresh roster pawn through the stock command, then verify it."""
        from .follow_target import FollowTargetMonitor
        from .replay_camera import ReplayCameraMonitor
        from .follow_camera import FollowSettings
        with self._op_lock:
            if not isinstance(settings, FollowSettings):
                raise ValueError('Choose valid Game Follow settings.')
            settings.values()
            self._require_quiet_session('selecting a Game Follow hero')
            self._require_demo(require_tick=False)
            bridge = self._native_bridge()
            require_capability = getattr(bridge, 'require_capability', None)
            if callable(require_capability):
                require_capability('follow')
            if self._recorder_active() or not bridge.status().get('paused'):
                raise RuntimeError('Pause the replay before selecting a Follow hero.')
            def current_player():
                roster = bridge.editor_roster()
                matches = [row for row in (roster or {}).get('players', [])
                           if all(row.get(key) == player.get(key)
                                  for key in ('handle', 'entity_index', 'model', 'model_path'))]
                if (len(matches) != 1 or not player.get('model_path')
                        or type(player.get('handle')) is not int
                        or type(player.get('entity_index')) is not int
                        or player['handle'] in (0xffffffff, 0xfffffffe)
                        or not 0 < player['entity_index'] < 0x7fff
                        or player['handle'] & 0x7fff != player['entity_index']):
                    raise RuntimeError('The selected Follow hero is no longer in the current roster.')
            current_player()
            monitor = FollowTargetMonitor(self._session)
            try:
                monitor.sample()
                monitor.sample_selection_context()
                self.toggle_game_ui(True)  # Restores any old rig before releasing the pose writer.
                # Programmatic hero selection must not flash the replay UI:
                # release the pose writer, then hide the game HUD/cursor while
                # keeping the stock camera context that Follow needs.
                self._hide_game_ui()
                current_player()
                self._suppress_own_health_hud()
                self._request('spec_target ' + str(player['entity_index']))
                original_mode = read_cvar_value('citadel_spectator_mode', self._request('citadel_spectator_mode'))
                if original_mode not in (0, 1, 2, 3):
                    raise RuntimeError('The game spectator camera mode is unrecognized.')
                self._follow_mode_original = original_mode
                self._follow_mode_session = self._session
                self._request('citadel_spectator_mode 2')
                self._verify_game_ui_values({'citadel_spectator_mode': 2})
                deadline = time.monotonic() + 5
                while True:
                    self._require_demo(require_tick=False)
                    current_player()
                    selected = monitor.sample_target(require_chase=False)
                    mode = read_cvar_value('citadel_spectator_mode', self._request('citadel_spectator_mode'))
                    camera = ReplayCameraMonitor.sample(monitor)
                    self._follow_selection_evidence = dict(selected=selected, console_mode=mode,
                                                          camera=camera, expected_handle=player['handle'])
                    if (selected['handle'] == player['handle'] and mode in (2, 3) and mode == selected['mode']
                            and camera.get('ready')):
                        self._game_hero_restore = (self._session, self._demo, player['model_path'])
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError('The game did not confirm the selected Follow hero camera.')
                    time.sleep(.05)
                self.start_game_follow(settings)
                try:
                    current_player()
                    if monitor.sample_target()['handle'] != player['handle']:
                        raise RuntimeError('The selected hero changed while applying Game Follow.')
                except BaseException:
                    self.stop_game_follow()
                    raise
            except BaseException:
                self.stop_game_follow()
                raise
            finally:
                monitor.close()

    def start_game_follow(self, settings):
        """Preview the stock rig after F9 selects a hero; no native pose writer."""
        from .follow_camera import FollowSettings, FollowTransaction
        from .follow_capabilities import FollowCapabilityMonitor
        from .replay_camera import ReplayCameraMonitor
        with self._op_lock:
            if not isinstance(settings, FollowSettings):
                raise ValueError("Choose valid Game Follow settings.")
            settings.values()
            self._require_quiet_session("previewing Game Follow")
            bridge = self._native_bridge()
            require_capability = getattr(bridge, 'require_capability', None)
            if callable(require_capability):
                require_capability('follow')
            if self._recorder_active() or bridge is None or not bridge.status().get("paused"):
                raise RuntimeError("Pause the selected replay and finish recording before Game Follow.")
            if ((not self._game_ui_visible and not self._follow_active)
                    or self._native_active or self._native_manual):
                raise RuntimeError("Press F9 and select a hero before starting Game Follow.")
            mode = read_cvar_value("citadel_spectator_mode", self._request("citadel_spectator_mode"))
            if mode not in (2, 3):
                raise RuntimeError("Choose Hero Chase or PlayerView in F9 before Game Follow.")
            if self._follow_transaction is not None and not self._follow_active:
                raise RuntimeError("Restore the pending Game Follow settings before starting again.")
            if self._follow_monitor is None:
                monitor = FollowCapabilityMonitor(self._session)
                try:
                    if not ReplayCameraMonitor.sample(monitor).get("ready"):
                        raise RuntimeError("Wait for the selected hero's normal camera before Game Follow.")
                    monitor.sample()
                except BaseException:
                    monitor.close()
                    raise
                self._follow_monitor = monitor
                self._follow_session, self._follow_demo = self._session, self._demo

                def read(name):
                    return self._follow_context()[name]["value"]

                def write(name, value):
                    self._follow_context()
                    self._request(name + " " + numeric(value))

                self._follow_transaction = FollowTransaction(read, write)
            elif not ReplayCameraMonitor.sample(self._follow_monitor).get("ready"):
                raise RuntimeError("Wait for the selected hero's normal camera before updating Game Follow.")
            try:
                self._follow_transaction.apply(settings)
                # The stock rig now owns the camera. Hide the replay HUD/cursor
                # before the editor panel opens so the F8 panel never overlaps
                # the game's replay menu, and F9 toggles from a known state.
                self._hide_game_ui()
                self._game_ui_visible = False
                self.open_pov_panel()
                self._follow_active = True
                self._follow_settings = settings
                self._message("Game Follow preview: hero aim drives the camera; replay remains paused.")
            except BaseException:
                self.stop_game_follow()
                raise

    def stop_game_follow(self):
        """Restore the complete rig before another camera can take ownership."""
        with self._op_lock:
            self._follow_active = False
            transaction = self._follow_transaction
            if transaction is not None:
                transaction.restore()  # Failure retains the monitor and originals for retry.
            if self._follow_mode_original is not None:
                if self._session is not self._follow_mode_session:
                    raise RuntimeError('Game Follow mode restoration belongs to a different session.')
                self._require_connection()
                from .follow_capabilities import FollowCapabilityMonitor
                mode_monitor = FollowCapabilityMonitor(self._session)
                try:
                    mode_monitor.sample()
                    self._suppress_own_health_hud()
                    self._request('citadel_spectator_mode ' + numeric(self._follow_mode_original))
                    self._verify_game_ui_values({'citadel_spectator_mode': self._follow_mode_original})
                finally:
                    mode_monitor.close()
                self._follow_mode_original = self._follow_mode_session = None
            if self._follow_monitor is not None:
                self._follow_monitor.close()
            self._follow_monitor = self._follow_transaction = None
            self._follow_session = self._follow_demo = None

    def prepare_pov_recording(self, project):
        """Prepare a segment clock; never calibrate or write a spectator pose."""
        with self._op_lock:
            project.validate()
            if project.tracks or project.setup_values or any(k.source != "free" for k in project.keyframes):
                raise ValueError("POV export requires a segment without camera/effect overrides.")
            if self._recorder_active():
                raise RuntimeError("Finish the current recording before preparing another.")
            self._require_probe()
            self._require_demo()
            bridge = self._native_bridge()
            if bridge is None:
                raise RuntimeError("POV recording requires the native recorder.")
            if not bridge.status().get("paused"):
                raise RuntimeError("Pause the replay at the POV segment's start before recording.")
            following = bool(self._follow_active)
            if not self._game_ui_visible and not following:
                raise RuntimeError("Choose Player POV, then select a hero with Follow, or press F9 to select a hero.")
            # A roster-selected Game Follow already hands the camera to the
            # game with the HUD hidden; stopping here would tear that rig down
            # and reveal the replay UI before the POV segment records.
            if not following:
                self.stop()
            self._stop_event.clear()
            self._game_ui_visible = True
            self._pov_active = True
            try:
                self._request("demo_pause")
                current = self._require_demo()
                before = bridge.status()["frame_count"]
                positioned = current if current["tick"] == project.start_tick else self._seek(project, 0)
                if positioned["tick"] != project.start_tick:
                    raise RuntimeError("The replay did not reach the POV segment's start tick.")
                self._wait_paused_native_view(bridge, before, expected_tick=project.start_tick)
                self._remember_game_ui_settings()
                self._hide_game_ui()
                self._recording_replay = (self._session, str(self._demo), project.start_tick)
            except Exception:
                self.finish_pov_recording()
                raise

    def play_pov(self, project, speed=1.0):
        with self._op_lock:
            bridge = self._native_bridge()
            if not getattr(self, "_pov_active", False) or bridge is None:
                raise RuntimeError("Prepare a POV recording before starting the segment.")
            try:
                self._prepare_native_shot_replay()
                self._wait_for_recorder_ready()
                self._native_active = True
                self._native_manual = False
                bridge.prepare(project, 0, speed, False, self._demo.name, pov=True)
                bridge.play()
                self._demo_speed_changed = True
                self._request("demo_timescale " + numeric(speed) + "; demo_resume")
                self._playback_details = {"project": project.to_dict(), "camera_source": "player_pov", "speed": speed}
                self._message("Playing POV segment.", playing=True, time=0)
                self._thread = threading.Thread(target=self._run_native,
                    args=(project, 0, speed, 60, False), daemon=True, name="DollyPOVPlayback")
                self._thread.start()
            except Exception:
                self.finish_pov_recording()
                raise

    def finish_pov_recording(self):
        if not getattr(self, "_pov_active", False):
            return
        following = bool(self._follow_active)
        self._halt(native_action="release", preserve_follow=following)
        self._pov_active = False
        self._finish_playback()
        bridge = self._native_bridge()
        if following:
            # A roster-selected Game Follow is the camera the user chose, so
            # finishing a segment must not tear the rig down or reveal the
            # replay UI (and its build-unverifiable health panel). Keep the
            # follow active with the HUD hidden for the next segment.
            self._game_ui_visible = False
            if bridge is not None and self._alive():
                bridge.configure_editor(owner="panel")
            return
        error = self._restore_game_ui_settings()
        self._game_ui_visible = True
        if bridge is not None and self._alive():
            bridge.configure_editor(owner="panel")
        if error:
            raise RuntimeError(error)

    def prepare_native_recording(self, project=None, *, frozen=False):
        """Recover before opening a video file; reserve one paused shot start."""
        with self._op_lock:
            self._recording_replay = None
            if self._recorder_active():
                raise RuntimeError("Finish the current recording before preparing another.")
            self._require_probe()
            self._require_demo()
            bridge = self._native_bridge()
            if bridge is None:
                raise RuntimeError("Recording preparation requires the native camera.")
            if frozen:
                return  # Record the current frozen scene without seeking or reloading it.
            pose = self._native_pose(bridge.status(), "applied_pose" if self._native_active else "original_pose")
            self.stop()
            self._stop_event.clear()
            if project is not None:
                project = Project.from_dict(project.to_dict())
                compile_effects(project)
                pose = project.evaluate(0)
            self._recover_replay_for_shot()
            if project is not None:
                self._seek(project, 0)
            self.enter_native_flight(pose=pose)
            info = self._require_demo()
            self._recording_replay = (self._session, str(self._demo), info["tick"])
            self._message("Replay prepared for recording. Play the shot once, then finish recording.")

    def _wait_for_recorder_ready(self, timeout=10.0):
        """Bounded wait so the prepared take is not played before capture is live.

        The native recorder can still be ``starting`` when the user presses Play.
        Starting the shot first would lose its opening frames, so wait for the
        confirmed recording state instead of consuming the prepared take early.
        """
        if getattr(self, "_player_capture_pending", False):
            # The capture marker was acknowledged before reserving this take.
            return {"state": "recording"}
        bridge = self._native_bridge()
        read = getattr(bridge, "video_status", None)
        if not callable(read):
            raise RuntimeError("Recorder status is unavailable; the shot was not started.")
        deadline = time.perf_counter() + max(0.0, float(timeout))
        while True:
            status = read()
            if not isinstance(status, dict):
                raise RuntimeError("Recorder status is unavailable; the shot was not started.")
            state = str(status.get("state", "idle"))
            if state == "recording":
                return status
            if state == "failed":
                raise RuntimeError(str(status.get("error") or "The recorder failed before the shot began."))
            if state != "starting":
                raise RuntimeError("The recording is no longer starting. Finish or discard it and start a new recording.")
            if self._stop_event.is_set():
                raise RuntimeError("Recording preparation was cancelled before the shot began.")
            if time.perf_counter() >= deadline:
                raise RuntimeError("The recorder did not start in time. Wait for the recording counter before playing the shot.")
            self._stop_event.wait(.05)

    def _prepare_native_shot_replay(self):
        if self._recorder_active():
            prepared, self._recording_replay = self._recording_replay, None
            info = self._require_demo()
            native = self._native_bridge().status()
            if (prepared is None or (prepared[0] is not self._session or prepared[1:] != (str(self._demo), info["tick"]))
                    or not native.get("paused") or native.get("tick") != info["tick"]):
                raise RuntimeError("This recording has no unused prepared shot. Finish recording and start a new recording before playing again.")
            self._wait_for_recorder_ready()
            return
        self._recording_replay = None
        self._recover_replay_for_shot()

    def probe(self):
        with self._op_lock:
            try:
                result = self._probe()
            except Exception as exc:
                # A partial command list or an earlier successful check cannot
                # grant camera access after any part of this check fails.
                self._invalidate_probe(str(exc))
                self._message(str(exc), startup_stage="failed")
                raise
            self._probe_failure = None
            if isinstance(self._session, launcher.Session):
                from .graphics_profiles import audit_runtime
                try:
                    graphics = audit_runtime(self._session.session_dir,
                        lambda command: self._request(command, timeout=3, allow_error=True))
                except (OSError, ValueError, RuntimeError) as exc:
                    graphics = {"warning": "Graphics profile runtime check unavailable: " + str(exc)}
                if graphics is not None:
                    self._startup_evidence["graphics_profile"] = graphics
                    result["graphics_profile"] = graphics
                    if graphics.get("warning"):
                        LOG.warning(graphics["warning"])
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
                    available = self._console_command_available(name)
                    capabilities[name] = available is not False
            self._probe_result = {"version": __version__, "capabilities": capabilities, "demo": info,
                                  "live_tick_available": info.get("tick") is not None,
                                  "camera_effect_verified": False,
                                  "note": "Command availability only. Visual rotation, framing and DOF must be tested in the installed game."}
            self._probe_result["replay_tick_rate"] = self.replay_tick_rate()
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

    def _invalidate_probe(self, reason=None):
        self._probe_result = {}
        self._probe_failure = reason

    def _require_probe(self):
        capabilities = self._probe_result.get("capabilities", {})
        for name in ("spec_goto", "cl_citadel_forceangles", self.lens_cvar):
            if not capabilities.get(name):
                reason = (" Last readiness failure: " + self._probe_failure
                          if self._probe_failure else "")
                raise RuntimeError(
                    "Camera support is not verified for this replay." + reason
                    + " In Dolly's desktop window, open Settings > Troubleshooting & recovery > "
                    "Startup controls... and click Check camera support once the replay has "
                    "finished loading. Then return to the editor.")

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

    def _wait_paused_native_view(self, bridge, after_frame, *, timeout=None, expected_tick=None, tick_tolerance=0):
        """Wait for fresh, settled render telemetry after a pause command."""
        deadline = time.perf_counter() + (NATIVE_PAUSE_TIMEOUT if timeout is None else timeout)
        previous_tick = None
        previous_frame = int(after_frame)
        while True:
            self._require_connection()
            self._check_paused_cancelled()
            if self._replay_recovery_active:
                self._check_position_cancelled()
            current = bridge.status()
            self._require_native_demo(current, allow_idle=True)
            frame = int(current.get("frame_count", 0))
            if frame > previous_frame:
                tick = int(current["tick"])
                at_expected = (expected_tick is None
                               or int(expected_tick) <= tick <= int(expected_tick) + tick_tolerance)
                if current.get("paused") and tick >= 0 and at_expected:
                    if previous_tick == tick:
                        return current
                    previous_tick = tick
                else:
                    previous_tick = None
                previous_frame = frame
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise RuntimeError("The pause command was sent, but Deadlock has not rendered a stable paused view. Wait for the game to respond before editing or capturing a camera.")
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
        return {"pose": list(current[field]), "tick": int(current["tick"]), "paused": True,
                "horizontal_fov": current.get("applied_fov" if self._native_active else "original_fov")}

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
            horizontal_fov = snapshot.get("horizontal_fov")
            if horizontal_fov is not None:
                if (isinstance(horizontal_fov, bool) or not isinstance(horizontal_fov, (float, int))
                        or not math.isfinite(horizontal_fov) or not 1 < horizontal_fov < 179):
                    raise ValueError("Native camera capture has an invalid rendered lens.")
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
            if horizontal_fov is not None:
                frame.lens_scale = math.tan(math.radians(horizontal_fov / 2)) / frame.aspect_ratio
            if self._native_manual:
                if not self.status().get("paused_flight"):
                    # A hold/Stop path released manual movement; a capture
                    # pauses the replay too, so re-arm native movement without
                    # changing its visible seed or taking focus from the open
                    # editor panel.
                    owner = "panel"
                    if callable(getattr(bridge, "editor_status", None)):
                        editor = bridge.editor_status()
                        owner = editor.get("owner", editor.get("input_mode", "panel"))
                        if owner not in ("panel", "flight", "console", "game_ui"):
                            owner = "panel"
                    armed = bridge.start_flight(self._demo.name)
                    self._detach_spectator_view()
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
                else:
                    # A capture from playback free-cam pauses the replay at a
                    # later tick; keep the held tick/pose current for the next
                    # movement or capture without touching the captured key.
                    paused_pose = self._native_pose(current)
                    paused_pose.update(time=float(current.get("phase", 0)), cvars={})
                    self._set_paused_pose(paused_pose, int(current["tick"]))
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
            detected = self.replay_tick_rate()
            if detected is not None and not tick_rates_match(detected, tick_rate):
                # Refuse to write a camera time against a clock this replay
                # does not use; the UI retimes the shot instead.
                raise RuntimeError(tick_rate_advice(detected, tick_rate))
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
        if any(k.lens_scale is not None for k in project.keyframes) and self._native_bridge() is None:
            raise ValueError("This shot contains a captured lens and requires the Native camera backend.")
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
            self._suppress_own_health_hud()
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
            # Preserve originals from an interrupted older playback transaction.
            # Never expose this container during automatic shot completion.
            if OWN_HEALTH_HUD in self._playback_restore:
                self._game_ui_restore.setdefault(OWN_HEALTH_HUD, self._playback_restore.pop(OWN_HEALTH_HUD))
            if not self._playback_restore:
                return None
            values, pending = self._safe_health_hud_values(self._playback_restore)
            if pending:
                # Main HUD visibility also schedules the collapsed ability
                # children. Keep its reveal in the outer camera transaction.
                for name, value in self._playback_restore.items():
                    if values.get(name) != value:
                        self._game_ui_restore.setdefault(name, value)
            self._require_own_health_hud(values)
            self._request("; ".join(name + " " + numeric(value)
                                    for name, value in sorted(values.items())))
            if pending:
                self._verify_game_ui_values(values)
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
        self._request("demo_pause")
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
            # A responsive paused camera needs no replay reconstruction. Some
            # builds cannot land the adjacent-tick round trip reliably. Keep
            # that bounded recovery for measured camera failures only.
            self._position_direct_frame(frame, tick)
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
                self._require_native_dof_support(project)
                self._request("demo_pause")
                self._snapshot(project)
                bridge = self._native_bridge()
                bridge.prepare(project, shot_time, 1.0, True, self._demo.name)
                self._native_active = True
                self._native_manual = True
                self._detach_spectator_view()
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

    @staticmethod
    def _project_uses_native_dof(project):
        setup = getattr(project, "setup_values", None) or {}
        if float(setup.get("r_dof_override", 0) or 0) != 0:
            return True
        return any(getattr(track, "name", "") == "r_dof_override"
                   for track in getattr(project, "tracks", ()))

    def _dof_compiler_bin_dir(self):
        """The game's ``bin/win64`` folder from the last launch, or None."""
        raw = str((self._launch_attempt or {}).get("game_path") or "").strip()
        if not raw:
            return None
        base = Path(raw)
        if base.suffix.casefold() == ".exe":
            return base.parent
        if base.name.casefold() == "win64" and base.parent.name.casefold() == "bin":
            return base
        if base.name.casefold() == "game":
            return base / "bin" / "win64"
        return base / "game" / "bin" / "win64"

    def _missing_dof_compiler_files(self):
        """Game-bin compiler DLLs absent for the launched install.

        Returns the missing names, or an empty list when the install looks
        complete or the game path is unknown, which keeps the reload-based
        detection in :meth:`ensure_native_dof_shader_support`.
        """
        bin_dir = self._dof_compiler_bin_dir()
        if bin_dir is None:
            return []
        try:
            return [name for name in DOF_COMPILER_FILES if not (bin_dir / name).is_file()]
        except OSError:
            return []

    def ensure_native_dof_shader_support(self, project):
        """Keep the engine able to compile its Native DOF pass in this session.

        The engine's ``r_dof_override`` pass uses a developer DOF material built
        with ``DynamicShaderCompile 1``. A session that disables dynamic shader
        compilation (a common performance tweak) makes the engine substitute its
        error material for the full-screen pass: the magenta/black checkerboard
        users report. Enable compilation for this session, ask the engine to
        reload the DOF shaders once, and remember the original value for
        :meth:`disconnect`. Never blocks editing when a setting is unreadable.
        """
        if self._dof_shader_checked or not self._project_uses_native_dof(project):
            return False
        if self._console is None or not self._console.is_connected or not self._alive():
            return False
        self._dof_shader_checked = True
        evidence = {"settings": {}, "reload": None}
        changed = False
        for name in DOF_SHADER_SETTINGS:
            try:
                current = read_cvar_value(name, self._request(name, allow_error=True))
            except (RuntimeError, ValueError):
                continue
            if current is None:
                continue
            evidence["settings"][name] = current
            if current == 0:
                continue
            try:
                self._request(name + " 0")
                if read_cvar_value(name, self._request(name, allow_error=True)) == 0:
                    self._dof_shader_restore[name] = current
                    changed = True
                    evidence["settings"][name] = 0
            except (RuntimeError, ValueError):
                continue
        missing = self._missing_dof_compiler_files()
        if missing:
            # Retail Deadlock ships no shader source and no vfx compiler, so
            # ``mat_forcereloadshaders dof`` can only fail and swap in the engine
            # error material (the magenta/black checkerboard). The engine renders
            # the pass from its shipped compiled ``dof`` shader, so skip the
            # reload instead of refusing the pass.
            evidence["missing_compiler_files"] = missing
            evidence["reload"] = "skipped (vfx compiler files missing)"
            if changed:
                self._message("Native DOF enabled the engine's dynamic shader compilation for this session; "
                              "your shader setting is restored when you disconnect.", playing=False)
            self._startup_evidence["dof_shader"] = evidence
            LOG.warning("Native DOF compiler files are missing; skipping the forced reload: %s",
                        ", ".join(missing))
            return changed
        # Always refresh the DOF shaders once so a stale compile cannot keep
        # the engine's error material on screen, even when the setting above is
        # not readable through this build's console.
        try:
            evidence["reload"] = str(self._request(
                DOF_SHADER_RELOAD, timeout=15, allow_error=True, allow_truncated=True) or "")[:4000]
        except (RuntimeError, ValueError, OSError) as exc:
            evidence["reload"] = "reload unavailable: " + str(exc)[:200]
        if _dof_shader_compile_failed(evidence["reload"]):
            evidence["checkerboard_risk"] = True
            # Remember the failure for the whole session and refuse the pass at
            # every apply point; re-running the check would just warn again and
            # still let the engine render the checkerboard (and crash).
            self._native_dof_unavailable = NATIVE_DOF_UNAVAILABLE
            LOG.error("Native DOF shader compilation is unavailable; refusing the pass: %s",
                      evidence["reload"])
            self._message(NATIVE_DOF_UNAVAILABLE)
            self._startup_evidence["dof_shader"] = evidence
            return False
        if changed:
            self._message("Native DOF enabled the engine's dynamic shader compilation for this session; "
                          "your shader setting is restored when you disconnect.", playing=False)
        self._startup_evidence["dof_shader"] = evidence
        return changed

    def _require_native_dof_support(self, project):
        """Check Native DOF shader support and refuse an unsafe ``r_dof_override`` pass.

        :meth:`ensure_native_dof_shader_support` only records the failure; this
        guard is what keeps an authored override from reaching the engine, where
        the missing compiler would render the magenta/black checkerboard and has
        crashed Deadlock. Called at every point that applies DOF to the game.
        Other DOF modes (Citadel) and projects without a native override do not
        use the engine's dynamic-shader-compile pass, so they are never blocked.
        """
        if not self._project_uses_native_dof(project):
            return
        self.ensure_native_dof_shader_support(project)
        if self._native_dof_unavailable:
            raise RuntimeError(self._native_dof_unavailable)

    def _restore_dof_shader_settings(self):
        if not self._dof_shader_restore:
            return None
        if self._console is None or not self._console.is_connected or not self._alive():
            return ("Native DOF changed the game's shader setting for this session; "
                    "reconnect and use Stop / restore to put it back.")
        error = None
        for name, value in list(self._dof_shader_restore.items()):
            try:
                self._request(name + " " + numeric(value))
                actual = read_cvar_value(name, self._request(name, allow_error=True))
            except (RuntimeError, ValueError, OSError) as exc:
                error = "Could not restore the game's " + name + " setting: " + str(exc)
                continue
            if actual is not None and math.isclose(actual, value, rel_tol=1e-6, abs_tol=1e-6):
                self._dof_shader_restore.pop(name, None)
            else:
                error = "Could not restore the game's " + name + " setting."
        return error

    def preview_native_effects(self, project, shot_time):
        """Apply edited effects at the held camera without seeking the replay."""
        with self._op_lock:
            self._validate_project(project)
            bridge = self._native_bridge()
            if bridge is None or self.status().get("playing"):
                raise RuntimeError("Pause native camera playback before editing DOF.")
            self._require_probe()
            self._require_demo()
            self._require_native_dof_support(project)
            current = bridge.status()
            editor = bridge.editor_status()
            if not current.get("paused") or not editor.get("ready") or editor.get("input_mode") != "panel":
                raise RuntimeError("Open the in-game Editor on a paused replay before editing DOF.")
            if current.get("state") in ("stopped", "probe") and not self._game_ui_visible:
                # Stop and failed preparation release the camera while the
                # panel stays usable. An explicit edit must reacquire it.
                self._require_native_demo(current, allow_idle=True)
                self.enter_native_flight(owner="panel")
                current = bridge.status()
            self._require_native_demo(current)
            shot_time = self._shot_time(project, shot_time)
            pose = self._native_pose(current)
            preview = deepcopy(project)
            # A private constant camera replaces only the runtime preview. The
            # user's saved camera positions/times remain in their project.
            preview.keyframes = [Keyframe(time=shot_time, **pose)]
            if current.get("applied_fov") is not None:
                preview.keyframes[0].lens_scale = math.tan(math.radians(current["applied_fov"] / 2)) / pose["aspect_ratio"]
            self._snapshot(project)
            try:
                bridge.prepare(preview, shot_time, 1.0, True, self._demo.name)
                result = bridge.start_flight(self._demo.name, owner="panel")
                self._detach_spectator_view()
                self._require_native_demo(result)
                if not result.get("paused") or int(result["tick"]) != int(current["tick"]):
                    raise RuntimeError("The replay moved while applying DOF. Pause it and retry.")
            except Exception:
                self._release_native_camera()
                raise
            self._native_active = self._native_manual = True
            frame = dict(pose, time=shot_time, cvars=project.evaluate(shot_time)["cvars"])
            self._set_paused_pose(frame, int(result["tick"]))
            self._message("Native DOF updated at the current camera.", time=shot_time, paused_flight=True)
            return deepcopy(frame)

    def set_native_particles(self, enabled, spawn_height, despawn_on_ground, preset, intensity):
        """Update the active particle preset without moving the camera."""
        bridge = self._native_bridge()
        setter = getattr(bridge, "set_particles", None)
        if callable(setter):
            setter(enabled, spawn_height, despawn_on_ground, preset, intensity)
            return True
        return False

    def set_native_confetti(self, enabled, spawn_height, despawn_on_ground):
        """Backward-compatible path used by the in-game particle card."""
        bridge = self._native_bridge()
        setter = getattr(bridge, "set_confetti", None)
        if callable(setter):
            setter(enabled, spawn_height, despawn_on_ground)
            return True
        return False

    def _supports_native_flight(self):
        bridge = self._native_bridge()
        return bridge is not None and callable(getattr(bridge, "start_flight", None))

    def enter_native_flight(self, pose=None, *, cancelled=None, owner="flight"):
        """Let the in-game view callback own input and manual camera movement.

        The replay keeps its current pause state: a paused replay gives the
        existing paused camera, and a playing replay arms the same manual
        override without pausing it (playback free-cam).
        """
        with self._op_lock, self._paused_preparation(cancelled):
            if not self._supports_native_flight():
                raise RuntimeError("Native free camera requires the matching native editor build.")
            self._require_probe()
            self._halt(native_action="native_hold")
            self._require_demo()
            restoration_error = self._restore_playback_settings()
            if restoration_error:
                raise RuntimeError(restoration_error)
            # Automatic startup has not passed through F9. The replay can
            # still own an automatic/free cursor even though our UI flag is
            # false. Establish the same explicit handoff on every flight
            # entry, preserving the pre-edit HUD/cursor for Stop / restore.
            self._remember_game_ui_settings()
            if self._console_open:
                self.toggle_console(enabled=False)
            self._hide_game_ui()
            self._game_ui_visible = False
            self._check_paused_cancelled()
            bridge = self._native_bridge()
            # With no supplied pose, the callback seeds from the currently
            # rendered view, including a held path endpoint. No spec_goto or
            # paused player-eye calibration can move that seed vertically.
            if isinstance(pose, dict):
                pose = tuple(pose[name] for name in ("x", "y", "z", "pitch", "yaw", "roll", "aspect_ratio"))
            options = {"owner": owner} if owner != "flight" else {}
            armed = None
            try:
                armed = bridge.start_flight(self._demo.name, pose=pose, cancelled=cancelled,
                                            playback=None, **options)
                self._native_active = self._native_manual = True
                self._detach_spectator_view()
            except Exception:
                if armed is not None:
                    # A failed spectator switch must not leave the successfully
                    # armed native camera hidden behind the recovered game UI.
                    self._release_native_camera()
                # start_flight releases its command on failure. Do not leave
                # stale ownership that would make the next retry hold a path
                # the bridge no longer has (notably after attach faults).
                self._native_active = self._native_manual = False
                self._invalidate_paused_camera()
                # The HUD was already hidden, but the failed arm released the
                # native camera. Restore the actual replay controls as well as
                # ownership so engine spectator movement is not disguised as
                # a frozen Dolly view. Keep the original setup error visible.
                try:
                    self.toggle_game_ui(enabled=True)
                except (RuntimeError, ValueError, OSError):
                    LOG.exception("Could not restore replay UI after camera setup failed")
                raise
            self._native_active = True
            self._native_manual = True
            playback = not bool(armed.get("paused"))
            current = bridge.status()
            self._require_native_demo(current)
            if not playback:
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
            self._message("Free camera ready. Use the in-game movement controls; F7 opens the console." if playback
                          else "Paused camera ready. Use the in-game movement controls; F7 opens the console.",
                          playing=False, paused_flight=owner == "flight", replay_paused=not playback)
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
            # Startup closes the console before preload/replay readiness. Keep
            # the editor's recovery keys enabled without opening its panel over
            # the hideout. Only a verified replay may return to the panel.
            return_owner = ("game_ui" if self._game_ui_visible else
                            "panel" if self._probe_result else "disabled")
            if callable(editor_configure):
                # A seek already in progress may have overwritten the optimistic
                # F7 owner before this queued event runs. Suspend input again
                # for the actual command, including a delayed close operation.
                editor_configure(owner="console")
            command = "showconsole" if enabled else "hideconsole"
            available = self._console_command_available(command)
            if available is False:
                # The game explicitly rejected this name. Fall back only when a
                # blind toggle can still reach the requested state; otherwise
                # refuse rather than leave console visibility unknown.
                if self._console_open == enabled:
                    if not enabled and callable(editor_configure):
                        editor_configure(owner=return_owner)
                    return self.status()
                if self._console_open is not None and self._console_command_available("toggleconsole"):
                    self._request("toggleconsole")
                else:
                    raise RuntimeError("This game build does not support " + command
                                       + "; the console's current visibility is unknown. Console access was not changed.")
            else:
                # Confirmed, or the flood/stall hid the confirmation: issue the
                # explicit command. Unknown availability is not a rejection. A
                # -dev preload stall can delay the echo well past a normal
                # window, and this visibility command is cosmetic (the editor
                # already suspends input), so a delayed acknowledgement must not
                # abort startup; visibility is re-checked before the editor.
                try:
                    self._request(command, timeout=8, allow_error=True)
                except ConsoleTimeout:
                    LOG.warning("Console command %r was not acknowledged within the "
                                "window; console visibility will be re-checked before "
                                "the editor opens.", command)
            self._console_open = enabled
            if not enabled and callable(editor_configure):
                editor_configure(owner=return_owner)
            self._message("Console open. Editor movement is suspended while typing." if enabled else "Console closed. Return to the editor to continue.")
            return self.status()

    def destroy_ragdolls(self):
        """Clear accumulated ragdolls in the owned local replay."""
        with self._op_lock:
            if self.status().get("playing") or getattr(self, "_export_timing", None):
                raise RuntimeError("Stop shot playback or recording before clearing ragdolls.")
            self._require_probe()
            self._require_demo()
            self._request("cl_destroy_ragdolls")
            self._message("Ragdoll cleanup command sent.")

    def _console_cvar(self, name):
        """Current value of a console cvar, or None when it cannot be read."""
        try:
            return read_cvar_value(name, self._request(name, allow_error=True))
        except (RuntimeError, ValueError, OSError):
            return None

    def _require_quiet_session(self, label):
        if self.status().get("playing") or getattr(self, "_export_timing", None):
            raise RuntimeError(f"Stop shot playback or recording before {label}.")
        self._require_probe()
        self._require_demo()

    def _write_look_values(self, values):
        """Require readable controls and verify the entire visual setting group."""
        original = {name: self._console_cvar(name) for name in values}
        if any(value is None for value in original.values()):
            raise RuntimeError("This Deadlock build does not expose all required Look controls. "
                               "No settings were changed.")
        try:
            for name, value in values.items():
                self._request(f"{name} {numeric(value)}")
            if any(self._console_cvar(name) != value for name, value in values.items()):
                raise RuntimeError("Deadlock did not accept the requested Look settings.")
        except Exception:
            for name, value in original.items():
                try:
                    self._request(f"{name} {numeric(value)}", allow_error=True)
                    if self._console_cvar(name) == value:
                        continue
                except Exception:
                    LOG.exception("Could not restore Look control %s", name)
                self._look_restore.setdefault(name, value)
            raise

    def _restore_look_values(self):
        """Retry an incomplete Look rollback without camera-track validation."""
        for name, value in list(self._look_restore.items()):
            try:
                self._request(f"{name} {numeric(value)}", allow_error=True)
                if self._console_cvar(name) == value:
                    del self._look_restore[name]
            except Exception:
                LOG.exception("Could not restore Look control %s", name)
        if self._look_restore:
            return "Some Look settings could not be restored; use Stop / restore to retry."
        return None

    def _apply_citadel_glow(self, disabled=None):
        """Write the remembered glow choice; used by the toggle and recovery."""
        state = int(self._glow_disabled if disabled is None else disabled)
        values = {name: state for name in ("citadel_boss_glow_disabled", "citadel_player_glow_disabled",
                                           "citadel_trooper_glow_disabled")}
        values["r_citadel_glow_health_bars"] = 1 - state
        self._write_look_values(values)

    def toggle_citadel_glow(self):
        """Flip the Citadel glow set and keep the choice across replay resets."""
        with self._op_lock:
            self._require_quiet_session("toggling glow")
            disabled = self._console_cvar("citadel_boss_glow_disabled") != 1
            self._apply_citadel_glow(disabled)
            self._glow_disabled = disabled
            self._message("Citadel glow disabled." if self._glow_disabled
                          else "Citadel glow enabled.")

    def _restore_healthbar_values(self):
        values = dict(self._healthbar_restore)
        for name, value in values.items():
            self._request(f"{name} {numeric(value)}", allow_error=True)
        for name, expected in values.items():
            if self._console_cvar(name) != expected:
                raise RuntimeError(f"The game did not restore {name}. Saved health-bar settings remain pending; retry.")
        self._healthbar_restore.clear()
        self._healthbars_hidden = False

    def toggle_healthbars(self):
        """Hide or restore the health bars.

        Only the live-verified switches are written, one command at a time.
        The exact prior values are snapshotted and restored on the next press.
        Bar glow stays owned by :meth:`toggle_citadel_glow`.
        """
        with self._op_lock:
            self._require_quiet_session("toggling health bars")
            if self._healthbar_restore:
                self._restore_healthbar_values()
                self._message("Health bars restored.")
                return
            scales = {name: self._console_cvar(name) for name in HEALTHBAR_SCALE_CVARS}
            if all(value is not None and value >= 0 for value in scales.values()):
                if all(value == 0 for value in scales.values()):
                    self._message("Floating health bars are already hidden by the game's settings.")
                    return
                # Build6723's live-tested panel scale controls hide existing
                # bars without destroying their renderer. Keep exact originals.
                self._healthbar_restore = scales
                self._write_look_values({name: 0 for name in scales})
                self._healthbars_hidden = True
                self._message("Floating health bars hidden. The game HUD option controls the hero health panel.")
                return
            snapshot = {name: self._console_cvar(name) for name in HEALTHBAR_SWITCH_CVARS}
            if any(value is None for value in snapshot.values()):
                raise RuntimeError(
                    "This Deadlock build does not expose the supported health-bar switches. "
                    "The health-bar toggle is unavailable; no settings were changed.")
            if snapshot["citadel_healthbars_enabled"] == 0:
                # Already hidden outside Dolly; turn the switches back on.
                self._healthbar_restore = snapshot
                applied = []
                for name, value in HEALTHBAR_SWITCH_ON.items():
                    self._request(f"{name} {numeric(value)}", allow_error=True)
                    if self._console_cvar(name) == value:
                        applied.append(name)
                if len(applied) != len(HEALTHBAR_SWITCH_ON):
                    raise RuntimeError("This game build did not accept the health-bar switches. "
                                       "Press again to restore the saved settings.")
                self._healthbar_restore.clear()
                self._message("Health bars enabled.")
                return
            self._healthbar_restore = snapshot
            applied = []
            for name in HEALTHBAR_SWITCH_CVARS:
                self._request(f"{name} 0", allow_error=True)
                if self._console_cvar(name) == 0.0:
                    applied.append(name)
            if len(applied) != len(HEALTHBAR_SWITCH_CVARS):
                raise RuntimeError(
                    "This game build did not accept the health-bar switches. "
                    "Press again to restore the saved settings.")
            self._message("Health bars hidden.")

    def near_player_opacity_fix(self):
        """Force full opacity on the near-player camera fades."""
        with self._op_lock:
            self._require_quiet_session("fixing near-player opacity")
            self._write_look_values({"citadel_camera_fade_viewed_near_opacity": 1,
                                     "citadel_camera_fade_other_near_opacity": 1})
            self._message("Near-player fade opacity forced to full.")

    def set_playback_speed(self, speed):
        """Apply a replay speed immediately through the demo timescale.

        While paused the value applies on the next resume; while playing the
        replay slows or speeds up immediately. The native camera path follows
        replay time, so an authored shot scales with it. Editing Stop preserves
        the chosen speed; final session cleanup returns a Dolly-owned speed to 1x.
        """
        with self._op_lock:
            if isinstance(speed, bool) or not isinstance(speed, (int, float)):
                raise ValueError("Playback speed must be a number between 0.05 and 4.")
            speed = float(speed)
            if not math.isfinite(speed) or not .05 <= speed <= 4:
                raise ValueError("Playback speed must be between 0.05 and 4.")
            if getattr(self, "_export_timing", None) is not None:
                raise RuntimeError("Stop the fixed-step export before changing playback speed.")
            details = self._playback_details
            if details is not None and details.get("camera_backend") == "console":
                raise RuntimeError(
                    "Live speed changes require the native camera. Stop the shot and choose a speed before playing.")
            if self._demo is None:
                self._message(f"Replay speed set to {numeric(speed)}x for the next replay.")
                return self.status()
            self._require_demo(require_tick=False)
            self._request("demo_timescale " + numeric(speed))
            self._demo_speed_changed = True
            if details is not None:
                details["speed"] = speed
            self._message(f"Replay speed set to {numeric(speed)}x.")
            return self.status()

    def _restore_replay_hud(self):
        if not self._replay_hud_restore:
            return
        if self._session is not self._replay_hud_session:
            raise RuntimeError('Replay HUD restoration belongs to a different session.')
        self._require_connection()
        values, _ = self._safe_health_hud_values(self._replay_hud_restore)
        self._require_own_health_hud(values)
        self._request('; '.join(name + ' ' + numeric(value) for name, value in values.items()))
        self._verify_game_ui_values(values)
        self._replay_hud_restore = {}
        self._replay_hud_session = None

    def _apply_replay_hud(self):
        names = ('citadel_hud_visible', 'citadel_hide_replay_hud', 'hud_free_cursor')
        own_health = self._own_health_hud_value()
        if own_health is not None:
            names += (OWN_HEALTH_HUD,)
            self._game_ui_restore.setdefault(OWN_HEALTH_HUD, own_health)
        if not self._replay_hud_restore:
            output = self._request('; '.join(names))
            self._replay_hud_restore = {name: read_cvar_value(name, output) for name in names}
            self._replay_hud_session = self._session
        elif self._session is not self._replay_hud_session:
            raise RuntimeError('Replay HUD settings belong to a different session.')
        try:
            values = dict(zip(names[:3], (1, 0, 0) if self._show_replay_hud else (0, 1, 0)))
            if OWN_HEALTH_HUD in self._replay_hud_restore:
                values[OWN_HEALTH_HUD] = (self._game_ui_restore.get(
                    OWN_HEALTH_HUD, self._replay_hud_restore[OWN_HEALTH_HUD])
                    if self._show_replay_hud else 1)
            values, _ = self._safe_health_hud_values(values)
            self._require_own_health_hud(values)
            self._request('; '.join(name + ' ' + numeric(value) for name, value in values.items()))
            self._verify_game_ui_values(values)
        except BaseException:
            self._restore_replay_hud()
            raise

    def set_replay_hud(self, enabled):
        """Session playback preference, shared by Follow and ordinary replay views."""
        if type(enabled) is not bool:
            raise ValueError('Replay HUD visibility must be a boolean.')
        with self._op_lock:
            self._require_demo(require_tick=False)
            if self._recorder_active() or self.status().get('playing'):
                raise RuntimeError('Finish shot playback or recording before changing the replay HUD.')
            previous = self._show_replay_hud
            self._show_replay_hud = enabled
            try:
                if not self._native_bridge().status().get('paused'):
                    self._apply_replay_hud()
            except BaseException:
                self._show_replay_hud = previous
                raise
            self._message('Game HUD shown during replay.' if enabled else 'Game HUD hidden during replay.')

    def _toggle_replay_time(self, paused):
        try:
            self._request('demo_resume' if paused else 'demo_pause')
        except BaseException:
            if paused:
                self._restore_replay_hud()
            raise

    def toggle_replay(self):
        """Pause/resume replay time without restarting an authored native path.

        While the native free camera is armed, movement stays live in both
        states; only the replay clock toggles. Movement keeps integrating on
        each rendered view even though the replay is not paused.
        """
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
            if paused:
                self._apply_replay_hud()
            else:
                self._restore_replay_hud()
            if self._native_manual:
                if paused:
                    self._toggle_replay_time(True)
                    self._message("Replay playing. Free camera movement stays active.",
                                  playing=False, paused_flight=True, replay_paused=False)
                else:
                    self._toggle_replay_time(False)
                    # A paused free camera must track the tick it paused on so
                    # desktop movement and captures stay valid after playback.
                    current = self._wait_paused_native_view(bridge, current["frame_count"])
                    frame = self._native_pose(current)
                    frame.update(time=float(current.get("phase", 0)), cvars={})
                    self._set_paused_pose(frame, int(current["tick"]))
                    self._message("Replay paused. Free camera movement stays active.",
                                  playing=False, paused_flight=True, replay_paused=True)
                return self.status()
            self._toggle_replay_time(paused)
            self._message("Replay playing." if paused else "Replay paused.", replay_paused=not paused)
            return self.status()

    def step_replay_ticks(self, ticks):
        """Step replay time while retaining the visible camera and open panel."""
        if isinstance(ticks, bool) or not isinstance(ticks, (int, float)) or ticks not in (-25, -10, -5, -2, -1, 1, 2, 5, 10, 25):
            raise ValueError("Choose a tick step of 1, 2, 5, 10 or 25 in either direction.")
        with self._op_lock:
            if self.status().get("playing"):
                raise RuntimeError("Pause shot playback before stepping the replay.")
            bridge = self._native_bridge()
            if not self._supports_native_flight():
                raise RuntimeError("Tick stepping requires the native editor.")
            self._stop_event.clear()
            self._require_probe()
            self._require_demo()
            before = bridge.status()
            self._require_native_demo(before, allow_idle=True)
            self._request("demo_pause")
            with self._paused_preparation(self._stop_event.is_set):
                view = self._wait_paused_native_view(bridge, before["frame_count"])
            info = self._require_demo()
            current = int(view["tick"])
            if int(info["tick"]) != current:
                raise RuntimeError("The replay moved before the tick step. Pause and retry.")
            pose = self._native_pose(view, "applied_pose" if self._native_active else "original_pose")
            target = max(0, current + int(ticks))
            if info.get("total_ticks") is not None:
                target = min(target, max(0, int(info["total_ticks"]) - 1))
            requested = target
            index = packet_index(self._demo, check_cancelled=self._check_position_cancelled)
            if index is not None:
                if info.get("total_ticks") not in (None, index.total_ticks):
                    raise RuntimeError("The replay file differs from the loaded replay. Reopen it through Dolly.")
                recorded = index.following(target) if ticks > 0 else index.preceding(target)
                if recorded is not None:
                    target = recorded
                elif ticks > 0 and index.ticks and target > index.ticks[-1]:
                    target = max(current, int(index.ticks[-1]))
            self._check_position_cancelled()
            if target != current:
                self._halt(native_action="release")
                self._invalidate_paused_camera()
                self._stop_event.clear()
                settled = self._seek_tick(target, allow_start_boundary=True)
                target = int(settled["tick"])
                self.enter_native_flight(pose=pose, owner="panel")
            bridge.configure_editor(owner="panel")
            moved = target - current
            detail = " (nearest recorded tick)" if target != requested else ""
            self._message(f"Replay tick {target}: moved {moved:+d} ticks{detail}. Camera held fixed.")
            return {"tick": target, "requested_tick": requested, "moved_ticks": moved}

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
            self._position_direct_frame(frame, tick)
            self._message("Camera position verified and lens settings applied. Replay paused at this view.", time=float(shot_time))

    @staticmethod
    def _shot_time(project, value):
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("Shot time must be finite.")
        return max(0.0, min(project.duration, value))

    def _seek(self, project, shot_time):
        target = int(round(project.start_tick + shot_time * project.tick_rate))
        # SourceTV recordings may store one packet per several simulation
        # ticks. Normal pausing can expose a tick between those records, but
        # rebuilding that tick stops at a later packet. Only native shot seeks
        # opt into a boundary backed by the selected, completed replay file.
        # Legacy position refresh and relative controls retain exact semantics.
        if target > 0 and self._native_bridge() is not None:
            index = packet_index(self._demo, check_cancelled=self._check_position_cancelled)
            actual = index.following(target) if index is not None else None
            if actual is not None and actual != target:
                self._check_position_cancelled()
                live = self._require_demo()
                if live.get("total_ticks") not in (None, index.total_ticks):
                    # A same-named file may have been replaced since playback
                    # loaded it. Its new index cannot adjust this live replay.
                    return self._seek_tick(target, allow_start_boundary=True)
                if (actual - project.start_tick) / project.tick_rate > project.duration:
                    raise RuntimeError(f"The next recorded replay packet is tick {actual}, after this shot ends. "
                                       "Extend the shot or preview its camera without seeking.")
                self._check_position_cancelled()
                info = self._seek_tick(actual)
                self._last_seek_details.update(target_tick=target, commanded_tick=actual,
                    actual_tick=actual, boundary="recorded_packet",
                    boundary_delta_ticks=actual - target)
                return dict(info, seek_boundary={"requested_tick": target,
                    "actual_tick": actual, "reason": "recorded_packet"})
        return self._seek_tick(target, allow_start_boundary=True)

    def _seek_shot_time(self, project, shot_time, info):
        """Sample the original shot timeline at an explicit reconstruction tick."""
        if not info.get("seek_boundary"):
            return shot_time
        actual_time = (info["tick"] - project.start_tick) / project.tick_rate
        boundary = info["seek_boundary"]
        if boundary.get("reason") == "recorded_packet":
            if actual_time > project.duration:
                raise RuntimeError(f"The next recorded replay packet is tick {info['tick']}, after this shot ends. "
                                   "Extend the shot or preview its camera without seeking.")
            if actual_time < 0:
                # The engine cannot reconstruct the requested packet-start tick
                # and settled one tick earlier. Hold the first camera over that
                # instant instead of failing the whole shot.
                self._message(f"The replay does not include a reconstructible packet at tick "
                              f"{boundary['requested_tick']}; starting one frame earlier at tick "
                              f"{info['tick']} with the first camera held for that instant.")
                return 0.0
            skipped = (info["tick"] - boundary["requested_tick"]) / project.tick_rate
            self._message(f"Replay tick {boundary['requested_tick']} falls between recorded packets. "
                          f"Starting at tick {info['tick']}; skipping the first {skipped:.6f} shot seconds of this playback. "
                          "Saved camera and effect times are unchanged.")
            return actual_time
        if actual_time > project.duration:
            raise RuntimeError("This replay's first seekable tick is 1, after this shot ends. "
                               "Extend the shot past that tick or preview the camera without seeking.")
        self._message("Replay tick 0 precedes its first seekable packet. "
                      f"Starting at tick 1 ({actual_time:.6f} shot seconds); saved camera times are unchanged.")
        return actual_time

    def _seek_tick(self, target, *, allow_start_boundary=False):
        """Pin fast-goto on for the seek, then verify a paused tick.

        A paused ``demo_gototick`` to a far tick needs ``demo_usefastgoto``
        enabled to fast-skip; with it off the seek stalls and can even unload the
        demo (verified by an A/B test). Dolly normally benefits from the default,
        but pin it explicitly so a user setting or a future default cannot break
        seeking, and restore the exact prior value afterward.
        """
        fast_original = None
        try:
            fast_original = read_cvar_value("demo_usefastgoto",
                                            self._request("demo_usefastgoto"))
        except (ValueError, RuntimeError):
            fast_original = None
        if fast_original is not None and not fast_original:
            try:
                self._request("demo_usefastgoto 1")
            except (RuntimeError, OSError):
                pass
        try:
            return self._seek_tick_run(target, allow_start_boundary=allow_start_boundary)
        finally:
            if fast_original is not None and not fast_original:
                try:
                    self._request("demo_usefastgoto 0")
                except (RuntimeError, OSError):
                    pass

    def _seek_tick_run(self, target, *, allow_start_boundary=False):
        """Verify a paused tick; optionally recognize the replay's packet-1 floor."""
        self._recording_replay = None
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
        # The engine reports the packet it reconstructs from when a paused skip
        # crosses full-packet boundaries. Only that explicit report allows the
        # one-tick floor policy below; a stuck tick after an overshoot never is.
        packet_floor_reported = bool(re.search(r"Demo Skipping:[^\n]*from full packet\s+\d+",
                                               seek_output, re.IGNORECASE))
        boundary_samples = 0
        boundary_pause_confirmed = False
        self._check_position_cancelled()
        end = time.perf_counter() + 15
        samples = deque(maxlen=32)
        native = self._native_bridge() if self._supports_native_flight() else None
        wait_for_view = native is not None
        stable = 0
        overshoot_tick = None
        overshoot_samples = 0
        undershoot_tick = None
        undershoot_samples = 0
        undershoot_nudged = False
        renderer_error = None
        pause_confirmed = False
        corrections = []
        self._last_seek_details = {"target_tick": target, "verified": False,
                                   "corrections": corrections}
        while time.perf_counter() < end:
            self._check_position_cancelled()
            if wait_for_view:
                # Commands sent during replay reconstruction can lose their
                # console echo delimiters, even after the renderer later settles.
                # Observe fresh native paused frames before querying the console.
                # The wait only proves the renderer is alive and paused: a target
                # the engine cannot reconstruct can settle one tick below it, so
                # the exact tick is enforced by the console settle policy below.
                with self._paused_preparation(self._stop_event.is_set):
                    try:
                        self._wait_paused_native_view(
                            native, native.status()["frame_count"],
                            timeout=min(NATIVE_PAUSE_TIMEOUT,
                                        max(.001, end - time.perf_counter())),
                            expected_tick=None)
                    except RuntimeError as exc:
                        renderer_error = str(exc)
                wait_for_view = False
                self._check_position_cancelled()
            # Backward reconstruction can block console replies for more
            # than the ordinary 3-second request timeout. Spend only the
            # remaining existing seek budget, then still require settled ticks.
            # A transport failure is retried inside that budget; an identity or
            # policy error still propagates immediately.
            try:
                info = self._require_demo(timeout=max(.001, end - time.perf_counter()))
            except ConsoleError:
                if time.perf_counter() >= end:
                    raise
                if self._stop_event.wait(SEEK_SETTLE_INTERVAL):
                    raise RuntimeError("Seek cancelled.")
                continue
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
            if target < observed <= target + 4:
                overshoot_samples = overshoot_samples + 1 if observed == overshoot_tick else 1
                overshoot_tick = observed
            else:
                overshoot_tick = None
                overshoot_samples = 0
            if target - 2 <= observed < target:
                undershoot_samples = undershoot_samples + 1 if observed == undershoot_tick else 1
                undershoot_tick = observed
            else:
                undershoot_tick = None
                undershoot_samples = 0
            if undershoot_tick == target - 1 and undershoot_samples >= SEEK_SETTLE_SAMPLES \
                    and packet_floor_reported:
                if not undershoot_nudged and time.perf_counter() < end:
                    # A paused skip can stop one tick before a packet-start
                    # target without loading that packet. Nudge playback across
                    # the boundary once, then demand the exact tick again.
                    undershoot_nudged = True
                    undershoot_samples = 0
                    self._check_position_cancelled()
                    self._request("demo_resume", allow_error=True)
                    self._stop_event.wait(min(.05, max(.001, end - time.perf_counter())))
                    self._check_position_cancelled()
                    self._request("demo_pause")
                    stable = 0
                    pause_confirmed = False
                    wait_for_view = False
                    continue
                # The engine cannot reconstruct the requested tick. Start at
                # the recorded tick just before it instead of failing the shot;
                # the caller holds the first camera over the short pre-roll.
                self._last_seek_details.update(verified=True, actual_tick=undershoot_tick,
                                               boundary="recorded_packet",
                                               boundary_delta_ticks=undershoot_tick - target)
                return dict(self._require_demo(require_tick=False),
                            seek_boundary={"requested_tick": target,
                                           "actual_tick": undershoot_tick,
                                           "reason": "recorded_packet"})
            if overshoot_samples >= SEEK_SETTLE_SAMPLES and not corrections:
                # Native forward seeks can stop up to four ticks late (6774:
                # request15301 settled at15305). Reissuing the
                # SAME target from there takes the backward-seek route. Do not
                # react to a transient ahead sample or chase an unrelated seek.
                self._check_position_cancelled()
                self._request("demo_pause")
                self._check_position_cancelled()
                confirmed = int(self._require_demo(timeout=max(.001, end - time.perf_counter()))["tick"])
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
                    wait_for_view = native is not None
                    correction_output = self._request(f"demo_gototick {target} 0 1", timeout=6)
                    boundary_reported = bool(boundary_reported and
                        re.search(boundary_pattern, correction_output, re.IGNORECASE))
                    packet_floor_reported = bool(re.search(
                        r"Demo Skipping:[^\n]*from full packet\s+\d+", correction_output,
                        re.IGNORECASE))
                    self._check_position_cancelled()
                    stable = 0
                    overshoot_tick = None
                    overshoot_samples = 0
                    pause_confirmed = False
            if self._stop_event.wait(SEEK_SETTLE_INTERVAL):
                raise RuntimeError("Seek cancelled.")
        last_tick = samples[-1]["tick"] if samples else None
        if renderer_error and last_tick is not None and last_tick != target:
            raise RuntimeError(
                f"The replay stayed at tick {last_tick} while seeking to {target}; the engine may "
                "not be able to reconstruct that tick. Try a start time a moment later, or restart "
                "the replay. Renderer detail: " + renderer_error)
        if renderer_error:
            raise RuntimeError(renderer_error)
        raise RuntimeError(f"Replay did not reach tick {target}. Export diagnostics; the demo may have ended or seeking may differ in this build.")

    def seek(self, project, shot_time):
        with self._op_lock:
            self.stop()
            self._stop_event.clear()
            self._require_probe()
            self._require_demo()
            self._require_native_dof_support(project)
            self._snapshot(project)
            shot_time = self._shot_time(project, shot_time)
            bridge = self._native_bridge() if self._supports_native_flight() else None
            before_frame = int(bridge.status().get("frame_count", 0)) if bridge else 0
            positioned_demo = self._seek(project, shot_time)
            shot_time = self._seek_shot_time(project, shot_time, positioned_demo)
            if bridge is None:
                self._position_direct_frame(project.evaluate(shot_time), positioned_demo["tick"])
            else:
                # Seeking can leave the spectator console angle override stale.
                # Wait for the reconstructed scene, then hold the complete authored
                # native pose, including rotation curves, lens and source blending.
                self._wait_paused_native_view(bridge, before_frame,
                                              expected_tick=positioned_demo["tick"])
                self._check_position_cancelled()
                bridge.prepare(project, shot_time, 1.0, True, self._demo.name)
                self._native_active = self._native_manual = True
                self._detach_spectator_view()
                status = bridge.status()
                self._require_native_demo(status)
                if not status.get("paused") or int(status["tick"]) != int(positioned_demo["tick"]):
                    self._release_native_camera()
                    raise RuntimeError("The replay moved while applying the shot view. Pause it and retry.")
                frame = self._native_pose(status)
                frame.update(time=shot_time, cvars=dict(project.evaluate(shot_time)["cvars"]))
                self._set_paused_pose(frame, int(status["tick"]))
                bridge.configure_editor(owner="panel")
            boundary = positioned_demo.get("seek_boundary")
            if boundary:
                self._message(f"Replay paused at recorded tick {positioned_demo['tick']} "
                              f"({shot_time:.6f} shot seconds); requested tick {boundary['requested_tick']}. "
                              "Saved camera and effect times are unchanged.", time=shot_time)
            else:
                self._message("Replay paused at the requested path time.", time=shot_time)

    def _stop_before_new_shot(self, *, preserve_layers=False):
        # Play's internal Stop may consume the verified prior-hero intent while
        # returning to stock view. Preserve that intent for the NEW camera owner:
        # Directed can subsequently change actual observer mode before detach,
        # so rediscovering it then is not reliable. No old handles are retained.
        intent = self._game_hero_restore
        self.stop(preserve_layers=preserve_layers)
        if (intent is not None and self._game_hero_restore is None
                and intent[0] is self._session and intent[1] == self._demo):
            self._game_hero_restore = intent

    def play(self, project, time=0, speed=1, rate=60, frozen=False, hide_hud=True, smoothing="off"):
        # The main UI starts at zero; explicit API callers may choose a start.
        # Reject invalid experimental modes before stopping a working camera.
        window = smoothing_window(smoothing)
        if self._native_bridge() is not None:
            compile_effects(project)  # Reject unsupported tracks before stopping a working shot.
        with self._op_lock:
            if self._recorder_active():
                self._stop_before_new_shot(preserve_layers=True)
            else:
                self._stop_before_new_shot()
            # A suppressed health container may intentionally span repeated
            # shots when the original game view has no safe HUD player. Playback
            # only needs the panel hidden, so it does not require the full-HUD
            # hide that a guarded replay reload needs. Other failed restorations
            # still block playback as before.
            health_held = self._health_panel_held(require_full_hud=False)
            if self._playback_restore or self._restore or (self._game_ui_restore and not health_held) or self._demo_speed_changed:
                raise RuntimeError("Previous settings still need restoration. Reconnect and use Stop / restore before playing again.")
            self._stop_event.clear()
            self._require_probe()
            self._require_demo(require_tick=not frozen)
            self._require_native_dof_support(project)
            project = Project.from_dict(project.to_dict())
            speed, rate = float(speed), float(rate)
            if not math.isfinite(speed) or not .05 <= speed <= 4:
                raise ValueError("Playback speed must be between 0.05 and 4.")
            # A fixed-step export fixes the playback speed for the whole export so
            # the recorded video's slow-motion is exactly the selected value,
            # independent of the free-play speed control.
            export = getattr(self, "_export_timing", None)
            if export and export.get("speed"):
                speed = float(export["speed"])
            # Calibrate the paused camera under normal engine pacing; the
            # fixed-step timing is re-applied just before the path dispatches.
            self.suspend_export_timing()
            if rate not in (30, 60, 120):
                raise ValueError("Choose a command rate of 30, 60, or 120.")
            if hide_hud and ("citadel_hud_visible" in project.setup_values or
                             any(track.name == "citadel_hud_visible" for track in project.tracks)):
                raise ValueError("Remove the citadel_hud_visible track/fixed value or turn off Hide HUD during playback.")
            start = self._shot_time(project, time)
            if self._native_bridge() is not None and not frozen:
                self._prepare_native_shot_replay()
            self._snapshot(project)
            tick_rate_warning = self._replay_tick_rate_warning(project)
            self._playback_details = {"project": project.to_dict(), "start_time": start,
                                      "speed": speed, "rate": rate, "frozen": bool(frozen),
                                      "hide_hud": bool(hide_hud),
                                      "smoothing": {"mode": smoothing, "window_seconds": window,
                                                    "nominal_delay_seconds": window / 2,
                                                    "kind": "shared_phase_finite_window"},
                                      "clock_max_lead_ticks": 0 if frozen else 1}
            if tick_rate_warning:
                self._playback_details["tick_rate_warning"] = tick_rate_warning
            native = self._native_bridge()
            self._playback_details["camera_backend"] = "native" if native is not None else "console"
            self._playback_details["replay_recovery"] = deepcopy(self._replay_recovery) if native is not None and not frozen else None
            setter = getattr(native, "set_seek_relief", None)
            if setter is not None:
                setter(self._seek_relief)
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
                    view_before = int(native.status().get("frame_count", 0)) if native is not None else 0
                    positioned_demo = self._seek(project, start)
                    actual_start = self._seek_shot_time(project, start, positioned_demo)
                    if native is not None and positioned_demo.get("tick") is not None:
                        # A long demo skip keeps reconstructing after the tick
                        # reports settled. Wait for a rendered paused view at the
                        # target tick before writing and checking the camera, so
                        # the check cannot trigger another full seek.
                        self._wait_paused_native_view(native, view_before,
                                                      expected_tick=positioned_demo["tick"])
                    if positioned_demo.get("seek_boundary"):
                        self._playback_details.update(requested_start_time=start,
                            start_time=actual_start,
                            seek_boundary=deepcopy(positioned_demo["seek_boundary"]))
                        start = actual_start
                elif native is not None:
                    # A frozen native preview needs the current rendered scene,
                    # including a paused tick between SourceTV packet records.
                    # Seeking it or calibrating the console player eye can move
                    # that scene. Confirm two fresh paused views, then prepare
                    # the native camera directly without a replay rebuild.
                    before = native.status()
                    self._require_native_demo(before, allow_idle=True)
                    self._request("demo_pause")
                    paused_view = self._wait_paused_native_view(native, before["frame_count"])
                    positioned_demo = dict(self._require_demo(require_tick=False),
                                           tick=int(paused_view["tick"]))
                else:
                    self._request("demo_pause")
                    positioned_demo = self._require_demo(require_tick=False)
                    if positioned_demo.get("tick") is not None:
                        positioned_demo = self._seek_tick(positioned_demo["tick"])
                frame = project.evaluate(start)
                if native is not None:
                    frame["cvars"] = {}  # Native preparation snapshots originals before the first effect write.
                if native is not None and frozen:
                    self._playback_details["startup_calibration"] = {
                        "method": "native_frozen_view", "prepared_tick": positioned_demo["tick"],
                        "console_calibration_required": False}
                    self._playback_details["startup_seek"] = None
                else:
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
                # The paused view is verified; pace the engine for the export
                # before the authored path takes over.
                self.resume_export_timing()
                command = self._position_commands(frame)
                if native is not None:
                    # The whole shot is published once. HOLD must be acknowledged
                    # while paused, then PLAY is armed before demo_resume.
                    if not frozen:
                        self._request(command)
                    self._native_active = True
                    native.prepare(project, start, speed, bool(frozen), self._demo.name)
                    self._detach_spectator_view()
                    self._native_last_status = native.status()
                    self._require_native_demo(self._native_last_status)
                    if frozen and (not self._native_last_status.get("paused") or
                                   int(self._native_last_status["tick"]) != positioned_demo["tick"]):
                        raise RuntimeError("The replay moved while preparing frozen native preview. Pause it and retry.")
                    self._check_position_cancelled()
                    native.play()
                    if not frozen:
                        self._demo_speed_changed = True
                        self._request("demo_timescale " + numeric(speed) + "; demo_resume")
                    self._playback_details["initial_tick"] = positioned_demo.get("tick")
                    self._applied_pose = frame
                    message = ("Playing native frozen preview." if frozen
                               else "Playing native camera path at render time.")
                    if tick_rate_warning:
                        message += " WARNING: " + tick_rate_warning
                    self._message(message, playing=True, time=start)
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
                message = ("Playing frozen preview." if frozen else "Playing shot with the replay.")
                if tick_rate_warning:
                    message += " WARNING: " + tick_rate_warning
                self._message(message, playing=True, time=start)
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

    def set_seek_relief(self, enabled):
        """Enable or disable the native render relief applied while seeking."""
        self._seek_relief = bool(enabled)
        setter = getattr(self._native_bridge(), "set_seek_relief", None)
        if setter is not None:
            setter(self._seek_relief)

    def _require_native_demo(self, status, *, allow_idle=False):
        if self._demo is None or not same_replay_name(self._demo, status.get("demo_name")):
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
        deadline = time.perf_counter() + 2.0
        previous_frame = int(status["frame_count"])
        stable = 0
        tick = None
        paused_tick = None
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
            frame_count = int(current["frame_count"])
            if frame_count == previous_frame:
                continue
            previous_frame = frame_count
            current_tick = int(current["tick"])
            if tick is None:
                # Console delivery can precede the render callback's pause
                # acknowledgement. Do not use its older running tick as the
                # handoff baseline or reposition before a settled paused view.
                if current["paused"] and current_tick >= 0:
                    if paused_tick == current_tick:
                        tick = current_tick
                        self._native_handoff_details["paused_tick"] = tick
                        self._request(self._position_commands(target))
                        deadline = time.perf_counter() + 2.0
                    paused_tick = current_tick
                else:
                    paused_tick = None
                continue
            if current_tick != tick or not current["paused"]:
                raise RuntimeError("The replay moved during native camera handoff. The view remains held; pause the replay and use Stop / restore.")
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
        if tick is None:
            self._native_handoff_details["reason"] = "Waiting for rendered pause acknowledgement"
            if allow_hold:
                return
            raise RuntimeError("The renderer has not confirmed a paused replay for camera handoff. The view remains held; use Stop / restore or retry after the game responds.")
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
        if getattr(self, "_pov_active", False):
            # Keep the segment clock clamped until capture has drained. Do not
            # perform a spec_goto handoff or expose HUD in the recorded frames.
            try:
                self._request("demo_pause")
            except Exception as exc:
                return str(exc)
            with self._state_lock:
                self._state["playing"] = False
            return None
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
                    # A live speed change updates the demo timescale; keep the
                    # jump bound scaled to the speed actually playing.
                    live_speed = speed
                    details = self._playback_details
                    if details:
                        candidate = details.get("speed", speed)
                        if (isinstance(candidate, (int, float)) and math.isfinite(candidate)
                                and .05 <= candidate <= 4):
                            live_speed = float(candidate)
                    allowed = max(128, project.tick_rate * 2,
                                  max(0, now - previous_poll) * project.tick_rate * live_speed * 2 + 4)
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
                self._message("Native shot finished. POV segment paused; finishing recording." if getattr(self, "_pov_active", False)
                              else ("Native shot finished. Replay paused and final camera held. Play shot restarts; Stop / restore returns the game view and HUD." if self._native_active
                                    else "Native shot finished. Replay paused at the final camera. Stop / restore returns the game view and HUD."), playing=False)

    def _finish_playback(self):
        # Camera/cvar framing remains available for inspection until Stop.
        # Background throttling is restored on exit; unsafe HUD reveals stay
        # suppressed until the outer stock-camera handoff.
        try:
            self._require_demo(require_tick=False)
            # Preserve the active speed, including console hotkey changes.
            # Final session cleanup resets speed; editing Stop preserves it.
            self._request("demo_pause")
        except Exception as exc:
            LOG.info("Could not pause replay during playback cleanup: %s", exc)
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

    def _halt(self, *, native_action="handoff", preserve_follow=False):
        self._restore_replay_hud()
        if not preserve_follow:
            self.stop_game_follow()
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
                status = bridge.status()
                if status.get("state") in ("stopped", "probe", "fault"):
                    # Native preparation can release independently of these
                    # cached flags. Acknowledge release before allowing retry;
                    # holding an absent path cannot recover the camera.
                    bridge.release()
                    self._native_active = self._native_manual = False
                    self._invalidate_paused_camera()
                    with self._state_lock:
                        self._state["playing"] = False
                        self._state["paused_flight"] = False
                    return
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
            self._request("demo_pause")
        self._message(restoration_error or "Paused. The current camera and lens values are held.", playing=False)

    def stop(self, *, preserve_layers=False, preserve_speed=False):
        self._halt(native_action="release")
        bridge = self._native_bridge()
        if bridge is not None and callable(getattr(bridge, "editor_status", None)) and self._alive():
            try:
                editor = bridge.editor_status()
                if editor.get("enabled"):
                    # A verified editor needs controls after release. Startup
                    # also calls Stop before loading/probing: keep its panel
                    # closed while retaining console and recovery-key access.
                    owner = ("console" if editor.get("console_open") else
                             "panel" if self._probe_result else "disabled")
                    bridge.configure_editor(owner=owner)
            except (RuntimeError, ValueError, OSError):
                # UI recovery must never prevent the remaining cvar cleanup.
                LOG.exception("Could not open the editor panel after Stop")
        self._invalidate_paused_camera()
        restoration_error = self._restore_playback_settings()
        ui_error = self._restore_game_ui_settings()
        look_error = self._restore_look_values() if self._look_restore else None
        restoration_error = " ".join(part for part in (restoration_error, ui_error, look_error) if part) or None
        if (self._restore or (self._demo_speed_changed and not preserve_speed)) and self._console and self._console.is_connected and self._alive():
            try:
                self._require_demo(require_tick=False)
                commands = []
                for name, value in sorted(self._restore.items()):
                    validate_cvar_name(name)
                    commands.append(name + " " + format_cvar_value(validate_cvar_value(name, value)))
                if self._demo_speed_changed and not preserve_speed:
                    # The editing Stop action keeps the current chosen speed.
                    # Retain ownership so final session cleanup can still reset
                    # this command, which has no readable previous value.
                    commands.append("demo_timescale 1")
                self._request("; ".join(commands))
                self._restore.clear()
                if not preserve_speed:
                    self._demo_speed_changed = False
            except Exception as exc:
                self._message("Could not restore all cvars: " + str(exc))
                return
        with self._state_lock:
            self._state["playing"] = False
        if not preserve_layers and self._console and self._console.is_connected and self._alive():
            try:
                self.end_matte_layer()
            except (RuntimeError, ValueError, OSError) as exc:
                LOG.warning("Could not restore matte settings: %s", exc)
                restoration_error = ((restoration_error or "")
                                     + " Matte settings could not be restored; use Stop / restore to retry. "
                                     + str(exc))
            try:
                self.reset_layer_modes()
            except (RuntimeError, ValueError, OSError) as exc:
                LOG.warning("Could not restore scene classes: %s", exc)
        if restoration_error:
            self._message(restoration_error, playing=False)

    def disconnect(self):
        self._recording_replay = None
        self._recording_pending = False
        bridge = self._native_bridge()
        close_media = getattr(bridge, "_close_media", None)
        if callable(close_media):
            close_media()
        if self._healthbar_restore and self._console and self._console.is_connected and self._alive():
            self._restore_healthbar_values()
        self.stop()
        self.clear_export_resolution()
        dof_error = self._restore_dof_shader_settings()
        if self._console:
            self._console.close()
        self._console = None
        self._invalidate_probe()
        self._reset_camera_position()
        self._message("Disconnected." if not dof_error else "Disconnected. " + dof_error,
                      connected=False, playing=False)

    def _crash_dumps(self, limit: int = 3) -> list:
        """Newest Deadlock breakpad minidumps, newest first. Never raises.

        Breakpad writes the dump beside the game executable (its ``.\\`` is the
        executable folder, not the launcher's working directory), so the
        executable folder is searched as well as the game folder used by
        older/side-by-side launches.
        """
        roots = []
        game_path = str((self._launch_attempt or {}).get("game_path") or "").strip()
        if game_path:
            base = Path(game_path)
            try:
                paths = launcher.validate_game(base)
            except (launcher.LaunchError, OSError):
                # Retain collection from an installation-root selection even
                # if installation files disappeared after the crash.
                roots.append(base if base.name.lower() == "game" else base / "game")
                if base.suffix.casefold() == ".exe":
                    roots.append(base.parent)
                elif base.name.casefold() == "win64" and base.parent.name.casefold() == "bin":
                    roots.append(base)
            else:
                roots.append(paths.game_dir)
                roots.append(paths.executable.parent)
        roots.append(ROOT)
        if self._session:
            roots.append(self._session.session_dir)
        found = {}
        for root in roots:
            try:
                for path in root.glob("deadlock_*.mdmp"):
                    if path.is_file():
                        found[path.resolve()] = path
            except OSError:
                continue
        try:
            return sorted(found.values(), key=lambda item: item.stat().st_mtime,
                          reverse=True)[:limit]
        except OSError:
            return []

    def export_diagnostics(self, destination):
        from .player_layer import DIAGNOSTIC_NAMES, preserve_diagnostics
        if self._session:
            deployment = self.deployment_directory()
            if deployment is not None:
                preserve_diagnostics(deployment, self._session.session_dir)
        session_files = ("session.json", "launch.log", "game_stdout.log",
                         "native_diagnostics.json", "graphics-session.json", "graphics-runtime.json") + DIAGNOSTIC_NAMES
        destination = Path(destination)
        if destination.suffix.lower() != ".zip":
            destination = destination / ("Dolly_diagnostics_" + time.strftime("%Y%m%d_%H%M%S") + ".zip")
        destination.parent.mkdir(parents=True, exist_ok=True)
        report = {"version": __version__, "state": self.status(), "protocol": self._protocol,
                  "motion_clock": motion_clock_info(),
                  "launch_attempt": self._launch_attempt,
                  "startup_evidence": self._startup_evidence,
                  "replay_recovery": deepcopy(self._replay_recovery),
                  "game_exit_code": self._session.process.poll() if self._session else None,
                  "lens_control": self.lens_cvar, "standard_aspect": self.standard_aspect,
                  "aspect_capture": dict(self._aspect_capture), "probe": self._probe_result,
                  "probe_failure": self._probe_failure,
                  "recent_console_responses": self._last_output,
                  "pending_cvar_restoration": self._restore,
                  "pending_look_restoration": dict(self._look_restore),
                  "healthbar_originals": dict(self._healthbar_restore),
                  "floating_healthbars_hidden": self._healthbars_hidden,
                  "pending_playback_restoration": dict(self._playback_restore),
                  "pending_dof_shader_restoration": dict(self._dof_shader_restore),
                  "pending_game_ui_restoration": dict(self._game_ui_restore),
                  "pending_matte_restoration": dict(getattr(self, "_matte_cvar_restore", None) or {}),
                  "pending_layer_restoration": sorted(self._layer_hidden or ()),
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
                  "replay_header": dict(self._replay_header),
                  "replay_tick_rate": self.replay_tick_rate(),
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
        # Game crashes leave breakpad minidumps in the game's working folder.
        # Include the newest ones so a support ZIP does not need a second trip
        # to the game install. Oversized full-memory dumps are skipped.
        dumps = []
        for path in self._crash_dumps():
            try:
                if path.stat().st_size <= 256 * 1024 * 1024:
                    dumps.append(path)
            except OSError:
                continue
        report["crash_dumps"] = [{"name": path.name, "bytes": path.stat().st_size} for path in dumps]
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("diagnostics.json", json.dumps(report, indent=2, ensure_ascii=False))
            if history is not None:
                archive.writestr("logs/recent_console.txt", history)
            for path in (ROOT / "logs").glob("*.log"):
                if path.is_file():
                    archive.writestr("logs/" + path.name, _tail_file(path))
            if self._session:
                for name in session_files:
                    file = self._session.session_dir / name
                    if file.is_file():
                        archive.writestr("session/" + name, _tail_file(file))
            # A successful retry must not hide the previous crashed launch.
            journals = sorted((ROOT / "logs").glob("*/session.json"), key=lambda p: p.parent.name, reverse=True)[:8]
            for journal in journals:
                folder = journal.parent
                if self._session and folder.resolve() == self._session.session_dir.resolve():
                    continue
                for name in session_files:
                    file = folder / name
                    if file.is_file():
                        archive.writestr("previous_sessions/" + folder.name + "/" + name, _tail_file(file, 512_000))
            for path in dumps:
                try:
                    archive.write(path, "crashes/" + path.name)
                except OSError:
                    continue
        self._message("Diagnostics exported to " + str(destination))
        return destination

    def close(self):
        try:
            self.disconnect()
        finally:
            if self._session:
                try:
                    self._session.restore_gameinfo()
                finally:
                    # A daemon thread cannot clean up after the editor exits.
                    # The helper only waits for this launched process to end.
                    self._session.handoff_cleanup()
