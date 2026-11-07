"""Keep native voices audible during a loopback reference window."""
from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path
import re

import soundfile as sf


def filter_window(voice_log: Path, reference_wav: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError(output)
    metadata = json.loads(reference_wav.with_suffix(".json").read_text())
    info = sf.info(reference_wav)
    first_sample = (metadata["first_block_end_monotonic"] -
                    metadata["loopback_block_frames"] / info.samplerate)
    with voice_log.open(newline="", encoding="utf-8") as source:
        header = source.readline()
        columns = source.readline()
        body = source.read().splitlines()
    frequency = re.search(r"qpc_frequency=(\d+)", header)
    if not frequency:
        raise ValueError("Native voice log has no QPC frequency")
    qpc_start = round(first_sample * int(frequency[1]))
    qpc_end = round((first_sample + info.frames / info.samplerate) * int(frequency[1]))
    starts = {}
    stops = {}
    events = []
    for line in body:
        if line.startswith("# "):
            continue
        row = next(csv.DictReader(io.StringIO(columns + line + "\n")))
        if row["kind"] not in ("start", "stop"):
            raise ValueError(f"Unknown native voice kind: {row['kind']}")
        events.append((line, row))
        if row["kind"] == "start":
            starts[(row["voice_slot"], row["voice_id"])] = row
        else:
            stops[(row["voice_slot"], row["voice_id"])] = row
    selected = {key for key, row in starts.items()
                if int(row["qpc_ticks"]) < qpc_end and
                (key not in stops or int(stops[key]["qpc_ticks"]) > qpc_start)}
    footer = next((line for line in reversed(body) if line.startswith("# ")), None)
    if not footer:
        raise ValueError("Native voice log has no completion footer")
    with output.open("x", newline="", encoding="utf-8") as destination:
        destination.write(header)
        destination.write(columns)
        for line, row in events:
            if (row["voice_slot"], row["voice_id"]) in selected:
                destination.write(line + "\n")
        destination.write(footer + "\n")
    return {"selected_voices": len(selected), "qpc_start": qpc_start,
            "qpc_end": qpc_end, "output": str(output)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("voice_log", type=Path)
    parser.add_argument("reference_wav", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(filter_window(args.voice_log, args.reference_wav, args.output))


if __name__ == "__main__":
    main()
