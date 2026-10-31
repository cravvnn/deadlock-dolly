"""Real Windows child/window checks for quiet build execution; no game access."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from build_desktop import desktop_name


@unittest.skipUnless(sys.platform == 'win32', 'Windows build desktop contract')
class BuildDesktopTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='Dolly build probe ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.script = self.root / 'probe with spaces.py'
        self.output = self.root / 'result.json'
        self.script.write_text(f'''import sys,json,os,msvcrt
from pathlib import Path
sys.path.insert(0,{str(ROOT / 'tools')!r})
from build_desktop import isolate_build,desktop_name
handles=[msvcrt.get_osfhandle(s.fileno()) for s in (sys.stdout,sys.stderr)]
before=[os.get_handle_inheritable(h) for h in handles]
code=isolate_build()
if code is not None:
    assert before==[os.get_handle_inheritable(h) for h in handles]
    print('PARENT_STD_FLAGS_RESTORED',flush=True)
    raise SystemExit(code)
import tkinter as tk
root=tk.Tk();root.update()
Path(sys.argv[1]).write_text(json.dumps(dict(desktop=desktop_name(),mapped=bool(root.winfo_ismapped()))))
root.destroy()
print('BUILD_STDOUT_RETAINED',flush=True)
print('BUILD_STDERR_RETAINED',file=sys.stderr,flush=True)
raise SystemExit(7)
''', encoding='utf-8')
        self.environment = dict(os.environ)
        self.environment.pop('DOLLY_BUILD_TEST_DESKTOP', None)

    def run_probe(self):
        return subprocess.run([sys.executable, str(self.script), str(self.output)],
                              env=self.environment, capture_output=True, text=True, timeout=30)

    def test_window_is_private_and_logs_exit_code_and_inheritance_survive(self):
        original = desktop_name()
        child = self.run_probe()
        self.assertEqual(child.returncode, 7, (child.stdout, child.stderr))
        facts = json.loads(self.output.read_text())
        self.assertTrue(facts['desktop'].startswith('DollyBuild_'))
        self.assertNotEqual(facts['desktop'], original)
        self.assertTrue(facts['mapped'])
        self.assertIn('BUILD_STDOUT_RETAINED', child.stdout)
        self.assertIn('PARENT_STD_FLAGS_RESTORED', child.stdout)
        self.assertIn('BUILD_STDERR_RETAINED', child.stderr)
        self.assertEqual(desktop_name(), original)

    def test_environment_marker_cannot_bypass_private_desktop_verification(self):
        self.environment['DOLLY_BUILD_TEST_DESKTOP'] = 'NotTheWorkerDesktop'
        child = self.run_probe()
        self.assertNotEqual(child.returncode, 0)
        self.assertFalse(self.output.exists())
        self.assertIn('refusing visible UI checks', child.stderr)
