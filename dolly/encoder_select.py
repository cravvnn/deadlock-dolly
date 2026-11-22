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


def encoder_works(ffmpeg, codec: str) -> bool:
    """Encode one tiny frame once per FFmpeg build; never raises.

    ``ffmpeg -encoders`` lists NVENC, AMF and Quick Sync whatever the
    hardware, so only a real encode shows whether the driver can use them.
    """
    try:
        path = Path(ffmpeg)
        key = (str(path), path.stat().st_mtime_ns, codec)
    except (OSError, TypeError, ValueError):
        return False
    if key not in _PROBE_CACHE:
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
            _PROBE_CACHE[key] = False
        else:
            _PROBE_CACHE[key] = result.returncode == 0
            if result.returncode:
                lines = (result.stderr or "").strip().splitlines()
                LOG.warning("Auto encoder: %s failed its test encode (%s).", codec,
                            lines[-1] if lines else f"exit {result.returncode}")
    return _PROBE_CACHE[key]


def auto_encoder(vendor: str | None = None, ffmpeg=None) -> tuple[str, str]:
    """Return the (codec key, label) Auto should use for this machine.

    With ``ffmpeg``, a vendor encoder that cannot encode a test frame falls
    back to the vendor-neutral Media Foundation encoder.
    """
    resolved = detect_vendor() if vendor is None else vendor
    preferred = VENDOR_ENCODERS.get(resolved)
    if preferred is None or (ffmpeg is not None and not encoder_works(ffmpeg, preferred[0])):
        if preferred is not None:
            LOG.warning("Auto encoder: using %s instead of %s.", FALLBACK_CODEC, preferred[0])
        return FALLBACK_CODEC, "H.264 via ffmpeg Media Foundation"
    return preferred
