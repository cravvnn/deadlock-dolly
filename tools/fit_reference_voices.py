"""Calibrate captured source voices to a temporary game loopback recording.

This produces a source-only assembly and a per-use editor sheet. The reference
is used to estimate voice timing and stereo gain; it is never included in the
assembled WAV. Calibration is specific to the measured replay scene.
"""

import argparse
import csv
import json
import shutil
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import correlate, resample_poly


def fit(args):
    """Fit a scene from an argparse-compatible options object."""
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "sounds").mkdir(exist_ok=True)

    reference, sr = sf.read(args.reference_wav, always_2d=True, dtype="float32")
    if sr != 48000 or reference.shape[1] != 2:
        raise ValueError("Reference must be 48 kHz stereo")
    if not np.isfinite(reference).all():
        raise ValueError("Reference audio contains nonfinite samples")
    reference = reference.astype(np.float64)
    engine_start = getattr(args, "engine_start", None)
    speed = float(getattr(args, "speed", 1.0) or 1.0)
    if engine_start is None:
        if not getattr(args, "reference_json", None):
            raise ValueError("A reference JSON is required for loopback audio.")
        meta = json.loads(args.reference_json.read_text(encoding="utf-8"))
        origin = meta["first_block_end_monotonic"] - meta["loopback_block_frames"] / sr
        qpc_frequency = int(meta.get("qpc_frequency", 10_000_000))
    else:
        meta = None
        origin = 0.0
        qpc_frequency = 10_000_000
    rates = {}
    with args.state_csv.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            rate = float(row["slot_20"])
            if 0.25 <= rate <= 4:
                rates.setdefault(row["voice_id"], []).append(rate)

    cache = {}
    voices = []
    with (args.source_dir / "audio_usage.csv").open(newline="", encoding="utf-8") as stream:
        usage = list(csv.DictReader(stream))
    for row in usage:
        asset = row["source_wav"]
        if asset not in cache:
            wave, source_rate = sf.read(args.source_dir / asset, always_2d=True, dtype="float32")
            if source_rate != sr:
                wave = resample_poly(wave, sr, source_rate, axis=0)
            if wave.shape[1] == 1:
                wave = np.repeat(wave, 2, axis=1)
            if not np.isfinite(wave[:, :2]).all():
                raise ValueError("Source audio contains nonfinite samples")
            cache[asset] = wave[:, :2]
        source = cache[asset]
        if engine_start is not None:
            start_engine = row.get("start_engine_seconds") or ""
            if not start_engine:
                continue
            raw = round(((float(start_engine) - float(engine_start)) / speed) * sr)
            stop_engine = row.get("stop_engine_seconds") or ""
            end = (round(((float(stop_engine) - float(engine_start)) / speed) * sr)
                   if stop_engine else len(reference))
            rate = speed
            # The engine WAV is frame-locked, so the onset can land exactly on
            # the event sample. Search a little before it instead of assuming
            # the loopback device's output delay.
            lo = max(0, raw - round(.015 * sr))
        else:
            raw = round((int(row["start_qpc"]) / qpc_frequency - origin) * sr)
            end = (round((int(row["stop_qpc"]) / qpc_frequency - origin) * sr)
                   if row["stop_qpc"] else len(reference))
            rate = float(np.median(rates.get(row["voice_id"], [1.0])))
            lo = max(0, raw + round(.015 * sr))
        hi = min(len(reference) - 500, raw + round(.16 * sr))
        if hi <= lo or (engine_start is None and end < lo):
            continue
        source_offset = max(0, -raw) * rate
        if engine_start is not None:
            # One-shot voices can stop on the same engine tick they start. Use
            # the full source length and let the engine window clip it.
            n = min(len(reference) - hi,
                    int((len(source) - source_offset) / rate), round(8 * sr))
        else:
            n = min(end - lo, len(reference) - hi,
                    int((len(source) - source_offset) / rate), round(8 * sr))
        if n < 512:
            continue
        positions = source_offset + np.arange(n) * rate
        wave = np.column_stack([
            np.interp(positions, np.arange(len(source)), source[:, channel])
            for channel in range(2)
        ]).astype(np.float32)
        energy = np.sum(wave.astype(np.float64) ** 2, axis=0)
        if np.sum(energy) < 1e-9:
            continue
        voices.append({"row": row, "lo": lo, "hi": hi, "wave": wave,
                       "energy": energy, "place": None, "gain": np.zeros(2),
                       "rate": rate, "source_offset": source_offset})

    residual = reference.copy()
    progress = []
    for iteration in range(args.passes):
        for voice in voices:
            wave = voice["wave"]
            n = len(wave)
            if voice["place"] is not None:
                at = voice["place"]
                residual[at:at + n] += wave * voice["gain"]
            lo = voice["lo"]
            hi = min(voice["hi"], len(reference) - n)
            if hi <= lo:
                continue
            section = residual[lo:hi + n]
            cov = np.column_stack([
                correlate(section[:, channel], wave[:, channel],
                          mode="valid", method="fft")
                for channel in range(2)
            ])
            gains = np.clip(np.divide(cov, voice["energy"], out=np.zeros_like(cov),
                                      where=voice["energy"] > 0), 0, getattr(args, "max_gain", 1.0))
            score = np.sum(2 * gains * cov - gains * gains * voice["energy"], axis=1)
            index = int(np.argmax(score))
            voice["place"] = lo + index
            voice["gain"] = gains[index]
            at = voice["place"]
            residual[at:at + n] -= wave * voice["gain"]
        assembled = reference - residual
        correlation = float(np.corrcoef(reference.ravel(), assembled.ravel())[0, 1])
        progress.append(correlation)
        print(f"pass {iteration + 1}: correlation={correlation:.6f}", flush=True)

    offset = round(args.clip_offset * sr)
    finish = (min(len(reference), offset + round(args.clip_duration * sr))
              if args.clip_duration is not None else len(reference))
    if not 0 <= offset < finish:
        raise ValueError("Invalid clip interval")
    assembled = reference - residual
    clip_reference = reference[offset:finish]
    clip_assembled = assembled[offset:finish]
    clip_residual = residual[offset:finish]
    sf.write(args.output / "reconstructed.wav", clip_assembled, sr, subtype="FLOAT")

    rows = []
    copied = set()
    for voice in voices:
        at = voice["place"]
        if at is None or max(voice["gain"]) < 1e-5:
            continue
        stop = at + len(voice["wave"])
        if stop <= offset or at >= finish:
            continue
        row = voice["row"]
        asset = row["source_wav"]
        if asset not in copied:
            destination = args.output / asset
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(args.source_dir / asset, destination)
            copied.add(asset)
        start_in_clip = max(at, offset)
        rows.append({
            "voice_id": row["voice_id"],
            "soundevent": row["soundevent"],
            "source_wav": asset,
            "start_seconds": f"{(start_in_clip - offset) / sr:.9f}",
            "end_seconds": f"{(min(stop, finish) - offset) / sr:.9f}",
            "asset_offset_seconds": f"{(voice['source_offset'] + (start_in_clip - at) * voice['rate']) / sr:.9f}",
            "playback_rate": f"{voice['rate']:.9f}",
            "gain_left": f"{voice['gain'][0]:.9f}",
            "gain_right": f"{voice['gain'][1]:.9f}",
            "native_start_qpc": row["start_qpc"],
            "native_stop_qpc": row["stop_qpc"],
            "stop_reason": row["stop_reason"],
        })
    rows.sort(key=lambda row: (float(row["start_seconds"]), row["voice_id"]))
    columns = ("voice_id", "soundevent", "source_wav", "start_seconds", "end_seconds",
               "asset_offset_seconds", "playback_rate", "gain_left", "gain_right",
               "native_start_qpc", "native_stop_qpc", "stop_reason")
    with (args.output / "fitted_usage.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    correlation = float(np.corrcoef(clip_reference.ravel(), clip_assembled.ravel())[0, 1])
    report = {
        "measurement": "Pearson waveform correlation of interleaved stereo samples",
        "correlation": correlation,
        "explained_reference_energy": 1 - float(np.mean(clip_residual ** 2) / np.mean(clip_reference ** 2)),
        "reference_rms": float(np.sqrt(np.mean(clip_reference ** 2))),
        "difference_rms": float(np.sqrt(np.mean(clip_residual ** 2))),
        "sample_rate": sr,
        "clip_offset_seconds": offset / sr,
        "duration_seconds": len(clip_reference) / sr,
        "fitted_uses": len(rows),
        "unique_source_wavs": len(copied),
        "pass_correlations_full_capture": progress,
        "calibration": "Per-voice start offset and stereo gain fitted to this reference recording; playback rate measured from soundsystem voice slots.",
    }
    print(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--reference-wav", type=Path, required=True)
    parser.add_argument("--reference-json", type=Path)
    parser.add_argument("--state-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clip-offset", type=float, default=0.0)
    parser.add_argument("--clip-duration", type=float)
    parser.add_argument("--passes", type=int, default=6)
    parser.add_argument("--max-gain", type=float, default=1.0)
    parser.add_argument("--engine-start", type=float,
                        help="Replay seconds at the first movie WAV sample")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="Movie export speed (0.5 = half-speed reference)")
    args = parser.parse_args()
    fit(args)


if __name__ == "__main__":
    main()
