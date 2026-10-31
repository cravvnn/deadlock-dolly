"""Published build definitions: strict schema, additive merge, safe cache."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dolly import compatibility, definitions

ROOT = Path(__file__).resolve().parents[1]


def document(**overrides):
    base = {
        "format": 1,
        "native_abi": 4,
        "updated": "2026-10-25T00:00:00Z",
        "notes": "test",
        "modules": {},
        "engine": {"layouts": {}, "profiles": []},
        "tier0": {"layouts": {}, "profiles": []},
        "client": {"profiles": []},
        "editing": {},
    }
    base.update(overrides)
    return base


CLIENT = {
    "sha256": "c09143cf9cd722909404c64e3ffa05157afc15e8a3519b49a1396a627af8ca85",
    "image_size": "0x43bf000",
    "main_view_setup": "0x19e7770",
    "main_view_caller": "0x19e1194",
    "primary_vtable": "0x2782d90",
    "globals": "0x34331c0",
    "engine_client": "0x3e600d8",
    "render_fraction": "0x0",
    "camera_layout_revision": 1,
    "view_setup_delegate": "0x5e2ce0",
    "setup_prologue": "48" * 32,
}

ENGINE_LAYOUT = {
    "demo_global": "0x5b92b0", "demo_table": "0x4d7e88", "engine_table": "0x4e2790",
    "playing": "0x2d590", "active": "0x732c0", "paused": "0x34ea0",
    "seeking": "0x28b40", "tick": "0x34ee0", "filename": "0x73310",
}
CVAR_LAYOUT = {
    "table": "0x313208", "setter": "0x20fea0", "find": "0x6c6b0",
    "data": "0x6c5f0", "data_slot": 41, "extended": True,
}


class DefinitionsSchemaTests(unittest.TestCase):
    def test_checked_in_document_is_valid_and_intentionally_live(self):
        parsed = definitions.load(ROOT / definitions.DEFINITIONS_PATH)
        self.assertIn("6728", parsed.engine_layouts)
        self.assertIn("modern_6801", parsed.tier0_layouts)
        self.assertEqual(set(parsed.engine_layouts["6728"]), set(definitions.ENGINE_LAYOUT_FIELDS))
        # The published file starts as an additive overlay: bundled builds keep
        # working with no network access at all.
        definitions.parse(document())

    def test_every_engine_and_cvar_layout_is_referenced_by_a_name(self):
        parsed = definitions.load(ROOT / definitions.DEFINITIONS_PATH)
        for name in parsed.engine_layouts:
            self.assertRegex(name, r"[a-z0-9_]{1,24}")
        for name in parsed.tier0_layouts:
            self.assertRegex(name, r"[a-z0-9_]{1,24}")

    def test_unknown_fields_modules_and_layouts_are_rejected(self):
        cases = [
            document(format=2),
            document(native_abi=3),
            document(modules={"bin/win64/other.dll": {"accepted": ["0" * 64]}}),
            document(modules={"bin/win64/engine2.dll": {"accepted": ["0" * 63]}}),
            document(modules={"bin/win64/engine2.dll": {"accepted": [], "extra": 1}}),
            document(extra={}),
            document(engine={"layouts": {}, "profiles": [
                {"sha256": "0" * 64, "image_size": 1, "layout": "missing"}]}),
            document(tier0={"layouts": {}, "profiles": [
                {"sha256": "0" * 64, "image_size": 1, "layout": "missing"}]}),
            document(client={"profiles": [dict(CLIENT, image_size=True)]}),
            document(client={"profiles": [dict(CLIENT, setup_prologue="zz")]}),
            document(client={"profiles": [{k: v for k, v in CLIENT.items() if k != "globals"}]}),
            document(editing={"configuration_modules": {"bin/win64/other.dll": "0" * 64}}),
            document(editing={"stock_provenance_files": {"../cfg/x.cfg": {"bytes": 1, "sha1": "0" * 40}}}),
        ]
        for value in cases:
            with self.subTest(value=str(value)[:60]), self.assertRaises(definitions.DefinitionsError):
                definitions.parse(value)

    def test_bounded_document_limit_is_enforced_before_json(self):
        with self.assertRaises(definitions.DefinitionsError):
            definitions.decode(b"{" + b" " * definitions.MAX_BYTES + b"}")

    def test_signature_must_agree_with_the_reviewed_prologue(self):
        good = dict(CLIENT, signature="48" * 4, signature_mask="01" * 4)
        definitions.parse(document(client={"profiles": [good]}))
        bad = dict(CLIENT, signature="00" * 4, signature_mask="01" * 4)
        with self.assertRaises(definitions.DefinitionsError):
            definitions.parse(document(client={"profiles": [bad]}))
        with self.assertRaises(definitions.DefinitionsError):
            definitions.parse(document(client={"profiles": [dict(CLIENT, signature="48" * 4)]}))

    def test_layouts_must_be_complete_and_typed(self):
        layout = dict(ENGINE_LAYOUT)
        del layout["tick"]
        with self.assertRaises(definitions.DefinitionsError):
            definitions.parse(document(engine={"layouts": {"6728": layout}, "profiles": []}))
        with self.assertRaises(definitions.DefinitionsError):
            definitions.parse(document(tier0={"layouts": {"x": dict(CVAR_LAYOUT, data_slot=42)},
                                              "profiles": []}))
        with self.assertRaises(definitions.DefinitionsError):
            definitions.parse(document(tier0={"layouts": {"x": dict(CVAR_LAYOUT, extended=1)},
                                              "profiles": []}))

    def test_stock_rows_accept_one_or_many_variants(self):
        parsed = definitions.parse(document(editing={"stock_provenance_files": {
            "game/citadel/gameinfo.gi": [
                {"bytes": 10, "sha1": "a" * 40},
                {"bytes": 11, "sha1": "b" * 40},
            ],
        }}))
        self.assertEqual(parsed.stock_provenance_files["game/citadel/gameinfo.gi"],
                         ((10, "a" * 40), (11, "b" * 40)))
        parsed = definitions.parse(document(editing={"stock_provenance_files": {
            "game/citadel/gameinfo.gi": {"bytes": 10, "sha1": "a" * 40},
        }}))
        self.assertEqual(parsed.stock_provenance_files["game/citadel/gameinfo.gi"],
                         ((10, "a" * 40),))


class NativeDocumentTests(unittest.TestCase):
    def test_render_matches_the_bridge_line_format(self):
        parsed = definitions.parse(document(
            engine={"layouts": {"6728": ENGINE_LAYOUT}, "profiles": [
                {"sha256": "a" * 64, "image_size": "0x906000", "layout": "6728"}]},
            tier0={"layouts": {"modern_6801": CVAR_LAYOUT}, "profiles": [
                {"sha256": "b" * 64, "image_size": "0x401000", "layout": "modern_6801"}]},
            client={"profiles": [dict(CLIENT, signature="48" * 4, signature_mask="01" * 4)]},
        ))
        text = definitions.native_document(parsed)
        lines = text.splitlines()
        self.assertEqual(lines[0], "DOLLY_DEFINITIONS 1")
        self.assertEqual(lines[1], "abi 4")
        self.assertEqual(lines[-1], "end")
        engine_line = next(line for line in lines if line.startswith("+engine "))
        self.assertEqual(engine_line.split(),
                         ["+engine", "a" * 64, "0x906000", "6728"])
        client_line = next(line for line in lines if line.startswith("+client "))
        fields = client_line.split()
        self.assertEqual(len(fields), 14)
        self.assertEqual(fields[1], CLIENT["sha256"])
        self.assertEqual(fields[2], "0x43bf000")
        self.assertEqual(fields[9], "0x1")
        self.assertEqual(fields[11], "48" * 32)
        self.assertEqual(fields[12], "48" * 4)
        self.assertEqual(fields[13], "01" * 4)
        signature = next(line for line in lines if line.startswith("+cvar "))
        self.assertEqual(signature.split()[6:], ["41", "1"])

    def test_profile_without_signature_renders_wildcard_placeholders(self):
        parsed = definitions.parse(document(client={"profiles": [CLIENT]}))
        line = definitions.native_document(parsed).splitlines()[2]
        self.assertEqual(line.split()[-2:], ["-", "-"])

    def test_empty_document_still_renders_a_consumable_terminator(self):
        text = definitions.native_document(definitions.EMPTY)
        self.assertEqual(text, "DOLLY_DEFINITIONS 1\nabi 4\nend\n")


class CacheTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.cache = self.root / definitions.CACHE_NAME
        patcher = patch.object(definitions, "cache_path", return_value=self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)
        definitions.reset()
        self.addCleanup(definitions.reset)

    def test_refresh_caches_and_adopts(self):
        raw = json.dumps(document(client={"profiles": [CLIENT]}, updated="2026-10-25T01:00:00Z")).encode()
        with patch.object(definitions, "fetch", return_value=raw):
            parsed = definitions.refresh()
        self.assertEqual(len(parsed.client_profiles), 1)
        self.assertEqual(self.cache.read_bytes(), raw)
        self.assertEqual(len(definitions.current().client_profiles), 1)

    def test_empty_document_replaces_the_cache_and_withdraws_support(self):
        first = json.dumps(document(client={"profiles": [CLIENT]})).encode()
        with patch.object(definitions, "fetch", return_value=first):
            definitions.refresh()
        with patch.object(definitions, "fetch", return_value=json.dumps(document()).encode()):
            empty = definitions.refresh()
        self.assertTrue(empty.empty)
        self.assertEqual(json.loads(self.cache.read_text()), json.loads(json.dumps(document())))
        self.assertTrue(definitions.current().empty)

    def test_cache_write_failure_keeps_this_session_supported(self):
        raw = json.dumps(document(client={"profiles": [CLIENT]})).encode()
        with patch.object(definitions, "fetch", return_value=raw), \
                patch.object(definitions, "_atomic_write", side_effect=OSError("read-only")):
            parsed = definitions.refresh()
        self.assertEqual(len(parsed.client_profiles), 1)

    def test_failed_refresh_keeps_the_previous_cache(self):
        first = json.dumps(document(client={"profiles": [CLIENT]})).encode()
        with patch.object(definitions, "fetch", return_value=first):
            definitions.refresh()
        with patch.object(definitions, "fetch", side_effect=definitions.DefinitionsError("down")):
            with self.assertRaises(definitions.DefinitionsError):
                definitions.refresh()
        definitions.reset()
        self.assertEqual(len(definitions.current().client_profiles), 1)

    def test_unreadable_cache_falls_back_to_bundled_only(self):
        self.cache.write_text("not json")
        self.assertTrue(definitions.current().empty)

    def test_oversized_remote_document_is_never_written(self):
        with patch.object(definitions, "fetch",
                          return_value=b"{" + b" " * definitions.MAX_BYTES + b"}"):
            with self.assertRaises(definitions.DefinitionsError):
                definitions.refresh()
        self.assertFalse(self.cache.exists())

    def test_fetch_refuses_non_raw_hosts(self):
        for url in ("http://raw.githubusercontent.com/x", "https://example.com/x",
                    "https://raw.githubusercontent.com.evil.test/x"):
            with self.subTest(url=url), self.assertRaises(definitions.DefinitionsError):
                definitions.fetch(url)


class PinMergeTests(unittest.TestCase):
    def test_published_pins_are_additive_and_keep_reviewed_first(self):
        base = {"bin/win64/engine2.dll": ("a" * 64,)}
        merged = compatibility.merged_pins(base, {"bin/win64/engine2.dll": ("b" * 64,)})
        self.assertEqual(merged["bin/win64/engine2.dll"], ("a" * 64, "b" * 64))
        merged = compatibility.merged_pins(
            {"bin/win64/engine2.dll": "a" * 64}, {"bin/win64/engine2.dll": ["b" * 64]})
        self.assertEqual(merged["bin/win64/engine2.dll"], ("a" * 64, "b" * 64))

    def test_published_pins_cannot_name_unknown_modules_or_bad_hashes(self):
        with self.assertRaises(compatibility.CompatibilityError):
            compatibility.merged_pins({}, {"bin/win64/other.dll": ("a" * 64,)})
        with self.assertRaises(compatibility.CompatibilityError):
            compatibility.merged_pins({}, {"bin/win64/engine2.dll": ("short",)})

    def test_duplicate_published_hash_is_not_repeated(self):
        merged = compatibility.merged_pins(
            {"bin/win64/engine2.dll": ("a" * 64,)},
            {"bin/win64/engine2.dll": ("a" * 64, "b" * 64)})
        self.assertEqual(merged["bin/win64/engine2.dll"], ("a" * 64, "b" * 64))


class PublisherTests(unittest.TestCase):
    def test_publisher_appends_then_refuses_duplicates(self):
        import sys
        sys.path.insert(0, str(ROOT / "tools"))
        import publish_definitions

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        target = Path(folder.name) / "definitions.json"
        target.write_text(json.dumps(document(
            engine={"layouts": {"6728": ENGINE_LAYOUT}, "profiles": []},
            tier0={"layouts": {"modern_6801": CVAR_LAYOUT}, "profiles": []},
        )), encoding="utf-8")
        profile = json.loads((ROOT / "native/profiles/deadlock-2026-10-22-6819-complete.json")
                             .read_text(encoding="utf-8"))
        self.assertEqual(publish_definitions.main([
            "--document", str(target), "--profile",
            str(ROOT / "native/profiles/deadlock-2026-10-22-6819-complete.json"),
            "--engine-layout", "6728", "--tier0-layout", "modern_6801",
        ]), 0)
        published = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(len(published["client"]["profiles"]), 1)
        self.assertEqual(published["client"]["profiles"][0]["sha256"],
                         profile["client"]["client_sha256"])
        self.assertEqual(published["client"]["profiles"][0]["camera_layout_revision"], 1)
        self.assertEqual(len(published["modules"]["citadel/bin/win64/client.dll"]["accepted"]), 1)
        # Re-running is idempotent, not duplicating rows.
        self.assertEqual(publish_definitions.main([
            "--document", str(target), "--profile",
            str(ROOT / "native/profiles/deadlock-2026-10-22-6819-complete.json"),
            "--engine-layout", "6728", "--tier0-layout", "modern_6801",
        ]), 0)
        again = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(len(again["client"]["profiles"]), 1)
        # The result is exactly what installed copies validate.
        definitions.parse(again)

    def test_publisher_refuses_an_unknown_layout_name(self):
        import sys
        sys.path.insert(0, str(ROOT / "tools"))
        import publish_definitions

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        target = Path(folder.name) / "definitions.json"
        target.write_text(json.dumps(document()), encoding="utf-8")
        self.assertEqual(publish_definitions.main([
            "--document", str(target),
            "--profile", str(ROOT / "native/profiles/deadlock-2026-10-22-6819-complete.json"),
            "--engine-layout", "missing", "--tier0-layout", "modern_6801",
        ]), 1)

    def test_publisher_derives_and_reuses_the_reviewed_layouts(self):
        import sys
        sys.path.insert(0, str(ROOT / "tools"))
        import publish_definitions

        profile = publish_definitions.read_json(
            ROOT / "native/profiles/deadlock-2026-10-22-6819-complete.json")
        engine = publish_definitions.derived_engine_layout(profile)
        tier0 = publish_definitions.derived_tier0_layout(profile)
        seed = publish_definitions.read_json(ROOT / definitions.DEFINITIONS_PATH)

        def as_ints(row):
            return {name: (int(value, 16) if isinstance(value, str) else value)
                    for name, value in row.items()}

        # The 6819 profile must derive exactly the layouts shipped in the seed,
        # otherwise a publish would silently add a duplicate layout.
        self.assertEqual(engine, as_ints(seed["engine"]["layouts"]["6728"]))
        self.assertEqual(tier0, as_ints(seed["tier0"]["layouts"]["modern_6801"]))
        self.assertEqual(tier0["table"], 0x313208)
        self.assertEqual(tier0["find"], 0x6c6b0)
        self.assertEqual(tier0["data"], 0x6c5f0)
        self.assertEqual(tier0["data_slot"], 41)
        self.assertTrue(tier0["extended"])

    def test_check_profile_validates_one_record_without_writing(self):
        import sys
        sys.path.insert(0, str(ROOT / "tools"))
        import publish_definitions

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        profile_path = ROOT / "native/profiles/deadlock-2026-10-22-6819-complete.json"
        self.assertEqual(publish_definitions.main(
            ["--profile", str(profile_path), "--check-profile"]), 0)
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        del profile["client"]["main_view_setup_prologue_32"]
        broken = Path(folder.name) / "broken.json"
        broken.write_text(json.dumps(profile), encoding="utf-8")
        self.assertEqual(publish_definitions.main(
            ["--profile", str(broken), "--check-profile"]), 1)
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        profile["tier0"]["get_data"]["slot"] = 42
        unreviewed = Path(folder.name) / "unreviewed.json"
        unreviewed.write_text(json.dumps(profile), encoding="utf-8")
        self.assertEqual(publish_definitions.main(
            ["--profile", str(unreviewed), "--check-profile"]), 1)

    def test_publisher_adds_one_named_layout_when_accessors_move(self):
        import sys
        sys.path.insert(0, str(ROOT / "tools"))
        import publish_definitions

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        target = Path(folder.name) / "definitions.json"
        target.write_text(json.dumps(document(
            engine={"layouts": {"6728": ENGINE_LAYOUT}, "profiles": []},
            tier0={"layouts": {"modern_6801": CVAR_LAYOUT}, "profiles": []},
        )), encoding="utf-8")
        profile = json.loads((ROOT / "native/profiles/deadlock-2026-10-22-6819-complete.json")
                             .read_text(encoding="utf-8"))
        profile["engine"]["addresses"]["demo_player_pointer"] = "0x5b92c0"
        profile["tier0"]["set_value"]["rva"] = "0x20feb0"
        source = Path(folder.name) / "deadlock-2026-10-26-6820-complete.json"
        source.write_text(json.dumps(profile), encoding="utf-8")
        self.assertEqual(publish_definitions.main([
            "--document", str(target), "--profile", str(source)]), 0)
        published = json.loads(target.read_text(encoding="utf-8"))
        self.assertIn("2026_10_26_6820", published["engine"]["layouts"])
        self.assertIn("2026_10_26_6820", published["tier0"]["layouts"])
        self.assertEqual(published["engine"]["profiles"][0]["layout"], "2026_10_26_6820")
        self.assertEqual(published["tier0"]["profiles"][0]["layout"], "2026_10_26_6820")
        definitions.parse(published)


if __name__ == "__main__":
    unittest.main()
