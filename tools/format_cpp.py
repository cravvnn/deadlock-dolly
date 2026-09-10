"""Format owned C++ with clang-format 18.1.8; vendor code is excluded.

Install the optional contributor tool with: python -m pip install clang-format==18.1.8
Run: python tools/format_cpp.py --check (or --write to apply formatting).
The guard compares preprocessing tokens and exact literals, preserves directive
boundaries and macro definitions, and checks a second pass before writing files.
It deliberately rejects line-spliced code and __LINE__, which need manual review.
This is a formatting guard, not a replacement for native build validation.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CLANG_FORMAT_VERSION = "18.1.8"
OWNED_DIRS = ("native/include", "native/src", "native/tests")
CPP_SUFFIXES = {".h", ".hpp", ".c", ".cc", ".cpp", ".cxx"}

# Longest preprocessing-token spelling first. Literal contents are compared
# byte-for-byte, including spaces, escape sequences and raw-string delimiters.
RAW = re.compile(r'(?:u8|u|U|L)?R"([^\s()\\]{0,16})\(')
QUOTED = re.compile(r'''(?:u8|u|U|L)?(?:"(?:\\[\s\S]|[^"\\\n])*"|'(?:\\[\s\S]|[^'\\\n])*')''')
IDENTIFIER = re.compile(r"[A-Za-z_]\w*")
NUMBER = re.compile(r"(?:\d|\.\d)(?:[eEpP][+-]|[\w.'])*")
PUNCTUATION = re.compile(
    r"%:%:|>>=|<<=|<=>|->\*|\.\.\.|##|::|\.\*|->|\+\+|--|<<|>>|<=|>=|"
    r"==|!=|&&|\|\||\*=|/=|%=|\+=|-=|&=|\^=|\|=|<:|:>|<%|%>|%:|"
    r"[{}\[\]();:?~!+\-*/%^&|=<>.,#]"
)


def owned_files(root: Path = ROOT) -> list[Path]:
    files = sorted(path for directory in OWNED_DIRS
                   for path in (root / directory).rglob("*")
                   if path.is_file() and path.suffix in CPP_SUFFIXES)
    if not files:
        raise ValueError("No owned C++ files found")
    for path in files:
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Unsafe source path: {path}")
    return files


def token_fingerprint(source: str) -> list[tuple[str, str]]:
    """Keep token boundaries, literals, comments and preprocessing line boundaries."""
    tokens = []
    offset = 0
    line_start = True
    directive_start = None

    def end_directive(end: int) -> None:
        nonlocal directive_start
        if directive_start is not None:
            directive = source[directive_start:end].strip()
            if re.match(r"#\s*define\b", directive):
                # Even whitespace can affect function-like macros/stringification.
                tokens.append(("macro_definition", directive))
            include = re.match(r"#\s*include\s*([<\"].*?[>\"])", directive)
            if include:
                tokens.append(("include_header", include.group(1)))
            if re.match(r"#\s*line\b", directive):
                raise ValueError("#line needs manual formatting review")
            tokens.append(("directive_end", ""))
            directive_start = None

    while offset < len(source):
        char = source[offset]
        if char.isspace():
            if char == "\n":
                end_directive(offset)
                line_start = True
            offset += 1
            continue
        if source.startswith("//", offset):
            end = source.find("\n", offset)
            end = len(source) if end < 0 else end
            comment = source[offset:end]
            if comment.rstrip("\r").endswith("\\"):
                raise ValueError("Line-spliced comment needs manual formatting review")
            tokens.append(("line_comment", re.sub(r"\s+", "", comment)))
            offset = end
            continue
        if source.startswith("/*", offset):
            end = source.find("*/", offset + 2)
            if end < 0:
                raise ValueError("Unterminated block comment")
            end += 2
            comment = source[offset:end]
            tokens.append(("block_comment", re.sub(r"\s+", "", comment)))
            if "\n" in comment:
                end_directive(offset)
                line_start = True
            offset = end
            continue
        if line_start and char == "#":
            directive_start = offset
            tokens.append(("directive_start", ""))
        line_start = False
        raw = RAW.match(source, offset)
        if raw:
            delimiter = ")" + raw.group(1) + '"'
            closing = source.find(delimiter, raw.end())
            if closing < 0:
                raise ValueError("Unterminated raw string")
            end = closing + len(delimiter)
            kind = "literal"
        else:
            match = QUOTED.match(source, offset)
            if match:
                end = match.end()
                kind = "literal"
            else:
                match = NUMBER.match(source, offset) or IDENTIFIER.match(source, offset)
                match = match or PUNCTUATION.match(source, offset)
                if not match:
                    raise ValueError(f"Unsupported C++ token near {source[offset:offset + 30]!r}")
                end = match.end()
                kind = "token"
        if kind == "literal":
            suffix = IDENTIFIER.match(source, end)
            if suffix:
                end = suffix.end()
        value = source[offset:end]
        if value == "__LINE__":
            raise ValueError("__LINE__ needs manual formatting review")
        tokens.append((kind, value))
        offset = end
    end_directive(len(source))
    return tokens


def verify_equivalent(before: bytes, after: bytes) -> None:
    old = token_fingerprint(before.decode("utf-8"))
    new = token_fingerprint(after.decode("utf-8"))
    if old != new:
        index = next((i for i, pair in enumerate(zip(old, new))
                      if pair[0] != pair[1]), min(len(old), len(new)))
        raise ValueError(f"Formatting changed a protected token at index {index}: "
                         f"{old[index:index + 1]!r} -> {new[index:index + 1]!r}")


def format_source(executable: str, path: Path, source: bytes) -> bytes:
    result = subprocess.run(
        [executable, "--style=file", "--fallback-style=none", f"--assume-filename={path}"],
        input=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="report files needing formatting")
    mode.add_argument("--write", action="store_true", help="apply verified formatting")
    parser.add_argument("--clang-format", default="clang-format", help="path to clang-format 18.1.8")
    args = parser.parse_args()
    executable = shutil.which(args.clang_format)
    if not executable:
        raise ValueError("clang-format 18.1.8 is required; install clang-format==18.1.8")
    version = subprocess.run([executable, "--version"], capture_output=True, text=True,
                             check=True).stdout
    if not re.search(r"\bversion " + re.escape(CLANG_FORMAT_VERSION) + r"\b", version):
        raise ValueError(f"Expected clang-format {CLANG_FORMAT_VERSION}; got {version.strip()}")
    changes = []
    paths = owned_files()
    for path in paths:
        before = path.read_bytes()
        after = format_source(executable, path, before)
        try:
            verify_equivalent(before, after)
        except ValueError as error:
            raise ValueError(f"{path.relative_to(ROOT)}: {error}") from error
        if format_source(executable, path, after) != after:
            raise ValueError(f"Formatting is not idempotent: {path.relative_to(ROOT)}")
        if before != after:
            changes.append((path, before, after))
    # Check the entire batch before touching sources, and detect concurrent edits.
    if args.write:
        for path, before, _ in changes:
            if path.read_bytes() != before:
                raise ValueError(f"Source changed during formatting: {path.relative_to(ROOT)}")
        for path, _, after in changes:
            path.write_bytes(after)
    for path, _, _ in changes:
        print(path.relative_to(ROOT).as_posix())
    verb = "Formatted" if args.write else "Need formatting:"
    print(f"{verb} {len(changes)} of {len(paths)} owned C++ files; "
          "token preservation and idempotence verified.")
    return int(args.check and bool(changes))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(error, file=sys.stderr)
        raise SystemExit(2) from error
