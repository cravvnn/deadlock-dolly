"""Recovery, close, settings, mod and updater failure boundaries (0.7.41)."""
import io
import os
import queue
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from dolly import (gameinfo_transaction, launcher, mod_policy, release_launcher, session_cleanup, settings,
                   update_ui, update_worker, updater)
from dolly.launch_errors import LaunchError
import test_core_install as core
from test_metadata_boundaries import vpk
import test_update_handoff_process as handoff


class RecoveryBoundaries(unittest.TestCase):
    setUp = core.InstallTests.setUp
    journal_fixture = core.InstallTests.journal_fixture

    def recovery(self, package_root):
        return (patch.object(launcher, '_check_runtime'), patch.object(launcher, 'PACKAGE_ROOT', package_root),
                patch.object(launcher, 'running_processes', return_value={'steam.exe'}),
                patch.object(launcher, 'discover_game', return_value=None))

    def test_session_folder_with_only_the_games_steam_cache_is_removed(self):
        # Every session the game writes session-files/cache_<id>.soc and an
        # empty save folder; that used to keep the whole folder forever.
        session, overlay, backup, record, patched = self.journal_fixture('cache')
        self.paths.gameinfo.write_bytes(core.GAMEINFO)
        (overlay / 'session-files').mkdir()
        (overlay / 'session-files' / 'cache_222088733.soc').write_bytes(b'steam cache')
        (overlay / 'cvar_unlocker' / 'save').mkdir(parents=True)
        self.assertTrue(session_cleanup.remove_overlay(overlay, self.paths, session))
        self.assertFalse(overlay.exists())
        # Anything else in the session write folder is still the user's.
        session, overlay, backup, record, patched = self.journal_fixture('userfile')
        self.paths.gameinfo.write_bytes(core.GAMEINFO)
        (overlay / 'session-files').mkdir()
        (overlay / 'session-files' / 'cache_222088733.soc').write_bytes(b'steam cache')
        (overlay / 'session-files' / 'my-notes.txt').write_bytes(b'keep')
        self.assertFalse(session_cleanup.remove_overlay(overlay, self.paths, session))
        self.assertEqual((overlay / 'session-files' / 'my-notes.txt').read_bytes(), b'keep')

    def test_session_folder_with_a_crashed_atomic_write_temp_is_removed(self):
        # A crash inside editing_assets.deploy leaves file_ops' temporary file
        # in the cfg folder; it is Dolly's own and must not keep the folder.
        session, overlay, backup, record, patched = self.journal_fixture('cfgtemp')
        self.paths.gameinfo.write_bytes(core.GAMEINFO)
        cfg = overlay / 'cvar_unlocker' / 'cfg'
        cfg.mkdir(parents=True)
        (cfg / '.dolly-write-abc_123.tmp').write_bytes(b'partial')
        self.assertTrue(session_cleanup.remove_overlay(overlay, self.paths, session))
        self.assertFalse(overlay.exists())
        # Similar names elsewhere, or without the exact shape, stay the user's.
        session, overlay, backup, record, patched = self.journal_fixture('cfgkeep')
        self.paths.gameinfo.write_bytes(core.GAMEINFO)
        cfg = overlay / 'cvar_unlocker' / 'cfg'
        cfg.mkdir(parents=True)
        kept = [cfg / 'dolly-write-abc.tmp', cfg / '.dolly-write-abc.txt', overlay / 'cvar_unlocker' / '.dolly-write-abc.tmp']
        for path in kept:
            path.write_bytes(b'keep')
        self.assertFalse(session_cleanup.remove_overlay(overlay, self.paths, session))
        self.assertEqual([path.read_bytes() for path in kept], [b'keep'] * 3)

    def test_failed_marker_write_leaves_no_partial_marker(self):
        editing = core.InstallTests.editing_fixture(self)
        real_replace = os.replace

        def replace(source, target):
            if Path(target).name == '.dolly-session.json':
                raise OSError(28, 'No space left on device')
            return real_replace(source, target)
        with patch.object(launcher, '_check_runtime'), patch.object(launcher, 'PACKAGE_ROOT', self.root / 'Dolly'),              patch.object(launcher, 'EDITING_ROOT', editing),              patch.object(launcher, 'running_processes', return_value={'steam.exe'}),              patch('dolly.stock_provenance.verify', return_value={}), patch('dolly.file_ops.os.replace', side_effect=replace),              patch.object(launcher.subprocess, 'Popen', side_effect=OSError('launched')) as popen:
            with self.assertRaisesRegex(LaunchError, 'No space left'):
                launcher.launch(self.paths.root)
            popen.assert_not_called()
        self.assertEqual(self.paths.gameinfo.read_bytes(), core.GAMEINFO)
        for overlay in self.game.glob('citadel_dolly_*'):
            self.assertEqual([p.name for p in overlay.rglob('*') if p.is_file()], [])

    def test_session_folder_is_removed_after_a_players_capture(self):
        from dolly import player_layer
        session, overlay, backup, record, patched = self.journal_fixture('players')
        self.paths.gameinfo.write_bytes(core.GAMEINFO)
        deployment = overlay / 'cvar_unlocker' / 'bin' / 'win64'
        deployment.mkdir(parents=True, exist_ok=True)
        # What the native capture leaves beside its DLL, progress file included.
        for name in (player_layer.STATUS_NAME, player_layer.MARKER_NAME, player_layer.STOP_NAME,
                     player_layer.PACING_NAME, player_layer.PACING_NAME + '.tmp'):
            (deployment / name).write_text('x')
        self.assertTrue(session_cleanup.remove_overlay(overlay, self.paths, session))
        self.assertFalse(overlay.exists())

    def test_unfinished_session_is_recovered_after_the_dolly_folder_moves(self):
        session, overlay, backup, record, patched = self.journal_fixture('moved')
        moved = self.root / 'Dolly renamed'
        shutil.move(str(self.package), str(moved))
        a, b, c, d = self.recovery(moved)
        with a, b, c, d:
            self.assertEqual(launcher.recover_pending(), [str(moved / 'logs' / 'moved')])
        self.assertEqual(self.paths.gameinfo.read_bytes(), core.GAMEINFO)
        self.assertFalse(overlay.exists())
        # A journal copied under another session name is still refused.
        session, overlay, backup, record, patched = self.journal_fixture('real')
        impostor = session.parent / 'other'
        shutil.copytree(session, impostor)
        with self.assertRaises(LaunchError):
            gameinfo_transaction.load_record(impostor)
        self.assertEqual(self.paths.gameinfo.read_bytes(), patched)

    def test_two_cleaners_of_one_session_never_record_a_false_conflict(self):
        # GameinfoCleaners.tla: the old session's helper pauses mid-restore
        # while another Dolly's launch recovers the session and writes its own
        # gameinfo.gi. Without a shared lock the helper then reads the new file
        # and marks the old session as changed outside Dolly.
        import threading
        from dolly import session_recovery
        session, overlay, backup, record, patched = self.journal_fixture('race')
        newer = core.GAMEINFO.replace(b'Game "core"', b'Game "citadel_dolly_next/cvar_unlocker"\r\n   Game "core"')
        paused, resume = threading.Event(), threading.Event()
        real_read = Path.read_bytes
        target = self.paths.gameinfo

        def read_bytes(path):
            if threading.current_thread().name == 'helper' and path == target and not paused.is_set():
                paused.set()
                resume.wait(10)
            return real_read(path)

        def helper():
            try:
                session_recovery.restore_session_configs(session)
            except LaunchError:
                pass

        def launch():
            a, b, c, d = self.recovery(self.package)
            with a, b, c, d:
                launcher.recover_pending()
            target.write_bytes(newer)  # The new session's own file.

        with patch.object(Path, 'read_bytes', read_bytes):
            first = threading.Thread(target=helper, name='helper')
            first.start()
            self.assertTrue(paused.wait(10))
            second = threading.Thread(target=launch, name='launch')
            second.start()
            second.join(0.5)  # Without the lock the launch finishes here.
            resume.set()
            first.join(10); second.join(10)
        self.assertNotEqual(gameinfo_transaction.load_record(session)['config_state'], 'conflict')
        self.assertEqual(target.read_bytes(), newer)
        self.assertEqual(backup.read_bytes(), core.GAMEINFO)

    def test_gameinfo_that_is_not_utf8_is_a_launch_error(self):
        session, overlay, backup, record, patched = self.journal_fixture('codec')
        self.paths.gameinfo.write_bytes(core.GAMEINFO.replace(b'// user comment', b'// caf\xe9'))
        with self.assertRaisesRegex(LaunchError, 'not UTF-8'):
            session_cleanup._mount_is_referenced(self.paths, overlay)

    def test_log_failure_cannot_abort_the_launch_after_the_game_started(self):
        def full_disk(_message):
            raise OSError(28, 'No space left on device')
        for started in (True, False):
            session = SimpleNamespace(_cleanup_handed_off=False, log=full_disk)
            with patch.object(session_cleanup, 'start_waiter', side_effect=None if started else OSError('no helper')):
                self.assertEqual(launcher.Session.start_cleanup_helper(session), started)

    def test_pack_extension_case_does_not_refuse_the_mount(self):
        for index, name in enumerate(('pak01_dir.VPK', 'PAK02_DIR.VPK')):
            mount = self.root / f'mount{index}'; mount.mkdir()
            vpk(mount / name, [('models', 'skin')])
            self.assertTrue(mod_policy.classify_mount(mount)['allowed'], name)
        mount = self.root / 'render'; mount.mkdir()
        vpk(mount / 'pak01_dir.VPK', [('particles', 'low')])
        self.assertFalse(mod_policy.classify_mount(mount)['allowed'])


class SettingsBoundary(unittest.TestCase):
    def test_settings_that_could_not_be_read_are_kept_before_a_save_replaces_them(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'settings.json'
            mine = settings.AppSettings(full_editor=True, export_folder=str(Path(folder) / 'exports'))
            settings.save_settings(mine, target)
            original = target.read_bytes()
            with patch.object(Path, 'open', side_effect=PermissionError('locked')):
                with self.assertRaises(OSError):
                    settings.load_settings(target)
            settings.save_settings(settings.AppSettings(), target)  # What the UI does after falling back.
            backups = [p for p in Path(folder).iterdir() if p != target]
            self.assertEqual([p.read_bytes() for p in backups], [original])
            settings.save_settings(mine, target)  # Only the first save needs the copy.
            self.assertEqual(len([p for p in Path(folder).iterdir() if p != target]), 1)

    def test_settings_held_briefly_by_a_scanner_still_load(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'settings.json'
            mine = settings.AppSettings(full_editor=True, movement_speed=500)
            settings.save_settings(mine, target)
            real_open, held = Path.open, []

            def locked_twice(path, *args, **kwargs):
                if path == target and len(held) < 2:
                    held.append(path)
                    raise PermissionError(13, 'The process cannot access the file (scanner)')
                return real_open(path, *args, **kwargs)

            settings._UNREAD.clear()
            with patch.object(Path, 'open', locked_twice), patch('dolly.file_ops.time.sleep') as sleep:
                self.assertEqual(settings.load_settings(target), mine)
            self.assertEqual(len(held), 2)
            self.assertEqual(sleep.call_count, 2)
            self.assertEqual(settings._UNREAD, set())


class CloseAndDialogBoundaries(unittest.TestCase):
    def test_failed_stop_while_closing_still_asks_about_unsaved_work(self):
        from dolly import gui
        app = SimpleNamespace(playing=True, busy=True, _closing=True, _discard_asked=False,
                              _allow_discard=MagicMock(return_value=False), _submit=MagicMock(),
                              _destroy=MagicMock(), controller=MagicMock())
        gui.DollyApp._close_after_failure(app)
        app._allow_discard.assert_called_once()
        app._submit.assert_not_called(); app._destroy.assert_not_called(); app.controller.close.assert_not_called()
        self.assertFalse(app._closing)
        # Once answered, a later failure closes without asking twice.
        app = SimpleNamespace(playing=False, busy=False, _closing=True, _discard_asked=True,
                              _allow_discard=MagicMock(), _submit=MagicMock(return_value=True),
                              _destroy=MagicMock(), controller=MagicMock())
        gui.DollyApp._close_after_failure(app)
        app._allow_discard.assert_not_called(); app._submit.assert_called_once()

    def test_a_close_that_keeps_failing_still_closes_dolly(self):
        # CloseFlow.tla: controller.close raises every time while a gameinfo
        # conflict is pending; each failure used to resubmit the close forever.
        from dolly import gui
        app = SimpleNamespace(playing=False, busy=False, _closing=True, _discard_asked=True,
                              _allow_discard=MagicMock(), _submit=MagicMock(return_value=True),
                              _destroy=MagicMock(), controller=MagicMock())
        gui.DollyApp._close_after_failure(app)  # The stop failed: the close runs once.
        app._submit.assert_called_once(); app._destroy.assert_not_called()
        gui.DollyApp._close_after_failure(app)  # The close itself failed: exit, no retry.
        app._submit.assert_called_once(); app._destroy.assert_called_once()
        # A new close attempt starts fresh.
        app = SimpleNamespace(busy=False, playing=False, _cancel_renderlock_export=MagicMock(),
                              _finish_close=MagicMock(), _close_submitted=True)
        gui.DollyApp._on_close(app)
        self.assertFalse(app._close_submitted)

    def test_only_the_close_steps_finish_a_close_after_a_failure(self):
        # The save question keeps the poll running; a game-exit cleanup that
        # fails then must not open a second save question or end the close.
        from dolly import gui

        def polled(label):
            app = MagicMock()
            app.closed = app.busy = app.playing = False
            app._closing = True
            app.startup_cancel = None
            app.capture_buttons = []
            app.controller.status.return_value = {'connected': False, 'startup_stage': 'game_closed'}
            app.operations.pending_events.return_value = [('error', label, RuntimeError('worker still stopping'))]
            with patch.object(gui, 'editor_session'):
                gui.DollyApp._poll_once(app)
            return app

        app = polled('Clearing closed game session')
        app._close_after_failure.assert_not_called()
        app._report_operation_error.assert_called_once()
        for label in ('Stopping before closing', 'Closing replay connection'):
            app = polled(label)
            app._close_after_failure.assert_called_once()
            app._report_operation_error.assert_not_called()

    def test_background_results_wait_for_an_open_dialog(self):
        from dolly import gui
        show = MagicMock(return_value='ok')
        app = SimpleNamespace(closed=False, _error_dialog_open=True, root=MagicMock())
        self.assertIsNone(gui.DollyApp._background_dialog(app, show, 'Title', 'Text'))
        show.assert_not_called(); app.root.after.assert_called_once()
        app = SimpleNamespace(closed=False, _error_dialog_open=False, root=MagicMock())
        show.side_effect = lambda *args, **kwargs: app._error_dialog_open
        self.assertIs(gui.DollyApp._background_dialog(app, show, 'Title', 'Text'), True)
        self.assertFalse(app._error_dialog_open)
        app.closed = True
        show.reset_mock()
        self.assertIsNone(gui.DollyApp._background_dialog(app, show, 'Title', 'Text'))
        show.assert_not_called()
        # The background notices use it.
        source = Path(gui.__file__).read_text(encoding='utf-8')
        for title in ('"Game configuration"', '"Replay copy saved"', '"Native DOF support"', '"Keybind settings"',
                      '"Start a new camera path?"'):
            self.assertIn('_background_dialog(', source[max(0, source.index(title) - 120):source.index(title)], title)


class UpdaterBoundaries(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.target = Path(folder.name) / 'Dolly'
        self.target.mkdir()

    def test_unusable_update_record_does_not_block_an_intact_install(self):
        handoff.package(self.target, '1.0.0')
        marker = self.target / update_worker.PENDING
        cases = [b'', b'{}', b'[]', b'\xff\xfe', ('{"work": "%s", "target": "%s"}' % (
            (self.target / '.dolly-update-gone').as_posix(), self.target.as_posix())).encode()]
        for data in cases:
            with self.subTest(marker=data[:20]):
                marker.write_bytes(data)
                with self.assertRaises(update_worker.PendingUpdateError):
                    update_worker.pending_work(self.target)
                with patch.object(update_worker, 'notify') as notify, \
                        patch.object(release_launcher, 'open_editor') as editor:
                    self.assertEqual(release_launcher.main([], target=self.target), 0)
                editor.assert_called_once(); notify.assert_not_called()
                self.assertFalse(marker.exists())
        # With app files that no longer match the manifest it stays, with a clear message.
        marker.write_bytes(b'')
        changed = next(self.target / name for name in updater.read_manifest(self.target)['files'])
        changed.write_text('half-installed')
        with patch.object(update_worker, 'notify') as notify, patch.object(release_launcher, 'open_editor') as editor:
            self.assertEqual(release_launcher.main([], target=self.target), 1)
        editor.assert_not_called()
        self.assertIn('unfinished update', notify.call_args.args[0])
        self.assertTrue(marker.exists())

    def test_only_a_held_install_lock_reports_another_update(self):
        handoff.package(self.target, '1.0.0')
        with update_worker.install_lock(self.target):
            with patch.object(update_worker, 'notify') as notify,                     patch.object(release_launcher, 'open_editor') as editor, \
                    patch.object(release_launcher, 'LOCK_WAIT_SECONDS', .2):
                self.assertEqual(release_launcher.main([], target=self.target), 1)
        editor.assert_not_called()
        self.assertIn('Another update may be in progress', notify.call_args.args[0])
        with patch.object(update_worker, 'cleanup_workspaces', side_effect=PermissionError('folder is read-only')),                 patch.object(update_worker, 'notify') as notify,                 patch.object(release_launcher, 'open_editor') as editor:
            self.assertEqual(release_launcher.main([], target=self.target), 1)
        editor.assert_not_called()
        self.assertNotIn('Another update', notify.call_args.args[0])
        self.assertIn('folder is read-only', notify.call_args.args[0])

    def path_kind_release(self, old_names, new_names):
        target, work = self.target / 'installed', self.target / '.dolly-update-kind'
        for folder, version, names in ((target, '1.0.0', old_names), (work / 'payload', '1.0.1', new_names)):
            for name in names:
                path = folder / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(version + ':' + name)
            updater.write_manifest(folder, version)
        (target / 'my-shot.json').write_text('keep')
        return {'work': str(work), 'target': str(target)}, target, work

    def test_update_that_turns_a_file_into_a_folder_rolls_back_and_installs(self):
        # UpdateRollback (fileToDir/dirToFile): remove before writing, prune
        # emptied folders, in both install and rollback.
        common = list(handoff.REQUIRED)
        for old, new in ((['_internal/data'], ['_internal/data/x.bin']),
                         (['_internal/data/x.bin'], ['_internal/data'])):
            with self.subTest(old=old, new=new):
                shutil.rmtree(self.target, ignore_errors=True); self.target.mkdir()
                plan, target, work = self.path_kind_release(common + old, common + new)
                before = {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob('*') if p.is_file()}
                def failed_health(_target):
                    raise RuntimeError('simulated startup failure')
                with self.assertRaisesRegex(RuntimeError, 'simulated startup failure'):
                    update_worker.install(plan, failed_health)
                after = {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob('*') if p.is_file()}
                self.assertEqual(after, before)
                self.assertFalse((target / update_worker.PENDING).exists())
                (work / 'journal.json').unlink()
                update_worker.install(plan)
                self.assertEqual(updater.read_manifest(target)['version'], '1.0.1')
                for name in new:
                    self.assertEqual((target / name).read_text(), '1.0.1:' + name)
                self.assertEqual((target / 'my-shot.json').read_text(), 'keep')
        # A folder that holds a user file is never removed to make room.
        shutil.rmtree(self.target, ignore_errors=True); self.target.mkdir()
        plan, target, work = self.path_kind_release(common + ['_internal/data/x.bin'], common + ['_internal/data'])
        (target / '_internal/data/mine.txt').write_text('user')
        with self.assertRaises(ValueError):
            update_worker.install(plan)
        self.assertEqual((target / '_internal/data/mine.txt').read_text(), 'user')
        self.assertEqual(updater.read_manifest(target)['version'], '1.0.0')

    def test_failed_download_leaves_no_workspace(self):
        body = b'not the release' * 1000
        release = {'version': '9.9.9', 'url': 'https://example.invalid/x.zip', 'sha256': '0' * 64, 'size': len(body)}
        for _attempt in range(2):
            with patch.object(updater, 'open_url', return_value=io.BytesIO(body)):
                with self.assertRaises(ValueError):
                    updater.download_update(release, self.target)
        self.assertEqual(list(self.target.glob('.dolly-update-*')), [])

    def ui(self, **app):
        ui = update_ui.UpdateUI.__new__(update_ui.UpdateUI)
        ui.app = SimpleNamespace(closed=False, root=MagicMock(), update_status=MagicMock(), **app)
        ui.events = queue.Queue()
        ui.request_id = 1
        ui.running = ui.restarting = ui.dismissed = False
        ui.available = {'version': '9.9.9', 'url': 'u', 'sha256': 's', 'size': 1}
        ui.ready = (self.target / '.dolly-update-x', '9.9.9')
        ui.ready[0].mkdir(exist_ok=True)
        (ui.ready[0] / 'verified.json').write_text('{}')
        ui.approved = ui.ready_release = ui.release_key(ui.available)
        ui._status = MagicMock()
        return ui

    def test_update_restart_declined_at_the_save_prompt_keeps_the_updater_usable(self):
        ui = self.ui(busy=False, _allow_discard=MagicMock(return_value=False), _on_close=MagicMock())
        with patch.object(update_ui, 'launch_worker') as worker, patch.object(ui, '_can_update', return_value=True):
            ui.restart()
        worker.assert_not_called(); ui.app._on_close.assert_not_called()
        self.assertFalse(ui.restarting)
        # A close that does not start (an operation began meanwhile) is undone too.
        ui = self.ui(busy=False, _allow_discard=MagicMock(return_value=True), _on_close=MagicMock())
        with patch.object(update_ui, 'launch_worker'), patch.object(ui, '_can_update', return_value=True):
            ui.restart()
        ui.app._on_close.assert_called_once_with(discard_confirmed=True)
        self.assertFalse(ui.restarting)

    def test_cleanup_keeps_a_verified_download_until_it_is_used(self):
        handoff.package(self.target, '1.0.0')

        def staged(name, version, *, verified=True):
            work = self.target / name
            handoff.package(work / 'payload', version)
            if verified:
                (work / 'verified.json').write_text(
                    '{"version": "%s", "sha256": "%s"}' % (version, 'a' * 64))
            return work

        def age(work, seconds=7200):
            old = os.path.getmtime(work) - seconds
            for path in [work, *work.rglob('*')]:
                os.utime(path, (old, old))

        ready = staged('.dolly-update-ready', '1.0.1')
        downloading = staged('.dolly-update-busy', '1.0.1', verified=False)
        superseded = staged('.dolly-update-older', '0.9.0')
        abandoned = staged('.dolly-update-gone', '1.0.1', verified=False)
        for work in (ready, downloading, superseded, abandoned):
            age(work)
        # A download in progress writes into the zip; its folder time does not change.
        (downloading / 'download.zip').write_bytes(b'partial')
        os.utime(downloading, (os.path.getmtime(downloading) - 7200,) * 2)
        with patch.object(release_launcher, 'open_editor') as editor:
            self.assertEqual(release_launcher.main([], target=self.target), 0)
        editor.assert_called_once()
        self.assertTrue(ready.is_dir())
        self.assertEqual(updater.staged_payload(self.target, '1.0.1', 'a' * 64), ready)
        self.assertTrue(downloading.is_dir())
        self.assertFalse(superseded.exists())
        self.assertFalse(abandoned.exists())
        # An editor whose ready download vanished downloads it again on the next click.
        ui = self.ui(busy=False, _allow_discard=MagicMock(return_value=True), _on_close=MagicMock())
        shutil.rmtree(ui.ready[0])
        with patch.object(update_ui, 'launch_worker') as worker, patch.object(ui, '_can_update', return_value=True):
            ui.restart()
        worker.assert_not_called(); ui.app._on_close.assert_not_called()
        self.assertIsNone(ui.ready)
        self.assertIsNone(ui.ready_release)
        self.assertIn('download', ui._status.call_args.args[0].lower())

    def test_update_blocked_by_another_dolly_window_says_so(self):
        ui = self.ui()
        ui.safe_to_restart = MagicMock(return_value=True)
        reason = 'Another instance of this Dolly installation is still open'
        with patch.object(update_ui, 'check_processes', side_effect=RuntimeError(reason)), \
                patch.object(update_ui, 'application_root', return_value=self.target):
            self.assertFalse(ui._can_update())
        self.assertIn(reason, ui._status.call_args.args[0])
        self.assertNotIn('close Deadlock', ui._status.call_args.args[0])
        self.assertIsNone(ui.approved)
        # An unsaved shot or a running game keeps the existing advice.
        ui.safe_to_restart.return_value = False
        self.assertFalse(ui._can_update())
        self.assertIn('Save your shot', ui._status.call_args.args[0])

    def staged_release(self):
        target, work = self.target / 'installed', self.target / 'installed/.dolly-update-x'
        handoff.package(target, '1.0.0')
        handoff.package(work / 'payload', '1.0.1')
        before = {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob('*')
                  if p.is_file() and work not in p.parents}
        return {'work': str(work), 'target': str(target)}, target, work, before

    def installed(self, target, work):
        return {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob('*')
                if p.is_file() and work not in p.parents}

    def test_update_whose_recovery_record_cannot_be_written_can_be_retried(self):
        plan, target, work, before = self.staged_release()
        real = update_worker.atomic_json
        refused = set()

        def disk_full(path, value):
            if Path(path).name == update_worker.PENDING or value.get('state') in refused:
                raise OSError(28, 'No space left on device (simulated)')
            return real(path, value)

        with patch.object(update_worker, 'atomic_json', disk_full):
            with self.assertRaises(OSError):
                update_worker.install(plan)
        journal = (work / 'journal.json').read_text()
        self.assertIn('"rolled_back"', journal)
        self.assertEqual(self.installed(target, work), before)
        self.assertFalse((target / update_worker.PENDING).exists())
        # If even the rollback journal cannot be written, nothing was touched:
        # the next attempt starts over instead of being blocked forever.
        (work / 'journal.json').unlink()
        refused.add('rolled_back')
        with patch.object(update_worker, 'atomic_json', disk_full):
            with self.assertRaises(OSError):
                update_worker.install(plan)
        self.assertIn('"applying"', (work / 'journal.json').read_text())
        self.assertFalse((target / update_worker.PENDING).exists())
        update_worker.install(plan)
        self.assertEqual(updater.read_manifest(target)['version'], '1.0.1')
        self.assertFalse((target / update_worker.PENDING).exists())

    def test_completed_update_is_kept_when_its_recovery_record_is_stuck(self):
        plan, target, work, before = self.staged_release()
        update_worker.atomic_json(work / 'plan.json', plan)
        marker = target / update_worker.PENDING
        real = Path.unlink

        def held(path, *args, **kwargs):
            if path == marker:
                raise PermissionError(13, 'The file is in use (simulated)')
            return real(path, *args, **kwargs)

        with patch.object(Path, 'unlink', held), patch.object(update_worker.time, 'sleep'):
            with self.assertRaises(PermissionError):
                update_worker.install(plan)
        self.assertEqual(updater.read_manifest(target)['version'], '1.0.1')
        self.assertIn('"complete"', (work / 'journal.json').read_text())
        self.assertTrue(marker.exists())
        # Opening Dolly.exe again recovers: the finished update stays and the record goes.
        with patch.object(update_worker, 'check_processes'), \
                patch.object(update_worker, 'launch_program') as launch, \
                patch.object(update_worker, 'notify') as notify:
            self.assertEqual(update_worker.main(['--plan', str(work / 'plan.json'), '--recover']), 0)
        notify.assert_not_called(); launch.assert_called_once()
        self.assertEqual(updater.read_manifest(target)['version'], '1.0.1')
        self.assertTrue(all(updater.digest_file(target / name) == digest
                            for name, digest in updater.read_manifest(target)['files'].items()))
        self.assertFalse(marker.exists())
        self.assertIn('"complete"', (work / 'journal.json').read_text())

    def interrupted_update(self):
        plan, target, work, before = self.staged_release()
        update_worker.atomic_json(work / 'plan.json', plan)

        def power_loss(_target):
            raise KeyboardInterrupt

        with patch.object(update_worker, 'rollback'), self.assertRaises(KeyboardInterrupt):
            update_worker.install(plan, power_loss)
        self.assertTrue((target / update_worker.PENDING).exists())
        return target, work

    def test_two_starts_during_recovery_restore_once_and_open_one_editor(self):
        target, work = self.interrupted_update()
        argv = ['--plan', str(work / 'plan.json'), '--recover']
        # Two quick starts each hand off to a recovery helper.
        with patch.object(update_worker, 'check_processes'), \
                patch.object(update_worker, 'launch_program') as launch, \
                patch.object(update_worker, 'notify') as notify:
            self.assertEqual(update_worker.main(argv), 0)
            # The first helper's editor is open by now; the second must not mind.
            with patch.object(update_worker, 'check_processes',
                              side_effect=RuntimeError('Another instance of this Dolly installation is still open')):
                self.assertEqual(update_worker.main(argv), 0)
        notify.assert_not_called(); launch.assert_called_once()
        self.assertEqual(updater.read_manifest(target)['version'], '1.0.0')
        self.assertIn('"passed": true', (work / 'result.json').read_text())

    def test_recovery_waits_for_another_start_that_holds_the_lock(self):
        import threading
        target, work = self.interrupted_update()
        held, release = threading.Event(), threading.Event()

        def other_start():
            with update_worker.install_lock(target):
                held.set(); release.wait(10)

        thread = threading.Thread(target=other_start); thread.start()
        self.assertTrue(held.wait(10))
        threading.Timer(.3, release.set).start()
        try:
            with patch.object(update_worker, 'check_processes'), \
                    patch.object(update_worker, 'launch_program') as launch, \
                    patch.object(update_worker, 'notify') as notify:
                self.assertEqual(update_worker.main(['--plan', str(work / 'plan.json'), '--recover']), 0)
        finally:
            release.set(); thread.join()
        notify.assert_not_called(); launch.assert_called_once()
        self.assertFalse((target / update_worker.PENDING).exists())

    def test_second_start_leaves_a_running_recovery_alone(self):
        target, work = self.interrupted_update()
        with update_worker.install_lock(target):
            with patch.object(update_worker, 'notify') as notify, \
                    patch.object(update_worker, 'launch_worker') as helper, \
                    patch.object(release_launcher, 'open_editor') as editor:
                self.assertEqual(release_launcher.main([], target=target), 0)
        notify.assert_not_called(); helper.assert_not_called(); editor.assert_not_called()

    def test_start_waits_out_a_briefly_held_lock(self):
        import threading
        handoff.package(self.target, '1.0.0')
        held, release = threading.Event(), threading.Event()

        def other_start():
            with update_worker.install_lock(self.target):
                held.set(); release.wait(10)

        thread = threading.Thread(target=other_start); thread.start()
        self.assertTrue(held.wait(10))
        threading.Timer(.3, release.set).start()
        try:
            with patch.object(update_worker, 'notify') as notify, \
                    patch.object(release_launcher, 'open_editor') as editor:
                self.assertEqual(release_launcher.main([], target=self.target), 0)
        finally:
            release.set(); thread.join()
        notify.assert_not_called(); editor.assert_called_once()

    def test_recovery_gives_a_second_launcher_time_to_exit(self):
        target, work = self.interrupted_update()
        closing = update_worker.DollyStillOpen('still open', launcher=True)
        editor_open = update_worker.DollyStillOpen('still open', launcher=False)
        with patch.object(update_worker, 'check_processes', side_effect=[closing, closing, None]) as check, \
                patch.object(update_worker.time, 'sleep'):
            update_worker.check_processes_settled(target)
        self.assertEqual(check.call_count, 3)
        # An open editor is not going away by itself: refuse at once.
        with patch.object(update_worker, 'check_processes', side_effect=editor_open) as check, \
                patch.object(update_worker.time, 'sleep'):
            with self.assertRaises(update_worker.DollyStillOpen):
                update_worker.check_processes_settled(target)
        self.assertEqual(check.call_count, 1)
        with patch.object(update_worker, 'check_processes', side_effect=closing), \
                patch.object(update_worker.time, 'sleep'):
            with self.assertRaises(update_worker.DollyStillOpen):
                update_worker.check_processes_settled(target, timeout=0)

    def test_startup_check_that_never_answers_leaves_no_process_behind(self):
        import ctypes, subprocess, sys, time
        plan, target, work, before = self.staged_release()
        note = work / 'grandchild.txt'
        script = work / 'stuck.py'
        # Stands in for Dolly.exe: it starts the editor and waits for it.
        script.write_text('\n'.join((
            'import subprocess, sys, time',
            'child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])',
            'open(sys.argv[1], "w").write(str(child.pid))',
            'time.sleep(120)')))
        real = subprocess.Popen

        def stand_in(command, *args, **kwargs):
            return real([sys.executable, str(script), str(note)], *args, **kwargs)

        def alive(pid):
            handle = ctypes.windll.kernel32.OpenProcess(0x100000, False, pid)
            if not handle:
                return False
            try:
                return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) != 0
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)

        with patch.object(update_worker.subprocess, 'Popen', stand_in), \
                patch.object(update_worker, 'SMOKE_TIMEOUT', 3, create=True):
            with self.assertRaisesRegex(RuntimeError, 'startup check'):
                update_worker.smoke(target, work)
        pid = int(note.read_text())
        deadline = time.monotonic() + 5
        while alive(pid) and time.monotonic() < deadline:
            time.sleep(.1)
        left = alive(pid)
        if left:
            subprocess.run(['taskkill', '/F', '/PID', str(pid)], capture_output=True)
        self.assertFalse(left, 'the editor started by the startup check kept running')

    def test_failed_update_message_says_whether_files_were_restored(self):
        plan, target, work, before = self.staged_release()
        real = update_worker.replace_file

        def broken_backup(source, destination):
            if 'backup' in Path(source).parts:
                raise OSError('simulated restore failure')
            return real(source, destination)

        def failed_health(_target):
            raise RuntimeError('simulated startup failure')

        with patch.object(update_worker, 'replace_file', broken_backup):
            with self.assertRaises(OSError) as failure:  # the rollback failed too
                update_worker.install(plan, failed_health)
        self.assertTrue((target / update_worker.PENDING).exists())
        message = update_worker._install_error_message(failure.exception, work, target)
        self.assertNotIn('retained or restored', message)
        self.assertIn('not finished', message)
        self.assertIn('Open Dolly.exe again', message)
        update_worker.rollback(plan)
        self.assertEqual(self.installed(target, work), before)
        message = update_worker._install_error_message(failure.exception, work, target)
        self.assertIn('Your previous files were retained or restored', message)

    def test_rollback_removes_a_briefly_locked_recovery_record(self):
        plan, target, work, before = self.staged_release()
        marker = target / update_worker.PENDING
        real = Path.unlink
        held = []

        def locked_once(path, *args, **kwargs):
            if path == marker and not held:
                held.append(path)
                raise PermissionError(13, 'The file is in use (simulated)')
            return real(path, *args, **kwargs)

        def failed_health(_target):
            raise RuntimeError('simulated startup failure')

        with patch.object(Path, 'unlink', locked_once), patch.object(update_worker.time, 'sleep'):
            with self.assertRaises(RuntimeError) as failure:
                update_worker.install(plan, failed_health)
        self.assertEqual(held, [marker])
        self.assertEqual(self.installed(target, work), before)
        self.assertFalse(marker.exists())
        message = update_worker._install_error_message(failure.exception, work, target)
        self.assertIn('Your previous files were retained or restored', message)

    def test_update_poll_survives_one_failing_event(self):
        ui = self.ui()
        ui._status.side_effect = RuntimeError('widget gone')
        ui.events.put((1, 'status', 'x'))
        with self.assertLogs('dolly', level='ERROR'):
            ui.poll()
        ui.app.root.after.assert_called_once()


class LeftoverBoundaries(unittest.TestCase):
    def test_session_log_is_not_written_through_a_link(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            outside = root / 'outside'; outside.mkdir()
            session = root / 'session'
            if os.name == 'nt':
                import _winapi
                _winapi.CreateJunction(str(outside), str(session))
            else:
                session.symlink_to(outside, target_is_directory=True)
            try:
                session_cleanup._append_log(session, 'linked')
                self.assertFalse((outside / 'launch.log').exists())
            finally:
                os.rmdir(session) if os.name == 'nt' else session.unlink()
            session.mkdir()
            session_cleanup._append_log(session, 'plain')
            self.assertIn('plain', (session / 'launch.log').read_text(encoding='utf-8'))
            (session / 'launch.log').unlink()
            try:
                (session / 'launch.log').symlink_to(outside / 'elsewhere.log')
            except (OSError, NotImplementedError):
                pass  # Windows without symlink privileges.
            else:
                session_cleanup._append_log(session, 'linked file')
                self.assertFalse((outside / 'elsewhere.log').exists())

    def replay_app(self, folder, busy):
        app = SimpleNamespace(closed=False, busy=busy, replay_entries=['old'], submitted=[],
                              replay_folder=MagicMock(), root=MagicMock(), _filter_replays=MagicMock(),
                              replay_summary=MagicMock())
        app.replay_folder.get.side_effect = lambda: folder[0]
        app._submit = lambda label, function, callback: (not app.busy and app.submitted.append(callback) is None)
        from dolly import gui
        for name in ('_refresh_replays', '_retry_replay_refresh'):
            if hasattr(gui.DollyApp, name):
                setattr(app, name, getattr(gui.DollyApp, name).__get__(app))
        return app

    def test_replay_list_follows_a_folder_chosen_during_an_operation(self):
        # Chosen while another operation is running: read once it finishes.
        folder = ['B']
        app = self.replay_app(folder, busy=True)
        app._refresh_replays()
        self.assertEqual(app.submitted, [])
        app.root.after.assert_called_once()
        app.busy = False
        app.root.after.call_args.args[1]()
        self.assertEqual(len(app.submitted), 1)
        # Changed while the old folder was being read: discard and read again.
        folder[0] = 'C'
        app.submitted.pop()(['from B'])
        self.assertEqual(app.replay_entries, ['old'])
        self.assertEqual(len(app.submitted), 1)
        app.submitted.pop()(['from C'])
        self.assertEqual(app.replay_entries, ['from C'])

    def test_a_replay_dated_before_1970_still_lists_with_the_others(self):
        from dolly import gui
        from dolly.replays import ReplayEntry
        entries = [ReplayEntry(Path('new.dem'), 'new.dem', 1024, 1_700_000_000 * 10**9),
                   ReplayEntry(Path('old.dem'), 'old.dem', 1024, -10**9)]
        app = SimpleNamespace(replay_entries=entries, replay_search=MagicMock(), replay_sort=MagicMock(),
                              demo_path=MagicMock(), replay_tree=MagicMock(), replay_summary=MagicMock())
        app.replay_search.get.return_value = ''
        app.replay_sort.get.return_value = 'Name A-Z'
        app.demo_path.get.return_value = ''
        app.replay_tree.get_children.return_value = ()
        gui.DollyApp._filter_replays(app)
        self.assertEqual(app.replay_tree.insert.call_count, 2)
        rows = {call.kwargs['values'][0]: call.kwargs['values'][2]
                for call in app.replay_tree.insert.call_args_list}
        self.assertEqual(rows['old.dem'], '')
        self.assertTrue(rows['new.dem'].startswith('2023-11-'))
        app.replay_summary.set.assert_called_once_with('2 replays')


if __name__ == '__main__':
    unittest.main()
