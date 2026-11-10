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
    selected = set()
    events = []
    for line in body:
        if line.startswith("# "):
            continue
        row = next(csv.DictReader(io.StringIO(columns + line + "\n")))
        if row["kind"] not in ("start", "stop"):
            raise ValueError(f"Unknown native voice kind: {row['kind']}")
        events.append((line, row))
    # Pair each lifetime in clock order; an older stop cannot close a reused
    # identity's newer start. Keep all rows for selected identities so export's
    # existing pairing and partial-capture diagnostics remain authoritative.
    for _line, row in sorted(events, key=lambda event: int(event[1]["qpc_ticks"])):
        key = (row["voice_slot"], row["voice_id"])
        if row["kind"] == "start":
            previous = starts.get(key)
            if previous is not None and int(previous["qpc_ticks"]) < qpc_end:
                selected.add(key)  # No observed stop: conservatively retain.
            starts[key] = row
        else:
            previous = starts.pop(key, None)
            if (previous is not None and int(previous["qpc_ticks"]) < qpc_end
                    and int(row["qpc_ticks"]) > qpc_start):
                selected.add(key)
    selected.update(key for key, row in starts.items()
                    if int(row["qpc_ticks"]) < qpc_end)
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
