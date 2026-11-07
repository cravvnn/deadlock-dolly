"""Reconstructed-audio and camera export boundary contracts."""
import contextlib
import csv
import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import wave

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly
from tools import fit_reference_voices
from tools import export_captured_audio, filter_captured_audio_window
from dolly import camera_export, clip_audio, runtime, video_export


def voice_log(path, assets=('sounds/a.vsnd',)):
    with path.open('w', encoding='utf-8', newline='') as stream:
        stream.write('# qpc_frequency=1000; capture_start_qpc=1000\n')
        writer = csv.writer(stream, lineterminator='\n')
        writer.writerow(['kind', 'qpc_ticks', 'voice_slot', 'voice_id', 'demo_tick',
                         'engine_seconds', 'soundevent', 'vsnd_path'])
        for n, asset in enumerate(assets):
            writer.writerow(['start', 1050 + n * 10, n, n, 10, 0, 'quoted, event', asset])
            writer.writerow(['stop', 1100 + n * 10, n, n, 11, .1, 'quoted, event', asset])
        stream.write('# capture_end_qpc=2000; dropped=0; write_error=0\n')


def pcm(path):
    with wave.open(str(path), 'wb') as output:
        output.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
        output.writeframes(bytes(1920))


def fit_fixture(root, *, channel='left', rate=48000, loopback=False):
    source = root / 'sources'
    (source / 'sounds').mkdir(parents=True)
    values = (np.random.default_rng(61107).normal(size=4096) * .05).astype('float32')
    samples = np.column_stack((values, np.zeros_like(values)))
    if channel == 'right':
        samples = samples[:, ::-1]
    elif channel == 'both':
        samples[:, 1] = values * .3
    elif channel == 'tiny':
        samples[:, 1] = values * 1e-12
    elif channel == 'mono':
        samples = values[:, None]
    elif channel == 'unused-nan':
        samples = np.column_stack((samples, np.full_like(values, np.nan)))
    elif channel == 'silent':
        samples[:] = 0
    sf.write(source / 'sounds/a.wav', samples, rate, subtype='FLOAT')
    stereo = samples[:, :2]
    if rate != 48000:
        stereo = resample_poly(stereo, 48000, rate, axis=0)
    if stereo.shape[1] == 1:
        stereo = np.repeat(stereo, 2, axis=1)
    reference = np.zeros((20000, 2), dtype='float32')
    reference[2000:2000 + len(stereo)] = stereo * .5
    ref = root / 'reference.wav'
    sf.write(ref, reference, 48000, subtype='FLOAT')
    row = dict(source_wav='sounds/a.wav', voice_id='1', start_engine_seconds='0',
               stop_engine_seconds='.1', start_qpc='1000', stop_qpc='1200',
               soundevent='fixture', stop_reason='voice_released')
    with (source / 'audio_usage.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=row)
        writer.writeheader()
        writer.writerow(row)
    state = root / 'state.csv'
    state.write_text('voice_id,slot_20\n1,1\n', encoding='utf-8')
    meta = root / 'reference.json'
    meta.write_text('{"first_block_end_monotonic":1,"loopback_block_frames":0,"qpc_frequency":1000}', encoding='utf-8')
    return NS(source_dir=source, reference_wav=ref, reference_json=meta if loopback else None,
              state_csv=state, output=root / 'fitted', engine_start=None if loopback else 0,
              speed=1, passes=2, clip_offset=0, clip_duration=None), reference


class AudioBoundaryTests(unittest.TestCase):
    def test_voice_csv_preserves_quoting_without_full_body_buffer(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'voice.csv'
            voice_log(path)
            original = Path.open
            class Stream:
                def __init__(self, source):
                    self.source = source
                def readline(self, *args):
                    return self.source.readline(*args)
                def __iter__(self):
                    return iter(self.source)
                def readlines(self, *args):
                    raise AssertionError('unbounded second CSV body allocation')
            @contextlib.contextmanager
            def opening(candidate, *args, **kwargs):
                with original(candidate, *args, **kwargs) as source:
                    yield Stream(source) if candidate == path else source
            with patch.object(Path, 'open', opening):
                header, rows, footer = export_captured_audio._events(path)
            self.assertEqual(header['qpc_frequency'], '1000')
            self.assertEqual([row['kind'] for row in rows], ['start', 'stop'])
            self.assertEqual(rows[0]['soundevent'], 'quoted, event')
            self.assertEqual(footer['capture_end_qpc'], '2000')

    def test_both_asset_children_use_external_environment_and_unwind(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for fail_at in (0, 1, 2):
                with self.subTest(fail_at=fail_at):
                    state = dict(active=False, enters=0, exits=0, calls=0)
                    environment = {'OWNED_CONVERSION': '1'}
                    @contextlib.contextmanager
                    def context():
                        state['active'] = True
                        state['enters'] += 1
                        try:
                            yield environment
                        finally:
                            state['active'] = False
                            state['exits'] += 1
                    def child(command, **options):
                        self.assertTrue(state['active'])
                        self.assertIs(options.get('env'), environment)
                        state['calls'] += 1
                        if state['calls'] == fail_at:
                            raise OSError('owned spawn refusal')
                        Path(command[-1]).write_bytes(b'owned child output')
                        return NS(returncode=0, stderr='', stdout='')
                    with patch.object(runtime, 'external_program_environment', context), \
                            patch.object(export_captured_audio.subprocess, 'run', side_effect=child):
                        call = lambda: export_captured_audio._convert_asset('sounds/a.vsnd', root / 'a.wav',
                            vpk=root / 'pak_dir.vpk', viewer=root / 'viewer.exe',
                            ffmpeg=root / 'ffmpeg.exe', temporary=root)
                        if fail_at:
                            with self.assertRaisesRegex(OSError, 'owned spawn refusal'):
                                call()
                        else:
                            call()
                    self.assertFalse(state['active'])
                    self.assertEqual(state['enters'], state['exits'])
                    self.assertEqual(state['calls'], fail_at or 2)

    def test_wav_hash_reads_are_bounded_and_identical_assets_deduplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            log = root / 'voice.csv'
            voice_log(log, ('sounds/a.vsnd', 'sounds/b.vsnd'))
            original, requests, case = Path.open, [], self
            class Bounded:
                def __init__(self, source):
                    self.source = source
                def read(self, size=-1):
                    case.assertGreater(size, 0)
                    case.assertLessEqual(size, 1024 * 1024)
                    requests.append(size)
                    return self.source.read(size)
            @contextlib.contextmanager
            def opening(path, *args, **kwargs):
                with original(path, *args, **kwargs) as source:
                    mode = args[0] if args else kwargs.get('mode', 'r')
                    yield Bounded(source) if path.suffix == '.wav' and mode == 'rb' else source
            with patch.object(Path, 'open', opening), patch.object(export_captured_audio, '_convert_asset',
                    side_effect=lambda asset, destination, **kwargs: pcm(destination)):
                report = export_captured_audio.export(log, root / 'out', vpk=root / 'vpk',
                                                      viewer=root / 'viewer', ffmpeg=root / 'ffmpeg')
            self.assertTrue(requests)
            self.assertEqual(report['exported_unique_wavs'], 1)
            self.assertEqual(report['unique_selected_assets'], 2)
            self.assertEqual(report['failed_assets'], {})
            self.assertEqual(len({row['path'] for row in report['assets'].values()}), 1)

    def test_asset_cleanup_refusal_keeps_primary_failure_and_next_asset(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            log = root / 'voice.csv'
            voice_log(log, ('sounds/a.vsnd', 'sounds/b.vsnd'))
            original = Path.unlink
            for malformed in (False, True):
                with self.subTest(malformed=malformed):
                    def convert(asset, destination, **kwargs):
                        if asset.endswith('/a.vsnd'):
                            destination.write_bytes(b'not a reviewed WAV file')
                            if not malformed:
                                raise RuntimeError('original decoder refusal')
                        else:
                            pcm(destination)
                    def remove(path, *args, **kwargs):
                        if not malformed and path.name.startswith('a_'):
                            raise PermissionError('owned partial is locked')
                        return original(path, *args, **kwargs)
                    with patch.object(export_captured_audio, '_convert_asset', side_effect=convert), \
                            patch.object(Path, 'unlink', remove):
                        report = export_captured_audio.export(log, root / str(malformed),
                            vpk=root / 'vpk', viewer=root / 'viewer', ffmpeg=root / 'ffmpeg')
                    self.assertEqual(set(report['failed_assets']), {'sounds/a.vsnd'})
                    if not malformed:
                        self.assertEqual(report['failed_assets']['sounds/a.vsnd'], 'original decoder refusal')
                    self.assertEqual(report['exported_unique_wavs'], 1)
                    self.assertTrue(report['fidelity'].startswith('Partial:'))

    def test_audio_encoder_lookup_falls_back_after_inaccessible_override(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            denied, valid = root / 'denied.exe', root / 'bundled.exe'
            valid.write_bytes(b'owned fixture, never executed')
            original = Path.is_file
            def available(path):
                if path == denied:
                    raise PermissionError('audio override unavailable')
                return original(path)
            with patch.dict(os.environ, DOLLY_FFMPEG=str(denied)), patch.object(Path, 'is_file', available), \
                    patch.object(video_export, 'bundled_ffmpeg_path', return_value=valid):
                self.assertEqual(clip_audio._ffmpeg(), valid)

    def test_filtered_voice_log_preserves_late_competing_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            log = root / 'voice.csv'
            voice_log(log)
            reference = root / 'reference.wav'
            sf.write(reference, np.zeros((9600, 2)), 48000)
            reference.with_suffix('.json').write_text(
                '{"first_block_end_monotonic":1,"loopback_block_frames":0}', encoding='utf-8')
            output = root / 'window.csv'
            original = sf.info
            def competing(path):
                output.write_bytes(b'competing output')
                return original(path)
            with patch.object(filter_captured_audio_window.sf, 'info', side_effect=competing):
                with self.assertRaises(FileExistsError):
                    filter_captured_audio_window.filter_window(log, reference, output)
            self.assertEqual(output.read_bytes(), b'competing output')
            report = filter_captured_audio_window.filter_window(log, reference, root / 'normal.csv')
            self.assertEqual(report['selected_voices'], 1)

    def test_camera_arrival_keeps_interpolation_without_suffix_copy(self):
        class FrameBudget(list):
            def __getitem__(self, key):
                if isinstance(key, slice):
                    raise AssertionError('complete suffix copied for arrival lookup')
                return super().__getitem__(key)
        frames = FrameBudget(camera_export.Frame(n, n / 60, (n, 0, 0), (0, 0, 0), 90, n / 60)
                             for n in range(64))
        self.assertEqual(camera_export.arrival_at(frames, 0), (0, (0, 0, 0)))
        self.assertEqual(camera_export.arrival_at(frames, 1 / 120), (1 / 120, (.5, 0, 0)))
        self.assertIsNone(camera_export.arrival_at(frames, 1000))

    def test_fit_keeps_silent_channels_finite_and_rejects_invalid_used_samples(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for channel in ('left', 'right', 'both', 'mono', 'unused-nan'):
                with self.subTest(channel=channel):
                    args, expected = fit_fixture(root / channel, channel=channel)
                    with contextlib.redirect_stdout(io.StringIO()), np.errstate(divide='raise', invalid='raise'):
                        report = fit_reference_voices.fit(args)
                    actual, rate = sf.read(args.output / 'reconstructed.wav', always_2d=True)
                    self.assertEqual(rate, 48000)
                    self.assertTrue(np.isfinite(actual).all())
                    np.testing.assert_allclose(actual, expected, atol=1e-6, rtol=0)
                    self.assertGreater(report['correlation'], .99999)
                    if channel in ('left', 'unused-nan'):
                        self.assertTrue(np.all(actual[:, 1] == 0))
                    elif channel == 'right':
                        self.assertTrue(np.all(actual[:, 0] == 0))
            for location in ('reference', 'source'):
                for bad in (np.nan, np.inf):
                    with self.subTest(location=location, bad=bad):
                        args, _ = fit_fixture(root / f'{location}-{bad}')
                        path = args.reference_wav if location == 'reference' else args.source_dir / 'sounds/a.wav'
                        samples, rate = sf.read(path, always_2d=True)
                        samples[0, 0] = bad
                        sf.write(path, samples, rate, subtype='FLOAT')
                        with self.assertRaisesRegex(ValueError, 'nonfinite samples'):
                            fit_reference_voices.fit(args)
                        self.assertFalse((args.output / 'reconstructed.wav').exists())
