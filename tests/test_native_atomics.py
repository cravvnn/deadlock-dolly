"""Native atomic binding regressions; no kernel32 Interlocked exports required."""
import ctypes
import hashlib
import json
import mmap
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dolly import native_bridge as nb


class NativeAtomicTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="Dolly atomic loader ")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.native = self.root / "native"
        self.dll = self.native / "bin/win64/DollyNative.dll"
        self.dll.parent.mkdir(parents=True)
        data = bytearray(128)
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 60, 64)
        data[64:68] = b"PE\0\0"
        struct.pack_into("<H", data, 68, 0x8664)
        struct.pack_into("<H", data, 86, 0x2000)
        struct.pack_into("<H", data, 88, 0x20B)
        self.dll.write_bytes(data)  # Inert PE-header fixture; always mocked load.
        self.manifest = self.native / "build_info.json"
        self.write_manifest()
        self.resource_patch = patch.object(nb, "resource_root", return_value=self.root)
        self.resource_patch.start()
        self.addCleanup(self.resource_patch.stop)

    def write_manifest(self, **changes):
        info = {"abi": 2, "sha256": hashlib.sha256(self.dll.read_bytes()).hexdigest()}
        info.update(changes)
        self.manifest.write_text(json.dumps(info), encoding="utf-8")

    def library(self):
        # Exercise ctypes conversions and actual mapped bytes through C-callable
        # fixtures. Native Windows CTest verifies the wrappers' atomic semantics.
        def exchange(pointer, value):
            before = pointer[0]
            pointer[0] = value
            return before
        def compare(pointer, value, comparand):
            before = pointer[0]
            if before == comparand:
                pointer[0] = value
            return before
        return SimpleNamespace(
            DollyNativeProtocolVersion=Mock(return_value=2),
            DollyAtomicExchange32=ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.POINTER(ctypes.c_int32), ctypes.c_int32)(exchange),
            DollyAtomicExchange64=ctypes.CFUNCTYPE(ctypes.c_int64, ctypes.POINTER(ctypes.c_int64), ctypes.c_int64)(exchange),
            DollyAtomicCompareExchange32=ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.POINTER(ctypes.c_int32), ctypes.c_int32, ctypes.c_int32)(compare))

    def test_verified_dolly_library_loads_from_absolute_path_without_game_factory(self):
        library = self.library()
        library.CreateInterface = Mock(side_effect=AssertionError("Editor must not initialize game hooks"))
        with patch.object(nb.ctypes, "WinDLL", create=True, return_value=library) as load:
            self.assertIs(nb._load_atomic_library(), library)
        load.assert_called_once_with(str(self.dll.resolve()), use_last_error=True, winmode=0x1100)
        library.DollyNativeProtocolVersion.assert_called_once_with()
        library.CreateInterface.assert_not_called()

    def test_missing_or_untrusted_helper_fails_before_loading(self):
        for problem in ("abi", "bool_abi", "hash", "manifest", "missing"):
            self.write_manifest()
            if problem == "abi": self.write_manifest(abi=1)
            elif problem == "bool_abi": self.write_manifest(abi=True)
            elif problem == "hash": self.write_manifest(sha256="0" * 64)
            elif problem == "manifest": self.manifest.write_text("[]")
            else: self.manifest.unlink()
            with self.subTest(problem=problem), patch.object(nb.ctypes, "WinDLL", create=True) as load:
                with self.assertRaises(nb.NativeBridgeError): nb._load_atomic_library()
                load.assert_not_called()

    def test_wrong_architecture_executable_and_truncated_pe_fail_before_loading(self):
        original = self.dll.read_bytes()
        for problem in ("x86", "exe", "pe32", "truncated"):
            data = bytearray(original)
            if problem == "x86": struct.pack_into("<H", data, 68, 0x14C)
            elif problem == "exe": struct.pack_into("<H", data, 86, 0)
            elif problem == "pe32": struct.pack_into("<H", data, 88, 0x10B)
            else: data = data[:80]
            self.dll.write_bytes(data)
            self.write_manifest()
            with self.subTest(problem=problem), patch.object(nb.ctypes, "WinDLL", create=True) as load:
                with self.assertRaisesRegex(nb.NativeBridgeError, "Windows x64"):
                    nb._load_atomic_library()
                load.assert_not_called()

    def test_loaded_wrong_protocol_and_os_failure_are_actionable(self):
        library = self.library()
        library.DollyNativeProtocolVersion.return_value = 1
        with patch.object(nb.ctypes, "WinDLL", create=True, return_value=library):
            with self.assertRaisesRegex(nb.NativeBridgeError, "different protocol"):
                nb._load_atomic_library()
        with patch.object(nb.ctypes, "WinDLL", create=True, side_effect=OSError("Load failed")):
            with self.assertRaisesRegex(nb.NativeBridgeError, "could not load"):
                nb._load_atomic_library()

    def test_real_mapping_uses_dolly_exports_and_preserves_unsigned_sequence_and_heartbeat(self):
        memory = mmap.mmap(-1, nb.MAPPING_BYTES)
        library = self.library()  # Deliberately has no kernel32 export names.
        with patch.object(nb, "os", SimpleNamespace(name="nt", getpid=os.getpid)), \
                patch.object(nb.ctypes, "WinDLL", create=True, return_value=library) as load:
            bridge = nb.NativeBridge.create(mapping_factory=lambda *args: memory, start_heartbeat=False)
        self.addCleanup(bridge.close)
        self.assertIs(bridge._atomic_library, library)
        self.assertEqual(load.call_args.args[0], str(self.dll.resolve()))
        self.assertEqual(nb.CONTROL.unpack(memory[:nb.CONTROL.size])[:2], (nb.CONTROL_MAGIC, 2))
        bridge._store(nb.CONTROL_BYTES + 8, 0xFFFFFFFE)
        self.assertEqual(bridge._load_sequence(), 0xFFFFFFFE)
        bridge._heartbeat = 0xFFFFFFFF
        bridge._beat()
        self.assertEqual(struct.unpack_from("<Q", memory, 32)[0], 0x100000000)
        bridge._store(32, 0xFFFFFFFFFFFFFFFF, 64)
        self.assertEqual(struct.unpack_from("<Q", memory, 32)[0], 0xFFFFFFFFFFFFFFFF)
        bridge._sequence = 0xFFFFFFFE
        bridge._publish(0)
        self.assertEqual(struct.unpack_from("<I", memory, 12)[0], 0)
        bridge.close()
        self.assertTrue(memory.closed)

    def test_missing_atomic_export_closes_mapping_and_reports_old_package(self):
        for name in ("DollyAtomicExchange32", "DollyAtomicExchange64", "DollyAtomicCompareExchange32"):
            memory = mmap.mmap(-1, nb.MAPPING_BYTES)
            library = self.library()
            delattr(library, name)
            with self.subTest(export=name), \
                    patch.object(nb, "os", SimpleNamespace(name="nt", getpid=os.getpid)), \
                    patch.object(nb.ctypes, "WinDLL", create=True, return_value=library):
                with self.assertRaisesRegex(nb.NativeBridgeError, "older build"):
                    nb.NativeBridge.create(mapping_factory=lambda *args: memory, start_heartbeat=False)
                self.assertTrue(memory.closed)


if __name__ == "__main__":
    unittest.main()
