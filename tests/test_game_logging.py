"""Real throwaway Python children only. Never invokes Deadlock."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from dolly.game_logging import spawn, gui_stdio, append_transcript, diagnostic_log

PROBE = r'''
import ctypes, json, os, sys
from ctypes import wintypes as w
api=ctypes.WinDLL('kernel32',use_last_error=True)
api.GetStdHandle.argtypes=[w.DWORD];api.GetStdHandle.restype=w.HANDLE
api.GetFileType.argtypes=[w.HANDLE];api.GetFileType.restype=w.DWORD
handle=api.GetStdHandle(w.DWORD(-11))
kind=api.GetFileType(handle)
os.write(2,b'early stderr retained\n')
facts=dict(stdout_null=not handle,stdout_file_type=kind,valve_console_classification=bool(kind))
open(sys.argv[1],'w').write(json.dumps(facts))
os.write(2,b'late stderr retained\n')
if len(sys.argv)>2:raise RuntimeError('probe startup/exit failure retained')
'''

@unittest.skipUnless(os.name == "nt", "Windows standard-handle contract")
class GuiStdioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
    def probe(self, compatible, failure=False):
        facts = self.root / "facts.json"
        # Direct GUI executable like deadlock.exe. The venv redirector starts
        # another interpreter and reconstructs its inherited standard handles.
        executable = Path(sys._base_executable).with_name("pythonw.exe")
        command = [str(executable), "-c", PROBE, str(facts)] + (["fail"] if failure else [])
        output = self.root / "stderr.log"
        with output.open("wb") as stream:
            if compatible:
                child = spawn(command, stream, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stream,
                    stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
            code = child.wait(timeout=10)
        return code, json.loads(facts.read_text()), output.read_text()
    def test_current_file_redirection_triggers_valve_console_classification(self):
        code, facts, log = self.probe(False)
        self.assertEqual(code, 0); self.assertTrue(facts["valve_console_classification"])
        self.assertEqual(facts["stdout_file_type"], 1)
    def test_candidate_avoids_console_classification_and_retains_stderr(self):
        code, facts, log = self.probe(True)
        self.assertEqual(code, 0); self.assertTrue(facts["stdout_null"], facts)
        self.assertFalse(facts["valve_console_classification"])
        self.assertEqual(log.splitlines(), ["early stderr retained", "late stderr retained"])
    def test_child_exception_evidence_is_retained(self):
        code, facts, log = self.probe(True, True)
        self.assertNotEqual(code, 0)
        self.assertIn("probe startup/exit failure retained", log)
        self.assertFalse(facts["valve_console_classification"])
    def test_stdout_pipe_still_triggers_the_same_console_gate(self):
        facts = self.root / "pipe.json"
        executable = Path(sys._base_executable).with_name("pythonw.exe")
        with (self.root / "pipe-errors.log").open("wb") as errors:
            child = subprocess.Popen([str(executable), "-c", PROBE, str(facts)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors,
                creationflags=subprocess.CREATE_NO_WINDOW)
            child.communicate(timeout=10)
        result = json.loads(facts.read_text())
        self.assertEqual(child.returncode, 0)
        self.assertEqual(result["stdout_file_type"], 3)
        self.assertTrue(result["valve_console_classification"])
    def test_handle_inheritance_restored_on_creation_failure(self):
        import msvcrt
        with (self.root / "failure.log").open("wb") as stream:
            handle = msvcrt.get_osfhandle(stream.fileno())
            original = os.get_handle_inheritable(handle)
            with self.assertRaises(FileNotFoundError): spawn([str(self.root / "missing.exe")], stream)
            self.assertEqual(os.get_handle_inheritable(handle), original)
    def test_only_named_handles_are_exposed(self):
        with (self.root / "handles.log").open("wb") as stream:
            with gui_stdio(stream) as options:
                self.assertTrue(options["close_fds"])
                info = options["startupinfo"]
                self.assertEqual(info.hStdOutput, 0)
                self.assertNotIn(0, info.lpAttributeList["handle_list"])
                self.assertEqual(set(info.lpAttributeList["handle_list"]), {info.hStdInput, info.hStdError})

class DurableLogTests(unittest.TestCase):
    def test_transcript_keeps_initial_output_across_reconnect_and_caps_utf8(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "console.log"
            append_transcript(path, "startup failure detail\n", limit=512)
            append_transcript(path, "\u00e9" * 1000, limit=512)
            before = path.read_bytes()
            append_transcript(path, "later connection", limit=512)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(len(before), 512)
            self.assertTrue(before.decode("utf-8").startswith("startup failure detail\n"))
            self.assertEqual(before.count(b"transcript limit reached"), 1)

    def test_support_log_keeps_boot_and_exit_when_middle_is_omitted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "engine.log"
            path.write_bytes(b"BOOT FAILURE EVIDENCE\n" + b"x" * 2000 + b"\nEXIT FAILURE EVIDENCE")
            text = diagnostic_log(path, limit=512)
            self.assertLessEqual(len(text.encode("utf-8")), 512)
            self.assertIn("BOOT FAILURE EVIDENCE", text)
            self.assertIn("EXIT FAILURE EVIDENCE", text)
            self.assertIn("omitted the middle", text)

    def test_real_console_transcript_and_disk_failure_do_not_break_requests(self):
        from test_console import Peer
        from dolly.console import ConsoleClient
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            for protocol in ("netcon", "vconsole"):
                peer = Peer(protocol=protocol)
                self.addCleanup(peer.close)
                path = Path(directory) / (protocol + ".log")
                client = ConsoleClient(protocol=protocol, transcript_path=path)
                self.addCleanup(client.close)
                client.connect(port=peer.port)
                self.assertIn("EARLY_HOOK_ERROR", client.request("echo EARLY_HOOK_ERROR"))
                self.assertIn("EARLY_HOOK_ERROR", path.read_text())
                with patch("dolly.game_logging.append_transcript", side_effect=OSError("disk unavailable")), self.assertLogs("dolly.console", level="WARNING"):
                    self.assertIn("AFTER_DISK_FAILURE", client.request("echo AFTER_DISK_FAILURE"))
                self.assertEqual(client.transcript_error, "disk unavailable")
                self.assertTrue(client.is_connected)

if __name__ == "__main__": unittest.main()
