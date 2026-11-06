"""Native boundary fixtures; console semantics share the core transport fake."""
from copy import deepcopy
from core_helpers import FakeConsole as ConsoleBase, CountedEvent


class FakeConsole(ConsoleBase):
    def __init__(self):
        super().__init__()
        self.replay_loaded = True

    def send(self, command):
        if command.startswith('playdemo "') and self.complete_replay_on_send:
            self.replay_loaded = True
            self.tick = 3
            self.goto_output = None
        return super().send(command)

    def _request_item(self, command):
        if command == 'disconnect':
            self.replay_loaded = False
            return ''
        if not self.replay_loaded and command in ('demo_goto', 'demo_info'):
            return 'Error - Not currently playing back a demo.'
        return super()._request_item(command)


class Clock:
    now = 0.0

    def advance(self, seconds):
        self.now += seconds
        return False


class Bridge:
    def __init__(self, console, clock):
        self.console, self.clock = console, clock
        self.project = None
        self.state = "probe"
        self.phase = 0.0
        self.frame_count = 0
        self.events = []
        self.requests = []
        self.fail_prepare = False
        self.fail_handoff = False
        self.freeze_frames = False
        self.change_demo = False
        self.rewind = False
        self.frozen = False
        self.effect_originals = {}
        self.effect_samples = []
        self.roll = 0
        self.manual_pose = None
        self.original_fov = 90.0
        request = console.request

        def record(command, **kwargs):
            self.requests.append((self.state, command))
            self.events.append(command)
            for item in command.split(";"):
                if item.strip().startswith("cl_citadel_forceangles "):
                    self.roll = float(item.split()[-1])
            return request(command, **kwargs)

        console.request = record

    def start_flight(self, demo_name, pose=None, **kwargs):
        self.events.append("native.start_flight")
        self.manual_pose = list(pose) if pose is not None else list(self.console.pose) + [self.roll, self.console.values["r_aspectratio"]]
        self.state = "armed"
        return self.status()

    def prepare(self, project, start, speed, frozen, demo_name, timeout=5):
        self.events.append("native.prepare")
        if self.fail_prepare:
            raise RuntimeError("Native prepare timed out")
        self.project = deepcopy(project)
        self.manual_pose = None
        self.effect_originals = {name:self.console.values[name] for name in project.evaluate(start)["cvars"]}
        self.phase, self.speed, self.frozen = start, speed, frozen
        self.state = "armed"

    def play(self, timeout=3):
        self.events.append("native.play")
        self.state = "playing"

    def hold(self, timeout=3):
        self.events.append("native.hold")
        self.state = "armed"

    def release(self, timeout=3):
        self.events.append("native.release")
        self.state = "stopped"
        self.console.values.update(self.effect_originals)
        self.effect_originals = {}

    def status(self):
        self.clock.advance(.008)
        if not self.freeze_frames:
            self.frame_count += 1
            if self.state == "playing" and (not self.console.paused or self.frozen):
                self.phase = min(self.project.duration, self.phase + .125)
                if not self.frozen:
                    self.console.tick = self.project.start_tick + round(self.phase * self.project.tick_rate)
                if self.phase == self.project.duration:
                    self.state = "completed"
        if self.change_demo and self.state == "playing":
            self.console.demo_name = "another.dem"
        if self.rewind and self.phase > .25:
            self.phase = 0
        frame = self.project.evaluate(self.phase) if self.project else dict(x=0, y=0, z=0, pitch=0, yaw=0, roll=0, aspect_ratio=16/9)
        applied = self.manual_pose if self.manual_pose is not None else [frame[k] for k in ("x", "y", "z", "pitch", "yaw", "roll", "aspect_ratio")]
        effects = self.project.evaluate(self.phase)["cvars"] if self.project and self.state in ("armed", "playing", "completed") else {}
        self.console.values.update(effects)
        if effects:self.effect_samples.append((self.phase, dict(effects)))
        original = self.console.pose + [self.roll, self.console.values["r_aspectratio"]]
        if self.fail_handoff:
            original[0] += 100
        return {"effect_count": len(effects), "effect_error": 0, "effect_frames": len(self.effect_samples), "effect_phase": self.phase, "state": self.state, "phase": self.phase, "frame_count": self.frame_count,
            "tick": self.console.tick, "paused": self.console.paused,
            "demo_name": self.console.demo_name, "game_pid": 1234,
            "original_pose": original,
            "applied_pose": applied, "original_fov": self.original_fov, "applied_fov": self.original_fov,
            "frame_interval_ms": 8, "max_frame_interval_ms": 8}
