"""Failed admissions and export cleanup preserve ownership and caller results."""
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from dolly import camera_export, console
from dolly.ui.operations import OperationQueue


class AdmissionCleanupTests(unittest.TestCase):
    def test_console_setup_closes_unpublished_socket_and_retains_success(self):
        class Reader:
            def __init__(self, *args, **kwargs): pass
            def start(self): pass
            def join(self, **kwargs): pass
        for failure in ('option', 'timeout', None):
            with self.subTest(failure=failure):
                class OwnedSocket(socket.socket):
                    def setsockopt(self, *args):
                        if failure == 'option': raise OSError('option refused')
                        return super().setsockopt(*args)
                    def settimeout(self, value):
                        if failure == 'timeout': raise OSError('timeout refused')
                        return super().settimeout(value)
                sock = OwnedSocket()
                client = console.ConsoleClient()
                held = None
                try:
                    with patch.object(console.socket, 'create_connection', return_value=sock), \
                         patch.object(console.threading, 'Thread', Reader):
                        if failure:
                            try: client.connect()
                            except console.ConsoleError as exc: held = exc
                            self.assertIsNotNone(held)
                            self.assertIn(failure + ' refused', str(held))
                            self.assertFalse(client.is_connected)
                            client.close()
                            self.assertEqual(sock.fileno(), -1)
                        else:
                            client.connect()
                            self.assertTrue(client.is_connected)
                            self.assertNotEqual(sock.fileno(), -1)
                            self.assertEqual(sock.gettimeout(), .25)
                            client.close()
                            self.assertEqual(sock.fileno(), -1)
                finally: sock.close()

    def test_failed_notification_releases_reservation_without_admitting_nested_work(self):
        for error in (RuntimeError('notification refused'), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                q = OperationQueue()
                rejected, executed = [], []
                def starting(label):
                    self.assertFalse(q.submit('nested', lambda: executed.append('nested'),
                        started=lambda _: None, rejected=rejected.append))
                    raise error
                with self.assertRaises(type(error)) as caught:
                    q.submit('failed', lambda: executed.append('failed'), started=starting, rejected=rejected.append)
                self.assertIs(caught.exception, error)
                self.assertEqual(rejected, ['Please wait for the current operation to finish.'])
                self.assertTrue(q.jobs.empty())
                self.assertFalse(q.busy)
                self.assertTrue(q.submit('retry', lambda: executed.append('retry') or 42,
                    started=lambda _: None, rejected=rejected.append))
                worker = q.start(lambda: 'error')
                try:
                    kind, label, payload = q.events.get(timeout=5)
                    self.assertEqual((kind, label, payload[0]), ('done', 'retry', 42))
                    self.assertEqual(executed, ['retry'])
                finally:
                    q.shutdown()
                    worker.join(timeout=5)
                self.assertFalse(worker.is_alive())

    def test_camera_export_cleanup_preserves_collision_success_and_programming_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root/'camera.usda'
            target.write_bytes(b'user edit')
            cleanup_error = PermissionError('stage locked')
            with patch.object(Path, 'unlink', side_effect=cleanup_error):
                with self.assertRaises(FileExistsError):
                    camera_export.write_script(target, '#usda 1.0\n')
            self.assertEqual(target.read_bytes(), b'user edit')
            target = root/'complete.usda'
            # Exercise actual hard-link publication on Windows, where rename
            # normally consumes the stage; this is not native POSIX proof.
            with patch.object(os, 'rename', side_effect=os.link), \
                 patch.object(Path, 'unlink', side_effect=cleanup_error):
                camera_export.write_script(target, '#usda 1.0\n')
            self.assertEqual(target.read_bytes(), b'#usda 1.0\n')
            error = ValueError('programming error')
            with patch.object(Path, 'unlink', side_effect=error):
                with self.assertRaises(ValueError) as caught:
                    camera_export.write_script(root/'program.usda', 'camera')
            self.assertIs(caught.exception, error)


if __name__ == '__main__': unittest.main()
