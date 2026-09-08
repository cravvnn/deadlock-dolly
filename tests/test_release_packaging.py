"""Source export privacy and embedded-icon release gates."""
from pathlib import Path
import struct
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
        for name in ["../private.txt", "/private.txt", "logs/private.txt", "x.dem", ".env", "build/app.py", "dolly\\app.py"]:
            with self.subTest(name=name):
                (self.root / "SOURCE_FILES.txt").write_text(name + "\n")
                with self.assertRaisesRegex(ValueError, "Non-source"):
                    release_files.source_files(self.root)

    def test_source_manifest_missing_and_duplicate_files_fail_release(self):
        for contents in ["missing.py\n", "one.py\none.py\n"]:
            (self.root / "SOURCE_FILES.txt").write_text(contents)
            with self.assertRaises(ValueError):
                release_files.source_files(self.root)

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


if __name__ == "__main__":
    unittest.main()
