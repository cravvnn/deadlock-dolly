"""Real dropdown, numeric shot edits and read-only paired-audit contracts.

Tk uses the normal source-check private desktop. Video bytes prove presence
only; codec/frame-order checks remain separate from this compact owner suite.
"""
from pathlib import Path
import copy
import json
import tempfile
from types import SimpleNamespace
import unittest

from dolly import export_audit, native_effects
from dolly.path import AttachKey, CameraTangent, CvarTrack, Keyframe, Project, TrackKey
from dolly.ui import dropdown_popup, shot_commands
from dolly.video_export import VideoExport


def key(time, **options):
    return Keyframe(time, time, 0, 0, 0, 0, 0, **options)


def popup_probe():
    import tkinter as tk
    from tkinter import ttk
    from dolly.gui import DollyApp
    root = tk.Tk()
    errors = []
    root.report_callback_exception = lambda *args: errors.append(args)
    try:
        players = [dict(model_path='models/heroes/astro.vmdl', entity_index=1),
                   dict(model_path='models/heroes/abrams.vmdl', entity_index=2)]
        for mode in ('stable', 'reorder', 'replace', 'empty', 'disabled'):
            parent = tk.Toplevel(root)
            value = tk.StringVar(parent)
            combo = ttk.Combobox(parent, textvariable=value, state='readonly')
            combo.pack()
            app = SimpleNamespace(attach_player_combo=combo, attach_player=value,
                                  attach_note=ttk.Label(parent), _attach_players={})
            DollyApp._attach_roster_changed(app, dict(available=True, players=players))
            parent.update_idletasks()
            events = []
            combo.bind('<<ComboboxSelected>>', lambda e: events.append(value.get()))
            popup = dropdown_popup.Popup(combo)
            popup.index = 0
            visible = popup.values[0]
            if mode == 'reorder':
                DollyApp._attach_roster_changed(app, dict(available=True, players=list(reversed(players))))
            elif mode == 'replace':
                DollyApp._attach_roster_changed(app, dict(available=True, players=[players[1]]))
            elif mode == 'empty':
                combo.configure(values=())
            elif mode == 'disabled':
                combo.state(['disabled'])
            before = value.get()
            popup._choose()
            root.update()
            if mode == 'stable':
                assert value.get() == visible and events == [visible]
            else:
                assert value.get() == before and not events, 'stale/disabled popup selected a row: ' + mode
            parent.destroy()
        parent = tk.Toplevel(root)
        combo = ttk.Combobox(parent, values=('A', 'B'))
        combo.pack()
        parent.update()
        parent.grab_set()
        popup = dropdown_popup.Popup(combo)
        popup.close()
        assert parent.grab_current() is parent
        parent.grab_release()
        parent.destroy()
        assert not errors, errors
    finally:
        root.destroy()


def rebase_probe():
    scalar, vector = 'r_dof_override_far_crisp', 'r_dof_override_ranges'
    for mode in ('linear', 'step', 'smooth'):
        p = Project(start_tick=64, tick_rate=64, keyframes=[key(0), key(5), key(10)], tracks=[
            CvarTrack(scalar, [TrackKey(0, 100), TrackKey(10, 200)], interpolation=mode, restore_value=75),
            CvarTrack(vector, [TrackKey(0, (-100, 0, 180, 2000)), TrackKey(10, (-50, 50, 280, 3000))], interpolation=mode),
            CvarTrack('r_dof_override', [], interpolation='step')])
        before = copy.deepcopy(p.to_dict())
        shifted = shot_commands.delete_camera(p, 0)
        expected = 100 if mode == 'step' else 150
        assert shifted.evaluate(0)['cvars'][scalar] == expected, 'lost interpolated effect at the cut'
        for t in (0, 1, 2, 5):
            assert shifted.evaluate(t)['cvars'] == p.evaluate(t + 5)['cvars']
        assert shifted.start_tick == 384 and [k.time for k in shifted.keyframes] == [0, 5]
        assert shifted.tracks[0].restore_value == 75 and not shifted.tracks[2].keys
        assert p.to_dict() == before
    # General smooth tracks preserve the cut value; later derivatives can change.
    p.tracks[0].keys = [TrackKey(0, 100), TrackKey(4, 160), TrackKey(10, 200)]
    assert shot_commands.delete_camera(p, 0).evaluate(0)['cvars'][scalar] == p.evaluate(5)['cvars'][scalar]
    p.tracks[0].keys = [TrackKey(5, 160), TrackKey(10, 200)]
    assert shot_commands.delete_camera(p, 0).tracks[0].keys[0].value == 160
    assert len(shot_commands.delete_camera(p, 1).keyframes) == 2


def retime_probe():
    for point, rigid in (('eyes', False), ('bone', False), ('bone', True)):
        attach = AttachKey(handle=1, model='models/heroes/astro.vmdl', point=point,
                           bone='head' if point == 'bone' else '', smoothing=.25,
                           orientation_mode='rigid' if rigid else 'legacy')
        p = Project(start_tick=64, tick_rate=64, keyframes=[key(0),
            key(10, source='attach', attach=attach, source_blend=2), key(20, source_blend=2)],
            tracks=[CvarTrack('r_dof_override_far_crisp', [TrackKey(0, 100), TrackKey(20, 200)])])
        p.keyframes[1].tangents = {'x': CameraTangent('broken', 2, 3)}
        before = copy.deepcopy(p.to_dict())
        original_packet = native_effects.compile_attach(p)
        p.retime(32)
        assert [k.source_blend for k in p.keyframes] == [0, 4, 4], 'retime lost source arrival timing'
        assert [k.time for k in p.keyframes] == [0, 20, 40] and p.start_tick == 64
        assert p.keyframes[1].attach.smoothing == .25
        assert p.keyframes[1].tangents['x'].incoming == 1
        assert p.tracks[0].keys[-1].time == 40
        packet = native_effects.compile_attach(p)
        header = native_effects.ATTACH_HEADER.unpack_from(packet)
        assert header[1] == (4 if rigid else 3)
        segments = [native_effects.ATTACH_SEGMENT3.unpack_from(packet,
            native_effects.ATTACH_HEADER.size + i * native_effects.ATTACH_SEGMENT3.size)
            for i in range(header[-1])]
        assert [(s[0], s[1], s[-1]) for s in segments] == [(0, 20, 0), (20, 40, 4), (40, 40, 4)]
        p.retime(64)
        assert p.to_dict() == before and native_effects.compile_attach(p) == original_packet
        p.keyframes[1].source_blend = 6
        before = copy.deepcopy(p.to_dict())
        try:
            p.retime(32)
        except ValueError:
            pass
        else:
            raise AssertionError('retime admitted a blend above the existing native limit')
        assert p.to_dict() == before, 'failed retime partially changed the shot'
    for point in ('eyes', 'bone'):
        p.keyframes[1].attach.point = point
        p.keyframes[1].attach.bone = 'head' if point == 'bone' else ''
        p.keyframes[1].attach.orientation_mode = 'legacy'
        for camera in p.keyframes:
            camera.source_blend = 0
        before = native_effects.compile_attach(p)
        assert native_effects.ATTACH_HEADER.unpack_from(before)[1] == (2 if point == 'bone' else 1)
        p.retime(128)
        p.retime(64)
        assert native_effects.compile_attach(p) == before


def paired_audit_probe(root):
    for name, color, depth, decoded, mismatch in (
        ('agree', 10, 10, None, False), ('short-depth', 10, 9, None, True),
        ('extra-depth', 10, 11, None, True), ('decoded-short', 10, 10, (10, 9), True),
        ('decoded-both-short', 10, 10, (9, 9), True), ('decoded-only', None, None, (10, 9), True),
        ('decoded-agree', 10, 10, (10, 10), False), ('legacy-color', 10, None, None, False),
        ('legacy-depth', None, 10, None, False)):
        folder = root / name
        folder.mkdir()
        if color is not None:
            (folder / 'color.mov').write_bytes(b'owned nonempty presence fixture')
            (folder / 'shot.json').write_text(json.dumps(dict(format=export_audit.SIDECAR_FORMAT,
                version=1, fixed_step=True, frames_written=color, first_frame=0,
                last_frame=color - 1, video_file='color.mov')), encoding='utf-8')
        if depth is not None or color is None:
            layer = folder / 'depth'
            layer.mkdir()
            (layer / 'depth.mov').write_bytes(b'owned nonempty depth fixture')
            if depth is not None:
                (layer / 'manifest.json').write_text(json.dumps(dict(complete=True, frames=depth,
                    master='depth.mov')), encoding='utf-8')
        if decoded is not None:
            (folder / 'decoded-audit.json').write_text(json.dumps(dict(streams={
                label: dict(decoded_frames=count) for label, count in zip(('color', 'depth'), decoded)})), encoding='utf-8')
        before = {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
        report = export_audit.audit_take(folder)
        assert report['ok'] == (not mismatch), 'paired frame evidence accepted disagreement: ' + name
        if mismatch:
            assert any('frame count mismatch' in s for s in report['findings'])
        # The automatic finishing caller must preserve completed status and counts.
        owner = VideoExport(SimpleNamespace())
        owner._layer_folder = folder
        status = dict(state='completed', frames_written=color or 10, last_frame=(color or 10) - 1)
        finished = owner._audit_take(status)
        assert {k: v for k, v in finished.items() if k != 'audit'} == status
        assert finished['audit'] == report
        assert before == {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob('*') if p.is_file()}


class AuthoringAuditBoundaryTests(unittest.TestCase):
    def test_popup_requires_visible_values_and_enabled_control(self):
        popup_probe()

    def test_leading_camera_delete_retains_sampled_effect_boundary(self):
        rebase_probe()

    def test_retime_retains_arrival_windows_and_rejects_overflow_atomically(self):
        retime_probe()

    def test_paired_frame_evidence_agrees_without_modifying_completed_take(self):
        with tempfile.TemporaryDirectory() as folder:
            paired_audit_probe(Path(folder))
