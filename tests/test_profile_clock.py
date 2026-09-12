import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import generate_profile


class ProfileClockTests(unittest.TestCase):
    def setUp(self):
        self.profile = json.loads((TOOLS.parent / "native/profiles/deadlock-2026-09-11b-complete.json").read_text())

    def test_exact_review_enables_render_fraction(self):
        self.assertEqual(generate_profile.reviewed_render_fraction(self.profile["client"]), 0x38)

    def test_unreviewed_profile_keeps_legacy_clock(self):
        self.profile["client"]["globals"].pop("replay_clock_review")
        self.assertEqual(generate_profile.reviewed_render_fraction(self.profile["client"]), 0)

    def test_carried_review_does_not_authorize_new_game_hash(self):
        self.profile["client"]["client_sha256"] = "0" * 64
        self.assertEqual(generate_profile.reviewed_render_fraction(self.profile["client"]), 0)

    def test_unknown_fraction_layout_is_rejected(self):
        self.profile["client"]["globals"]["replay_clock_review"]["render_fraction_offset"] = "0x3c"
        with self.assertRaises(ValueError):
            generate_profile.reviewed_render_fraction(self.profile["client"])

    def test_header_emits_reviewed_and_unreviewed_offsets(self):
        changed = copy.deepcopy(self.profile)
        changed["client"]["client_sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "profile.hpp"
            generate_profile.emit_header([(self.profile, None), (changed, None)], out)
            rows = [line for line in out.read_text().splitlines() if line.lstrip().startswith('{ "')]
        self.assertEqual(len(rows), 2)
        self.assertIn("0x38, nullptr, nullptr, 0", rows[0])
        self.assertIn("0x0, nullptr, nullptr, 0", rows[1])
