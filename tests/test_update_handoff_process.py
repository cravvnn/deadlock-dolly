"""Public executable startup, safe handoff, recovery and package migration."""
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from dolly import release_launcher as boot, update_worker as worker, updater
REQUIRED = ("Dolly.exe", "DollyUpdater.exe", "_internal/native/bin/win64/DollyNative.dll", "_internal/third_party/ffmpeg/bin/ffmpeg.exe")

def package(folder, version):
    for name in REQUIRED:
        file = folder / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(version)
    updater.write_manifest(folder, version)



class LauncherTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.target = Path(directory.name) / "Dolly folder"
        self.target.mkdir()
        (self.target / updater.MANIFEST).write_text("fixture")


    def _start_writer(self, work, control, mode):
        # Fake install only. Successful process admission is scheduled before
        # launcher start; these Python processes are not packaged Dolly PIDs.
        script = '''
import json, os, sys, time
from pathlib import Path
from dolly import update_worker as w
target, work, control = map(Path, sys.argv[1:4])
mode = sys.argv[4]
deadline = time.monotonic() + 15
def wait(name):
    while not (control / name).exists():
        if time.monotonic() > deadline:
            raise TimeoutError(name)
        time.sleep(.005)
real = w._replace_retrying
def replace(temporary, destination):
    if Path(destination).name == 'Dolly.exe':
        if mode == 'crash':
            real(temporary, destination)
        w.atomic_json(control / 'ready.json', {'ready': True})
        wait('release')
        if mode == 'crash':
            os._exit(77)
    return real(temporary, destination)
w._replace_retrying = replace
try:
    with w.install_lock(target):
        if mode == 'contend':
            w.atomic_json(control / 'ready.json', {'acquired': True})
        else:
            w.atomic_json(control / 'admitted.json', {'admitted': True})
            wait('go')
            w.install({'target': str(target), 'work': str(work)})
except OSError:
    if mode != 'contend':
        raise
    w.atomic_json(control / 'ready.json', {'acquired': False})
'''
        return subprocess.Popen([sys.executable, "-c", script, str(self.target),
                                 str(work), str(control), mode],
                                cwd=Path(__file__).resolve().parents[1],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


    def _wait_writer(self, path, child):
        deadline = time.monotonic() + 10
        while not path.exists():
            if child.poll() is not None or time.monotonic() > deadline:
                self.fail("Writer did not reach " + path.name)
            time.sleep(.005)


    def test_admitted_update_keeps_recovery_resources_through_launcher_start(self):
        # Include a commit delayed until after launcher return, as a mapped
        # public executable can delay replacement while cleanup proceeds.
        for mode in ("complete", "crash"):
            with self.subTest(mode=mode):
                package(self.target, "1.0.0")
                work = self.target / (".dolly-update-" + mode)
                package(work / "payload", "1.0.1")
                worker.atomic_json(work / "plan.json", {"target": str(self.target), "work": str(work)})
                os.utime(work, (0, 0))
                before = {name: (self.target / name).read_bytes()
                          for name in (*updater.read_manifest(self.target)["files"], updater.MANIFEST)}
                with tempfile.TemporaryDirectory() as folder:
                    control = Path(folder)
                    child = self._start_writer(work, control, mode)
                    try:
                        self._wait_writer(control / "admitted.json", child)
                        finished = worker._workspace_finished

                        def interleave(selected, keep):
                            allowed = finished(selected, keep)
                            if selected == work and allowed:
                                (control / "go").touch()
                                self._wait_writer(control / "ready.json", child)
                            return allowed

                        with patch.object(worker, "_workspace_finished", side_effect=interleave), \
                             patch.object(boot, "open_editor") as editor, \
                             patch.object(worker, "notify") as notify:
                            code = boot.main([], target=self.target)
                        (control / "go").touch()
                        self._wait_writer(control / "ready.json", child)
                        preserved = (work / "backup/Dolly.exe").is_file() and (work / "journal.json").is_file()
                    finally:
                        (control / "go").touch()
                        (control / "release").touch()
                        try:
                            stdout, stderr = child.communicate(timeout=15)
                        except subprocess.TimeoutExpired:
                            child.kill()
                            child.communicate()
                            raise
                    self.assertEqual(code, 1)
                    editor.assert_not_called()
                    notify.assert_called_once()
                    self.assertTrue(preserved, "Startup removed active recovery resources")
                    self.assertEqual(child.returncode, 77 if mode == "crash" else 0, stdout + stderr)
                if mode == "crash":
                    worker.rollback({"target": str(self.target), "work": str(work)})
                    self.assertEqual({name: (self.target / name).read_bytes() for name in before}, before)
                else:
                    manifest = updater.read_manifest(self.target)
                    self.assertEqual(manifest["version"], "1.0.1")
                    self.assertTrue(all(updater.digest_file(self.target / name) == digest
                                        for name, digest in manifest["files"].items()))
                self.assertIsNone(worker.pending_work(self.target))


    def test_startup_excludes_writer_until_editor_handoff_returns(self):
        work = self.target / ".dolly-update-old"
        work.mkdir()
        os.utime(work, (0, 0))
        with tempfile.TemporaryDirectory() as folder:
            control = Path(folder)
            observations = []

            def contender(*args):
                child = self._start_writer(work, control, "contend")
                try:
                    stdout, stderr = child.communicate(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.communicate()
                    raise
                self.assertEqual(child.returncode, 0, stdout + stderr)
                observations.append(json.loads((control / "ready.json").read_text())["acquired"])
                return True

            with patch.object(worker, "_workspace_finished", side_effect=contender), \
                 patch.object(boot, "open_editor", side_effect=contender):
                self.assertEqual(boot.main([], target=self.target), 0)
            self.assertEqual(observations, [False, False])
        self.assertFalse(work.exists())
        with worker.install_lock(self.target):
            pass  # Startup released ownership after process handoff.
