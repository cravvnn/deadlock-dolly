"""Native release gates are inspected without executing game or fixture DLLs."""
import json
import re
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import build_native
from release_files import sha256


class NativePackagingTests(unittest.TestCase):
    def test_reviewed_module_profiles_match_both_launcher_and_native_pins(self):
        from dolly.launcher import NATIVE_GAME_SHA256
        root = TOOLS.parent
        profiles = [json.loads(p.read_text()) for p in (root / "native/profiles").glob("*.json")]
        for module, relative, key, symbol, file in (
            ("client", "citadel/bin/win64/client.dll", "client_sha256", "Client", "bridge_win.cpp"),
            ("engine", "bin/win64/engine2.dll", "sha256", "Engine", "bridge_win.cpp"),
            ("tier0", "bin/win64/tier0.dll", "sha256", "Tier0", "native_effects_win.hpp"),
        ):
            with self.subTest(module=module):
                pins = set(NATIVE_GAME_SHA256[relative])
                source = (root / "native/src" / file).read_text()
                native_pins = set(re.findall(r'constexpr char k(?:Updated)?' + symbol + r'Hash\[\]="([a-f0-9]{64})";', source))
                self.assertEqual(pins, {p[module][key] for p in profiles})
                self.assertEqual(pins, native_pins)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="Dolly native package ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def pe_fixture(self):
        image = SimpleNamespace(
            FILE_HEADER=SimpleNamespace(Machine=0x8664, Characteristics=0x2022),
            OPTIONAL_HEADER=SimpleNamespace(Magic=0x20B),
            DIRECTORY_ENTRY_EXPORT=SimpleNamespace(symbols=[
                SimpleNamespace(name=b"CreateInterface"),
                SimpleNamespace(name=b"DollyNativeProtocolVersion"),
                SimpleNamespace(name=b"DollyAtomicExchange32"),
                SimpleNamespace(name=b"DollyAtomicExchange64"),
                SimpleNamespace(name=b"DollyAtomicCompareExchange32")]),
            DIRECTORY_ENTRY_IMPORT=[SimpleNamespace(dll=b"KERNEL32.dll")],
            parse_data_directories=Mock(), close=Mock())
        module = SimpleNamespace(PE=Mock(return_value=image), DIRECTORY_ENTRY={
            "IMAGE_DIRECTORY_ENTRY_EXPORT": 0, "IMAGE_DIRECTORY_ENTRY_IMPORT": 1})
        return image, module

    def runtime_fixture(self):
        native = self.root / "native"
        dll = native / build_native.DLL_RELATIVE
        dll.parent.mkdir(parents=True)
        dll.write_bytes(b"Dolly test fixture, never executable")
        info = {"abi": 2, "sha256": sha256(dll)}
        metadata = native / "build_info.json"
        metadata.write_text(json.dumps(info))
        profiles = native / "profiles"
        profiles.mkdir()
        (profiles / "supported-build.json").write_text('{"profile": "fixture"}')
        return native, dll, metadata

    def test_pe_gate_checks_x64_dll_and_required_exports_without_loading(self):
        image, module = self.pe_fixture()
        with patch.dict(sys.modules, {"pefile": module}):
            report = build_native.verify_native_dll(self.root / "DollyNative.dll")
        self.assertEqual(report["machine"], "x64")
        self.assertEqual(set(report["exports"]), build_native.REQUIRED_EXPORTS)
        image.close.assert_called_once()

    def test_pe_gate_rejects_wrong_architecture_executable_missing_exports_and_dynamic_runtime(self):
        for problem in ("x86", "exe", "pe32", "export", "runtime"):
            image, module = self.pe_fixture()
            if problem == "x86":
                image.FILE_HEADER.Machine = 0x14C
            elif problem == "exe":
                image.FILE_HEADER.Characteristics &= ~0x2000
            elif problem == "pe32":
                image.OPTIONAL_HEADER.Magic = 0x10B
            elif problem == "export":
                image.DIRECTORY_ENTRY_EXPORT.symbols.pop()
            else:
                image.DIRECTORY_ENTRY_IMPORT.append(SimpleNamespace(dll=b"VCRUNTIME140.dll"))
            with self.subTest(problem=problem), patch.dict(sys.modules, {"pefile": module}):
                with self.assertRaises(ValueError):
                    build_native.verify_native_dll(self.root / "DollyNative.dll")
                image.close.assert_called_once()

    def test_runtime_allowlist_omits_sources_vendor_builds_and_game_binaries(self):
        native, dll, metadata = self.runtime_fixture()
        for relative in ("src/bridge_win.cpp", "include/dolly_protocol.hpp", "build/test.exe",
                         "vendor/minhook/src/hook.c", "client.dll", "engine2.dll", "tier0.dll",
                         "profiles/private.bin"):
            path = native / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("must not be bundled")
        output = self.root / "bundle" / "_internal" / "native"
        with patch.object(build_native, "verify_native_dll", return_value={"machine": "x64"}):
            build_native.copy_native_runtime(self.root, output)
        self.assertEqual({p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()},
                         {"bin/win64/DollyNative.dll", "build_info.json", "profiles/supported-build.json"})
        self.assertEqual(sha256(output / build_native.DLL_RELATIVE), sha256(dll))

    def test_pe_gate_requires_every_atomic_export(self):
        for missing in ("DollyAtomicExchange32", "DollyAtomicExchange64", "DollyAtomicCompareExchange32"):
            image, module = self.pe_fixture()
            image.DIRECTORY_ENTRY_EXPORT.symbols = [
                symbol for symbol in image.DIRECTORY_ENTRY_EXPORT.symbols
                if symbol.name != missing.encode("ascii")]
            with self.subTest(missing=missing), patch.dict(sys.modules, {"pefile": module}):
                with self.assertRaisesRegex(ValueError, "required bridge exports"):
                    build_native.verify_native_dll(self.root / "DollyNative.dll")
                image.close.assert_called_once()

    def test_runtime_requires_matching_hash_supported_abi_and_object_profile(self):
        native, dll, metadata = self.runtime_fixture()
        original = metadata.read_text()
        cases = [({"abi": 2, "sha256": "0" * 64}, "hash"),
                 ({"abi": 1, "sha256": sha256(dll)}, "ABI"),
                 ({"abi": True, "sha256": sha256(dll)}, "ABI")]
        for info, message in cases:
            metadata.write_text(json.dumps(info))
            with self.subTest(info=info), self.assertRaisesRegex(ValueError, message):
                build_native.runtime_files(self.root)
        metadata.write_text(original)
        (native / "profiles" / "supported-build.json").write_text("[]")
        with patch.object(build_native, "verify_native_dll", return_value={}):
            with self.assertRaisesRegex(ValueError, "JSON object"):
                build_native.runtime_files(self.root)

    def test_existing_unexpected_native_runtime_files_fail_release(self):
        self.runtime_fixture()
        output = self.root / "bundle" / "native"
        output.mkdir(parents=True)
        (output / "bridge_win.cpp").write_text("old source copy")
        with patch.object(build_native, "verify_native_dll", return_value={}):
            with self.assertRaisesRegex(RuntimeError, "Unexpected files"):
                build_native.copy_native_runtime(self.root, output)

    def test_game_binaries_are_rejected_anywhere_in_binary_bundle(self):
        for name in ("client.dll", "ENGINE2.DLL", "tier0.dll"):
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "_internal" / "unintended" / name
                path.parent.mkdir(parents=True)
                path.write_bytes(b"private game fixture")
                with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, "never enter"):
                    build_native.reject_game_binaries(Path(folder))

    def test_non_windows_native_build_fails_before_running_compiler(self):
        with patch.object(sys, "platform", "linux"), patch("subprocess.run") as run:
            with self.assertRaisesRegex(RuntimeError, "Windows x64"):
                build_native.build_native(self.root)
        run.assert_not_called()

    def test_windows_build_runs_cmake_then_ctest_and_hashes_result(self):
        native, dll, metadata = self.runtime_fixture()
        with patch.object(sys, "platform", "win32"), \
                patch.object(build_native.platform, "machine", return_value="AMD64"), \
                patch.object(build_native.struct, "calcsize", return_value=8), \
                patch.object(build_native, "verify_native_dll", return_value={"machine": "x64"}), \
                patch("subprocess.run") as run:
            info = build_native.build_native(self.root)
        self.assertEqual([call.args[0][0] for call in run.call_args_list], ["cmake", "cmake", "ctest"])
        configure = run.call_args_list[0].args[0]
        self.assertIn("Visual Studio 17 2022", configure)
        self.assertEqual(configure[configure.index("-A") + 1], "x64")
        self.assertTrue(all(call.kwargs["check"] for call in run.call_args_list))
        self.assertEqual(info["sha256"], sha256(dll))
        self.assertEqual(info["abi"], 2)
        self.assertFalse(info["game_runtime_verified"])
        self.assertEqual(json.loads(metadata.read_text()), info)

    def test_failed_native_build_invalidates_old_metadata(self):
        native, dll, metadata = self.runtime_fixture()
        with patch.object(sys, "platform", "win32"), \
                patch.object(build_native.platform, "machine", return_value="AMD64"), \
                patch.object(build_native.struct, "calcsize", return_value=8), \
                patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "cmake")):
            with self.assertRaises(subprocess.CalledProcessError):
                build_native.build_native(self.root)
        self.assertFalse(metadata.exists())


if __name__ == "__main__":
    unittest.main()
