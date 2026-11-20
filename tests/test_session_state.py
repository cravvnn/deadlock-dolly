"""Desktop state checks: what the window offers after a session or take changes.

No game, native DLL or Tk window is used; the real poll and recorder logic run
against simulated widgets, controller and bridge.
"""
import unittest
from unittest.mock import MagicMock, patch

from dolly import gui
from dolly.gui import DollyApp


def polled_app(status, **attributes):
    """Run one real interface poll against recording widgets."""
    app = MagicMock()
    app.closed = app.busy = app.playing = app.dragging = False
    app.startup_cancel = None
    app.operations.pending_events.return_value = []
    app.capture_buttons = []
    app.controller.status.return_value = status
    for name, value in attributes.items():
        setattr(app, name, value)
    with patch.object(gui, 'editor_session'):
        DollyApp._poll_once(app)
    return app


def button_state(button):
    return button.configure.call_args.kwargs['state']


class StartupControls(unittest.TestCase):
    def session(self, stage):
        return {'connected': True, 'unlocker_ready': True, 'game_running': True, 'playing': False,
                'startup_stage': stage, 'camera_backend': 'native', 'message': ''}

    def test_camera_check_stays_available_after_a_cancelled_replay_preparation(self):
        # Cancelling replay preparation tells the user to check camera support
        # again; that button has to be usable in the stage the cancel leaves.
        for stage in ('cancelled', 'failed', 'loading_replay', 'replay_ready'):
            with self.subTest(stage=stage):
                app = polled_app(self.session(stage))
                self.assertEqual(button_state(app.probe_button), 'normal')
        app = polled_app(self.session('unlocker_ready'))
        self.assertEqual(button_state(app.probe_button), 'disabled')


if __name__ == '__main__':
    unittest.main()
