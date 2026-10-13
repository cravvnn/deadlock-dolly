"""Generate metadata-only native contracts from the privately reviewed shader proof.

No game bytecode is emitted. Re-run offline shader verification before using a
new proof; this generator checks structure, not the shader's physical meaning.
"""
import argparse
import json
import re
from pathlib import Path


def generate(proof):
    source = proof.get("source_shader_sha256", "")
    if not re.fullmatch(r"[0-9a-f]{64}", source):
        raise ValueError("Missing source shader SHA256")
    variants = proof["variants"]
    if len(variants) != 32:
        raise ValueError("Expected both static states and all 16 dynamic states")
    seen = set()
    rows = []
    for v in variants:
        pair = v["static_id"], v["dynamic_id"]
        if pair in seen or pair[0] not in range(2) or pair[1] not in range(16):
            raise ValueError("Invalid or duplicate variant")
        seen.add(pair)
        digest = v["bytecode_sha256"]
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid bytecode hash")
        if not 36 <= v["bytecode_size"] <= 65536 or v["hdr_srv_slot"] != 0:
            raise ValueError("Unexpected shader size or source binding")
        view, post = v["per_view_cb_slot"], v["postprocess_cb_slot"]
        if (view, post) not in ((0, 1), (1, 2)):
            raise ValueError("Unreviewed constant buffer slots")
        octets = ",".join("0x" + digest[i:i+2] for i in range(0, 64, 2))
        rows.append("    {{%s}, %d, %d, %d, 0, %d, %d}," %
                    (octets, v["bytecode_size"], *pair, view, post))
    return ("// Generated metadata only; no game shader bytecode.\n"
            "// Source VCS SHA256: " + source + "\n#pragma once\n"
            '#include "dolly_environment_source.hpp"\n'
            "namespace dolly::environment {\n"
            "inline constexpr ShaderContract ReviewedPostProcess[] = {\n" +
            "\n".join(rows) + "\n};\n}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("proof", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(generate(json.loads(args.proof.read_text())), encoding="utf-8")
