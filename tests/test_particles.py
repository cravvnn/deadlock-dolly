"""Particle preset registry coherence with the native engine."""
from pathlib import Path
import re
import struct
import unittest

from dolly import particles
from dolly import launcher

ROOT = Path(__file__).resolve().parents[1]


def _parse_build_rows(source):
    """Return [(manager, create, control, transform, destroy, release, mb, rb)].

    Each row is six hex RVAs followed by an 8-byte manager signature and a
    16-byte release signature. The pattern matches that exact shape, so the
    nested byte-array braces never confuse the parse.
    """
    table = source[source.index("kBuilds{{") + len("kBuilds{{"):]
    table = table[:table.index("}};")]
    row_re = re.compile(
        r"\{\s*(0x[0-9a-fA-F]+)\s*,\s*(0x[0-9a-fA-F]+)\s*,\s*(0x[0-9a-fA-F]+)\s*,\s*"
        r"(0x[0-9a-fA-F]+)\s*,\s*(0x[0-9a-fA-F]+)\s*,\s*(0x[0-9a-fA-F]+)\s*,\s*"
        r"\{([0-9a-fA-Fx,\s]+)\}\s*,\s*\{([0-9a-fA-Fx,\s]+)\}\s*\}", re.S)
    rows = []
    for match in row_re.finditer(table):
        rvas = [int(match.group(i), 16) for i in range(1, 7)]
        manager_bytes = bytes(int(v, 16) for v in re.findall(r"0x([0-9a-fA-F]+)", match.group(7)))
        release_bytes = bytes(int(v, 16) for v in re.findall(r"0x([0-9a-fA-F]+)", match.group(8)))
        rows.append((*rvas, manager_bytes, release_bytes))
    return rows


def _read_rva(data, text_rva, text_ptr, rva, size):
    return data[text_ptr + (rva - text_rva):text_ptr + (rva - text_rva) + size]


class ParticleBuildTableTests(unittest.TestCase):
    def test_build_rows_have_complete_signatures(self):
        source = (ROOT / "native/src/dolly_confetti.cpp").read_text(encoding="utf-8")
        rows = _parse_build_rows(source)
        self.assertGreaterEqual(len(rows), 13)
        for manager, create, control, transform, destroy, release, mb, rb in rows:
            self.assertEqual(len(mb), 8)
            self.assertEqual(len(rb), 16)
            self.assertEqual(mb[-1], 0xC3, "manager getter must end in ret")
            # transform follows control by one fixed stride in every row.
            self.assertEqual(transform - control, 0x1A0)
            self.assertTrue(create < control < transform,
                            "create/control/transform RVAs must be ordered")
            self.assertTrue(create < destroy, "create must precede destroy")

    def test_newest_row_matches_the_installed_client_when_present(self):
        install = launcher.discover_game()
        if install is None:
            self.skipTest("Deadlock is not installed; offline signature check skipped")
        client = install / "game/citadel/bin/win64/client.dll"
        if not client.is_file():
            self.skipTest("client.dll not found in the discovered install")
        data = client.read_bytes()
        e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
        coff = e_lfanew + 4
        num_sections = struct.unpack_from("<H", data, coff + 2)[0]
        opt_size = struct.unpack_from("<H", data, coff + 16)[0]
        opt = coff + 20
        first = opt + opt_size
        text_rva = text_ptr = text_size = None
        for i in range(num_sections):
            off = first + i * 40
            name = data[off:off + 8].rstrip(b"\0").decode("ascii", "replace")
            vaddr, raw_size, raw_ptr = struct.unpack_from("<III", data, off + 12)
            if name == ".text":
                text_rva, text_ptr, text_size = vaddr, raw_ptr, raw_size
        self.assertIsNotNone(text_rva, "client.dll has no .text section")
        source = (ROOT / "native/src/dolly_confetti.cpp").read_text(encoding="utf-8")
        rows = _parse_build_rows(source)
        # The newest row must be the one that matches this install, otherwise
        # the shipped confetti table is stale for the current game build.
        matched = []
        for manager, create, control, transform, destroy, release, mb, rb in rows:
            if (_read_rva(data, text_rva, text_ptr, manager, 8) == mb and
                    _read_rva(data, text_rva, text_ptr, release, 16) == rb):
                matched.append(create)
        self.assertTrue(matched, "no kBuilds row matches the installed client.dll")
        self.assertEqual(create, matched[-1],
                         "the newest kBuilds row does not match the installed client.dll")


class ParticleRegistryTests(unittest.TestCase):
    def test_native_preset_table_matches_the_python_registry(self):
        header = (ROOT / "native/include/dolly_confetti.hpp").read_text(encoding="utf-8")
        count = int(re.search(r"kPresetCount\s*=\s*(\d+)", header).group(1))
        source = (ROOT / "native/src/dolly_confetti.cpp").read_text(encoding="utf-8")
        table = source[source.index("kPresets{{"):]
        ids = re.findall(r'\{"([a-z_]+)",', table)
        self.assertEqual(count, len(particles.PARTICLE_IDS))
        self.assertEqual(ids, list(particles.PARTICLE_IDS))

    def test_preset_index_and_labels_roundtrip(self):
        for index, name in enumerate(particles.PARTICLE_IDS):
            self.assertEqual(particles.preset_index(name), index)
            label = particles.PARTICLE_LABELS[name]
            self.assertEqual(particles.LABEL_TO_ID[label], name)
            self.assertEqual(particles.preset_label(name), label)
        with self.assertRaises(ValueError):
            particles.preset_index("missing")

    def test_intensity_defaults_are_encodable(self):
        from dolly import native_bridge as bridge

        raw = bridge._intensity_bits(particles.PARTICLE_INTENSITY_DEFAULT)
        self.assertEqual(raw, bridge.PARTICLE_INTENSITY_SCALE)
        self.assertLessEqual(raw, bridge.PARTICLE_INTENSITY_MASK)
        self.assertGreaterEqual(bridge._intensity_bits(particles.PARTICLE_INTENSITY_MIN), 2)


if __name__ == "__main__":
    unittest.main()
