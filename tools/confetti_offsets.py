"""Locate Dolly's confetti/particle wrapper RVAs and maintain the native table.

The particle entry points in ``native/src/dolly_confetti.cpp`` are gated by
exact-hash ``kBuilds`` rows (manager getter, create, control, transform, destroy
and a release thunk). Every game update relocates them, so this is a
compatibility step like the camera pipeline. This tool derives the row from an
installed ``client.dll`` fully offline and can verify the committed table.

Method (no game addresses hard-coded beyond relocation-free prologues): the
create wrapper has a unique 16-byte prologue; its first ``call`` targets the
manager getter. The control/transform/destroy/release wrappers share relocation-
free prologues but are not unique, so each is disambiguated by requiring its
first ``call`` to target the same manager getter. RIP-relative bytes in the
manager getter and release thunk are wildcarded only for the initial scan; the
resolved row stores the real runtime bytes.

Usage:
    python tools/confetti_offsets.py --client <client.dll>          # print the row
    python tools/confetti_offsets.py --check --client <client.dll>  # verify kBuilds
    python tools/confetti_offsets.py --append --client <client.dll> # add the row
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]

# Relocation-free prologues (identical across reviewed builds).
CREATE = bytes.fromhex("4057415641574881ec90000000458bf1")
CONTROL = bytes.fromhex("48895c24084889742410574883ec3049")
TRANSFORM = bytes.fromhex("48895c24084889742410574883ec5049")
DESTROY = bytes.fromhex("48895c2408574883ec20410fb6f88bda")
# Release thunk: prologue, call manager, test/jz, call manager again and load
# the vtable pointer (4c 8b ...) -- the instruction that distinguishes it from
# the sibling sharing the same prologue (which sets a flag instead).
# Wildcards: the two call rel32s and the jz displacement.
RELEASE_SIG = bytes.fromhex("40534883ec208bdae8b3a2ac004885c07416e8a9a2ac008bd3488bc84c8b00")
RELEASE_MASK = bytes([1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 1, 1, 1, 1, 0,
                      1, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1])
_MANAGER_BYTES_LEN = 8
_RELEASE_BYTES_LEN = 16
# The transform wrapper is always this many bytes after the control wrapper.
CONTROL_STRIDE = 0x1A0


@dataclass(frozen=True)
class Row:
    manager: int
    create: int
    control: int
    transform: int
    destroy: int
    release: int
    manager_bytes: bytes
    release_bytes: bytes

    def cpp(self) -> str:
        manager = ", ".join(f"0x{b:02x}" for b in self.manager_bytes)
        release = ", ".join(f"0x{b:02x}" for b in self.release_bytes)
        return (
            f"    {{0x{self.manager:x},\n"
            f"      0x{self.create:x},\n"
            f"      0x{self.control:x},\n"
            f"      0x{self.transform:x},\n"
            f"      0x{self.destroy:x},\n"
            f"      0x{self.release:x},\n"
            f"      {{{manager}}},\n"
            f"      {{{release}}}}},")


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def image_info(data: bytes) -> tuple[int, int, int, int]:
    """Return (image_base, text_rva, text_file_ptr, text_size)."""
    e_lfanew = _u32(data, 0x3C)
    assert data[e_lfanew:e_lfanew + 4] == b"PE\0\0", "not a PE image"
    coff = e_lfanew + 4
    num_sections = _u16(data, coff + 2)
    option_size = _u16(data, coff + 16)
    optional = coff + 20
    magic = _u16(data, optional)
    image_base = _u32(data, optional + 28) if magic == 0x10B else \
        struct.unpack_from("<Q", data, optional + 24)[0]
    first = optional + option_size
    for index in range(num_sections):
        entry = first + index * 40
        name = data[entry:entry + 8].rstrip(b"\0").decode("ascii", "replace")
        if name == ".text":
            virtual, raw_size, raw_ptr = struct.unpack_from("<III", data, entry + 12)
            return image_base, virtual, raw_ptr, raw_size
    raise ValueError("client image has no .text section")


class _Text:
    """The .text section, indexed by offset within the section (== RVA delta)."""

    def __init__(self, data: bytes):
        self.data = data
        _, self.base_rva, ptr, self.size = image_info(data)
        self.bytes = data[ptr:ptr + self.size]

    def read(self, rva: int, size: int) -> bytes:
        offset = rva - self.base_rva
        return self.bytes[offset:offset + size]

    def exact(self, signature: bytes) -> list[int]:
        hits = []
        start = 0
        while True:
            index = self.bytes.find(signature, start)
            if index < 0:
                return hits
            hits.append(self.base_rva + index)
            start = index + 1

    def masked(self, signature: bytes, mask: bytes) -> list[int]:
        # Anchor on the first contiguous run of fixed bytes, then verify.
        prefix = next(i for i in range(len(signature)) if mask[i])
        prefix_end = prefix
        while prefix_end < len(signature) and mask[prefix_end]:
            prefix_end += 1
        anchor = signature[prefix:prefix_end]
        hits = []
        start = 0
        while True:
            index = self.bytes.find(anchor, start)
            if index < 0:
                return hits
            origin = index - prefix
            if origin >= 0 and all(not mask[j] or self.bytes[origin + j] == signature[j]
                                   for j in range(len(signature))):
                hits.append(self.base_rva + origin)
            start = index + 1

    def calls_in(self, rva: int, window: int = 0x50) -> list[int]:
        """All direct call targets in the first ``window`` bytes of a function.

        Scanning every ``E8`` and computing its relative target avoids trusting
        an immediate byte that merely looks like the ``E8`` opcode.
        """
        start = rva - self.base_rva
        targets = []
        for i in range(start, min(start + window, len(self.bytes) - 4)):
            if self.bytes[i] == 0xE8:
                rel = struct.unpack_from("<i", self.bytes, i + 1)[0]
                targets.append(self.base_rva + i + 5 + rel)
        return targets

    def is_manager_getter(self, rva: int) -> bool:
        head = self.read(rva, 8)
        return head[:3] == bytes([0x48, 0x8B, 0x05]) and head[7] == 0xC3


def scan(client_path: Path) -> Row:
    """Derive the confetti row from an installed client.dll (offline)."""
    text = _Text(Path(client_path).read_bytes())
    create = text.exact(CREATE)
    if len(create) != 1:
        raise ValueError(f"expected one create wrapper, found {len(create)}")
    create_rva = create[0]
    manager_candidates = [target for target in text.calls_in(create_rva)
                          if text.is_manager_getter(target)]
    if not manager_candidates:
        raise ValueError("create wrapper has no manager getter call")
    manager = manager_candidates[0]

    def resolve(candidates: list[int], label: str) -> int:
        picked = [rva for rva in candidates if manager in text.calls_in(rva)]
        if len(picked) != 1:
            raise ValueError(f"expected one {label} wrapper, found {len(picked)}")
        return picked[0]

    transform = resolve(text.exact(TRANSFORM), "transform")
    destroy = resolve(text.exact(DESTROY), "destroy")
    release = resolve(text.masked(RELEASE_SIG, RELEASE_MASK), "release")
    # The control wrapper shares its prologue with two siblings; it is always
    # exactly one stride before the transform wrapper (transform - control).
    control = transform - CONTROL_STRIDE
    if control not in text.exact(CONTROL):
        raise ValueError("derived control wrapper lacks the reviewed prologue")

    return Row(manager, create_rva, control, transform, destroy, release,
               text.read(manager, _MANAGER_BYTES_LEN),
               text.read(release, _RELEASE_BYTES_LEN))


def parse_rows(source: str) -> list[Row]:
    table = source[source.index("kBuilds{{") + len("kBuilds{{"):]
    table = table[:table.index("}};")]
    row_re = re.compile(
        r"\{\s*(0x[0-9a-fA-F]+)\s*,\s*(0x[0-9a-fA-F]+)\s*,\s*(0x[0-9a-fA-F]+)\s*,\s*"
        r"(0x[0-9a-fA-F]+)\s*,\s*(0x[0-9a-fA-F]+)\s*,\s*(0x[0-9a-fA-F]+)\s*,\s*"
        r"\{([0-9a-fA-Fx,\s]+)\}\s*,\s*\{([0-9a-fA-Fx,\s]+)\}\s*\}", re.S)
    rows = []
    for match in row_re.finditer(table):
        rvas = [int(match.group(i), 16) for i in range(1, 7)]
        manager_bytes = bytes(int(v, 16) for v in re.findall(r"0x([0-9a-fA-F]+)", match.group(7)))
        release_bytes = bytes(int(v, 16) for v in re.findall(r"0x([0-9a-fA-F]+)", match.group(8)))
        rows.append(Row(*rvas, manager_bytes, release_bytes))
    return rows


def source_path() -> Path:
    return ROOT / "native" / "src" / "dolly_confetti.cpp"


def newest_row_matches(rows: list[Row], row: Row) -> bool:
    matched = [r.create for r in rows
               if r.manager_bytes == row.manager_bytes and r.release_bytes == row.release_bytes]
    return bool(matched) and matched[-1] == rows[-1].create == row.create


def append_row(source: str, row: Row) -> str:
    """Return source with ``row`` appended to kBuilds and the count incremented."""
    start = source.index("kBuilds{{") + len("kBuilds{{")
    end = source.index("}};", start)
    source = source[:start] + source[start:end].rstrip() + "\n" + row.cpp() + "\n" + \
        source[end:]
    return re.sub(r"std::array<BuildOffsets, (\d+)> kBuilds",
                  lambda m: f"std::array<BuildOffsets, {int(m.group(1)) + 1}> kBuilds", source, count=1)


def _client_from_args(args) -> Path:
    if args.client is not None:
        return Path(args.client)
    sys.path.insert(0, str(ROOT))
    from dolly import launcher  # noqa: E402
    install = launcher.discover_game()
    if install is None:
        raise SystemExit("Deadlock is not installed; pass --client <client.dll>")
    return install / "game/citadel/bin/win64/client.dll"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--client", type=Path, default=None,
                        help="installed client.dll (default: discovered install)")
    parser.add_argument("--check", action="store_true",
                        help="verify the committed kBuilds table against the install")
    parser.add_argument("--append", action="store_true",
                        help="append the derived row to native/src/dolly_confetti.cpp")
    args = parser.parse_args(argv)

    client = _client_from_args(args)
    if not client.is_file():
        print(f"error: no client.dll at {client}", file=sys.stderr)
        return 2
    try:
        row = scan(client)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    source = source_path().read_text(encoding="utf-8")
    rows = parse_rows(source)

    if args.append:
        if newest_row_matches(rows, row):
            print("kBuilds already has this row; nothing to do.")
            return 0
        source_path().write_text(append_row(source, row), encoding="utf-8", newline="\n")
        print("Appended row; REVIEW the diff and run the native tests before committing.\n")
        print(row.cpp())
        return 0

    if args.check:
        if newest_row_matches(rows, row):
            print(f"ok: kBuilds newest row matches {client.name} "
                  f"(create 0x{row.create:x}).")
            return 0
        print("STALE: the committed kBuilds newest row does not match the installed "
              f"{client.name}. Derived row:\n")
        print(row.cpp())
        return 1

    print(row.cpp())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
