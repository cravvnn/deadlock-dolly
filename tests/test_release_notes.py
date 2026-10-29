"""Publisher-authored, bounded release previews from existing GitHub metadata."""
from io import BytesIO
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from dolly import __version__
from dolly import updater
from dolly.release_notes import MAX_BODY_BYTES, MAX_DETAIL, MAX_ENTRIES, MAX_TITLE, parse_release_notes
from tools.check_release_notes import validate_publish_body


# Exact local publisher description from the 0.6.50-alpha release preparation.
# Kept inline so tests do not depend on private analysis or distribution files.
RELEASE_0650 = """**0.6.50-alpha**

- **Visible recording controls.** *Before:* a paused replay could leave the recorder open while Finish recording was only available on Export. *After:* Finish recording stays visible on every editor tab, with a saving status during finalization.
- **Safe camera clearing.** *Before:* Clear All could appear available while recording and raise a misleading playback error. *After:* camera edits stay locked until the take finishes, and queued Clear requests keep the cameras intact without an error popup.
"""


def bullet(title="Feature.", before="The old behavior.", after="The new behavior."):
    return f"- **{title}** *Before:* {before} *After:* {after}\n"


class ReleaseNotesTests(unittest.TestCase):
    def test_exact_published_0650_bullets_preserve_the_authored_text(self):
        self.assertEqual(parse_release_notes(RELEASE_0650, "0.6.50-alpha"), [
            {"title": "Visible recording controls.",
             "before": "a paused replay could leave the recorder open while Finish recording was only available on Export.",
             "after": "Finish recording stays visible on every editor tab, with a saving status during finalization."},
            {"title": "Safe camera clearing.",
             "before": "Clear All could appear available while recording and raise a misleading playback error.",
             "after": "camera edits stay locked until the take finishes, and queued Clear requests keep the cameras intact without an error popup."},
        ])

    def test_crlf_and_validated_v_prefix_keep_the_same_preview(self):
        expected = parse_release_notes(RELEASE_0650, "0.6.50-alpha")
        self.assertEqual(parse_release_notes(RELEASE_0650.replace("\n", "\r\n"), "v0.6.50-alpha"), expected)
        self.assertEqual(parse_release_notes(RELEASE_0650.replace("**0.6.50-alpha**", "## **v0.6.50-alpha**"),
                                             "0.6.50-alpha"), expected)

    def test_publisher_heading_that_names_the_version_starts_the_preview(self):
        # Andrew's published body style since 0.6.47: a plain heading with the
        # version, blank lines, then the bullets (exact 0.6.54 shape).
        body = ("### Deadlock Dolly v0.6.54-alpha\n\n\n"
                + bullet("Startup that waits out a slow shader rebuild.") + "\n"
                + bullet("Per-frame camera data for paired depth takes."))
        self.assertEqual([entry["title"] for entry in parse_release_notes(body, "0.6.54-alpha")],
                         ["Startup that waits out a slow shader rebuild.",
                          "Per-frame camera data for paired depth takes."])

    def test_publish_body_requires_both_opening_version_markers(self):
        version = "0.6.57-alpha"
        heading, legacy = f"### Deadlock Dolly v{version}", f"**{version}**"
        first, second = bullet("First."), bullet("Second.")
        plain = heading + "\n\n" + first + "\n" + second
        expected = [{"title": title, "before": "The old behavior.", "after": "The new behavior."}
                    for title in ("First.", "Second.")]
        # Current apps can read this body, while older installed apps need the
        # bold marker. Publishing must enforce both independently of parsing.
        self.assertEqual(parse_release_notes(plain, version), expected)
        invalid = {
            "missing legacy marker": plain,
            "wrong heading": "### Deadlock Dolly v0.6.56-alpha\n" + legacy + "\n" + first + second,
            "late heading": "Release summary\n" + heading + "\n" + legacy + "\n" + first + second,
            "wrong legacy version": heading + "\n**0.6.56-alpha**\n" + first + second,
            "late legacy marker": heading + "\n" + first + legacy + "\n" + second,
            "prefixed legacy marker": heading + f"\n**v{version}**\n" + first + second,
            "versioned section stops older previews":
                heading + "\n" + legacy + f"\n### Changes in {version}\n" + first + second,
        }
        for name, body in invalid.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_publish_body(body, version)
        valid = "\n" + heading + "\n\n" + legacy + "\n\n" + first + "\n" + second
        self.assertEqual(validate_publish_body(valid.replace("\n", "\r\n"), version), expected)

    def test_publisher_heading_with_another_version_does_not_start_the_preview(self):
        body = "### Deadlock Dolly v0.6.53-alpha\n\n" + bullet()
        self.assertEqual(parse_release_notes(body, "0.6.54-alpha"), [])

    def test_wrapped_title_and_before_after_continuations_are_plain_text(self):
        body = """**0.6.50-alpha**
- **Clearer
  controls.** *Before:* the **Update now** button
  was hard to find beside `host_timescale`.
  *After:* read [the guide](https://example.com/guide)
  and use _Update now_.
"""
        self.assertEqual(parse_release_notes(body, "0.6.50-alpha"), [
            {"title": "Clearer controls.",
             "before": "the Update now button was hard to find beside host_timescale.",
             "after": "read the guide and use Update now."}])

    def test_other_release_sections_never_contribute_bullets(self):
        body = ("**0.6.49-alpha**\n" + bullet("Older.") + RELEASE_0650 +
                "**0.6.51-alpha**\n" + bullet("Newer."))
        self.assertEqual(parse_release_notes(body, "0.6.50-alpha"),
                         parse_release_notes(RELEASE_0650, "0.6.50-alpha"))
        self.assertEqual(parse_release_notes(body, "0.6.52-alpha"), [])

    def test_fenced_bullets_and_version_markers_are_ignored(self):
        for fence in ("```", "~~~~"):
            with self.subTest(fence=fence):
                body = (fence + "markdown\n" + RELEASE_0650 + fence + "\n" +
                        "**0.6.50-alpha**\n" + bullet("Visible.") +
                        fence + "\n**0.6.49-alpha**\n" + bullet("Inside code.") + fence + "\n" +
                        bullet("Still visible."))
                self.assertEqual([entry["title"] for entry in parse_release_notes(body, "0.6.50-alpha")],
                                 ["Visible.", "Still visible."])

    def test_unclosed_fence_never_leaks_its_bullets(self):
        self.assertEqual(parse_release_notes("```markdown\n" + RELEASE_0650, "0.6.50-alpha"), [])

    def test_unknown_prose_html_and_headingless_bullets_have_no_fallback(self):
        for body in ("A faster editor with better controls.", "<h1>0.6.50-alpha</h1><p>New features.</p>",
                     bullet(), "# Release notes\n" + bullet(),
                     "**0.6.50-alpha**\n- A general release note without Before and After."):
            with self.subTest(body=body):
                self.assertEqual(parse_release_notes(body, "0.6.50-alpha"), [])

    def test_malformed_entries_do_not_borrow_text_from_neighboring_bullets(self):
        malformed = [
            "- **Missing After.** *Before:* old text.",
            "- **Missing Before.** *After:* new text.",
            "- **Reversed.** *After:* new text. *Before:* old text.",
            "- **Repeated.** *Before:* old text. *After:* new text. *After:* unrelated text.",
            "- **Empty.** *Before:*  *After:* new text.",
            "- **Empty.** *Before:* old text. *After:* ",
            "- **Empty before marker.** Before: old text. After: new text.",
        ]
        body = "**0.6.50-alpha**\n" + "\n".join(malformed) + "\n" + bullet("Valid.")
        self.assertEqual(parse_release_notes(body, "0.6.50-alpha"),
                         [{"title": "Valid.", "before": "The old behavior.", "after": "The new behavior."}])

    def test_unrecognized_section_heading_ends_the_matching_snippet(self):
        body = "**0.6.50-alpha**\n" + bullet("Matching.") + "## Older changes\n" + bullet("Other.")
        self.assertEqual([entry["title"] for entry in parse_release_notes(body, "0.6.50-alpha")], ["Matching."])
        body = "**0.6.50-alpha**\n" + bullet("Matching.") + "**0.6.49-unrecognized**\n" + bullet("Other.")
        self.assertEqual([entry["title"] for entry in parse_release_notes(body, "0.6.50-alpha")], ["Matching."])

    def test_only_the_first_four_authored_entries_are_returned(self):
        body = "**0.6.50-alpha**\n" + "".join(bullet(f"Feature {index}.") for index in range(8))
        notes = parse_release_notes(body, "0.6.50-alpha")
        self.assertEqual(len(notes), MAX_ENTRIES)
        self.assertEqual([entry["title"] for entry in notes], [f"Feature {index}." for index in range(4)])

    def test_each_truncated_field_is_bounded_and_ends_with_an_ellipsis(self):
        authored = {"title": "Title " * 100, "before": "Before text " * 100, "after": "After text " * 100}
        body = "**0.6.50-alpha**\n" + bullet(**authored)
        note = parse_release_notes(body, "0.6.50-alpha")[0]
        for field, limit in (("title", MAX_TITLE), ("before", MAX_DETAIL), ("after", MAX_DETAIL)):
            with self.subTest(field=field):
                self.assertLessEqual(len(note[field]), limit)
                self.assertTrue(note[field].endswith("…"))
                self.assertTrue(" ".join(authored[field].split()).startswith(note[field][:-1]))

    def test_body_limit_is_in_utf8_bytes_and_oversized_metadata_has_no_preview(self):
        content = RELEASE_0650 + " " * (MAX_BODY_BYTES - len(RELEASE_0650.encode("utf-8")))
        self.assertEqual(len(content.encode("utf-8")), MAX_BODY_BYTES)
        self.assertEqual(parse_release_notes(content, "0.6.50-alpha"),
                         parse_release_notes(RELEASE_0650, "0.6.50-alpha"))
        self.assertEqual(parse_release_notes(content + " ", "0.6.50-alpha"), [])
        self.assertEqual(parse_release_notes(RELEASE_0650 + "🙂" * (MAX_BODY_BYTES // 4), "0.6.50-alpha"), [])

    def test_bad_optional_body_types_and_invalid_unicode_are_unavailable(self):
        for body in (None, False, 123, [], {}, object(), "\ud800"):
            with self.subTest(body_type=type(body).__name__):
                self.assertEqual(parse_release_notes(body, "0.6.50-alpha"), [])
        for version in (None, 50, "latest", "0.6.50-alpha/elsewhere"):
            with self.subTest(version=version):
                self.assertEqual(parse_release_notes(RELEASE_0650, version), [])

    def test_markdown_links_emphasis_and_control_characters_do_not_become_actions(self):
        body = ("**0.6.50-alpha**\n" + bullet("[Camera](https://example.com).",
                "Use __Capture__ with a literal host_timescale value.",
                "Read [notes](javascript:alert) and ~~old~~ new text.\x00"))
        self.assertEqual(parse_release_notes(body, "0.6.50-alpha"), [
            {"title": "Camera.", "before": "Use Capture with a literal host_timescale value.",
             "after": "Read notes and old new text."}])


class ReleaseNotesMetadataTests(unittest.TestCase):
    def release(self, body=RELEASE_0650):
        return {"tag_name": "v0.6.50-alpha", "draft": False, "prerelease": False, "body": body,
                "html_url": "https://untrusted.example/notes",
                "assets": [{"name": "Deadlock_Dolly_0.6.50-alpha_Windows_x64.zip", "state": "uploaded",
                            "size": 100, "digest": "sha256:" + "a" * 64,
                            "browser_download_url": f"https://github.com/{updater.REPOSITORY}/releases/download/v0.6.50-alpha/Deadlock_Dolly_0.6.50-alpha_Windows_x64.zip"}]}

    def test_existing_latest_metadata_supplies_preview_without_another_fetch(self):
        payload = BytesIO(json.dumps(self.release()).encode("utf-8"))
        with patch.object(updater, "open_url", return_value=payload) as network, \
             patch.object(updater, "download_update") as download:
            selected = updater.check_latest("0.6.49-alpha")
        self.assertEqual(selected["notes"], parse_release_notes(RELEASE_0650, "0.6.50-alpha"))
        self.assertEqual(selected["notes_url"],
                         f"https://github.com/{updater.REPOSITORY}/releases/tag/v0.6.50-alpha")
        network.assert_called_once_with(updater.LATEST_URL)
        download.assert_not_called()

    def test_unavailable_optional_preview_does_not_block_a_validated_update(self):
        for body in (None, {}, 123, "unknown format", "x" * (MAX_BODY_BYTES + 1), "\ud800"):
            with self.subTest(body_type=type(body).__name__):
                selected = updater.select_release(self.release(body), "0.6.49-alpha")
                self.assertEqual(selected["notes"], [])
                self.assertEqual(selected["version"], "0.6.50-alpha")
                self.assertEqual(selected["sha256"], "a" * 64)
                self.assertEqual(selected["size"], 100)

    def test_asset_validation_still_rejects_bad_update_metadata_before_notes(self):
        raw = self.release()
        raw["assets"][0]["digest"] = None
        with patch.object(updater, "parse_release_notes") as parser, self.assertRaises(ValueError):
            updater.select_release(raw, "0.6.49-alpha")
        parser.assert_not_called()


class ShippedReleaseNotesTests(unittest.TestCase):
    """The pasted file must satisfy current and older installed preview readers."""

    def test_current_release_notes_parse_into_highlight_entries(self):
        notes = Path(__file__).resolve().parents[1] / "docs" / f"RELEASE_NOTES_{__version__}.md"
        self.assertTrue(notes.is_file(), f"no release notes file for {__version__}")
        entries = validate_publish_body(notes.read_text(encoding="utf-8"), __version__)
        self.assertGreaterEqual(len(entries), 2, "the publish description needs two to four bullets")
        self.assertLessEqual(len(entries), 4, "only the first four bullets reach the desktop preview")
