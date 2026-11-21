import struct
import unittest
from types import SimpleNamespace

from dolly._runtime_generated import FollowTarget as profile
from dolly.replay_player_focus import ReplayPlayerUnavailable, resolve_participant


BASE = 0x140000000
SYSTEM = 0x10000000
CHUNK = 0x20000000
CONTROLLER = 0x30000000
IDENTIFIER = 3


class FakeMemory:
    def __init__(self):
        self.values = {}

    def put_pointer(self, address, value):
        self.values[address] = struct.pack('<Q', value)

    def put_uint(self, address, value):
        self.values[address] = struct.pack('<I', value)

    def read(self, address, size):
        value = self.values.get(address, b'')
        return (value + bytes(size))[:size]


def monitor_with(memory):
    return SimpleNamespace(base=BASE, memory=memory, _owned=lambda: None)


class ResolveParticipantTests(unittest.TestCase):
    def setUp(self):
        self.memory = FakeMemory()
        self.memory.put_pointer(BASE + profile.ENTITY_LIST, SYSTEM)
        self.identity = CHUNK + profile.IDENTITY_STRIDE*IDENTIFIER

    def resolve(self):
        evidence = {}
        with self.assertRaises(RuntimeError) as caught:
            resolve_participant(monitor_with(self.memory), IDENTIFIER, {'players': []}, evidence=evidence)
        return caught.exception, evidence

    def test_missing_controller_chunk_stays_unavailable(self):
        error, evidence = self.resolve()
        self.assertIsInstance(error, ReplayPlayerUnavailable)
        self.assertIn('unavailable at this tick', str(error))
        self.assertEqual(evidence['primary_unavailable'], str(error))

    def test_missing_controller_pointer_stays_unavailable(self):
        self.memory.put_pointer(SYSTEM, CHUNK)
        self.memory.put_uint(self.identity + profile.IDENTITY_HANDLE, IDENTIFIER)
        error, evidence = self.resolve()
        self.assertIsInstance(error, ReplayPlayerUnavailable)
        self.assertEqual(evidence['primary_unavailable'], str(error))

    def test_controller_with_wrong_type_still_reports_change(self):
        self.memory.put_pointer(SYSTEM, CHUNK)
        self.memory.put_pointer(self.identity, CONTROLLER)
        self.memory.put_uint(self.identity + profile.IDENTITY_HANDLE, IDENTIFIER)
        self.memory.put_pointer(CONTROLLER + profile.ENTITY_IDENTITY, self.identity)
        self.memory.put_pointer(CONTROLLER, BASE + 8)
        error, _ = self.resolve()
        self.assertNotIsInstance(error, ReplayPlayerUnavailable)
        self.assertIn('controller changed', str(error))


if __name__ == '__main__':
    unittest.main()
