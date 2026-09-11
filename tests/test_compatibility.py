"""The compatibility scanner is the runtime source of truth for Native mode."""
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

from dolly import compatibility


def pe_bytes(timestamp: int) -> bytes:
    data = bytearray(0x60)
    data[0:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x40)
    data[0x40:0x44] = b"PE\0\0"
    struct.pack_into("<H", data, 0x44, 0x8664)
    struct.pack_into("<H", data, 0x46, 0)
    struct.pack_into("<I", data, 0x48, timestamp)
    return bytes(data)


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="Dolly compatibility ")
        self.addCleanup(temporary.cleanup)
        self.game = Path(temporary.name)
        self.files = {}
        for index, relative in enumerate(compatibility.MODULE_RELATIVES):
            path = self.game / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(pe_bytes(10 + index))
            self.files[relative] = path

    def hashes(self):
        return {relative: hashlib.sha256(path.read_bytes()).hexdigest()
                for relative, path in self.files.items()}

    def manifest_for(self, accepted, reviewed="2026-01-01", latest=5):
        modules = {}
        for relative in compatibility.MODULE_RELATIVES:
            modules[relative] = {
                "role": relative.split("/")[0],
                "reviewed_utc": reviewed,
                "latest_timestamp": latest,
                "accepted": list(accepted[relative]),
            }
        return {"manifest_format": compatibility.MANIFEST_FORMAT, "native_abi": 3, "modules": modules}

    def test_supported_when_every_module_hash_is_accepted(self):
        pins = {relative: (digest,) for relative, digest in self.hashes().items()}
        report = compatibility.scan_game_modules(self.game, manifest=self.manifest_for(pins))
        self.assertEqual(report.state, compatibility.SUPPORTED)
        self.assertEqual(report.unsupported_modules, ())
        self.assertTrue(all(module.matched for module in report.modules))

    def test_unsupported_reports_newer_than_reviewed_build(self):
        pins = {relative: ("0" * 64,) for relative in compatibility.MODULE_RELATIVES}
        report = compatibility.scan_game_modules(self.game, manifest=self.manifest_for(pins))
        self.assertEqual(report.state, compatibility.UNSUPPORTED)
        self.assertEqual(len(report.unsupported_modules), 3)
        message = report.describe()
        self.assertIn("does not support", message)
        self.assertIn("appears to have been updated", message)
        for module in report.modules:
            self.assertIn(module.name, message)

    def test_unsupported_without_newer_timestamp_uses_generic_hint(self):
        pins = {relative: ("0" * 64,) for relative in compatibility.MODULE_RELATIVES}
        report = compatibility.scan_game_modules(
            self.game, manifest=self.manifest_for(pins, latest=9999))
        self.assertEqual(report.state, compatibility.UNSUPPORTED)
        self.assertNotIn("appears to have been updated", report.describe())

    def test_incomplete_when_a_module_is_missing(self):
        missing = compatibility.MODULE_RELATIVES[0]
        pins = {relative: (digest,) for relative, digest in self.hashes().items()}
        self.files[missing].unlink()
        report = compatibility.scan_game_modules(self.game, manifest=self.manifest_for(pins))
        self.assertEqual(report.state, compatibility.INCOMPLETE)
        self.assertIn(Path(missing).name, report.describe())

    def test_pins_override_uses_installed_hashes(self):
        pins = {relative: hashlib.sha256(path.read_bytes()).hexdigest()
                for relative, path in self.files.items()}
        report = compatibility.scan_game_modules(self.game, pins=pins)
        self.assertEqual(report.state, compatibility.SUPPORTED)

    def test_pe_timestamp_reads_coff_header(self):
        self.assertEqual(compatibility.pe_timestamp(self.files[compatibility.MODULE_RELATIVES[0]]), 10)

    def test_pe_timestamp_rejects_unrecognized_files(self):
        path = self.game / "not-a-pe.bin"
        path.write_bytes(b"hello")
        self.assertIsNone(compatibility.pe_timestamp(path))

    def test_manifest_validation_rejects_missing_or_malformed(self):
        with self.assertRaises(compatibility.CompatibilityError):
            compatibility.load_manifest(self.game / "absent.json")
        broken = self.game / "broken.json"
        broken.write_text(json.dumps({"manifest_format": 99}))
        with self.assertRaises(compatibility.CompatibilityError):
            compatibility.load_manifest(broken)
        document = self.manifest_for({r: ("0" * 64,) for r in compatibility.MODULE_RELATIVES})
        document["modules"][compatibility.MODULE_RELATIVES[0]]["accepted"] = ["xyz"]
        broken.write_text(json.dumps(document))
        with self.assertRaises(compatibility.CompatibilityError):
            compatibility.load_manifest(broken)

    def test_manifest_requires_every_module_entry(self):
        document = self.manifest_for({r: ("0" * 64,) for r in compatibility.MODULE_RELATIVES})
        del document["modules"][compatibility.MODULE_RELATIVES[1]]
        path = self.game / "partial.json"
        path.write_text(json.dumps(document))
        with self.assertRaises(compatibility.CompatibilityError):
            compatibility.load_manifest(path)

    def test_report_identifier_is_stable(self):
        pins = {relative: (digest,) for relative, digest in self.hashes().items()}
        report = compatibility.scan_game_modules(self.game, manifest=self.manifest_for(pins))
        self.assertEqual(compatibility.report_identifier(report),
                         compatibility.report_identifier(report))


if __name__ == "__main__":
    unittest.main()
