import io
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np
from dolly.environment_frame import HEADER, export_frame
from dolly.environment_hdr import read_float_rgb_exr


class FrameTransferTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.output = Path(temp.name)/'frame.exr'
        self.token = bytes([7])+bytes(15)
        self.shader = bytes(range(32))
        self.pixels = np.array([[[8, -.5, 2, 1], [4, 16, .125, 1]]], dtype='<f2')

    def packet(self, **changes):
        values = dict(magic=b'DLYHDR01', abi=1, size=88, token=self.token,
                      width=2, height=1, count=16, view=1.25, post=1.865, digest=self.shader)
        values.update(changes)
        return HEADER.pack(*values.values())+self.pixels.tobytes()

    def export(self, packet, **kwargs):
        return export_frame(io.BytesIO(packet), self.output, request=self.token,
                            shader_sha256=self.shader, **kwargs)

    def test_fragmented_transport_preserves_hdr_and_metadata(self):
        class Fragmented(io.BytesIO):
            def read(self, n): return super().read(min(n, 3))
        result = export_frame(Fragmented(self.packet()), self.output, request=self.token,
                              shader_sha256=self.shader)
        np.testing.assert_array_equal(read_float_rgb_exr(self.output, 2, 1), self.pixels[:,:,:3])
        self.assertEqual(result['exposure_view'], 1.25)
        self.assertEqual(result['color_encoding'], 'unqualified_pre_tonemap_rgba16f')

    def test_header_rejection_never_reads_or_allocates_payload(self):
        class HeaderOnly(io.BytesIO):
            def read(self, n):
                if self.tell() >= 88: raise AssertionError('Payload must not be read')
                return super().read(n)
        for change in (dict(magic=b'wrong!!!'), dict(abi=2), dict(size=96),
                       dict(token=bytes(16)), dict(digest=bytes(32)), dict(width=4097),
                       dict(height=0), dict(count=2**63), dict(view=float('nan')),
                       dict(post=0)):
            with self.subTest(change=change), self.assertRaises(ValueError):
                export_frame(HeaderOnly(self.packet(**change)), self.output,
                             request=self.token, shader_sha256=self.shader)
            self.assertFalse(self.output.exists())

    def test_truncated_or_extra_data_never_publishes(self):
        packet=self.packet()
        for data in (packet[:7], packet[:87], packet[:-1], packet+b'x'):
            with self.assertRaises(ValueError): self.export(data)
            self.assertFalse(self.output.exists())

    def test_cancellation_during_transfer_never_publishes(self):
        calls=0
        def cancelled():
            nonlocal calls
            calls+=1
            return calls>=2
        with self.assertRaises(InterruptedError): self.export(self.packet(), cancelled=cancelled)
        self.assertFalse(self.output.exists())

    def test_bad_rgb_is_not_hidden_by_valid_header(self):
        self.pixels[0,0,0]=np.inf
        with self.assertRaises(ValueError): self.export(self.packet())
        self.assertFalse(self.output.exists())

    def test_existing_export_is_never_overwritten(self):
        self.output.write_bytes(b'original')
        with self.assertRaises(FileExistsError): self.export(self.packet())
        self.assertEqual(self.output.read_bytes(), b'original')

    def test_wire_offsets_are_fixed(self):
        packet=self.packet()
        self.assertEqual(len(packet), 104)
        self.assertEqual(packet[16:32], self.token)
        self.assertEqual(struct.unpack('<IIQff',packet[32:56]),
                         (2,1,16,1.25,struct.unpack('<f',struct.pack('<f',1.865))[0]))
        self.assertEqual(packet[56:88], self.shader)
