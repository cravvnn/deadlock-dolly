"""Build the replay track reader (requires Go 1.25+): hero positions read from a .dem with
s2replay, pinned in native/replay_tracks/go.mod."""
from pathlib import Path
import os
import subprocess


def main():
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, CGO_ENABLED="0", GOFLAGS="-mod=readonly")
    subprocess.run(["go", "build", "-trimpath", "-ldflags", "-s -w", "-o",
                    str(root / "native/bin/win64/DollyReplayTracks.exe"), "."],
                   cwd=root / "native/replay_tracks", env=env, check=True)


if __name__ == "__main__":
    main()
