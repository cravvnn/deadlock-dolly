#!/usr/bin/env bash
# Launch the Windows Deadlock Dolly build inside Deadlock's own Proton prefix.
#
#   ./run_dolly_proton.sh [--dry-run] [path/to/Dolly.exe]
#
# Dolly reads the game's process, memory and console through Windows APIs, so
# it and Deadlock must share one Wine server. A non-Steam shortcut, a separate
# prefix, or a different Proton version cannot see a game that Steam started.
#
# The script reads the Proton version Deadlock already uses from
# compatdata/1422450/version, finds the matching compatibility tool and Steam
# Linux Runtime, and starts Dolly through them. It installs nothing and changes
# no Steam setting. Override detection with:
#   PROTON="/path/to/steamapps/common/Proton 10.0"
#   STEAM_ROOT="/path/to/Steam"
#
set -euo pipefail

APP_ID=1422450
DRY_RUN=0
DOLLY=""

say() { printf '%s\n' "$*" >&2; }
die() { say "error: $*"; exit 1; }

usage() {
    cat >&2 <<'EOF'
usage: run_dolly_proton.sh [--dry-run] [path/to/Dolly.exe]

Runs the extracted Windows Dolly next to Deadlock's replay editor, inside the
same Proton prefix the game uses. Close Deadlock first: Dolly launches it.

  --dry-run   print the detected paths and exact command, start nothing
  --help      show this text

Environment overrides: STEAM_ROOT, PROTON, PROTON_RUNTIME.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        -h|--help) usage; exit 0 ;;
        -*) die "unknown option: $1 (try --help)" ;;
        *) DOLLY=$1 ;;
    esac
    shift
done

# --- Steam roots and libraries -------------------------------------------

roots=()
[ -n "${STEAM_ROOT:-}" ] && roots+=("$STEAM_ROOT")
roots+=("$HOME/.local/share/Steam" "$HOME/.steam/steam"
        "$HOME/.var/app/com.valvesoftware.Steam/.local/share/Steam")

root=""
for candidate in "${roots[@]}"; do
    if [ -f "$candidate/steamapps/libraryfolders.vdf" ]; then
        root=$candidate
        break
    fi
done
[ -n "$root" ] || die "no Steam library found; set STEAM_ROOT=/path/to/Steam"

libs=("$root")
while IFS= read -r path; do
    path=${path//\\\\//}
    [ -n "$path" ] && libs+=("$path")
done < <(sed -n 's/.*"path"[[:space:]]*"\([^"]*\)".*/\1/p' \
             "$root/steamapps/libraryfolders.vdf")

game_lib=""
for lib in "${libs[@]}"; do
    if [ -d "$lib/steamapps/common/Deadlock/game/citadel" ]; then
        game_lib=$lib
        break
    fi
done
[ -n "$game_lib" ] || die "Deadlock is not installed in any Steam library Steam lists"

compat=""
for lib in "$game_lib" "$root" "${libs[@]}"; do
    if [ -d "$lib/steamapps/compatdata/$APP_ID/pfx" ]; then
        compat="$lib/steamapps/compatdata/$APP_ID"
        break
    fi
done
[ -n "$compat" ] || die "Deadlock's Proton prefix is missing under $game_lib/steamapps/compatdata/$APP_ID
Run Deadlock through Proton once, then try again."

# --- Matching Proton tool and Steam Linux Runtime -------------------------

find_tool() {  # $1 = compatmanager_layer_name from compatdata/version
    local name=$1 lib dir
    for lib in "${libs[@]}"; do
        for dir in "$lib/steamapps/common"/Proton* "$lib/compatibilitytools.d"/*; do
            [ -f "$dir/toolmanifest.vdf" ] || continue
            if grep -q "\"compatmanager_layer_name\"[[:space:]]*\"$name\"" \
                    "$dir/toolmanifest.vdf" 2>/dev/null; then
                printf '%s\n' "$dir"
                return 0
            fi
        done
    done
    return 1
}

proton=""
if [ -n "${PROTON:-}" ]; then
    proton=$PROTON
else
    layer=""
    [ -f "$compat/version" ] && layer=$(tr -d '[:space:]' < "$compat/version")
    if [ -n "$layer" ]; then
        proton=$(find_tool "$layer" || true)
    fi
    if [ -z "$proton" ] && [ -d "$root/steamapps/common/Proton - Experimental" ]; then
        proton="$root/steamapps/common/Proton - Experimental"
        say "warning: adopted 'Proton - Experimental' because the version file did not match"
    fi
fi
if [ -z "$proton" ] || [ ! -f "$proton/proton" ]; then
    say "installed compatibility tools:"
    for lib in "${libs[@]}"; do
        for dir in "$lib/steamapps/common"/Proton* "$lib/compatibilitytools.d"/*; do
            [ -f "$dir/proton" ] && say "  $dir"
        done
    done
    die "could not identify Deadlock's Proton. Set PROTON=\"/path/to/Proton 10.0\"."
fi

runtime=""
if [ -n "${PROTON_RUNTIME:-}" ]; then
    runtime=$PROTON_RUNTIME
else
    runtime_name=""
    tool_app=$(sed -n 's/.*"require_tool_appid"[[:space:]]*"\([0-9][0-9]*\)".*/\1/p' \
                   "$proton/toolmanifest.vdf" | head -n 1)
    case "$tool_app" in
        4183110) runtime_name=SteamLinuxRuntime_4 ;;       # Proton 11+
        1628350) runtime_name=SteamLinuxRuntime_sniper ;;  # Proton 8-10
    esac
    if [ -n "$runtime_name" ]; then
        for lib in "${libs[@]}"; do
            if [ -x "$lib/steamapps/common/$runtime_name/_v2-entry-point" ]; then
                runtime="$lib/steamapps/common/$runtime_name"
                break
            fi
        done
    fi
    if [ -z "$runtime" ]; then
        for name in SteamLinuxRuntime_4 SteamLinuxRuntime_sniper; do
            for lib in "${libs[@]}"; do
                if [ -x "$lib/steamapps/common/$name/_v2-entry-point" ]; then
                    runtime="$lib/steamapps/common/$name"
                    say "warning: runtime chosen by availability; make sure it matches $proton"
                    break 2
                fi
            done
        done
    fi
fi
if [ -z "$runtime" ] || [ ! -x "$runtime/_v2-entry-point" ]; then
    die "no Steam Linux Runtime found; install the runtime that matches $proton"
fi

# --- Dolly and launch ----------------------------------------------------

[ -n "$DOLLY" ] || DOLLY=$PWD/Dolly.exe
[ -f "$DOLLY" ] || die "Dolly.exe not found: $DOLLY
Run this from the extracted Dolly folder, or pass the path to Dolly.exe."
dolly_dir=$(cd "$(dirname "$DOLLY")" && pwd)
DOLLY="$dolly_dir/$(basename "$DOLLY")"

if ! pgrep -x steam >/dev/null 2>&1; then
    say "warning: the Steam client does not look running; Deadlock may refuse to start"
fi

say "Steam:   $root"
say "Game:    $game_lib/steamapps/common/Deadlock"
say "Proton:  $proton"
say "Runtime: $runtime"
say "Prefix:  $compat/pfx"

export STEAM_COMPAT_DATA_PATH="$compat"
export STEAM_COMPAT_CLIENT_INSTALL_PATH="$root"
export SteamAppId=$APP_ID
export SteamGameId=$APP_ID

command=("$runtime/_v2-entry-point" --verb=waitforexitandrun --
         "$proton/proton" waitforexitandrun "$DOLLY")

if [ "$DRY_RUN" = 1 ]; then
    printf 'dry run:'
    printf ' %q' "${command[@]}"
    printf '\n'
    exit 0
fi

say "starting Dolly inside Deadlock's prefix; leave this terminal open"
exec "${command[@]}"
