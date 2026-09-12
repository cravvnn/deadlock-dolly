# Build through tools/build_windows.py on Windows x64.
from pathlib import Path

root = Path(SPECPATH).parent
a = Analysis(
    [str(root / "packaging" / "entrypoint.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / "assets" / "dolly.ico"), "assets"),
           (str(root / "assets" / "dolly.png"), "assets"),
           (str(root / "assets" / "reshade"), "assets/reshade")],
    hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="Dolly",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False,
    icon=str(root / "assets" / "dolly.ico"),
    version=str(root / "build" / "windows-version.txt"),
    contents_directory="_internal",
)
bundle = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="DeadlockDolly")
# The official game DLL is copied byte-for-byte AFTER freezing by the build
# script. PyInstaller must not analyze/rewrite it as a Python dependency.
