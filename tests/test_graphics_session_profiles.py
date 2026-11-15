"""Dolly-only graphics profiles: layering, in-session changes and saving."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dolly import editing_session, graphics_profiles
from dolly.launch_errors import LaunchError


def video(values):
    body = "".join(f'\t"{key}"\t"{value}"\n' for key, value in {"Version": "20", **values}.items())
    return ('"video.cfg"\n{\n' + body + "}\n").encode("utf-8")


class GraphicsSessionProfiles(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.library = self.root / "graphics-profiles.json"
        self.normal = self.root / "normal-video.txt"
        self.normal.write_bytes(video({"setting.defaultres": "1920", "setting.r_citadel_shadow_quality": "0"}))
        self.patches = [patch.object(graphics_profiles, "library_path", return_value=self.library),
                        patch.object(graphics_profiles, "current_video", return_value=self.normal)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.folder.cleanup()

    def profile(self, name, values):
        library = graphics_profiles.load_library()
        profile = {"id": f"{len(library['profiles']) + 1:032x}", "name": name, "video_version": "20",
                   "values": values}
        library["profiles"].append(profile)
        library["selected"] = profile["id"]
        graphics_profiles.save_library(library)
        return profile

    def session(self, launched, written, profile=None):
        session = self.root / "session"
        (session / editing_session.DIRECTORY / "cfg").mkdir(parents=True)
        audit = {"profile_fields": launched}
        if profile:
            audit["profile"] = {"id": profile["id"], "name": profile["name"]}
        (session / editing_session.JOURNAL).write_text(json.dumps(audit))
        (session / editing_session.DIRECTORY / "cfg" / "video.txt").write_bytes(video(written))
        return session

    def test_profile_carries_display_and_dlss_and_launch_options_win(self):
        profile = self.profile("4K DLSS", {"setting.defaultres": "3840", "setting.defaultresheight": "2160",
                                           "setting.r_citadel_upscaling": "1", "setting.r_dlss_preset": "5",
                                           "setting.r_citadel_shadow_quality": "3"})
        baseline = editing_session._baseline()
        data, audit = editing_session._video(baseline, {"setting.defaultres": "1920"}, profile["id"])
        values = graphics_profiles._values(data)
        self.assertEqual((values["setting.defaultres"], values["setting.r_citadel_upscaling"],
                          values["setting.r_dlss_preset"]), ("3840", "1", "5"))
        self.assertEqual(audit["profile_fields"]["setting.defaultresheight"], "2160")
        data, _audit = editing_session._video(baseline, {}, profile["id"], {"setting.defaultres": "1280"})
        self.assertEqual(graphics_profiles._values(data)["setting.defaultres"], "1280")
        with self.assertRaises(LaunchError):
            graphics_profiles._quality({"setting.defaultres": "99999"})
        with self.assertRaises(LaunchError):
            graphics_profiles._quality({"setting.monitor_index": "1"})  # Never part of a profile.

    def test_in_game_changes_save_to_the_profile_or_a_new_one_and_never_to_normal_settings(self):
        normal = self.normal.read_bytes()
        launched = {"setting.r_citadel_shadow_quality": "2", "setting.r_citadel_upscaling": "0"}
        self.assertIsNone(editing_session.session_graphics(self.session(launched, launched)))
        (self.root / "session").rename(self.root / "unchanged")
        profile = self.profile("Rec", {"setting.r_citadel_shadow_quality": "2"})
        changed = {"setting.r_citadel_shadow_quality": "3", "setting.r_citadel_upscaling": "1",
                   "setting.r_citadel_dlss_settings_mode": "2", "setting.monitor_index": "1"}
        session = self.session(launched, changed, profile)
        found = editing_session.session_graphics(session)
        self.assertEqual(found["profile"]["id"], profile["id"])
        self.assertNotIn("setting.monitor_index", found["changes"])
        editing_session.save_session_graphics(session, profile_id=profile["id"])
        saved = next(p for p in graphics_profiles.load_library()["profiles"] if p["id"] == profile["id"])
        self.assertEqual((saved["values"]["setting.r_citadel_shadow_quality"],
                          saved["values"]["setting.r_citadel_dlss_settings_mode"]), ("3", "2"))
        created = editing_session.save_session_graphics(session, name="Recording")
        library = graphics_profiles.load_library()
        self.assertEqual((library["format"], library["selected"]), (2, created["id"]))
        with self.assertRaisesRegex(LaunchError, "already exists"):
            editing_session.save_session_graphics(session, name="recording")
        self.assertEqual(self.normal.read_bytes(), normal)

    def test_format_one_library_still_loads(self):
        identifier = "b" * 32
        self.library.write_text(json.dumps({"format": 1, "selected": identifier, "profiles": [
            {"id": identifier, "name": "Old", "video_version": "20",
             "values": {"setting.r_citadel_shadow_quality": "1"}}]}))
        self.assertEqual(graphics_profiles.load_library()["profiles"][0]["name"], "Old")


if __name__ == "__main__":
    unittest.main()
