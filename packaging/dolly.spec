# Build through tools/build_windows.py on Windows x64.
from pathlib import Path

root = Path(SPECPATH).parent
from PyInstaller.utils.hooks import collect_all
ffmpeg_data, ffmpeg_binaries, ffmpeg_hidden = collect_all("imageio_ffmpeg")
a = Analysis(
    [str(root / "packaging" / "entrypoint.py")],
    pathex=[str(root)],
    binaries=ffmpeg_binaries,
    datas=[(str(root / "assets" / "dolly.ico"), "assets"),
           (str(root / "assets" / "dolly.png"), "assets"),
           (str(root / "assets" / "reshade"), "assets/reshade"),
           (str(root / "assets" / "editing"), "assets/editing"),
           (str(root / "assets" / "ui"), "assets/ui"),
           (str(root / "assets" / "fonts" / "inter"), "assets/fonts/inter"), *ffmpeg_data],
    hiddenimports=["tools.export_captured_audio", "tools.filter_captured_audio_window",
                   "tools.fit_reference_voices", *ffmpeg_hidden],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="DollyApp",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False,
    icon=str(root / "assets" / "dolly.ico"),
    version=str(root / "build" / "windows-version.txt"),
    contents_directory=".",
)
bundle = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="DollyAppRuntime")
# The official game DLL is copied byte-for-byte AFTER freezing by the build
# script. PyInstaller must not analyze/rewrite it as a Python dependency.
