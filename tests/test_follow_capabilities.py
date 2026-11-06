import struct
import unittest
from unittest.mock import Mock, patch

from dolly.follow_camera import BOUNDS, PREFIX, ENABLED, FOLLOW_AIM
from dolly.follow_capabilities import (FollowCapabilityMonitor, REFS, BLOCKED_FLAGS,
                                       OWN_HEALTH_HUD, OWN_HEALTH_SPANS)
from dolly._runtime_generated import FollowCapabilities as _profile
from dolly.preload import PreloadError


class FollowCapabilityTests(unittest.TestCase):
    def health_monitor(self):
        monitor, blocks, headers = self.monitor()
        monitor._client_data = b'reviewed image'
        header = bytearray(0x5c)
        struct.pack_into('<Q', header, 0, 0x900100)
        struct.pack_into('<Q', header, 0x30, 0x80000)
        blocks[monitor.base + _profile.OWN_HEALTH_REF] = struct.pack('<QQ', 7, 0x900000)
        blocks[0x900000] = header
        blocks[0x900100] = OWN_HEALTH_HUD.encode() + b'\0'
        for rva, size in OWN_HEALTH_SPANS:
            blocks[monitor.base + rva] = b'\x01' * size
        return monitor, blocks, header

    def test_health_panel_release_bool_and_checkbox_metadata_are_supported(self):
        monitor, _, header = self.health_monitor()
        with patch('dolly.follow_capabilities._image_bytes', side_effect=lambda _data, _rva, size: b'\x01' * size):
            self.assertEqual(monitor.sample_own_health()['value'], 0)
            header[0x58] = 1
            struct.pack_into('<Q', header, 0x30, 0x40080008)
            self.assertEqual(monitor.sample_own_health()['value'], 1)

    def test_health_panel_changed_code_type_flags_or_value_refused(self):
        for offset, fmt, value in ((0x28, '<H', 7), (0x30, '<Q', 0x84000),
                                  (0x30, '<Q', 0x80080008), (0x30, '<Q', 0x10080008),
                                  (0x30, '<Q', 0x40088008),
                                  (0x30, '<Q', 0x80), (0x58, '<B', 2)):
            monitor, _, header = self.health_monitor()
            struct.pack_into(fmt, header, offset, value)
            with self.subTest(offset=offset, value=value), patch(
                    'dolly.follow_capabilities._image_bytes', side_effect=lambda _data, _rva, size: b'\x01' * size):
                with self.assertRaises(PreloadError):
                    monitor.sample_own_health()
        monitor, _, _ = self.health_monitor()
        with patch('dolly.follow_capabilities._image_bytes', return_value=b'changed'):
            with self.assertRaisesRegex(PreloadError, 'panel code differs'):
                monitor.sample_own_health()

    def monitor(self):
        monitor = FollowCapabilityMonitor.__new__(FollowCapabilityMonitor)
        monitor.base = 0x180000000
        monitor._owned = Mock()
        refs = {PREFIX + name: (rva, 9) for name, rva in REFS.items()}
        refs.update({ENABLED: (_profile.ENABLED_REF, 0), FOLLOW_AIM: (_profile.FOLLOW_AIM_REF, 0)})
        blocks, headers = {}, {}
        for i, (name, (rva, value_type)) in enumerate(refs.items()):
            data = 0x200000 + i * 0x1000
            name_address = data + 0x100
            header = bytearray(0x5c)
            struct.pack_into('<Q', header, 0, name_address)
            struct.pack_into('<H', header, 0x28, value_type)
            struct.pack_into('<Q', header, 0x30, 0x80080)
            if value_type:
                low, high = BOUNDS[name[len(PREFIX):]]
                struct.pack_into('<f', header, 0x58, (low + high) / 2)
            else:
                header[0x58] = 1
            blocks[monitor.base + rva] = struct.pack('<QQ', i, data)
            blocks[data] = header
            blocks[name_address] = name.encode() + b'\0'
            headers[name] = header
        monitor.memory = Mock()
        monitor.memory.read.side_effect = lambda address, size: bytes(blocks[address])[:size]
        return monitor, blocks, headers

    def test_complete_typed_snapshot(self):
        monitor, _, _ = self.monitor()
        values = monitor.sample()
        self.assertEqual(len(values), 19)
        self.assertEqual(values[ENABLED]['type'], 0)
        self.assertEqual(values[PREFIX + 'x_offset']['type'], 9)
        self.assertEqual(monitor._owned.call_count, 2)

    def test_wrong_type_and_blocked_flags_fail_closed(self):
        for offset, fmt, value in ((0x28, '<H', 3), (0x30, '<Q', 0x80080 | BLOCKED_FLAGS),
                                   (0x30, '<Q', 0x80)):
            monitor, _, headers = self.monitor()
            struct.pack_into(fmt, headers[PREFIX + 'x_offset'], offset, value)
            with self.subTest(value=value), self.assertRaises(PreloadError):
                monitor.sample()

    def test_invalid_values_fail_closed(self):
        for name, value in ((ENABLED, 2), (PREFIX + 'x_offset', float('nan')),
                            (PREFIX + 'x_offset', 1000)):
            monitor, _, headers = self.monitor()
            if name == ENABLED:
                headers[name][0x58] = value
            else:
                struct.pack_into('<f', headers[name], 0x58, value)
            with self.subTest(name=name, value=value), self.assertRaises(PreloadError):
                monitor.sample()

    def test_season2_float_type_is_exact_and_old_type_is_refused(self):
        monitor, _, headers = self.monitor()
        self.assertEqual(_profile.FLOAT_TYPE, 9)
        self.assertEqual(len(monitor.sample()), len(REFS) + 2)
        for old_type in (3, 7, 15):
            struct.pack_into('<H', headers[PREFIX + 'x_offset'], 0x28, old_type)
            with self.subTest(type=old_type), self.assertRaisesRegex(PreloadError, 'identity/type differs'):
                monitor.sample()

    def test_name_mismatch_and_unregistered_refs_refused(self):
        monitor, blocks, _ = self.monitor()
        address = next(address for address, data in blocks.items() if bytes(data).startswith(PREFIX.encode()))
        blocks[address] = b'x' + blocks[address][1:]
        with self.assertRaisesRegex(PreloadError, 'identity/type'):
            monitor.sample()
        monitor, blocks, _ = self.monitor()
        blocks[monitor.base + REFS['x_offset']] = struct.pack('<QQ', 0xffffffff, 0)
        with self.assertRaisesRegex(PreloadError, 'not registered'):
            monitor.sample()
        blocks[monitor.base + REFS['x_offset']] = struct.pack('<QQ', 0x1234ffff, 0x200000)
        with self.assertRaisesRegex(PreloadError, 'not registered'):
            monitor.sample()

    def test_torn_reads_never_report_capabilities(self):
        monitor, blocks, _ = self.monitor()
        seen = set()

        def read(address, size):
            raw = bytes(blocks[address])[:size]
            if address in seen:
                return b'\xff' + raw[1:]
            seen.add(address)
            return raw

        monitor.memory.read.side_effect = read
        with self.assertRaisesRegex(PreloadError, 'changed during'):
            monitor.sample()

    def test_closed_or_unowned_monitor_does_not_read(self):
        monitor, _, _ = self.monitor()
        monitor._owned.side_effect = PreloadError('unowned')
        with self.assertRaises(PreloadError):
            monitor.sample()
        monitor.memory.read.assert_not_called()
        monitor._owned.side_effect = None
        monitor.memory = None
        with self.assertRaisesRegex(PreloadError, 'closed'):
            monitor.sample()
