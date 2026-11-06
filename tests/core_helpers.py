from pathlib import Path
from collections import deque

class FakeConsole:
    def __init__(self):
        self.is_connected = True
        self.demo_name = "example.dem"
        self.tick = 100
        self.demo_outputs = deque()
        self.demo_output = None
        self.goto_outputs = deque()
        self.goto_output = None
        self.goto_seek_output = ""
        self.requests = []
        self.sent = []
        self.events = []
        self.operations = []
        self.resume_snapshots = []
        self.paused = True
        self.unhide_output = "Removed hidden flags from 120 concommands\nRemoved hidden flags from 340 cvars"
        self.complete_replay_on_send = False
        self.values = {
            "r_aspectratio": 0.0,
            "citadel_camera_fov": 90.0,
            "citadel_camera_spectator_fov": 80.0,
            "citadel_spectator_mode": 0.0,
            "r_citadel_depthoffield_enable": 0.0,
            "r_citadel_depthoffield_focus_distance": 600.0,
            "r_citadel_depthoffield_aperture_diameter": 0.5,
            "r_depth_of_field": 1.0,
            "demo_timescale": 0.5,
            "citadel_hud_visible": 1.0,
            "citadel_hide_replay_hud": 0.0,
            "hud_free_cursor": -1.0,
            "engine_no_focus_sleep": 20.0,
        }
        self.pose = [1, 2, 3, 4, 5]
        self.closed = False
        self.fail_commands = set()

    def connect(self, **kwargs):
        self.is_connected = True

    def close(self):
        self.is_connected = False
        self.closed = True

    @property
    def camera_writes(self):
        return [command for command in self.requests + self.sent if command.startswith("spec_goto ")]

    def supports(self, name):
        return True

    def send(self, command):
        self.sent.append(command)
        self.events.append(command)
        if command.startswith('playdemo "') and self.complete_replay_on_send:
            self.demo_name = Path(command[len('playdemo "'):-1]).name
            self.demo_output = None
        self._execute(command)

    def _execute(self, command):
        for item in command.split(";"):
            parts = item.strip().split()
            if not parts:
                continue
            if parts[0] == "demo_gototick":
                self.tick = int(parts[1])
            elif parts[0] == "spec_goto":
                self.pose = [float(value) for value in parts[1:6]]
            elif parts[0] == "demo_pause":
                self.paused = True
            elif parts[0] == "demo_resume":
                self.paused = False
                self.resume_snapshots.append({"tick": self.tick, "pose": list(self.pose),
                                              "values": dict(self.values)})
            elif parts[0] in self.values and len(parts) == 2:
                self.values[parts[0]] = float(parts[1])

    def request(self, command, timeout=3, completion_patterns=None, allow_truncated=False):
        self.requests.append(command)
        self.events.append(command)
        if command in self.fail_commands:
            raise RuntimeError("Simulated console failure")
        # The engine executes semicolon-separated commands in order. A camera
        # batch may write the pose/lens and then return demo_goto's status.
        outputs = []
        for item in command.split(";"):
            item = item.strip()
            if not item:
                continue
            self.operations.append(item)
            if item in self.fail_commands:
                raise RuntimeError("Simulated console failure")
            output = self._request_item(item)
            if output:
                outputs.append(output)
        return "\n".join(outputs)

    def _request_item(self, command):
        if command.startswith("demo_gototick "):
            self._execute(command)
            return self.goto_seek_output
        if command == "demo_goto":
            if self.goto_outputs:
                self.goto_output = self.goto_outputs.popleft()
                if isinstance(self.goto_output, int):
                    self.tick = self.goto_output
            if isinstance(self.goto_output, int):
                return (f"Currently playing {self.tick} of 161418 ticks. "
                        f"Minutes:42.04 File:{self.demo_name}")
            return self.goto_output or ""
        if command == "demo_info":
            if self.demo_outputs:
                item = self.demo_outputs.popleft()
                if isinstance(item, int):
                    self.tick = item
                else:
                    self.demo_output = item
            return self.demo_output if self.demo_output is not None else (
                f"[Demo] Playing back demo: '{self.demo_name}' at tick {self.tick}")
        if command == "spec_pos":
            return "[Console] spec_goto " + " ".join(map(str, self.pose))
        if command == "cvar_unhide":
            return self.unhide_output
        if command in self.values:
            return f'[Console] "{command}" = "{self.values[command]}" ( def. "12345" )'
        self._execute(command)
        return ""


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now


class CountedEvent:
    """Virtual Event; an optional wait limit cancels finite playback loops."""
    def __init__(self, clock, limit=None):
        self.clock = clock
        self.limit = limit
        self.waits = 0
        self.stopped = False

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def clear(self):
        self.stopped = False

    def wait(self, timeout):
        if self.stopped:
            return True
        self.clock.now += timeout
        self.waits += 1
        if self.limit is not None and self.waits >= self.limit:
            self.stopped = True
        return self.stopped
