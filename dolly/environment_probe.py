"""Opt-in, bounded GPU binding diagnostics for the environment edition.

No image recording, renderer controls, or user configuration changes. The owned
controller must verify its replay before arming; native checks its own ownership,
paused view, heartbeat, shader identity and strict observation limits as well.
"""
import json
import struct
import time
from .native_errors import NativeBridgeError

REQUEST_OFFSET = 2 * 1024 * 1024 + 24576
STATUS_OFFSET = 2 * 1024 * 1024 + 24640
REQUEST = struct.Struct("<8s10I")
STATUS = struct.Struct("<8s4I1024s")


def measure_source(bridge, check, *, clock=time.monotonic, sleep=time.sleep):
    """Measure the actual HDR resource, which may differ from a borderless window.

    Eight Present boundaries, no pixels copied. Native ownership checks remain
    active and the caller revalidates its paused replay throughout.
    """
    check()
    probe=EnvironmentProbe(bridge)
    command=probe.start(frames=8,draws=100000,milliseconds=5000)
    try:
        deadline=clock()+7
        while clock()<deadline:
            check()
            sample=probe.status()
            if sample and sample['command']>command:
                raise RuntimeError('HDR source observation was replaced')
            if sample and sample['command']==command and sample['state']!=1:
                data=sample['details']
                # candidates accumulates across Present boundaries, not just
                # one frame. This is size discovery; the actual acquisition
                # independently checks its exact shader, bounds and frame.
                if (sample['state']!=2 or data.get('source_state')!=9 or
                    data.get('candidates',0)<1 or data.get('ambiguous_frames')!=0):
                    # source_state 6 is UnsupportedViewport: the HDR texture was
                    # found but its render viewport is smaller (upscaling/FSR/DLSS
                    # or a render scale), so the exact source cannot be pinned.
                    # The caller forces full render scale first; if it still
                    # fails, tell the user the one setting to change.
                    if data.get('source_state')==6:
                        raise RuntimeError(
                            'The game is rendering the scene below the HDR texture size '
                            '(viewport '+str(data.get('viewport'))+' vs texture '
                            +str(data.get('width'))+'x'+str(data.get('height'))
                            +'), so the exact source could not be established. Turn off '
                            'upscaling (FSR/DLSS) or set render scale to 100% in Deadlock, '
                            'then capture again. Details: '+str(sample))
                    raise RuntimeError('HDR source dimensions were not established: '+str(sample))
                return data
            sleep(.025)
        raise TimeoutError('HDR source observation timed out')
    finally:
        sample=probe.status()
        if sample is None or sample['command']<=command:
            stop=probe.stop()
            deadline=clock()+2
            while clock()<deadline:
                sample=probe.status()
                if sample and sample['command']==stop and sample['state']==3:break
                sleep(.025)
            else:raise TimeoutError('HDR source observation stop was not acknowledged')


class EnvironmentProbe:
    def __init__(self, bridge):
        self.bridge = bridge
        self.command = 0

    def _send(self, enabled, frames, draws, milliseconds):
        bridge = self.bridge
        with bridge._lock:
            if bridge._closed or not bridge.game_pid:
                raise NativeBridgeError("Environment probe requires an owned game session")
            # Read the shared counter so a replacement wrapper cannot replay an
            # already consumed command. Serialize with all other bridge writers.
            current = bytes(bridge._mapping[REQUEST_OFFSET:REQUEST_OFFSET + REQUEST.size])
            values = REQUEST.unpack(current)
            command = values[3] + 1 if values[0] == b"DLYENVR1" else 1
            if command > 0xffffffff:
                raise NativeBridgeError("Environment probe counter exhausted")
            odd = ((values[1] | 1) + 2) & 0xffffffff
            packet = REQUEST.pack(b"DLYENVR1", odd, 1, command, int(enabled),
                                  bridge.editor_pid, bridge.game_pid, frames, draws, milliseconds, 0)
            bridge._store(REQUEST_OFFSET + 8, odd)
            bridge._mapping[REQUEST_OFFSET:REQUEST_OFFSET + 8] = packet[:8]
            bridge._mapping[REQUEST_OFFSET + 12:REQUEST_OFFSET + REQUEST.size] = packet[12:]
            bridge._store(REQUEST_OFFSET + 8, (odd + 1) & 0xffffffff)
            self.command = command
        return command

    def start(self, *, frames=8, draws=100000, milliseconds=5000):
        for name, value, limit in (("frames", frames, 64), ("draws", draws, 100000),
                                   ("milliseconds", milliseconds, 10000)):
            if type(value) is not int or not 1 <= value <= limit:
                raise ValueError(f"{name} must be an integer from 1 to {limit}")
        return self._send(True, frames, draws, milliseconds)

    def stop(self):
        return self._send(False, 0, 0, 0)

    def status(self):
        bridge = self.bridge
        with bridge._lock:
            if bridge._closed:
                raise NativeBridgeError("Environment probe mapping is closed")
            for _ in range(8):
                before = bridge._load_sequence(STATUS_OFFSET + 8)
                if before & 1:
                    continue
                data = bytes(bridge._mapping[STATUS_OFFSET:STATUS_OFFSET + STATUS.size])
                after = bridge._load_sequence(STATUS_OFFSET + 8)
                magic, sequence, abi, command, state, payload = STATUS.unpack(data)
                if before != after or before != sequence or after & 1:
                    continue
                if magic != b"DLYENVS1" or abi != 1:
                    return None
                try:
                    details = json.loads(payload.split(b"\0", 1)[0])
                except (ValueError, UnicodeError) as exc:
                    raise NativeBridgeError("Invalid environment probe diagnostics") from exc
                return dict(command=command, state=state, details=details)
        raise NativeBridgeError("Environment probe diagnostics remained busy")
