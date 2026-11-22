"""Resolve the automatic video encoder without assuming NVIDIA hardware.

The bundled FFmpeg build exposes NVENC, AMF and Quick Sync. Auto maps the
display adapters Windows reports to the matching encoder instead of always
choosing NVENC, so AMD users get AMF and Intel users get Quick Sync. An
unrecognized vendor keeps the vendor-neutral Media Foundation encoder.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
from pathlib import Path
import re
import subprocess
import sys
import threading

LOG = logging.getLogger(__name__)

VENDOR_PRIORITY = ("nvidia", "amd", "intel")
VENDOR_ENCODERS = {
    "nvidia": ("h264_nvenc", "NVIDIA H.264 (NVENC)"),
    "amd": ("h264_amf", "AMD H.264 (AMF)"),
    "intel": ("h264_qsv", "Intel H.264 (Quick Sync)"),
}
VENDOR_IDS = {"10de": "nvidia", "1002": "amd", "1022": "amd", "8086": "intel"}
VENDOR_KEYWORDS = (
    ("nvidia", ("nvidia", "geforce", "quadro", "rtx ", "gtx ")),
    ("amd", ("amd", "radeon", "ati ", "firepro")),
    ("intel", ("intel", "arc ", "iris", "uhd graphics")),
)
FALLBACK_CODEC = "h264_mf"


class _DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = (
        ("cb", wintypes.DWORD),
        ("DeviceName", wintypes.WCHAR * 32),
        ("DeviceString", wintypes.WCHAR * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", wintypes.WCHAR * 128),
        ("DeviceKey", wintypes.WCHAR * 128),
    )


def _vendor_from(device_id: str, description: str) -> str:
    match = re.search(r"VEN_([0-9A-Fa-f]{4})", device_id or "")
    if match:
        vendor = VENDOR_IDS.get(match.group(1).lower())
        if vendor:
            return vendor
    folded = (description or "").casefold()
    for vendor, keywords in VENDOR_KEYWORDS:
        if any(keyword in folded for keyword in keywords):
            return vendor
    return ""


def gpu_adapters() -> tuple[tuple[str, str], ...]:
    """Installed display adapters as (vendor, description); never raises."""
    if sys.platform != "win32":
        return ()
    try:
        user32 = ctypes.windll.user32
        result: list[tuple[str, str]] = []
        index = 0
        seen: set[tuple[str, str]] = set()
        while True:
            device = _DISPLAY_DEVICEW()
            device.cb = ctypes.sizeof(_DISPLAY_DEVICEW)
            if not user32.EnumDisplayDevicesW(None, index, ctypes.byref(device), 0):
                break
            index += 1
            description = str(device.DeviceString or "")
            vendor = _vendor_from(str(device.DeviceID or ""), description)
            if not vendor:
                continue
            entry = (vendor, description)
            if entry not in seen:
                seen.add(entry)
                result.append(entry)
        return tuple(result)
    except Exception:  # noqa: BLE001 - detection must never block an export
        LOG.exception("GPU adapter detection failed")
        return ()


def detect_vendor(adapters: tuple[tuple[str, str], ...] | None = None) -> str:
    entries = gpu_adapters() if adapters is None else tuple(adapters)
    present = {vendor for vendor, _description in entries}
    for vendor in VENDOR_PRIORITY:
        if vendor in present:
            return vendor
    return ""


def describe_adapters(adapters: tuple[tuple[str, str], ...] | None = None) -> str:
    entries = gpu_adapters() if adapters is None else tuple(adapters)
    if not entries:
        return "no display adapters detected"
    return "; ".join(f"{description or 'display adapter'} [{vendor}]"
                     for vendor, description in entries)


_PROBE_CACHE: dict[tuple[str, int, str], bool] = {}
_PROBING: set[tuple[str, int, str]] = set()
_PROBING_LOCK = threading.Lock()


def _probe(path: Path, key: tuple[str, int, str], codec: str) -> bool:
    """Run one test encode. Only a clean answer is cached for the session.

    A timeout or a process that could not start can pass a moment later, so
    those fail this call without being remembered.
    """
    from .runtime import external_program_environment
    try:
        with external_program_environment() as environment:
            result = subprocess.run(
                [str(path), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                 "-i", "color=s=256x144", "-frames:v", "1", "-c:v", codec, "-f", "null", "-"],
                capture_output=True, text=True, timeout=10, env=environment,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as exc:
        LOG.warning("Auto encoder: %s probe could not run: %s", codec, exc)
        return False
    if result.returncode:
        lines = (result.stderr or "").strip().splitlines()
        LOG.warning("Auto encoder: %s failed its test encode (%s).", codec,
                    lines[-1] if lines else f"exit {result.returncode}")
    _PROBE_CACHE[key] = result.returncode == 0
    return _PROBE_CACHE[key]


def _probe_once(path: Path, key: tuple[str, int, str], codec: str) -> None:
    """Background probe; skips an answered or already running test encode."""
    with _PROBING_LOCK:
        if key in _PROBING or key in _PROBE_CACHE:
            return
        _PROBING.add(key)
    try:
        _probe(path, key, codec)
    finally:
        with _PROBING_LOCK:
            _PROBING.discard(key)


def _probe_key(ffmpeg, codec: str):
    try:
        path = Path(ffmpeg)
        return path, (str(path), path.stat().st_mtime_ns, codec)
    except (OSError, TypeError, ValueError):
        return None


def encoder_works(ffmpeg, codec: str, *, wait: bool = True) -> bool:
    """Encode one tiny frame once per FFmpeg build; never raises.

    ``ffmpeg -encoders`` lists NVENC, AMF and Quick Sync whatever the
    hardware, so only a real encode shows whether the driver can use them.
    With ``wait=False`` an unanswered probe continues in the background and
    the encoder is assumed to work; a real failure is still reported when
    the take starts encoding.
    """
    probe = _probe_key(ffmpeg, codec)
    if probe is None:
        return False
    path, key = probe
    cached = _PROBE_CACHE.get(key)
    if cached is not None:
        return cached
    if not wait:
        threading.Thread(target=_probe_once, args=(path, key, codec),
                         name="Dolly encoder probe", daemon=True).start()
        LOG.info("Auto encoder: %s test encode not finished; using it for this take.", codec)
        return True
    return _probe(path, key, codec)


def prewarm(ffmpeg) -> None:
    """Answer Auto's test encode off the UI thread before Record needs it."""
    def run():
        preferred = VENDOR_ENCODERS.get(detect_vendor())
        probe = _probe_key(ffmpeg, preferred[0]) if preferred is not None else None
        if probe is not None:
            _probe_once(*probe, preferred[0])
    threading.Thread(target=run, name="Dolly encoder prewarm", daemon=True).start()


def auto_encoder(vendor: str | None = None, ffmpeg=None, *, wait: bool = True) -> tuple[str, str]:
    """Return the (codec key, label) Auto should use for this machine.

    With ``ffmpeg``, a vendor encoder that cannot encode a test frame falls
    back to the vendor-neutral Media Foundation encoder. ``wait`` is passed
    to :func:`encoder_works`.
    """
    resolved = detect_vendor() if vendor is None else vendor
    preferred = VENDOR_ENCODERS.get(resolved)
    if preferred is None or (ffmpeg is not None and not encoder_works(ffmpeg, preferred[0], wait=wait)):
        if preferred is not None:
            LOG.warning("Auto encoder: using %s instead of %s.", FALLBACK_CODEC, preferred[0])
        return FALLBACK_CODEC, "H.264 via ffmpeg Media Foundation"
    return preferred
