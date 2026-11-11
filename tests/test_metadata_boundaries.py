"""Five owner regressions for inspection and optional metadata failure boundaries."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from dolly import bootstrap, crash_dumps, mod_policy, updater
from dolly.ui import art
from test_review_hardening import minidump


def vpk(path, rows, *, malformed=False):
    tree = b""
    for folder, name in rows:
        tree += (b"vmdl_c\0" + folder.encode() + b"\0" + name.encode() + b"\0"
                 + struct.pack("<IHHIIH", 0, 0, 0x7fff, 0, 1, 0xffff) + b"\0\0")
    tree += b"bad" if malformed else b"\0"
    path.write_bytes(struct.pack("<III", 0x55aa1234, 1, len(tree)) + tree)


class ReadSpy:
    def __init__(self, stream, reads):
        self.stream, self.reads = stream, reads

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.stream.__exit__(*args)

    def read(self, amount=-1):
        self.reads.append(amount)
        return self.stream.read(amount)


class MetadataBoundaryTests(unittest.TestCase):
    def test_mixed_content_cannot_override_incomplete_inspection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cases = [([("models", "skin")], True, False),
                     ([("panorama", "hud"), ("models", "skin")], True, False),
                     ([("panorama", "hud"), ("models", "skin")], False, True),
                     ([("panorama", "hud")], False, False)]
            for index, (rows, malformed, allowed) in enumerate(cases):
                mount = root / str(index); mount.mkdir()
                pack = mount / "pak01_dir.vpk"; vpk(pack, rows, malformed=malformed)
                before = pack.read_bytes()
                verdict = mod_policy.classify_mount(mount)
                self.assertEqual(verdict["allowed"], allowed)
                carried, excluded = mod_policy.split_mounts([("Game", str(index))], root)
                self.assertEqual(bool(carried), allowed)
                self.assertEqual(bool(excluded), not allowed)
                self.assertTrue(mod_policy.classify_mount(mount, allow_overrides=True)["allowed"])
                self.assertEqual(pack.read_bytes(), before)
            mount = root / "linked"; mount.mkdir(); (mount / "models").mkdir()
            target = root / "owned-target"; target.mkdir(); (target / "user.txt").write_bytes(b"keep")
            link = mount / "redirect"
            if os.name == "nt":
                import _winapi
                _winapi.CreateJunction(str(target), str(link))
            else:
                link.symlink_to(target, target_is_directory=True)
            try:
                self.assertFalse(mod_policy.classify_mount(mount)["allowed"])
                self.assertEqual((target / "user.txt").read_bytes(), b"keep")
            finally:
                link.rmdir() if os.name == "nt" else link.unlink()

    def test_minidump_actual_read_budget_survives_growth_after_stat(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "owned.dmp"; valid = bytes(minidump())
            path.write_bytes(valid); real_open = Path.open; reads = []
            def observe(p, *args, **kwargs):
                stream = real_open(p, *args, **kwargs)
                return ReadSpy(stream, reads) if p == path else stream
            with patch.object(Path, "open", observe):
                self.assertEqual(crash_dumps.analyse(path)["faulting_module"], "engine2.dll")
            self.assertEqual(reads, [64 * 1024 * 1024 + 1])
            def grow(p, *args, **kwargs):
                if p == path and (args[0] if args else kwargs.get("mode")) == "rb":
                    with real_open(p, "wb") as output:
                        output.write(valid + bytes(8192))
                return real_open(p, *args, **kwargs)
            with patch.object(crash_dumps, "MAX_ANALYSIS_BYTES", 4096), patch.object(Path, "open", grow):
                with self.assertRaisesRegex(ValueError, "too large"):
                    crash_dumps.analyse(path)
            path.write_bytes(valid + bytes(4096 - len(valid)))
            with patch.object(crash_dumps, "MAX_ANALYSIS_BYTES", 4096):
                self.assertEqual(crash_dumps.analyse(path)["access_address"], "0xBEEF")

    def test_source_bootstrap_keeps_admitted_child_after_log_failure(self):
        # The GUI request is intercepted; a real harmless child still inherits
        # the real log handle and must finish, never touching the editor/game.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); real_open = Path.open; real_popen = subprocess.Popen
            children, commands = [], []
            marker = root / "child.txt"
            class FaultLog:
                def __init__(self, stream): self.stream = stream
                def __enter__(self): return self
                def __exit__(self, *args): return self.stream.__exit__(*args)
                def fileno(self): return self.stream.fileno()
                def write(self, data): raise OSError("owned post-spawn logging failure")
            def open_log(p, *args, **kwargs):
                stream = real_open(p, *args, **kwargs)
                return FaultLog(stream) if p == root / "logs/Dolly_startup.log" else stream
            def spawn(command, **kwargs):
                commands.append(command)
                child = real_popen([sys.executable, "-B", "-c",
                    "from pathlib import Path; Path(" + repr(str(marker)) + ").write_text('alive'); print('child output')"], **kwargs)
                children.append(child)
                return child
            try:
                with patch.object(bootstrap.sys, "platform", "win32"), \
                     patch.object(bootstrap, "is_frozen", return_value=False), \
                     patch.object(bootstrap.subprocess, "CREATE_NO_WINDOW", getattr(subprocess, "CREATE_NO_WINDOW", 0), create=True), \
                     patch.object(bootstrap.subprocess, "Popen", spawn), patch.object(Path, "open", open_log):
                    pid = bootstrap.launch_gui(root)
                self.assertEqual(pid, children[0].pid)
            finally:
                for child in children:
                    try: child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        child.terminate(); child.wait(timeout=5)
                        raise
            self.assertEqual(commands, [[sys.executable, "-u", "-m", "dolly"]])
            self.assertEqual(marker.read_text(), "alive")
            self.assertIn("child output", (root / "logs/Dolly_startup.log").read_text())
            with patch.object(bootstrap, "is_frozen", return_value=False), \
                 patch.object(Path, "open", side_effect=PermissionError("open refused")):
                with self.assertRaises(PermissionError): bootstrap.launch_gui(root)

    def test_bad_completion_marker_does_not_block_later_verified_stage(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in (".dolly-update-a", ".dolly-update-b"):
                work = root / name; payload = work / "payload"; payload.mkdir(parents=True)
                for relative in ("Dolly.exe", "_internal/DollyApp.exe", "_internal/native/bin/win64/DollyNative.dll", "_internal/third_party/ffmpeg/bin/ffmpeg.exe"):
                    target = payload / relative; target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"owned inert payload")
                updater.write_manifest(payload, "0.7.28-beta")
                (work / "verified.json").write_text(json.dumps(dict(version="0.7.28-beta", sha256="a" * 64)), encoding="utf-8")
            marker = root / ".dolly-update-a/verified.json"
            recursive = b"[" * 10000 + b"]" * 10000
            marker.write_bytes(recursive)
            release = dict(version="0.7.28-beta", sha256="a" * 64)
            with patch.object(updater, "open_url", side_effect=AssertionError("network must not run")):
                self.assertEqual(updater.download_update(release, root), root / ".dolly-update-b")
            self.assertEqual(marker.read_bytes(), recursive)
            valid = json.dumps(release).encode()
            marker.write_bytes(valid + b" " * (65536 - len(valid)))
            self.assertEqual(updater.staged_payload(root, release["version"], release["sha256"]), marker.parent)
            marker.write_bytes(valid + b" " * 65536)
            self.assertEqual(updater.staged_payload(root, release["version"], release["sha256"]), root / ".dolly-update-b")

    def test_optional_art_recursion_and_growing_manifest_use_flat_fallback(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(art, "_REPORTED", set()):
            root = Path(folder); path = root / "manifest.json"; data = b"owned art"
            (root / "owned.bin").write_bytes(data)
            document = dict(version=1, files={"owned.bin": dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())},
                levels=dict(files=["owned.bin"], cycle=[0]), shapes=dict(poses=[]),
                sidebar_art=dict(files={"1": "owned.bin"}), nav_icons=dict(files={"1": "owned.bin"}))
            raw = json.dumps(document).encode(); path.write_bytes(raw)
            self.assertIsNotNone(art.load(directory=root))
            recursive = b"[" * 10000 + b"]" * 10000; path.write_bytes(recursive)
            self.assertIsNone(art.load(directory=root)); self.assertEqual(path.read_bytes(), recursive)
            real_open = Path.open; reads = []
            def grow(p, *args, **kwargs):
                if p == path and (args[0] if args else kwargs.get("mode")) == "rb":
                    with real_open(p, "wb") as output: output.write(raw + b" " * (art._MANIFEST_LIMIT + 1))
                    return ReadSpy(real_open(p, *args, **kwargs), reads)
                return real_open(p, *args, **kwargs)
            path.write_bytes(raw)
            with patch.object(Path, "open", grow): self.assertIsNone(art.load(directory=root))
            self.assertEqual(reads, [256 * 1024 + 1])
            path.write_bytes(raw + b" " * (art._MANIFEST_LIMIT - len(raw)))
            self.assertIsNotNone(art.load(directory=root))
