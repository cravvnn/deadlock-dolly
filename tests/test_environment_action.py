from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dolly.editor_actions import default_action_bindings, validate_action_bindings, EditorBinding
from dolly.editor_wire import EXTRA_ACTIONS
from dolly.environment_job import PanoramaJob
from dolly import environment_ui
from tests.test_environment_capture import Backend


class EnvironmentActionTests(unittest.TestCase):
    def test_no_default_conflict_and_old_ids_unchanged(self):
        bindings=default_action_bindings()
        self.assertIsNone(bindings['capture_environment'])
        self.assertEqual(bindings['panel'],EditorBinding('F8'))
        with self.assertRaisesRegex(ValueError,'assigned to both'):
            validate_action_bindings(dict(bindings,capture_environment=EditorBinding('F8')))
        self.assertEqual(26+EXTRA_ACTIONS.index('take_screenshot'),98)
        # The environment action is appended after every existing action so no
        # earlier ABI id moves (the old edition used 99, before Object Picker).
        self.assertEqual(EXTRA_ACTIONS[-1],'capture_environment')
        self.assertEqual(26+EXTRA_ACTIONS.index('capture_environment'),
                         26+len(EXTRA_ACTIONS)-1)

    def test_binding_survives_existing_settings_codec(self):
        from dolly.editor_actions import bindings_from_dict, bindings_to_dict
        bindings=validate_action_bindings({'capture_environment':EditorBinding('Mouse5')})
        self.assertEqual(bindings_from_dict(bindings_to_dict(bindings)),bindings)

    def test_square_size_updates_next_launch_and_saved_preferences(self):
        from dolly.settings import AppSettings
        app=SimpleNamespace(busy=False,playing=False,environment_size=Mock(),
                            launch_options=Mock(),app_settings=AppSettings(),_persist_preferences=Mock(return_value=True))
        app.environment_size.get.return_value='1024'
        app.launch_options.get.return_value=''
        self.assertTrue(environment_ui.apply_size(app))
        options='-windowed -noborder -w 1024 -h 1024'
        app.launch_options.set.assert_called_once_with(options)
        self.assertEqual(app._persist_preferences.call_args.args[0].launch_options,options)
        app.launch_options.get.return_value='-refresh 144 -w 1920 -h 1080'
        environment_ui.apply_size(app)
        self.assertEqual(app.launch_options.set.call_args.args[0], '-refresh 144 '+options)

    def test_standard_edition_never_dispatches_panorama(self):
        app=Mock()
        with patch('dolly.edition.is_environment',return_value=False):
            self.assertFalse(environment_ui.start(app))
        app._submit.assert_not_called()

    def test_missing_backend_is_explicit_and_never_submitted(self):
        app=SimpleNamespace(closed=False,busy=False,playing=False,controller=object(),
                            status_text=Mock(),_submit=Mock())
        with patch('dolly.edition.is_environment',return_value=True):
            self.assertFalse(environment_ui.start(app))
        app._submit.assert_not_called()
        self.assertIn('not ready',app.status_text.set.call_args.args[0])

    def test_environment_button_and_shortcut_request_overlap(self):
        for event in (None, {'tick':64,'paused':True,'pose':[1,2,3,0,0,0,1]}):
            backend=Mock()
            backend.view.return_value=dict(position=[1,2,3],tick=64,face_size=1024)
            app=SimpleNamespace(closed=False,busy=False,playing=False,status_text=Mock(),
                controller=SimpleNamespace(environment_backend=Mock(return_value=backend)),
                environment_folder=Mock(),environment_job=Mock())
            app.environment_folder.get.return_value='capture-folder'
            app._submit=lambda title, work, done:work()
            with patch('dolly.edition.is_environment',return_value=True):
                environment_ui.start(app,event)
            app.environment_job.run.assert_called_once_with(backend,'capture-folder',[1,2,3],64,
                face_size=1024,face_fov=100,rendered_reference=True)

    def test_assembly_happens_only_after_verified_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=Backend()
            def assemble(*args):
                self.assertEqual(backend.calls[-1],'restore')
                return args[1]
            with patch('dolly.environment_job.assemble_capture',side_effect=assemble) as assembler:
                output=PanoramaJob().run(backend,folder,[1,2,3],64,face_size=512,ffmpeg='fake')
            self.assertEqual(output.suffix,'.png')
            assembler.assert_called_once()

    def test_restore_failure_prevents_assembly(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=Backend(); backend.restore=Mock(side_effect=RuntimeError('restore failed'))
            with patch('dolly.environment_job.assemble_capture') as assembler:
                with self.assertRaisesRegex(RuntimeError,'restore failed'):
                    PanoramaJob().run(backend,folder,[1,2,3],64,face_size=512,ffmpeg='fake')
            assembler.assert_not_called()

    def test_repeated_press_is_not_queued(self):
        job=PanoramaJob(); job._lock.acquire()
        try:
            with self.assertRaisesRegex(RuntimeError,'already running'):
                job.run(Backend(),'.',[1,2,3],64)
        finally: job._lock.release()

    def test_cancel_between_submission_and_capture_is_not_cleared(self):
        from dolly.environment_capture import CaptureCancelled
        with tempfile.TemporaryDirectory() as folder:
            job=PanoramaJob();job.cancelled.set();backend=Backend()
            with self.assertRaises(CaptureCancelled):
                job.run(backend,folder,[1,2,3],64,face_size=512)
            self.assertEqual(backend.calls,[])
