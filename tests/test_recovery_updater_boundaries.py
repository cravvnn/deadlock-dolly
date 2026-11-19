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
            with patch.object(update_worker, 'notify') as notify,                     patch.object(release_launcher, 'open_editor') as editor:
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


if __name__ == '__main__':
    unittest.main()
