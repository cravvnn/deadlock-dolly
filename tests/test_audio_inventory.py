"""Audio completion/voice evidence and VPK inventory owner regressions."""
import binascii
import csv
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf
from dolly import clip_audio, movie_audio, panorama_assets, dof_preflight
from tools import filter_captured_audio_window as filtering
from tools import export_captured_audio as exporting


class AudioInventoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='dolly-audio-inventory-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_audio_work_cleanup_keeps_completion_and_primary_stop_error(self):
        for mode in ('fixed', 'loopback', 'cancel'):
            with self.subTest(mode=mode):
                directory = self.root / mode
                directory.mkdir()
                controller = NS(movie_output_context=lambda: movie_audio.MovieOutputContext(
                    directory / 'movie', lambda owner: None))
                with patch.object(clip_audio, '_ffmpeg', return_value=Path(sys.executable)):
                    owner = clip_audio.ClipAudioCapture(directory / 'take.mp4', game_audio=True,
                        reconstructed=False, bridge=NS(), game=directory / 'game/bin/win64/deadlock.exe',
                        game_pid=1, controller=controller, fixed_step=mode == 'fixed')
                self.addCleanup(owner.temporary.cleanup)
                owner.video.write_bytes(b'silent master')
                sf.write(owner.reference, np.zeros((480, 2)), 48000, subtype='FLOAT')
                Path(str(owner.reference) + '.clock.json').write_text(
                    json.dumps(dict(first_sample_qpc_seconds=1)), encoding='utf-8')
                if mode == 'fixed':
                    owner.movie = NS(movie_engine_seconds=1, finish=lambda *args, **kwargs: owner.reference)
                primary = RuntimeError('source stop refused')
                if mode == 'cancel':
                    owner.voice_started = True
                    owner.bridge = NS(stop_source_audio=lambda: (_ for _ in ()).throw(primary))
                def encode(command, **kwargs):
                    Path(command[-1]).write_bytes(b'controlled mux output')
                    return NS(returncode=0, stderr='')
                with patch.object(owner.temporary, 'cleanup', side_effect=PermissionError('work retained')), \
                        patch.object(clip_audio.subprocess, 'run', side_effect=encode), \
                        self.assertLogs('dolly', level='WARNING'):
                    if mode == 'cancel':
                        with self.assertRaises(RuntimeError) as caught:
                            owner.cancel()
                        self.assertIs(caught.exception, primary)
                    else:
                        self.assertIsNone(owner.finish(dict(video_first_qpc=1000,
                            qpc_frequency=1000, duration=.01)))
                        self.assertEqual(owner.video.read_bytes(), b'controlled mux output')
                        self.assertEqual(sf.info(directory / 'take_game_audio.wav').frames, 480)
                self.assertTrue(owner.work.is_dir())
                owner.temporary.cleanup()
                self.assertFalse(owner.work.exists())

    def test_voice_filter_keeps_any_overlapping_lifetime_and_export_evidence(self):
        reference = self.root / 'reference.wav'
        sf.write(reference, np.zeros((9600, 2)), 48000)
        reference.with_suffix('.json').write_text(json.dumps(dict(
            first_block_end_monotonic=1.05 + 1024/48000, loopback_block_frames=1024)), encoding='utf-8')
        schedules = {
            'early': [('start', 1100), ('stop', 1200), ('start', 1400), ('stop', 1500)],
            'late': [('start', 800), ('stop', 900), ('start', 1100), ('stop', 1200)],
            'orphan': [('stop', 900), ('start', 1100)],
            'unmatched': [('start', 1000), ('start', 1400), ('stop', 1500)],
            'none': [('start', 800), ('stop', 900), ('start', 1400), ('stop', 1500)],
            'boundary': [('start', 1000), ('stop', 1050), ('start', 1250), ('stop', 1300)],
            'reordered': [('start', 1400), ('stop', 1500), ('start', 1100), ('stop', 1200)],
        }
        for case, events in schedules.items():
            with self.subTest(case=case):
                log, output = self.root / (case + '.csv'), self.root / (case + '-filtered.csv')
                with log.open('w', encoding='utf-8', newline='') as stream:
                    stream.write('# qpc_frequency=1000; capture_start_qpc=500\n')
                    writer = csv.writer(stream, lineterminator='\n')
                    writer.writerow(['kind', 'qpc_ticks', 'voice_slot', 'voice_id', 'demo_tick',
                        'engine_seconds', 'soundevent', 'vsnd_path'])
                    for kind, stamp in events:
                        writer.writerow([kind, stamp, 1, 7, 10, 1, 'quoted, voice', 'sounds/a.vsnd'])
                    stream.write('# capture_end_qpc=2000; dropped=0; write_error=0\n')
                report = filtering.filter_window(log, reference, output)
                selected = case not in ('none', 'boundary')
                self.assertEqual((report['qpc_start'], report['qpc_end']), (1050, 1250))
                self.assertEqual(report['selected_voices'], int(selected))
                _, rows, _ = exporting._events(output)
                self.assertEqual(len(rows), len(events) if selected else 0)
                if selected:
                    self.assertEqual([(row['kind'], int(row['qpc_ticks'])) for row in rows], events)
                if case == 'early':
                    def convert(asset, destination, **kwargs):
                        sf.write(destination, np.zeros((480, 2)), 48000, subtype='PCM_16')
                    with patch.object(exporting, '_convert_asset', side_effect=convert):
                        result = exporting.export(output, self.root / 'export', vpk=self.root / 'owned.vpk',
                            viewer=Path(sys.executable), ffmpeg=Path(sys.executable))
                    self.assertEqual((result['voice_starts'], result['voice_stops_matched']), (2, 2))
                    self.assertEqual(result['orphan_stops'], 0)

    def test_vpk_listing_keeps_preload_payload_and_rejects_duplicates(self):
        index = self.root / 'pak01_dir.vpk'
        def write(names):
            tree, payloads = b'vmat_c\0materials/dev\0', {}
            for name in names:
                payload = (name + ' preload bytes').encode()
                payloads['materials/dev/' + name + '.vmat_c'] = payload
                tree += name.encode() + b'\0' + struct.pack('<IHHIIH', binascii.crc32(payload),
                    len(payload), 0x7fff, 0, 0, 0xffff) + payload
            # A truly empty inline entry remains omitted.
            tree += b'dof1_empty\0' + struct.pack('<IHHIIH', 0, 0, 0x7fff, 0, 0, 0xffff)
            tree += b'\0\0\0'
            index.write_bytes(struct.pack('<III', 0x55aa1234, 1, len(tree)) + tree)
            return payloads
        payloads = write(('dof1_general', 'dof1_blur'))
        self.assertEqual(panorama_assets.read_vpk_entries(index, tuple(payloads)), payloads)
        self.assertEqual(set(panorama_assets.list_vpk_entries(index, prefixes=('materials/dev/',))), set(payloads))
        _, materials, problems = dof_preflight._materials(self.root)
        self.assertFalse(problems)
        self.assertTrue(all(materials.values()))
        with self.assertRaisesRegex(ValueError, 'limit'):
            panorama_assets.list_vpk_entries(index, prefixes=('materials/dev/',), max_entries=1)
        write(('dof1_general', 'dof1_general'))
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            panorama_assets.list_vpk_entries(index, prefixes=('materials/dev/',))
