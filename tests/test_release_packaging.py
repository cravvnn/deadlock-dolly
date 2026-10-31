"""Source export privacy and embedded-icon release gates."""
from contextlib import redirect_stderr
import io
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import release_files
import build_windows


class ReleasePackagingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="Dolly source ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_source_zip_includes_workflow_but_omits_unlisted_user_data(self):
        for name in ["dolly/app.py", ".github/workflows/windows.yml", "logs/private.log", "shot.dem", ".env"]:
            file = self.root / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("fixture")
        (self.root / "SOURCE_FILES.txt").write_text("SOURCE_FILES.txt\ndolly/app.py\n.github/workflows/windows.yml\n")
        archive = release_files.source_zip(self.root, self.root / "result.zip")
        with zipfile.ZipFile(archive) as package:
            self.assertEqual(set(package.namelist()), {"SOURCE_FILES.txt", "dolly/app.py", ".github/workflows/windows.yml"})
            self.assertIsNone(package.testzip())

    def test_export_rejects_accidental_logs_demos_and_traversal(self):
        for name in ["../private.txt", "/private.txt", "logs/private.txt", "x.dem", ".env", "build/app.py", "dolly\\app.py", "native/client.dll", "engine2.dll", "CLIENT.DLL"]:
            with self.subTest(name=name):
                (self.root / "SOURCE_FILES.txt").write_text(name + "\n")
                with self.assertRaisesRegex(ValueError, "Non-source"):
                    release_files.source_files(self.root)

    def test_portable_guides_resolve_repository_links_without_missing_neighbors(self):
        docs = self.root / "docs"
        docs.mkdir()
        source = ('[Guide](USER_GUIDE.md#controls)\n'
                  '[Credits](../CREDITS.md "Project credits")\n'
                  '![Logo](../assets/dolly.png)\n'
                  '[Here](#controls)\n'
                  '[Web](https://example.com/guide?q=1#controls)\n'
                  '[Mail](mailto:help@example.com)\n')
        for name in ("VIDEO_AND_RESHADE.md", "LAYER_EXPORT.md", "UPDATING.md",
                     "SUPPORTED_CAMERA_CVARS.md"):
            (docs / name).write_text(source, encoding="utf-8")
        (docs / "SUPPORTED_CAMERA_CVARS.json").write_bytes(b'{"controls": []}\n')
        bundle = self.root / "portable"
        bundle.mkdir()
        build_windows.stage_user_guides(self.root, bundle)
        expected = ('[Guide](https://github.com/cravvnn/deadlock-dolly/blob/main/docs/USER_GUIDE.md#controls)\n'
                    '[Credits](https://github.com/cravvnn/deadlock-dolly/blob/main/CREDITS.md "Project credits")\n'
                    '![Logo](https://github.com/cravvnn/deadlock-dolly/blob/main/assets/dolly.png)\n'
                    '[Here](#controls)\n'
                    '[Web](https://example.com/guide?q=1#controls)\n'
                    '[Mail](mailto:help@example.com)\n')
        self.assertEqual({path.name for path in bundle.iterdir()}, {
            "Video_and_ReShade.md", "LAYER_EXPORT.md", "Updating.md",
            "Supported_Camera_Cvars.md", "Supported_Camera_Cvars.json"})
        for path in bundle.glob("*.md"):
            with self.subTest(guide=path.name):
                self.assertEqual(path.read_text(encoding="utf-8"), expected)
        self.assertEqual((bundle / "Supported_Camera_Cvars.json").read_bytes(), b'{"controls": []}\n')

    def test_source_manifest_missing_and_duplicate_files_fail_release(self):
        (self.root / "one.py").write_text("fixture")
        for contents, message in [("missing.py\n", "Missing or unsafe source file"),
                                  ("one.py\none.py\n", "duplicate paths")]:
            with self.subTest(contents=contents):
                (self.root / "SOURCE_FILES.txt").write_text(contents)
                with self.assertRaisesRegex(ValueError, message):
                    release_files.source_files(self.root)

    def test_source_manifest_covers_every_shipped_source_file(self):
        # SOURCE_FILES.txt is an explicit allowlist, so a new module or test can
        # silently fall out of the Source ZIP. Require every shipped source file
        # in the documented families to be listed.
        root = Path(__file__).resolve().parents[1]
        listed = {line.strip() for line in (root / "SOURCE_FILES.txt").read_text(encoding="utf-8").splitlines()
                  if line.strip() and not line.lstrip().startswith("#")}
        shipped = []
        for pattern in ("dolly/*.py", "tests/test_*.py", "tools/*.py", "packaging/*",
                        "native/src/*.cpp", "native/src/*.hpp", "native/include/*.hpp",
                        "native/include/*.h", "native/tests/*.cpp"):
            shipped.extend(sorted(path.relative_to(root).as_posix()
                                  for path in root.glob(pattern) if path.is_file()))
        missing = [name for name in shipped if name not in listed]
        self.assertEqual(missing, [], "SOURCE_FILES.txt is missing shipped source files: %s" % missing)

    def test_separate_release_folder_refuses_to_replace_existing_outputs(self):
        destination = self.root / "candidate"
        destination.mkdir()
        self.assertEqual(build_windows.distribution_dir(destination, "0.6.80-alpha"), destination)
        for name in ("Deadlock_Dolly_0.6.80-alpha_Windows_x64.zip",
                     "Deadlock_Dolly_0.6.80-alpha_Source.zip", "SHA256SUMS.txt"):
            path = destination / name
            path.write_bytes(b"existing")
            with self.subTest(name=name), self.assertRaises(FileExistsError):
                build_windows.distribution_dir(destination, "0.6.80-alpha")
            self.assertEqual(path.read_bytes(), b"existing")
            path.unlink()

    def test_windows_bundle_zip_prefixes_and_never_archives_runtime_data(self):
        for name in ["Dolly.exe", "BUILD_INFO.json", "_internal/base_library.zip",
                     "_internal/python312.dll", "logs/Dolly.log",
                     "logs/20260912_065349_ccf753618f/session.json",
                     "shot.dem", "clip.mp4", ".env", ".env.local",
                     ".dolly-update-0.5.14/Dolly.zip", "build/note.txt"]:
            file = self.root / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("fixture")
        archive = release_files.bundle_zip(self.root, self.root / "windows.zip")
        with zipfile.ZipFile(archive) as package:
            self.assertEqual(set(package.namelist()),
                             {"DeadlockDolly/Dolly.exe", "DeadlockDolly/BUILD_INFO.json",
                              "DeadlockDolly/_internal/base_library.zip", "DeadlockDolly/_internal/python312.dll"})
            self.assertIsNone(package.testzip())

    def test_windows_bundle_zip_guard_rejects_a_leaked_excluded_path(self):
        leaked = self.root / "logs/Dolly.log"
        leaked.parent.mkdir(parents=True)
        leaked.write_text("fixture")
        with patch.object(release_files, "bundle_files", return_value=[leaked]):
            with self.assertRaisesRegex(OSError, "Excluded runtime data"):
                release_files.bundle_zip(self.root, self.root / "windows.zip")

    def icon_fixture(self):
        icon = Path(__file__).resolve().parents[1] / "assets/dolly.ico"
        raw = icon.read_bytes()
        count = struct.unpack_from("<H", raw, 4)[0]
        data = {}
        entries = []
        for index in range(count):
            size, offset = struct.unpack_from("<II", raw, 6 + index * 16 + 8)
            data[index] = raw[offset:offset + size]
            language = SimpleNamespace(data=SimpleNamespace(struct=SimpleNamespace(OffsetToData=index, Size=size)))
            entries.append(SimpleNamespace(directory=SimpleNamespace(entries=[language])))
        image = SimpleNamespace(FILE_HEADER=SimpleNamespace(Machine=0x8664),
            OPTIONAL_HEADER=SimpleNamespace(Subsystem=2),
            DIRECTORY_ENTRY_RESOURCE=SimpleNamespace(entries=[SimpleNamespace(id=14),
                SimpleNamespace(id=3, directory=SimpleNamespace(entries=entries))]),
            get_data=lambda address, size: data[address], parse_data_directories=Mock(), close=Mock())
        module = SimpleNamespace(PE=Mock(return_value=image), DIRECTORY_ENTRY={"IMAGE_DIRECTORY_ENTRY_RESOURCE": 2})
        return icon, image, module

    def test_release_gate_checks_all_supplied_icon_frames_and_windowed_executable(self):
        icon, image, module = self.icon_fixture()
        with patch.dict(sys.modules, {"pefile": module}):
            report = build_windows.check_executable(self.root / "Dolly.exe", icon)
        self.assertEqual(report["verified_icon_frames"], 9)
        image.close.assert_called_once()

    def test_release_gate_rejects_missing_icon_and_console_or_wrong_architecture(self):
        for problem in ["missing_icon", "console", "x86"]:
            icon, image, module = self.icon_fixture()
            if problem == "missing_icon":
                image.DIRECTORY_ENTRY_RESOURCE.entries[1].directory.entries.pop()
            elif problem == "console":
                image.OPTIONAL_HEADER.Subsystem = 3
            else:
                image.FILE_HEADER.Machine = 0x14c
            with self.subTest(problem=problem), patch.dict(sys.modules, {"pefile": module}):
                with self.assertRaises(ValueError):
                    build_windows.check_executable(self.root / "Dolly.exe", icon)
                image.close.assert_called_once()

    def test_non_windows_cannot_accidentally_produce_a_mislabeled_windows_release(self):
        with patch.object(sys, "platform", "linux"), patch("subprocess.run") as run:
            with self.assertRaisesRegex(RuntimeError, "Windows x64"):
                build_windows.main()
        run.assert_not_called()

    def test_failed_regressions_print_traceback_tail_and_preserve_fatal_gate(self):
        tests = self.root / "tests"
        tests.mkdir()
        (tests / "test_failure.py").write_text(
            "import unittest\n"
            "class Failure(unittest.TestCase):\n"
            "    def test_failure(self):\n"
            "        for index in range(200):\n"
            "            print(f'fixture noise {index:03d}', flush=True)\n"
            "        self.fail('native connection fixture failure')\n", encoding="utf-8")
        log = self.root / "tests.log"
        output = io.StringIO()
        with redirect_stderr(output), self.assertRaises(subprocess.CalledProcessError) as raised:
            build_windows.run_regression_tests(self.root, log)
        self.assertNotEqual(raised.exception.returncode, 0)
        self.assertIn("fixture noise 000", log.read_text(encoding="utf-8"))
        self.assertNotIn("fixture noise 000", output.getvalue())
        self.assertIn("Traceback (most recent call last)", output.getvalue())
        self.assertIn("AssertionError: native connection fixture failure", output.getvalue())
        self.assertIn("Windows-build-diagnostics", output.getvalue())
        self.assertIn(str(log), output.getvalue())
        self.assertLessEqual(len(output.getvalue().splitlines()), 154)

    def test_successful_regressions_keep_utf8_log_without_failure_output(self):
        tests = self.root / "tests"
        tests.mkdir()
        (tests / "test_success.py").write_text(
            "import unittest\n"
            "class Success(unittest.TestCase):\n"
            "    def test_success(self):\n"
            "        print('Camera \u2192 ready')\n", encoding="utf-8")
        log = self.root / "tests.log"
        output = io.StringIO()
        with redirect_stderr(output), patch.dict("os.environ", {"PYTHONIOENCODING": "ascii"}):
            build_windows.run_regression_tests(self.root, log)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("Camera \u2192 ready", log.read_text(encoding="utf-8"))
        self.assertIn("OK", log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
