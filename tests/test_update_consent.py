"""Desktop update discovery, exact-release consent, and asynchronous races."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import ANY, Mock, patch

from dolly import update_ui, updater


def release(version="9.0.1", *, digest="a" * 64, size=100):
    tag = "v" + version
    name = f"Deadlock_Dolly_{version}_Windows_x64.zip"
    return updater.select_release({
        "tag_name": tag, "draft": False, "prerelease": False,
        "html_url": "https://untrusted.example/notes",
        "assets": [{"name": name, "state": "uploaded", "size": size,
                    "digest": "sha256:" + digest,
                    "browser_download_url": f"https://github.com/{updater.REPOSITORY}/releases/download/{tag}/{name}"}],
    }, "0.1.0")


class UpdateConsentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.target = Path(directory.name)
        (self.target / updater.MANIFEST).write_text("fixture", encoding="utf-8")
        self.work = self.target / ".dolly-update-verified"
        self.jobs = []
        self.threads = []
        self.notice = Mock()
        self.notice_factory = self.patch(update_ui, "UpdateNotice", return_value=self.notice)
        self.patch(update_ui, "application_root", return_value=self.target)
        self.frozen = self.patch(update_ui, "is_frozen", return_value=True)
        self.thread_factory = self.patch(update_ui.threading, "Thread", side_effect=self.capture_thread)
        self.discovery = self.patch(updater, "check_latest", return_value=release())
        self.download = self.patch(updater, "download_update", return_value=self.work)
        self.network = self.patch(updater, "open_url")
        self.processes = self.patch(update_ui, "check_processes")
        self.worker = self.patch(update_ui, "launch_worker")
        self.browser = self.patch(update_ui.webbrowser, "open")
        self.manager = self.make_manager()

    def patch(self, owner, name, **kwargs):
        patcher = patch.object(owner, name, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def capture_thread(self, *, target, **kwargs):
        # Run the actual worker closure only when the test chooses, so clicks,
        # dismissal, dirty work, and queued completions can be interleaved.
        thread = Mock()
        thread.start.side_effect = lambda: self.jobs.append(target)
        self.threads.append(thread)
        return thread

    def make_manager(self, *, automatic=True, skip_startup=False, notebook=None, sidebar=None):
        app = SimpleNamespace(
            update_notice_parent=object(), app_settings=SimpleNamespace(auto_updates=automatic),
            root=Mock(), update_status=Mock(), update_check_button=Mock(),
            closed=False, busy=False, playing=False, dirty=False, startup_cancel=None,
            controller=Mock(), video_export=Mock(), _on_close=Mock(), notebook=notebook, sidebar=sidebar)
        app.controller.status.return_value = {"game_running": False}
        app.video_export.status.return_value = {"state": "idle"}
        app.root.grab_current.return_value = None
        return update_ui.UpdateUI(app, skip_startup=skip_startup)

    def run_job(self):
        self.assertTrue(self.jobs, "Expected a queued background worker")
        self.jobs.pop(0)()

    def discover(self):
        manager = self.manager
        manager.check()
        self.run_job()
        manager.poll()
        self.assertIsNotNone(manager.available)
        return manager

    def test_startup_preference_only_schedules_metadata_discovery_after_open(self):
        for automatic, skip, expected in ((True, False, True), (False, False, False),
                                          (True, True, False), (False, True, False)):
            with self.subTest(automatic=automatic, skip=skip):
                manager = self.make_manager(automatic=automatic, skip_startup=skip)
                calls = [(call.args[0], call.args[1]) for call in manager.app.root.after.call_args_list]
                self.assertIn((250, manager.poll), calls)
                self.assertEqual((2000, manager.check) in calls, expected)
        self.discovery.assert_not_called()
        self.download.assert_not_called()
        self.worker.assert_not_called()
        self.assertEqual(self.jobs, [])

    def test_source_or_missing_manifest_disables_checks_without_downloads(self):
        for source, manifest in ((True, True), (False, False)):
            with self.subTest(source=source):
                self.frozen.return_value = not source
                if not manifest:
                    (self.target / updater.MANIFEST).unlink()
                manager = self.make_manager()
                self.assertFalse(manager.enabled)
                manager.check()
                self.assertNotIn(2000, [call.args[0] for call in manager.app.root.after.call_args_list])
        self.discovery.assert_not_called()
        self.download.assert_not_called()
        self.worker.assert_not_called()
        self.assertEqual(self.jobs, [])

    def test_discovery_only_shows_notice_and_never_downloads_or_installs(self):
        manager = self.discover()
        self.discovery.assert_called_once_with(update_ui.__version__)
        self.network.assert_not_called()
        self.download.assert_not_called()
        self.processes.assert_not_called()
        self.worker.assert_not_called()
        self.assertIsNone(manager.approved)
        self.assertIsNone(manager.ready)
        self.notice.show.assert_called_with(manager.available["version"], manager.message, busy=False, error=False)

    def test_no_new_release_hides_notice_without_staging(self):
        self.discovery.return_value = None
        self.manager.check()
        self.run_job()
        self.manager.poll()
        self.assertIsNone(self.manager.available)
        self.notice.hide.assert_called()
        self.download.assert_not_called()
        self.worker.assert_not_called()

    def test_failed_discovery_keeps_installed_app_open(self):
        self.discovery.side_effect = OSError("offline")
        self.manager.check()
        self.run_job()
        self.manager.poll()
        self.assertFalse(self.manager.running)
        self.assertIn("offline", self.manager.message)
        self.assertIsNone(self.manager.approved)
        self.manager.app._on_close.assert_not_called()
        self.download.assert_not_called()
        self.worker.assert_not_called()

    def test_later_and_reopening_only_change_the_notice(self):
        manager = self.discover()
        selected = dict(manager.available)
        manager.later()
        self.assertTrue(manager.dismissed)
        self.assertIsNone(manager.approved)
        self.notice.collapse.assert_called_with(selected["version"])
        for action in (manager.poll, manager.reopen, manager.check, manager.poll):
            action()
        self.assertFalse(manager.dismissed)
        self.assertEqual(manager.available, selected)
        self.assertEqual(self.jobs, [])
        self.discovery.assert_called_once()
        self.download.assert_not_called()
        self.processes.assert_not_called()
        self.worker.assert_not_called()

    def test_update_now_click_approves_and_downloads_the_exact_release(self):
        manager = self.discover()
        selected = dict(manager.available)
        self.notice_factory.call_args.kwargs["update"]()
        self.assertEqual(manager.approved, manager.release_key(selected))
        self.assertTrue(manager.running)
        self.download.assert_not_called()
        self.worker.assert_not_called()
        manager.update_now()  # A double click cannot start a second download.
        self.assertEqual(len(self.jobs), 1)
        self.run_job()
        self.download.assert_called_once_with(selected, self.target, ANY)
        self.worker.assert_not_called()
        manager.poll()
        self.worker.assert_called_once_with(self.target.resolve(), self.work)
        manager.app._on_close.assert_called_once()
        self.assertTrue(manager.restarting)
        self.assertIsNone(manager.approved)

    def test_download_uses_approved_snapshot_and_changed_metadata_cannot_install(self):
        manager = self.discover()
        selected = dict(manager.available)
        manager.update_now()
        manager.available["sha256"] = "b" * 64
        self.run_job()
        manager.poll()
        self.download.assert_called_once_with(selected, self.target, ANY)
        self.worker.assert_not_called()
        self.assertIsNone(manager.ready)
        manager.restart()
        self.worker.assert_not_called()

    def test_all_existing_safety_gates_block_the_update_click_before_download(self):
        blockers = (
            lambda app: setattr(app, "busy", True),
            lambda app: setattr(app, "playing", True),
            lambda app: setattr(app, "dirty", True),
            lambda app: setattr(app, "startup_cancel", object()),
            lambda app: setattr(app.controller.status, "return_value", {"game_running": True}),
            lambda app: setattr(app.root.grab_current, "return_value", object()),
            lambda app: setattr(app.video_export.status, "return_value", {"state": "starting"}),
            lambda app: setattr(app.video_export.status, "return_value", {"state": "recording"}),
            lambda app: setattr(app.video_export.status, "return_value", {"state": "finalizing"}),
        )
        for index, block in enumerate(blockers):
            with self.subTest(blocker=index):
                manager = self.make_manager()
                manager.available = release()
                manager.approved = manager.release_key(manager.available)
                block(manager.app)
                manager.update_now()
                self.assertIsNone(manager.approved)
                self.assertFalse(manager.running)
                self.assertIn("click Update now", manager.message)
        self.assertEqual(self.jobs, [])
        self.processes.assert_not_called()
        self.download.assert_not_called()
        self.worker.assert_not_called()

    def test_process_guard_failure_revokes_approval_without_downloading(self):
        for error in (RuntimeError("another editor"), OSError("unreadable process")):
            with self.subTest(error=type(error).__name__):
                manager = self.make_manager()
                manager.available = release()
                self.processes.side_effect = error
                manager.update_now()
                self.assertIsNone(manager.approved)
                self.assertFalse(manager.restarting)
        self.download.assert_not_called()
        self.worker.assert_not_called()
        self.assertEqual(self.jobs, [])

    def test_becoming_dirty_during_download_requires_a_fresh_click(self):
        manager = self.discover()
        manager.update_now()
        manager.app.dirty = True
        self.run_job()
        manager.poll()
        self.assertIsNotNone(manager.ready)
        self.assertIsNone(manager.approved)
        self.worker.assert_not_called()
        manager.app.dirty = False
        manager.poll()
        manager.poll()
        self.worker.assert_not_called()
        manager.update_now()
        self.download.assert_called_once()
        self.worker.assert_called_once_with(self.target.resolve(), self.work)

    def test_later_revokes_inflight_consent_even_if_notice_is_reopened(self):
        manager = self.discover()
        manager.update_now()
        manager.later()
        manager.reopen()
        self.assertIsNone(manager.approved)
        self.run_job()
        manager.poll()
        self.assertIsNotNone(manager.ready)
        self.worker.assert_not_called()
        manager.check()
        manager.poll()
        self.worker.assert_not_called()
        manager.update_now()
        self.download.assert_called_once()
        self.worker.assert_called_once()

    def test_closed_app_discards_inflight_completion_and_consent(self):
        manager = self.discover()
        manager.update_now()
        manager.app.closed = True
        self.run_job()
        before = manager.app.root.after.call_count
        manager.poll()
        manager.update_now()
        manager.check()
        self.assertIsNone(manager.approved)
        self.assertEqual(manager.app.root.after.call_count, before)
        self.worker.assert_not_called()
        manager.app._on_close.assert_not_called()

    def test_stale_events_cannot_change_a_newer_approved_download(self):
        manager = self.discover()
        stale = manager.request_id
        manager.update_now()
        approved = manager.approved
        message = manager.message
        other = release("9.0.2")
        for kind, value in (("available", other), ("ready", (self.work, other)),
                            ("status", "obsolete status"), ("error", "obsolete error"), ("done", None)):
            manager.events.put((stale, kind, value))
        manager.poll()
        self.assertTrue(manager.running)
        self.assertEqual(manager.approved, approved)
        self.assertEqual(manager.release_key(manager.available), approved)
        self.assertEqual(manager.message, message)
        self.worker.assert_not_called()
        manager.later()
        self.run_job()
        manager.poll()
        self.worker.assert_not_called()

    def test_duplicate_ready_events_launch_only_one_worker(self):
        manager = self.discover()
        manager.update_now()
        self.run_job()
        manager.events.put((manager.request_id, "ready", (self.work, dict(manager.available))))
        manager.poll()
        manager.poll()
        self.worker.assert_called_once()
        manager.app._on_close.assert_called_once()

    def test_duplicate_metadata_events_do_not_download_or_grant_consent(self):
        manager = self.discover()
        selected = dict(manager.available)
        manager.events.put((manager.request_id, "available", selected))
        manager.events.put((manager.request_id, "available", selected))
        manager.poll()
        self.assertEqual(manager.available, selected)
        self.assertIsNone(manager.approved)
        self.assertEqual(self.jobs, [])
        self.download.assert_not_called()
        self.worker.assert_not_called()

    def test_changed_release_event_revokes_consent_before_an_older_payload_finishes(self):
        manager = self.discover()
        manager.update_now()
        changed = release("9.0.2")
        manager.events.put((manager.request_id, "available", changed))
        manager.poll()
        self.assertEqual(manager.available, changed)
        self.assertIsNone(manager.approved)
        self.run_job()
        manager.poll()
        self.assertEqual(manager.available, changed)
        self.assertIsNone(manager.ready)
        self.assertIsNone(manager.approved)
        self.worker.assert_not_called()

    def test_ready_payload_without_consent_never_installs_during_poll(self):
        manager = self.discover()
        manager.events.put((manager.request_id, "ready", (self.work, dict(manager.available))))
        for _ in range(4):
            manager.poll()
        self.assertIsNotNone(manager.ready)
        self.assertIsNone(manager.approved)
        self.processes.assert_not_called()
        self.worker.assert_not_called()
        manager.app._on_close.assert_not_called()

    def test_missing_or_mismatched_release_identity_rejects_restart(self):
        for field, value in (("version", "9.0.2"), ("url", "https://github.com/changed.zip"),
                             ("sha256", "b" * 64), ("size", 101), (None, None)):
            for mismatched in ("approved", "ready_release"):
                with self.subTest(field=field, mismatched=mismatched):
                    manager = self.make_manager()
                    manager.available = release()
                    key = manager.release_key(manager.available)
                    manager.approved = manager.ready_release = key
                    manager.ready = (self.work, manager.available["version"])
                    changed = None if field is None else manager.release_key({**manager.available, field: value})
                    setattr(manager, mismatched, changed)
                    manager.restart()
                    self.assertFalse(manager.restarting)
                    manager.app._on_close.assert_not_called()
        self.processes.assert_not_called()
        self.worker.assert_not_called()

    def test_download_failure_revokes_consent_and_does_not_retry_automatically(self):
        manager = self.discover()
        self.download.side_effect = ValueError("checksum mismatch")
        manager.update_now()
        self.run_job()
        manager.poll()
        self.assertIsNone(manager.approved)
        self.assertIsNone(manager.ready)
        self.assertIn("checksum mismatch", manager.message)
        manager.poll()
        manager.poll()
        self.download.assert_called_once()
        self.worker.assert_not_called()
        self.assertEqual(self.jobs, [])

    def test_failed_worker_handoff_consumes_consent_and_requires_a_new_click(self):
        manager = self.discover()
        self.worker.side_effect = OSError("cannot start helper")
        manager.update_now()
        self.run_job()
        manager.poll()
        self.assertIsNone(manager.approved)
        self.assertFalse(manager.restarting)
        manager.app._on_close.assert_not_called()
        manager.poll()
        self.worker.assert_called_once()
        self.worker.side_effect = None
        manager.update_now()
        self.assertEqual(self.worker.call_count, 2)
        self.download.assert_called_once()
        manager.app._on_close.assert_called_once()

    def test_highlights_toggle_inside_dolly_without_external_work_or_consent_changes(self):
        self.manager.open_notes()
        self.notice.toggle_notes.assert_not_called()
        self.browser.assert_not_called()
        manager = self.discover()
        before = (manager.approved, manager.ready, manager.ready_release, manager.request_id,
                  manager.running, manager.dismissed, dict(manager.available))
        self.notice_factory.call_args.kwargs["notes"]()
        self.notice_factory.call_args.kwargs["notes"]()
        self.assertEqual(self.notice.toggle_notes.call_count, 2)
        self.assertEqual((manager.approved, manager.ready, manager.ready_release, manager.request_id,
                          manager.running, manager.dismissed, manager.available), before)
        self.assertEqual(self.jobs, [])
        self.browser.assert_not_called()
        self.network.assert_not_called()
        self.processes.assert_not_called()
        self.download.assert_not_called()
        self.worker.assert_not_called()

    def test_discovered_highlights_are_forwarded_without_an_additional_request(self):
        selected = release()
        selected["notes"] = [dict(title="Camera editing", before="Clear could fail.",
                                  after="Cameras stay safe.")]
        self.discovery.return_value = selected
        manager = self.discover()
        self.notice.set_notes.assert_called_with(selected["notes"], release=manager.release_key(selected))
        self.discovery.assert_called_once_with(update_ui.__version__)
        manager.open_notes()
        self.browser.assert_not_called()
        self.network.assert_not_called()
        self.download.assert_not_called()
        self.worker.assert_not_called()
        self.assertIsNone(manager.approved)

    def test_older_metadata_without_highlights_uses_the_inline_empty_fallback(self):
        manager = self.discover()
        manager.available.pop("notes", None)
        manager.reopen()
        self.notice.set_notes.assert_called_with([], release=manager.release_key(manager.available))
        manager.open_notes()
        self.notice.toggle_notes.assert_called_once_with()
        self.browser.assert_not_called()
        self.network.assert_not_called()
        self.download.assert_not_called()
        self.worker.assert_not_called()

    def test_highlights_do_not_grant_or_revoke_an_inflight_update_approval(self):
        manager = self.discover()
        manager.update_now()
        before = (manager.approved, manager.ready, manager.ready_release, manager.request_id,
                  manager.running, manager.dismissed, len(self.jobs))
        manager.open_notes()
        manager.open_notes()
        self.assertEqual((manager.approved, manager.ready, manager.ready_release, manager.request_id,
                          manager.running, manager.dismissed, len(self.jobs)), before)
        self.notice.toggle_notes.assert_called()
        self.browser.assert_not_called()
        self.network.assert_not_called()
        self.download.assert_not_called()
        self.worker.assert_not_called()

    def test_full_release_notes_open_only_on_the_separate_validated_official_link(self):
        self.manager.open_full_notes()
        self.browser.assert_not_called()
        manager = self.discover()
        self.notice_factory.call_args.kwargs["full_notes"]()
        self.browser.assert_called_once_with(
            "https://github.com/cravvnn/deadlock-dolly/releases/tag/v9.0.1")
        self.notice.toggle_notes.assert_not_called()
        self.assertIsNone(manager.approved)
        self.network.assert_not_called()
        self.download.assert_not_called()
        self.worker.assert_not_called()

    def test_failed_notes_browser_leaves_the_update_unapproved(self):
        manager = self.discover()
        self.browser.side_effect = OSError("no browser")
        manager.open_full_notes()
        self.assertIsNone(manager.approved)
        self.assertIn("installed Dolly is unchanged", manager.message)
        self.download.assert_not_called()
        self.worker.assert_not_called()

    def test_closed_app_ignores_highlight_and_full_notes_callbacks(self):
        manager = self.discover()
        manager.app.closed = True
        self.notice_factory.call_args.kwargs["notes"]()
        self.notice_factory.call_args.kwargs["full_notes"]()
        self.notice.toggle_notes.assert_not_called()
        self.browser.assert_not_called()
        self.network.assert_not_called()
        self.download.assert_not_called()
        self.worker.assert_not_called()
        self.assertIsNone(manager.approved)

    def test_notes_budget_reserves_sidebar_request_and_adds_back_only_mapped_clip_height(self):
        notebook, sidebar = Mock(), Mock()
        manager = self.make_manager(notebook=notebook, sidebar=sidebar)
        notebook.winfo_height.return_value = 360
        notebook.winfo_reqheight.return_value = 2000
        sidebar.winfo_reqheight.return_value = 400
        sidebar.winfo_height.return_value = 10
        self.notice.scale = 1.5
        self.notice.notes_clip.winfo_ismapped.return_value = True
        self.notice.notes_clip.winfo_height.return_value = 110
        self.assertEqual(manager._notes_height_limit(), 470)  # New highlights use the page area: 360 + 110.
        notebook.winfo_reqheight.assert_not_called()
        sidebar.winfo_height.assert_not_called()
        notebook.winfo_height.return_value = 480
        self.notice.scale = 1
        self.notice.notes_clip.winfo_ismapped.return_value = False
        self.notice.notes_clip.winfo_height.reset_mock()
        self.notice.notes_clip.winfo_height.return_value = 1000
        self.assertEqual(manager._notes_height_limit(), 480)  # Hidden height contributes nothing.
        self.notice.notes_clip.winfo_height.assert_not_called()

    def test_notes_budget_is_invariant_when_the_mapped_clip_displaces_the_workspace(self):
        notebook, sidebar = Mock(), Mock()
        manager = self.make_manager(notebook=notebook, sidebar=sidebar)
        sidebar.winfo_reqheight.return_value = 400
        self.notice.scale = 1
        self.notice.notes_clip.winfo_ismapped.return_value = True
        for clip in (1, 40, 110, 168):
            with self.subTest(clip=clip):
                notebook.winfo_height.return_value = 480 - clip
                self.notice.notes_clip.winfo_height.return_value = clip
                self.assertEqual(manager._notes_height_limit(), 480)

    def test_notes_budget_never_returns_a_zero_or_negative_viewport(self):
        notebook, sidebar = Mock(), Mock()
        manager = self.make_manager(notebook=notebook, sidebar=sidebar)
        notebook.winfo_height.return_value = 0
        sidebar.winfo_reqheight.return_value = 400
        self.notice.scale = 1
        self.notice.notes_clip.winfo_ismapped.return_value = False
        self.assertEqual(manager._notes_height_limit(), 1)

    def test_notes_budget_uses_the_scaled_view_cap_when_layout_is_missing(self):
        self.notice.MAX_NOTES_HEIGHT = 168
        self.notice.scale = 1.5
        for notebook, sidebar in ((None, Mock()), (None, None)):
            with self.subTest(notebook=notebook, sidebar=sidebar):
                manager = self.make_manager(notebook=notebook, sidebar=sidebar)
                self.assertEqual(manager._notes_height_limit(), 252)
        del manager.app.notebook
        del manager.app.sidebar
        self.assertEqual(manager._notes_height_limit(), 252)
        self.notice.notes_clip.winfo_ismapped.assert_not_called()

    def test_workspace_configure_binding_fits_notes_without_granting_update_consent(self):
        notebook = Mock()
        manager = self.make_manager(notebook=notebook, sidebar=Mock())
        self.notice_factory.assert_called_with(
            manager.app.update_notice_parent, update=manager.update_now, notes=manager.open_notes,
            full_notes=manager.open_full_notes, later=manager.later, reopen=manager.reopen,
            height_limit=manager._notes_height_limit)
        notebook.bind.assert_called_once_with("<Configure>", ANY, add="+")
        configure = notebook.bind.call_args.args[1]
        manager.available = release()
        before = (manager.approved, manager.ready, manager.ready_release, manager.request_id,
                  manager.running, manager.dismissed, dict(manager.available), list(self.jobs))
        configure(SimpleNamespace(width=1000, height=720))
        self.notice.fit_height.assert_called_once_with()
        self.assertEqual((manager.approved, manager.ready, manager.ready_release, manager.request_id,
                          manager.running, manager.dismissed, manager.available, self.jobs), before)
        self.discovery.assert_not_called()
        self.network.assert_not_called()
        self.download.assert_not_called()
        self.worker.assert_not_called()
        self.browser.assert_not_called()
        self.processes.assert_not_called()

    def test_workspace_resize_preserves_an_inflight_exact_release_approval(self):
        notebook = Mock()
        manager = self.make_manager(notebook=notebook, sidebar=Mock())
        manager.available = release()
        manager.update_now()
        before = (manager.approved, manager.ready, manager.ready_release, manager.request_id,
                  manager.running, manager.dismissed, list(self.jobs), self.processes.call_count)
        self.assertIsNotNone(manager.approved)
        configure = notebook.bind.call_args.args[1]
        configure(SimpleNamespace(width=1180, height=820))
        self.notice.fit_height.assert_called_once_with()
        self.assertEqual((manager.approved, manager.ready, manager.ready_release, manager.request_id,
                          manager.running, manager.dismissed, self.jobs, self.processes.call_count), before)
        self.discovery.assert_not_called()
        self.network.assert_not_called()
        self.download.assert_not_called()
        self.worker.assert_not_called()
        self.browser.assert_not_called()
