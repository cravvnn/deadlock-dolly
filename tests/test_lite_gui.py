import tkinter as tk
import unittest
from unittest.mock import Mock, patch
from dolly.gui import DollyApp
from dolly.settings import AppSettings

class EditorLauncherTests(unittest.TestCase):
    def test_normal_launch_opens_our_editor_without_a_lite_choice(self):
        try:
            root=tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        app=None
        try:
            with patch.object(DollyApp,'_submit',return_value=False),patch.object(DollyApp,'_load_app_settings',return_value=AppSettings(auto_updates=False)):
                app=DollyApp(root)
            app.game_path.set('C:/game/citadel.exe')
            app.demo_path.set('C:/replays/test.dem')
            app.launch_options.set('-windowed -w 1920')
            app.controller=Mock()
            app._disable_external_input=Mock()
            app._submit=Mock(return_value=True)
            self.assertFalse(hasattr(app, 'launch_lite_button'))
            self.assertEqual(app.play_replay_button.cget('text'), 'Open replay in Dolly')
            app.play_replay_button.invoke()
            start=app._submit.call_args.args[1]
            with patch('dolly.gui.save_settings') as save:
                start()
            call=app.controller.start_editing.call_args
            self.assertTrue(call.kwargs['native'])
            self.assertEqual(call.kwargs['launch_options'],'-windowed -w 1920')
            self.assertEqual(save.call_args.args[0].launch_options,'-windowed -w 1920')
            self.assertNotIn('nvidia_dlss', call.kwargs)
            from dataclasses import replace
            app.app_settings = replace(app.app_settings, nvidia_dlss=True)
            app.play_replay_button.invoke()
            with patch('dolly.gui.save_settings'):
                app._submit.call_args.args[1]()
            self.assertIs(app.controller.start_editing.call_args.kwargs['nvidia_dlss'], True)
            app._launch()
            app._submit.call_args.args[1]()
            self.assertIs(app.controller.launch.call_args.kwargs['nvidia_dlss'], True)
            app.launch_options.set('-windowed -dolly-lite -w 1920')
            app.play_replay_button.invoke()
            with patch('dolly.gui.save_settings'):
                app._submit.call_args.args[1]()
            self.assertNotIn('-dolly-lite',app.controller.start_editing.call_args.kwargs['launch_options'])
        finally:
            if app is not None:
                app.closed = True
                app.startup_bar.stop()
                app.operations.shutdown()
            # The test invokes startup without running the normal completion
            # callback. Cancel its pending poll and progress timers before
            # destroying this root so they cannot fire in a later Tk test.
            for timer in root.tk.splitlist(root.tk.call("after", "info")):
                root.tk.call("after", "cancel", timer)
            root.destroy()

if __name__=='__main__':unittest.main()
