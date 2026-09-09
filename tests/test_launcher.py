import hashlib
import json
import socket
import struct
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from dolly import launcher


GAMEINFO = '''\ufeff"GameInfo"
{
    game "citadel"
    Hidden { SearchPaths { Game "untouched" } }
    FileSystem
    {
        SteamAppId 1422450
        SearchPaths
        {
            // SearchPaths { Game fake } in a comment
            Game_Language citadel_*LANGUAGE*
            /* a comment with } and { and "quotes" */
            Game "citadel"
            Game "core" [$WIN32]
            AddonRoot citadel_addons
        }
    }
    Other { text "SearchPaths { not a block }" }
}
'''


def fake_game(root: Path, executable_name: str = "citadel.exe") -> launcher.GamePaths:
    exe = root / "game/bin/win64" / executable_name
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"test executable")
    server = root / "game/citadel/bin/win64/server.dll"
    server.parent.mkdir(parents=True)
    server.write_bytes(b"original server")
    gameinfo = root / "game/citadel/gameinfo.gi"
    gameinfo.write_bytes(GAMEINFO.encode("utf-8"))
    return launcher.validate_game(root)


class GameInfoTests(unittest.TestCase):
    def test_changes_only_real_searchpath_and_preserves_comments_and_conditions(self):
        result = launcher.make_gameinfo(GAMEINFO, "citadel_dolly_test")
        self.assertIn('Hidden { SearchPaths { Game "untouched" } }', result)
        self.assertIn('Other { text "SearchPaths { not a block }" }', result)
        self.assertIn('Game "core" [$WIN32]', result)
        self.assertIn('/* a comment with } and { and "quotes" */', result)
        self.assertIn('Mod\t"citadel"', result)
        self.assertIn('Write\t"citadel"', result)
        self.assertLess(result.index('Game\t"citadel_dolly_test/cvar_unlocker"'), result.index('Game "citadel"'))
        self.assertTrue(result.startswith("\ufeff"))
        self.assertEqual(result[:result.index("        SearchPaths")], GAMEINFO[:GAMEINFO.index("        SearchPaths")])
        self.assertEqual(result[result.index("    Other"):], GAMEINFO[GAMEINFO.index("    Other"):])

    def test_keeps_gameinfo_relative_mounts_and_existing_mod_write(self):
        original = GAMEINFO.replace('Game "citadel"', 'Mod "|gameinfo_path|."\n            Write "|gameinfo_path|"\n            Game "|gameinfo_path|./custom"')
        result = launcher.make_gameinfo(original, "citadel_dolly_test")
        self.assertIn('Mod "|gameinfo_path|."', result)
        self.assertIn('Write "|gameinfo_path|"', result)
        self.assertIn('Game "|gameinfo_path|./custom"', result)
        self.assertNotIn('Mod\t"citadel"', result)

    def test_existing_unlocker_temporarily_replaced(self):
        original = GAMEINFO.replace('Game "citadel"', 'Game "citadel/cvar_unlocker"\n            Game "citadel"')
        result = launcher.make_gameinfo(original, "citadel_dolly_test")
        self.assertNotIn('Game "citadel/cvar_unlocker"', result)
        self.assertIn('Game "citadel"', result)
        self.assertIn('Game\t"citadel_dolly_test/cvar_unlocker"', result)
        # Generated mount must not become part of the removed-entry comment.
        self.assertEqual(len([t for t in launcher._tokens(result) if t.value == "citadel_dolly_test/cvar_unlocker"]), 1)

    def test_crlf_preserved(self):
        result = launcher.make_gameinfo(GAMEINFO.replace("\n", "\r\n"), "citadel_dolly_test")
        self.assertNotIn("\n", result.replace("\r\n", ""))

    def test_ambiguous_searchpaths_refused(self):
        original = GAMEINFO.replace("SteamAppId 1422450", 'SteamAppId 1422450 SearchPaths { Game "fake" }')
        with self.assertRaises(launcher.LaunchError):
            launcher.make_gameinfo(original, "citadel_dolly_test")

    def test_malformed_gameinfo_refused(self):
        for text in ('"GameInfo" { FileSystem {', 'GameInfo { "oops }', '/* unfinished', 'GameInfo }'):
            with self.subTest(text=text), self.assertRaises(launcher.LaunchError):
                launcher.make_gameinfo(text, "citadel_dolly_test")

    def test_overlay_name_cannot_inject_or_escape(self):
        for name in ('../citadel', 'citadel_dolly_x;quit', 'citadel_dolly_"x', 'citadel_dolly_x/y', 'citadel'):
            with self.subTest(name=name), self.assertRaises(launcher.LaunchError):
                launcher.make_gameinfo(GAMEINFO, name)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.paths = fake_game(self.folder / "Steam Library with spaces/Deadlock")
        self.package = self.folder / "Dolly with spaces"

    def test_accepts_common_user_selected_install_paths(self):
        for path in (self.paths.root, self.paths.game_dir, self.paths.citadel_dir, self.paths.executable):
            with self.subTest(path=path):
                self.assertEqual(launcher.validate_game(path), self.paths)

    def test_deadlock_exe_and_containing_install_folders_are_accepted(self):
        paths = fake_game(self.folder / "New Deadlock", "deadlock.exe")
        for path in (paths.root, paths.game_dir, paths.citadel_dir, paths.executable.parent, paths.executable):
            with self.subTest(path=path):
                self.assertEqual(launcher.validate_game(path), paths)
        command = launcher.build_command(paths, paths.game_dir / "citadel_dolly_test", 29090)
        self.assertEqual(command[0], str(paths.executable))
        self.assertIn("-dev", command)
        self.assertIn("-insecure", command)

    def test_folder_prefers_deadlock_but_explicit_executable_selection_is_preserved(self):
        current = self.paths.executable.with_name("deadlock.exe")
        current.write_bytes(b"current executable")
        self.assertEqual(launcher.validate_game(self.paths.root).executable, current)
        for executable in (self.paths.executable, current):
            with self.subTest(executable=executable):
                paths = launcher.validate_game(executable)
                self.assertEqual(paths.executable, executable)
                self.assertEqual(launcher.build_command(paths, paths.game_dir / "citadel_dolly_test", 29090)[0], str(executable))
        self.assertEqual(current.read_bytes(), b"current executable")
        self.assertEqual(self.paths.executable.read_bytes(), b"test executable")

    def test_mixed_case_executable_spelling_is_preserved_and_current_name_preferred(self):
        current = self.paths.executable.with_name("Deadlock.EXE")
        current.write_bytes(b"current executable")
        self.assertEqual(launcher.validate_game(self.paths.root).executable, current)
        self.assertEqual(launcher.validate_game(current).executable, current)

    def test_missing_selected_executable_does_not_silently_choose_legacy(self):
        with self.assertRaisesRegex(launcher.LaunchError, "selected Deadlock executable is missing"):
            launcher.validate_game(self.paths.executable.with_name("deadlock.exe"))

    def test_unrecognized_executable_is_refused_without_substitution(self):
        other = self.paths.executable.with_name("unrelated.exe")
        other.write_bytes(b"unrelated executable")
        with self.assertRaisesRegex(launcher.LaunchError, "deadlock.exe or legacy citadel.exe"):
            launcher.validate_game(other)

    def test_missing_executable_message_identifies_both_supported_names(self):
        self.paths.executable.unlink()
        with self.assertRaisesRegex(launcher.LaunchError, r"Missing Deadlock game executable \(deadlock.exe or legacy citadel.exe\)"):
            launcher.validate_game(self.paths.root)

    def test_missing_gameinfo_is_reported_separately_from_executable(self):
        self.paths.gameinfo.unlink()
        with self.assertRaisesRegex(launcher.LaunchError, "Missing Deadlock gameinfo.gi"):
            launcher.validate_game(self.paths.root)

    def test_missing_real_server_refused(self):
        (self.paths.citadel_dir / "bin/win64/server.dll").unlink()
        with self.assertRaisesRegex(launcher.LaunchError, "server.dll"):
            launcher.validate_game(self.paths.root)

    def test_build_command_mandatory_flags_and_paths_are_single_arguments(self):
        demo = self.folder / "Demos with spaces/test.dem"
        demo.parent.mkdir()
        demo.write_bytes(b"demo")
        overlay = self.paths.game_dir / "citadel_dolly_test"
        for protocol, flags in [("netcon", ["-netconport", "29090"]), ("vconsole", ["-vconsole", "-vconport", "29090"])]:
            with self.subTest(protocol=protocol):
                command = launcher.build_command(self.paths, overlay, 29090, demo, protocol)
                self.assertEqual(command, [str(self.paths.executable), "-dev", "-insecure", "-console", *flags, "-game", str(self.paths.citadel_dir)])
                self.assertNotIn(str(overlay), command)
                self.assertNotIn(str(demo), command)
                self.assertFalse(any("playdemo" in arg or "cvar_unhide" in arg for arg in command))

    def test_default_protocol_is_netconsole(self):
        overlay = self.paths.game_dir / "citadel_dolly_test"
        command = launcher.build_command(self.paths, overlay, 29090)
        self.assertIn("-netconport", command)
        self.assertNotIn("-vconsole", command)
        self.assertEqual(launcher.Session.__dataclass_fields__["protocol"].default, "netcon")

    def test_deferred_demo_still_must_exist(self):
        with self.assertRaisesRegex(launcher.LaunchError, "existing, extracted"):
            launcher.build_command(self.paths, self.paths.game_dir / "citadel_dolly_test", 29090, self.folder / "missing.dem")

    def test_invalid_port_and_demo_console_injection_refused(self):
        overlay = self.paths.game_dir / "citadel_dolly_test"
        for port in (True, 0, 65536, "29090", "29090 -secure"):
            with self.subTest(port=port), self.assertRaises(launcher.LaunchError):
                launcher.build_command(self.paths, overlay, port)
        # These names exist on Windows too; exercise the real filesystem and
        # full command builder for console separators accepted by the OS.
        for name in ('x;quit.dem', 'x;+connect.dem', 'x+quit.dem'):
            demo = self.folder / name
            demo.write_bytes(b"demo")
            with self.subTest(name=name), self.assertRaisesRegex(launcher.LaunchError, "console separators"):
                launcher.build_command(self.paths, overlay, 29090, demo)
        # Windows rejects control characters/quotes before a file can be
        # created. Simulate only filesystem lookup to exercise the actual
        # separator validator for every such input on every test platform.
        for name in ('x\nquit.dem', 'x\rquit.dem', 'x\x00quit.dem', 'x".dem'):
            demo = self.folder / name
            with self.subTest(name=name), \
                    patch.object(Path, "resolve", return_value=demo), \
                    patch.object(Path, "is_file", return_value=True), \
                    self.assertRaisesRegex(launcher.LaunchError, "console separators"):
                launcher._validate_demo(demo)

    def test_explicit_netcon_protocol_has_its_own_fixed_flags(self):
        overlay = self.paths.game_dir / "citadel_dolly_test"
        args = launcher.build_command(self.paths, overlay, 29091, protocol="netcon")
        self.assertIn("-insecure", args)
        self.assertIn("-dev", args)
        self.assertEqual(args[4:6], ["-netconport", "29091"])
        self.assertNotIn("-vconport", args)
        with self.assertRaisesRegex(launcher.LaunchError, "protocol"):
            launcher.build_command(self.paths, overlay, 29091, protocol="-secure")

    def test_busy_console_port_refused_before_mount(self):
        before = self.paths.gameinfo.read_bytes()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            with patch.object(launcher, "_check_runtime"), patch.object(launcher, "running_processes", return_value={"steam.exe"}), patch.object(launcher, "PACKAGE_ROOT", self.package), self.assertRaisesRegex(launcher.LaunchError, "busy"):
                launcher.launch(self.paths.root, port=port)
        self.assertEqual(self.paths.gameinfo.read_bytes(), before)

    def test_overlay_outside_game_refused(self):
        with self.assertRaises(launcher.LaunchError):
            launcher.build_command(self.paths, self.folder / "citadel_dolly_test", 29090)

    def test_running_game_refused_before_mutation(self):
        before = self.paths.gameinfo.read_bytes()
        for name in ("citadel.exe", "deadlock.exe", "Citadel.EXE", "Deadlock.EXE"):
            with self.subTest(process=name), patch.object(launcher, "_check_runtime"), patch.object(launcher, "running_processes", return_value={"steam.exe", name}), patch.object(launcher, "PACKAGE_ROOT", self.package), patch.object(launcher.subprocess, "Popen") as popen:
                with self.assertRaisesRegex(launcher.LaunchError, "already running"):
                    launcher.launch(self.paths.root)
                popen.assert_not_called()
            self.assertEqual(self.paths.gameinfo.read_bytes(), before)
        self.assertFalse(self.package.exists())
        self.assertFalse(list(self.paths.game_dir.glob("citadel_dolly_*")))

    def test_missing_steam_refused(self):
        with patch.object(launcher, "_check_runtime"), patch.object(launcher, "running_processes", return_value=set()):
            with self.assertRaisesRegex(launcher.LaunchError, "Open Steam"):
                launcher.launch(self.paths.root)

    def test_launch_transaction_then_restore_while_game_runs_and_cleanup(self):
        before = self.paths.gameinfo.read_bytes()
        process = MagicMock()
        process.pid = 5678
        process.poll.return_value = None
        with patch.object(launcher, "_check_runtime"), patch.object(launcher, "running_processes", return_value={"steam.exe"}), patch.object(launcher, "PACKAGE_ROOT", self.package), patch.object(launcher.subprocess, "Popen", return_value=process) as popen, patch.object(launcher.threading, "Thread"):
            session = launcher.launch(self.paths.root)
        self.assertNotEqual(self.paths.gameinfo.read_bytes(), before)
        self.assertIn(session.overlay_dir.name, self.paths.gameinfo.read_text())
        self.assertEqual((session.session_dir / "original.gameinfo.gi").read_bytes(), before)
        self.assertEqual((self.paths.citadel_dir / "bin/win64/server.dll").read_bytes(), b"original server")
        self.assertFalse((session.overlay_dir / "gameinfo.gi").exists())
        mounted_dll = session.overlay_dir / "cvar_unlocker/bin/win64/server.dll"
        self.assertEqual(hashlib.sha256(mounted_dll.read_bytes()).hexdigest(), launcher.UNLOCKER_SHA256)
        self.assertEqual(session.pid, 5678)
        self.assertEqual(session.status(), "running")
        self.assertFalse(session.close())
        self.assertTrue(session.overlay_dir.exists())
        self.assertTrue(session.restore_gameinfo())
        self.assertEqual(self.paths.gameinfo.read_bytes(), before)
        self.assertTrue(session.restore_gameinfo())
        process.terminate.assert_not_called()
        process.kill.assert_not_called()
        self.assertFalse(popen.call_args.kwargs["shell"])
        self.assertEqual(popen.call_args.kwargs["cwd"], str(self.paths.game_dir))
        process.poll.return_value = 0
        self.assertTrue(session.close())
        self.assertFalse(session.overlay_dir.exists())
        self.assertEqual(self.paths.gameinfo.read_bytes(), before)
        self.assertTrue(session.log_path.exists())
        self.assertEqual(json.loads((session.session_dir / "session.json").read_text())["pid"], 5678)

    def test_selected_replay_is_journaled_but_never_loaded_on_startup(self):
        demo = self.folder / "Chosen shot with spaces.dem"
        demo.write_bytes(b"demo")
        process = MagicMock()
        process.pid = 2345
        process.poll.return_value = None
        with patch.object(launcher, "_check_runtime"), patch.object(launcher, "running_processes", return_value={"steam.exe"}), patch.object(launcher, "PACKAGE_ROOT", self.package), patch.object(launcher.subprocess, "Popen", return_value=process) as popen, patch.object(launcher.threading, "Thread"):
            session = launcher.launch(self.paths.root, demo)
        record = json.loads((session.session_dir / "session.json").read_text())
        self.assertEqual(record["selected_demo"], str(demo.resolve()))
        self.assertEqual(record["protocol"], "netcon")
        self.assertNotIn("+playdemo", popen.call_args.args[0])
        self.assertNotIn("+cvar_unhide", popen.call_args.args[0])
        self.assertNotIn(str(demo), popen.call_args.args[0])

    def test_exit_watcher_preserves_restored_state_and_records_exit_code(self):
        before = self.paths.gameinfo.read_bytes()
        process = MagicMock()
        process.pid = 2346
        process.poll.return_value = None
        process.wait.return_value = 3221225477
        with patch.object(launcher, "_check_runtime"), patch.object(launcher, "running_processes", return_value={"steam.exe"}), patch.object(launcher, "PACKAGE_ROOT", self.package), patch.object(launcher.subprocess, "Popen", return_value=process), patch.object(launcher.threading, "Thread") as thread:
            session = launcher.launch(self.paths.root)
        session.restore_gameinfo()
        process.poll.return_value = process.wait.return_value
        thread.call_args.kwargs["target"]()
        record = json.loads((session.session_dir / "session.json").read_text())
        self.assertEqual(record["exit_code"], 3221225477)
        self.assertRegex(record["exited_utc"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(record["config_state"], "restored")
        self.assertEqual(self.paths.gameinfo.read_bytes(), before)
        self.assertFalse(session.overlay_dir.exists())
        self.assertIn("process exited with code 3221225477", session.read_log())

    def test_exit_watcher_restores_mounted_gameinfo_even_when_exit_journaling_fails(self):
        before = self.paths.gameinfo.read_bytes()
        process = MagicMock()
        process.pid = 2347
        process.poll.return_value = None
        process.wait.return_value = 1
        with patch.object(launcher, "_check_runtime"), patch.object(launcher, "running_processes", return_value={"steam.exe"}), patch.object(launcher, "PACKAGE_ROOT", self.package), patch.object(launcher.subprocess, "Popen", return_value=process), patch.object(launcher.threading, "Thread") as thread:
            session = launcher.launch(self.paths.root)
        process.poll.return_value = 1
        with patch.object(session, "record_exit", side_effect=OSError("disk temporarily busy")):
            thread.call_args.kwargs["target"]()
        self.assertEqual(self.paths.gameinfo.read_bytes(), before)
        self.assertFalse(session.overlay_dir.exists())

    def test_failed_process_creation_removes_only_generated_overlay(self):
        before = self.paths.gameinfo.read_bytes()
        with patch.object(launcher, "_check_runtime"), patch.object(launcher, "running_processes", return_value={"steam.exe"}), patch.object(launcher, "PACKAGE_ROOT", self.package), patch.object(launcher.subprocess, "Popen", side_effect=OSError("fake start failure")):
            with self.assertRaisesRegex(launcher.LaunchError, "fake start failure"):
                launcher.launch(self.paths.root)
        self.assertFalse(list(self.paths.game_dir.glob("citadel_dolly_*")))
        self.assertEqual(self.paths.gameinfo.read_bytes(), before)

    def test_game_starting_during_preparation_is_refused(self):
        before = self.paths.gameinfo.read_bytes()
        for name in ("citadel.exe", "deadlock.exe", "Citadel.EXE", "Deadlock.EXE"):
            with self.subTest(process=name), patch.object(launcher, "_check_runtime"), patch.object(launcher, "running_processes", side_effect=[{"steam.exe"}, {"steam.exe"}, {"steam.exe", name}]), patch.object(launcher, "PACKAGE_ROOT", self.package), patch.object(launcher.subprocess, "Popen") as popen:
                with self.assertRaisesRegex(launcher.LaunchError, "started while"):
                    launcher.launch(self.paths.root)
                popen.assert_not_called()
            self.assertEqual(self.paths.gameinfo.read_bytes(), before)
            self.assertFalse(list(self.paths.game_dir.glob("citadel_dolly_*")))

    def _launched_session(self):
        process = MagicMock()
        process.pid = 6789
        process.poll.return_value = None
        with patch.object(launcher, "_check_runtime"), patch.object(launcher, "running_processes", return_value={"steam.exe"}), patch.object(launcher, "PACKAGE_ROOT", self.package), patch.object(launcher.subprocess, "Popen", return_value=process), patch.object(launcher.threading, "Thread"):
            return launcher.launch(self.paths.root)

    def test_recovery_after_editor_crash_restores_backup(self):
        before = self.paths.gameinfo.read_bytes()
        session = self._launched_session()
        self.assertNotEqual(self.paths.gameinfo.read_bytes(), before)
        with patch.object(launcher, "_check_runtime"), patch.object(launcher, "running_processes", return_value={"steam.exe"}), patch.object(launcher, "PACKAGE_ROOT", self.package):
            self.assertEqual(launcher.recover_pending(self.paths.root), [str(session.session_dir)])
            self.assertEqual(launcher.recover_pending(self.paths.root), [])
        self.assertEqual(self.paths.gameinfo.read_bytes(), before)
        self.assertFalse(session.overlay_dir.exists())

    def test_recovery_never_overwrites_concurrent_steam_or_user_edit(self):
        session = self._launched_session()
        external = GAMEINFO.replace('game "citadel"', 'game "citadel new version"').encode("utf-8")
        self.paths.gameinfo.write_bytes(external)
        with self.assertRaisesRegex(launcher.LaunchError, "newer bytes untouched"):
            session.restore_gameinfo()
        self.assertEqual(self.paths.gameinfo.read_bytes(), external)
        self.assertEqual(json.loads((session.session_dir / "session.json").read_text())["config_state"], "conflict")
        with patch.object(launcher, "_check_runtime"), patch.object(launcher, "running_processes", return_value={"steam.exe"}), patch.object(launcher, "PACKAGE_ROOT", self.package), self.assertRaisesRegex(launcher.LaunchError, "newer bytes untouched"):
            launcher.recover_pending()
        self.assertEqual(self.paths.gameinfo.read_bytes(), external)

    def test_recovery_refused_while_any_game_runs(self):
        session = self._launched_session()
        patched = self.paths.gameinfo.read_bytes()
        for name in ("citadel.exe", "deadlock.exe", "Citadel.EXE", "Deadlock.EXE"):
            with self.subTest(process=name), patch.object(launcher, "_check_runtime"), patch.object(launcher, "running_processes", return_value={name}), patch.object(launcher, "PACKAGE_ROOT", self.package), self.assertRaisesRegex(launcher.LaunchError, "Exit Deadlock"):
                launcher.recover_pending()
            self.assertEqual(self.paths.gameinfo.read_bytes(), patched)
            self.assertTrue(session.overlay_dir.exists())

    def test_tampered_backup_is_not_restored(self):
        session = self._launched_session()
        patched = self.paths.gameinfo.read_bytes()
        (session.session_dir / "original.gameinfo.gi").write_bytes(b"changed backup")
        with self.assertRaisesRegex(launcher.LaunchError, "hash check"):
            session.restore_gameinfo()
        self.assertEqual(self.paths.gameinfo.read_bytes(), patched)

    def test_console_port_must_belong_only_to_our_live_pid(self):
        session = self._launched_session()
        for listeners, expected in [([], False), ([(session.port, 999)], False), ([(session.port, session.pid)], True), ([(session.port, session.pid), (session.port, 999)], False)]:
            with self.subTest(listeners=listeners), patch.object(launcher, "_tcp_listeners", return_value=listeners):
                self.assertEqual(session.owns_console_port(), expected)
        session.process.poll.return_value = 0
        with patch.object(launcher, "_tcp_listeners") as table:
            self.assertFalse(session.owns_console_port())
            table.assert_not_called()

    def test_windows_listener_table_decodes_ports_and_pid_and_filters_addresses(self):
        def ipv4(ip, port, pid):
            return struct.pack("<I", 2) + socket.inet_aton(ip) + struct.pack("<III", socket.htons(port), 0, 0) + struct.pack("<I", pid)
        data = struct.pack("<I", 3) + ipv4("127.0.0.1", 29090, 56) + ipv4("0.0.0.0", 29091, 57) + ipv4("192.168.0.2", 29090, 58)
        self.assertEqual(launcher._listener_table_rows(data, 2), [(29090, 56), (29091, 57)])
        data6 = struct.pack("<I", 1) + b"\0" * 16 + struct.pack("<II", 0, socket.htons(29090)) + b"\0" * 16 + struct.pack("<IIII", 0, 0, 2, 59)
        self.assertEqual(launcher._listener_table_rows(data6, 23), [(29090, 59)])
        with self.assertRaises(launcher.LaunchError):
            launcher._listener_table_rows(data[:-1], 2)

    def test_journal_and_backup_exist_before_original_is_patched(self):
        real_write = launcher._atomic_write
        observations = []

        def observed_write(path, data, mode=None):
            if path == self.paths.gameinfo:
                journals = list((self.package / "logs").glob("*/session.json"))
                self.assertEqual(len(journals), 1)
                metadata = json.loads(journals[0].read_text())
                self.assertEqual(metadata["config_state"], "prepared")
                self.assertEqual((journals[0].parent / "original.gameinfo.gi").read_bytes(), GAMEINFO.encode("utf-8"))
                observations.append(path)
            real_write(path, data, mode)

        with patch.object(launcher, "_atomic_write", side_effect=observed_write):
            self._launched_session()
        self.assertEqual(observations, [self.paths.gameinfo])

    def test_discovery_uses_secondary_library_manifest(self):
        steam = self.folder / "Primary Steam"
        (steam / "steamapps").mkdir(parents=True)
        library = self.folder / "Secondary Games"
        install = fake_game(library / "steamapps/common/Deadlock Custom Name")
        (library / "steamapps/appmanifest_1422450.acf").write_text('"AppState" { "installdir" "Deadlock Custom Name" }')
        (steam / "steamapps/libraryfolders.vdf").write_text('"libraryfolders" { "0" { "path" "' + str(library) + '" } }')
        with patch.object(launcher, "_steam_roots", return_value=[steam]):
            self.assertEqual(launcher.discover_game(), install.root)

    def test_discovery_finds_current_executable_in_primary_and_secondary_libraries(self):
        steam = self.folder / "Current Primary Steam"
        primary = fake_game(steam / "steamapps/common/Deadlock", "deadlock.exe")
        library = self.folder / "Current Secondary Games"
        secondary = fake_game(library / "steamapps/common/Deadlock Custom Name", "deadlock.exe")
        (library / "steamapps/appmanifest_1422450.acf").write_text('"AppState" { "installdir" "Deadlock Custom Name" }')
        (steam / "steamapps/libraryfolders.vdf").write_text('"libraryfolders" { "0" { "path" "' + str(library) + '" } }')
        with patch.object(launcher, "_steam_roots", return_value=[steam]):
            self.assertEqual(launcher.discover_game(), primary.root)
            primary.executable.unlink()
            self.assertEqual(launcher.discover_game(), secondary.root)

    def test_bundled_binary_is_pinned_x64_release(self):
        dll = launcher._verified_unlocker()
        meta = json.loads((launcher.UNLOCKER_ROOT / "THIRD_PARTY.json").read_text())
        self.assertEqual(hashlib.sha256(dll.read_bytes()).hexdigest(), meta["bundled_file_sha256"])
        self.assertEqual(meta["license"], "MIT")

    def _native_fixture(self):
        pins = {}
        for relative in launcher.NATIVE_GAME_SHA256:
            path = self.paths.game_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("native test " + relative).encode("ascii"))
            pins[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        native_root = self.folder / "native-resources"
        dll = native_root / "bin/win64/DollyNative.dll"
        dll.parent.mkdir(parents=True)
        binary = bytearray(80)
        binary[:2] = b"MZ"
        struct.pack_into("<I", binary, 60, 64)
        binary[64:68] = b"PE\0\0"
        struct.pack_into("<H", binary, 68, 0x8664)
        dll.write_bytes(binary)
        (native_root / "build_info.json").write_text(json.dumps({
            "abi": 2, "sha256": hashlib.sha256(binary).hexdigest()}))
        return native_root, pins, dll

    def test_native_game_hash_mismatch_is_refused_before_any_recovery_or_mount(self):
        native_root, pins, dll = self._native_fixture()
        (self.paths.game_dir / next(iter(pins))).write_bytes(b"new Steam build")
        before = self.paths.gameinfo.read_bytes()
        with patch.object(launcher, "_check_runtime"), patch.object(launcher, "running_processes", return_value={"steam.exe"}), patch.object(launcher, "NATIVE_ROOT", native_root), patch.object(launcher, "NATIVE_GAME_SHA256", pins), patch.object(launcher, "PACKAGE_ROOT", self.package), patch.object(launcher, "recover_pending") as recover, patch.object(launcher.subprocess, "Popen") as popen, patch.object(launcher.NativeBridge, "create") as bridge:
            with self.assertRaisesRegex(launcher.LaunchError, "does not support"):
                launcher.launch(self.paths.root, native=True)
        recover.assert_not_called()
        popen.assert_not_called()
        bridge.assert_not_called()
        self.assertEqual(self.paths.gameinfo.read_bytes(), before)
        self.assertFalse(self.package.exists())
        self.assertFalse(list(self.paths.game_dir.glob("citadel_dolly_*")))

    def test_native_approved_build_list_accepts_each_entry_and_rejects_unknown(self):
        native_root, pins, dll = self._native_fixture()
        relative = "citadel/bin/win64/client.dll"
        client = self.paths.game_dir / relative
        original = client.read_bytes()
        updated = b"second reviewed client fixture"
        pins[relative] = (pins[relative], hashlib.sha256(updated).hexdigest())
        with patch.object(launcher, "NATIVE_ROOT", native_root), patch.object(launcher, "NATIVE_GAME_SHA256", pins):
            for data in (original, updated):
                client.write_bytes(data)
                self.assertEqual(launcher._verified_native(self.paths), dll)
            client.write_bytes(updated + b"unreviewed change")
            with self.assertRaisesRegex(launcher.LaunchError, "does not support this client.dll"):
                launcher._verified_native(self.paths)

    def test_native_reports_all_changed_modules_before_modifying_game(self):
        native_root, pins, dll = self._native_fixture()
        for relative in pins:
            (self.paths.game_dir / relative).write_bytes(b"new unreviewed module")
        before = self.paths.gameinfo.read_bytes()
        with patch.object(launcher, "NATIVE_ROOT", native_root), patch.object(launcher, "NATIVE_GAME_SHA256", pins):
            with self.assertRaises(launcher.LaunchError) as raised:
                launcher._verified_native(self.paths)
        for name in ("client.dll", "engine2.dll", "tier0.dll"):
            self.assertIn(name, str(raised.exception))
        self.assertEqual(self.paths.gameinfo.read_bytes(), before)

    def test_native_binary_hash_abi_and_architecture_are_validated(self):
        native_root, pins, dll = self._native_fixture()
        manifest = native_root / "build_info.json"
        original = dll.read_bytes()
        original_manifest = manifest.read_bytes()
        with patch.object(launcher, "NATIVE_ROOT", native_root), patch.object(launcher, "NATIVE_GAME_SHA256", pins):
            self.assertEqual(launcher._verified_native(self.paths), dll)
            dll.write_bytes(original + b"tamper")
            with self.assertRaisesRegex(launcher.LaunchError, "SHA-256"):
                launcher._verified_native(self.paths)
            dll.write_bytes(original)
            for metadata in ({"abi": 1, "sha256": hashlib.sha256(original).hexdigest()},
                             {"abi": True, "sha256": hashlib.sha256(original).hexdigest()},
                             [1, 2]):
                manifest.write_text(json.dumps(metadata))
                with self.assertRaisesRegex(launcher.LaunchError, "manifest"):
                    launcher._verified_native(self.paths)
            invalid = bytearray(original)
            struct.pack_into("<H", invalid, 68, 0x14C)
            dll.write_bytes(invalid)
            manifest.write_text(json.dumps({"abi": 2, "sha256": hashlib.sha256(invalid).hexdigest()}))
            with self.assertRaisesRegex(launcher.LaunchError, "x64"):
                launcher._verified_native(self.paths)
            manifest.write_bytes(original_manifest)
            dll.unlink()
            with self.assertRaisesRegex(launcher.LaunchError, "missing"):
                launcher._verified_native(self.paths)

    def test_native_launch_starts_heartbeat_before_game_and_mounts_unchanged_unlocker_sibling(self):
        native_root, pins, dll = self._native_fixture()
        before = self.paths.gameinfo.read_bytes()
        process = MagicMock(pid=6781)
        process.poll.return_value = None
        bridge = MagicMock(token="a" * 32, editor_pid=5432)
        def start(*args, **kwargs):
            create.assert_called_once_with()
            bridge.bind_game.assert_not_called()
            return process
        with patch.object(launcher, "_check_runtime"), patch.object(launcher, "running_processes", return_value={"steam.exe"}), patch.object(launcher, "NATIVE_ROOT", native_root), patch.object(launcher, "NATIVE_GAME_SHA256", pins), patch.object(launcher, "PACKAGE_ROOT", self.package), patch.object(launcher.NativeBridge, "create", return_value=bridge) as create, patch.object(launcher.subprocess, "Popen", side_effect=start) as popen, patch.object(launcher.threading, "Thread"):
            session = launcher.launch(self.paths.root, native=True)
        bridge.bind_game.assert_called_once_with(6781)
        self.assertIs(session.native, bridge)
        target = session.overlay_dir / "cvar_unlocker/bin/win64"
        self.assertEqual((target / "server.dll").read_bytes(), dll.read_bytes())
        self.assertEqual(hashlib.sha256((target / "dolly_cvar_unlocker.dll").read_bytes()).hexdigest(), launcher.UNLOCKER_SHA256)
        self.assertEqual((target / "dolly_native.cfg").read_bytes(), b"DOLLY_NATIVE_1\n" + b"a" * 32 + b"\n5432\n")
        args = popen.call_args.args[0]
        self.assertIn("-dev", args)
        self.assertIn("-insecure", args)
        self.assertFalse(any("playdemo" in arg for arg in args))
        self.assertEqual((self.paths.citadel_dir / "bin/win64/server.dll").read_bytes(), b"original server")
        self.assertFalse(session.close())
        bridge.close.assert_not_called()
        process.poll.return_value = 0
        self.assertTrue(session.close())
        bridge.close.assert_called_once_with()
        self.assertEqual(self.paths.gameinfo.read_bytes(), before)
        self.assertFalse(session.overlay_dir.exists())

    def test_native_failed_process_creation_closes_bridge_and_restores_gameinfo(self):
        native_root, pins, dll = self._native_fixture()
        before = self.paths.gameinfo.read_bytes()
        bridge = MagicMock(token="c" * 32, editor_pid=5432)
        with patch.object(launcher, "_check_runtime"), patch.object(launcher, "running_processes", return_value={"steam.exe"}), patch.object(launcher, "NATIVE_ROOT", native_root), patch.object(launcher, "NATIVE_GAME_SHA256", pins), patch.object(launcher, "PACKAGE_ROOT", self.package), patch.object(launcher.NativeBridge, "create", return_value=bridge), patch.object(launcher.subprocess, "Popen", side_effect=OSError("test failed create")):
            with self.assertRaisesRegex(launcher.LaunchError, "test failed create"):
                launcher.launch(self.paths.root, native=True)
        bridge.close.assert_called_once_with()
        bridge.bind_game.assert_not_called()
        self.assertEqual(self.paths.gameinfo.read_bytes(), before)
        self.assertFalse(list(self.paths.game_dir.glob("citadel_dolly_*")))

    def test_console_launch_does_not_load_or_validate_native(self):
        with patch.object(launcher, "_verified_native") as native, patch.object(launcher.NativeBridge, "create") as create:
            session = self._launched_session()
        native.assert_not_called()
        create.assert_not_called()
        self.assertIsNone(session.native)


if __name__ == "__main__":
    unittest.main()
