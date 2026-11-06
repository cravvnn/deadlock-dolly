"""Owned output contracts absent from the compact game/control workflows."""
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zlib

from dolly import player_encode, reshade_setup, screenshot


def png_rows(path):
    raw = path.read_bytes()
    assert raw[:8] == b'\x89PNG\r\n\x1a\n'
    offset, compressed, header, ended = 8, bytearray(), None, False
    while offset < len(raw):
        size = struct.unpack_from('>I', raw, offset)[0]
        kind = raw[offset + 4:offset + 8]
        data = raw[offset + 8:offset + 8 + size]
        assert struct.unpack_from('>I', raw, offset + 8 + size)[0] == zlib.crc32(kind + data) & 0xffffffff
        if kind == b'IHDR': header = struct.unpack('>2I5B', data)
        if kind == b'IDAT': compressed.extend(data)
        if kind == b'IEND': ended = True
        offset += size + 12
    assert ended and offset == len(raw)
    return header, zlib.decompress(compressed)


class OutputOwnershipTests(unittest.TestCase):
    def test_worker_cleanup_preserves_cancel_and_failure_and_attempts_both_files(self):
        child_code = '''import json,sys,time
from pathlib import Path
request=Path(sys.argv[1]);folder=request.parent
deadline=time.monotonic()+5
while not (folder/'go').exists():
    if time.monotonic()>deadline:raise RuntimeError('missing admission')
    time.sleep(.01)
data=json.loads(request.read_text(encoding='utf-8'))
Path(data['partial']).write_bytes(b'partial')
Path(data['output']).write_bytes(b'staged')
(folder/'result.json').write_text(json.dumps({'ok':False,'error':'encoder refused'}),encoding='utf-8')
'''
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                output, raw = root/'output.mov', root/'capture.bin'
                output.write_bytes(b'prior movie'); raw.write_bytes(b'raw capture')
                event = threading.Event()
                if cancelled: event.set()
                popen, unlink = subprocess.Popen, Path.unlink
                children, owned, attempts = [], [], []
                def start(command, **kwargs):
                    request = Path(command[-1])
                    data = json.loads(request.read_text(encoding='utf-8'))
                    owned.extend([Path(data['partial']), Path(data['output'])])
                    child = popen([sys.executable, '-c', child_code, str(request)], **kwargs)
                    children.append(child)
                    return child
                def cleanup(path, *args, **kwargs):
                    if path in owned:
                        attempts.append(path)
                        if path == owned[0]: raise PermissionError('partial locked')
                    return unlink(path, *args, **kwargs)
                try:
                    with patch.object(player_encode.subprocess, 'Popen', side_effect=start), patch.object(Path, 'unlink', cleanup):
                        expected = InterruptedError if cancelled else RuntimeError
                        with self.assertRaisesRegex(expected, 'cancelled' if cancelled else 'encoder refused'):
                            player_encode.encode_in_worker(root/'unused-ffmpeg', raw, output, cancel=event)
                    self.assertEqual(attempts, owned)
                    self.assertFalse(owned[1].exists())
                    self.assertEqual(output.read_bytes(), b'prior movie')
                    self.assertEqual(raw.read_bytes(), b'raw capture')
                    self.assertTrue(all(p.poll() is not None for p in children))
                finally:
                    for child in children:
                        if child.poll() is None: child.kill(); child.wait(timeout=5)

    def test_still_png_keeps_unowned_partial_and_decodes_matte_and_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root/'image.png'; output.write_bytes(b'prior image')
            legacy = root/'image.png.partial'; legacy.write_bytes(b'unowned partial')
            def failing_rows():
                yield b'\x11\x11'
                raise RuntimeError('rows refused')
            with self.assertRaisesRegex(RuntimeError, 'rows refused'):
                screenshot._png(output, 2, 1, 8, 0, failing_rows())
            self.assertEqual(output.read_bytes(), b'prior image')
            self.assertEqual(legacy.read_bytes(), b'unowned partial')
            pixels = struct.pack('<8e', 0, 0, 0, 1, 0, 0, 0, 0)
            alpha, preview = screenshot.write_hero_stills(2, 1, pixels, root)
            self.assertEqual(png_rows(alpha), ((2, 1, 16, 0, 0, 0, 0), b'\0\xff\xff\0\0'))
            self.assertEqual(png_rows(preview), ((2, 1, 8, 6, 0, 0, 0), b'\0\0\0\0\xff\0\0\0\0'))

    def test_preset_write_failure_retries_and_existing_user_edits_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root/'source'; source.mkdir()
            preset = source/'Deadlock-Dolly.ini'; preset.write_bytes(b'[GENERAL]\nTechniques=Test\n')
            config = root/'user/ReShade.ini'; config.parent.mkdir()
            target = config.parent/'presets'/preset.name
            old_open = Path.open
            class BrokenWrite:
                def __init__(self, stream): self.stream = stream
                def __enter__(self): return self
                def write(self, data):
                    self.stream.write(data[:4]); self.stream.flush()
                    raise OSError('disk refused')
                def __exit__(self, *args): self.stream.close()
            def opening(path, *args, **kwargs):
                stream = old_open(path, *args, **kwargs)
                return BrokenWrite(stream) if args and args[0] == 'xb' else stream
            with patch.object(Path, 'open', opening), self.assertRaisesRegex(OSError, 'disk refused'):
                reshade_setup._install_presets(config, preset)
            self.assertFalse(target.exists())
            _, created = reshade_setup._install_presets(config, preset)
            self.assertEqual(created, [target])
            self.assertEqual(target.read_bytes(), preset.read_bytes())
            target.write_bytes(b'user edits')
            def refuse_new_write(path, *args, **kwargs):
                if args and args[0] == 'xb': raise PermissionError('no new writes')
                return old_open(path, *args, **kwargs)
            with patch.object(Path, 'open', refuse_new_write):
                self.assertEqual(reshade_setup._install_presets(config, preset)[1], [])
            self.assertEqual(target.read_bytes(), b'user edits')


if __name__ == '__main__': unittest.main()
