"""Export only WAV assets actually selected by DollyNative's source-voice log.

The native log records real voice starts and releases, not a captured mix.
Unmatched starts, dropped records, and missing assets stay visible in the
manifest instead of being described as exact cutoffs.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import wave

# Keep direct CLI execution able to import the shared Dolly runtime boundary.
if not __package__:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Dolly runs from a GUI process. Without this flag, every Source2Viewer and
# FFmpeg invocation opens a console window. Reconstructed audio can extract
# dozens of assets, which makes the export look like it is spawning cmd.exe
# forever. CREATE_NO_WINDOW keeps those child processes invisible.
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _convert_asset(asset: str, destination: Path, *, vpk: Path, viewer: Path,
                   ffmpeg: Path, temporary: Path) -> None:
    source_name = asset[:-5] + ".vsnd_c"
    raw = temporary / (hashlib.sha256(asset.encode()).hexdigest()[:16] + ".audio")
    from dolly.runtime import external_program_environment
    with external_program_environment() as environment:
        result = subprocess.run([str(viewer), "-i", str(vpk), "-d", "-f", source_name,
                                 "-o", str(raw)], cwd=temporary, capture_output=True, text=True,
                                env=environment, creationflags=_CREATE_NO_WINDOW)
    if result.returncode or not raw.is_file():
        raise RuntimeError(f"Source2Viewer could not extract {asset}: {result.stderr or result.stdout}")
    with external_program_environment() as environment:
        subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(raw), "-map", "0:a:0", "-c:a", "pcm_s16le",
                        str(destination)], check=True, env=environment, creationflags=_CREATE_NO_WINDOW)


def _metadata(line: str) -> dict[str, str]:
    return dict(part.strip().split("=", 1) for part in line.removeprefix("#").strip().split(";")
                if "=" in part)


def _events(path: Path) -> tuple[dict[str, str], list[dict[str, str]], dict[str, str]]:
    from itertools import chain
    with path.open(newline="", encoding="utf-8-sig") as source:
        first = source.readline()
        if not first.startswith("# "):
            raise ValueError("Native log has no clock metadata")
        header = _metadata(first)
        columns = source.readline()
        if not columns.startswith("kind,qpc_ticks,"):
            raise ValueError("Unsupported native source-voice log")
        rows = []
        footer = {}
        for row in csv.DictReader(chain((columns,), source)):
            if row["kind"].startswith("#"):
                footer = _metadata(row["kind"])
            else:
                rows.append(row)
    if not footer or "capture_end_qpc" not in footer:
        raise ValueError("Native capture was not finalized")
    for key in ("qpc_frequency", "capture_start_qpc"):
        if int(header[key]) <= 0:
            raise ValueError(f"Invalid native clock field: {key}")
    return header, rows, footer


def export(log: Path, output: Path, *, vpk: Path, viewer: Path, ffmpeg: Path) -> dict:
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Output folder must be empty: {output}")
    header, events, footer = _events(log)
    frequency = int(header["qpc_frequency"])
    origin = int(header["capture_start_qpc"])
    end = int(footer["capture_end_qpc"])
    if end < origin:
        raise ValueError("Capture end precedes capture start")
    events.sort(key=lambda item: int(item["qpc_ticks"]))
    active: dict[tuple[str, str], dict] = {}
    usages = []
    orphan_stops = 0
    for event in events:
        stamp = int(event["qpc_ticks"])
        if not origin <= stamp <= end:
            raise ValueError("Voice event is outside the recorded clock interval")
        key = (event["voice_slot"], event["voice_id"])
        if event["kind"] == "start":
            asset = event["vsnd_path"].replace("\\", "/")
            row = {
                "start_seconds": f"{(stamp - origin) / frequency:.9f}",
                "stop_seconds": "", "start_tick": event["demo_tick"], "stop_tick": "",
                "start_engine_seconds": event["engine_seconds"], "stop_engine_seconds": "",
                "start_qpc": stamp, "stop_qpc": "", "voice_slot": key[0],
                "voice_id": key[1], "soundevent": event["soundevent"],
                "source_volume": event.get("source_volume", ""),
                "source_rate_parameter": event.get("source_rate_parameter", ""),
                "source_x": event.get("source_x", ""),
                "source_y": event.get("source_y", ""),
                "source_z": event.get("source_z", ""),
                "source_vsnd": asset, "source_wav": "", "stop_reason": "not_observed",
            }
            if key in active:
                active[key]["stop_reason"] = "voice_id_reused_without_stop"
            active[key] = row
            usages.append(row)
        elif event["kind"] == "stop":
            row = active.pop(key, None)
            if row is None:
                orphan_stops += 1
                continue
            row.update(stop_seconds=f"{(stamp - origin) / frequency:.9f}",
                       stop_tick=event["demo_tick"],
                       stop_engine_seconds=event["engine_seconds"],
                       stop_qpc=stamp, stop_reason="voice_released")
        else:
            raise ValueError(f"Unknown voice event kind: {event['kind']}")
    output.mkdir(parents=True, exist_ok=True)
    wave_dir = output / "sounds"
    wave_dir.mkdir()
    assets = sorted({row["source_vsnd"] for row in usages})
    extracted = {}
    wave_hashes = {}
    failed = {}
    with tempfile.TemporaryDirectory(prefix="deadlock-selected-") as folder:
        temporary = Path(folder)
        for asset in assets:
            if not asset.lower().startswith("sounds/") or not asset.lower().endswith(".vsnd"):
                failed[asset] = "Voice path is not a game sounds/*.vsnd asset"
                continue
            digest = hashlib.sha256(asset.lower().encode()).hexdigest()[:10]
            wav = wave_dir / f"{Path(asset).stem}_{digest}.wav"
            try:
                _convert_asset(asset, wav, vpk=vpk, viewer=viewer, ffmpeg=ffmpeg,
                               temporary=temporary)
                with wave.open(str(wav), "rb") as audio:
                    duration = audio.getnframes() / audio.getframerate()
                digest_state = hashlib.sha256()
                with wav.open("rb") as content:
                    for block in iter(lambda: content.read(1024 * 1024), b""):
                        digest_state.update(block)
                content_hash = digest_state.hexdigest()
                existing = wave_hashes.get(content_hash)
                if existing:
                    wav.unlink()
                else:
                    existing = f"sounds/{wav.name}"
                    wave_hashes[content_hash] = existing
                extracted[asset] = {"path": existing,
                                    "source_duration_seconds": duration}
            except (OSError, RuntimeError, ValueError, wave.Error, subprocess.CalledProcessError) as exc:
                try:
                    wav.unlink(missing_ok=True)
                except OSError:
                    pass  # Retain a private leftover and report the original asset failure.
                failed[asset] = str(exc)
    for row in usages:
        row["source_wav"] = extracted.get(row["source_vsnd"], {}).get("path", "")
    columns = ["start_seconds", "stop_seconds", "start_tick", "stop_tick",
               "start_engine_seconds", "stop_engine_seconds", "start_qpc", "stop_qpc",
               "voice_slot", "voice_id", "soundevent", "source_volume",
               "source_rate_parameter", "source_x", "source_y", "source_z",
               "source_vsnd", "source_wav",
               "stop_reason"]
    with (output / "audio_usage.csv").open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=columns)
        writer.writeheader()
        writer.writerows(usages)
    dropped = int(footer.get("dropped", "0"))
    write_error = int(footer.get("write_error", "0"))
    manifest = {
        "native_log": str(log), "capture_duration_seconds": (end - origin) / frequency,
        "voice_starts": len(usages),
        "voice_stops_matched": sum(row["stop_reason"] == "voice_released" for row in usages),
        "starts_without_observed_stop": sum(row["stop_reason"] == "not_observed" for row in usages),
        "orphan_stops": orphan_stops, "dropped_native_records": dropped,
        "native_write_error": write_error,
        "unique_selected_assets": len(assets), "exported_unique_wavs": len(wave_hashes),
        "failed_assets": failed, "assets": extracted,
        "timebase": "Relative to native capture start, from QueryPerformanceCounter",
        "fidelity": ("All logged voices paired and assets exported" if
                     not dropped and not write_error and not orphan_stops and not failed and
                     all(row["stop_reason"] == "voice_released" for row in usages)
                     else "Partial: inspect unmatched, dropped, or failed records"),
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                           encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--vpk", type=Path, required=True)
    parser.add_argument("--viewer", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.log, args.output, vpk=args.vpk,
                            viewer=args.viewer, ffmpeg=args.ffmpeg), indent=2))


if __name__ == "__main__":
    main()
