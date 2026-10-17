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
#   DOLLY_COMPAT_TOOL_DIRS="/extra/tool/dir:/another"
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

Environment overrides: STEAM_ROOT, PROTON, PROTON_RUNTIME,
DOLLY_COMPAT_TOOL_DIRS (colon-separated extra compatibility tool folders).
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

# --- Compatibility tools --------------------------------------------------

# Steam reads compatibility tools from each library's steammapps/common
# (official Proton) and compatibilitytools.d, from the Steam root's own
# compatibilitytools.d, and from the read-only system folders that distribution
# packages use. Missing globs stay literal and are filtered by the proton
# executable check.
tool_dirs=()
add_tool_dirs() {
    local dir
    for dir in "$@"; do
        if [ -f "$dir/proton" ]; then
            tool_dirs+=("$dir")
        fi
    done
    return 0
}
for lib in "${libs[@]}"; do
    add_tool_dirs "$lib/steamapps/common"/Proton* "$lib/compatibilitytools.d"/*
done
add_tool_dirs "$root/compatibilitytools.d"/* \
    /usr/share/steam/compatibilitytools.d/* \
    /usr/local/share/steam/compatibilitytools.d/* \
    /usr/lib/steam/compatibilitytools.d/*

if [ -n "${DOLLY_COMPAT_TOOL_DIRS:-}" ]; then
    IFS=: read -r -a extra_tool_dirs <<< "$DOLLY_COMPAT_TOOL_DIRS"
    for dir in "${extra_tool_dirs[@]}"; do
        if [ -n "$dir" ]; then
            add_tool_dirs "$dir" "$dir"/*
        fi
    done
fi

# Steam records the tool as compatmanager_layer_name in its manifest, but custom
# builds differ: compare the folder name and the tool's own version file too.
# Everything is folded to lowercase without spaces, with - and _ treated alike,
# so "Proton - Experimental" matches the recorded "proton_experimental".
normalize_tool_name() {
    printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]' | tr '-' '_'
}

tool_version_file() {  # $1 = tool directory; prints its recorded version text
    local dir=$1 file
    for file in "$dir/version" "$dir/version.txt"; do
        if [ -f "$file" ]; then
            tr -d '[:space:]' < "$file"
            return 0
        fi
    done
    return 0
}

tool_matches() {  # $1 = tool directory, $2 = normalized recorded name, $3 = raw value
    local dir=$1 wanted=$2 raw=$3 name
    if [ -f "$dir/toolmanifest.vdf" ]; then
        name=$(sed -n 's/.*"compatmanager_layer_name"[[:space:]]*"\([^"]*\)".*/\1/p' \
                   "$dir/toolmanifest.vdf" 2>/dev/null | head -n 1)
        [ -n "$name" ] && [ "$(normalize_tool_name "$name")" = "$wanted" ] && return 0
        # Custom builds record a version string rather than a layer name; the
        # same value appears somewhere in the tool's own manifest.
        [ -n "$raw" ] && grep -qF "\"$raw\"" "$dir/toolmanifest.vdf" 2>/dev/null && return 0
    fi
    [ "$(normalize_tool_name "$(basename "$dir")")" = "$wanted" ] && return 0
    name=$(tool_version_file "$dir")
    if [ -n "$name" ]; then
        [ "$(normalize_tool_name "$name")" = "$wanted" ] && return 0
        [ -n "$raw" ] && [ "$name" = "$raw" ] && return 0
    fi
    return 1
}

# Steam names the tool it launches the game with in config.vdf. The prefix
# records a version string for some custom builds, so the mapping is the more
# reliable name when the prefix value matches nothing.
steam_tool_mapping() {  # $1 = config.vdf, $2 = app id
    [ -f "$1" ] || return 0
    awk -v app="\"$2\"" '
        $1 == app { inside = 1; next }
        inside && $1 == "\"name\"" { n = split($0, parts, "\""); if (n >= 4) print parts[4]; exit }
        inside && $1 == "}" { inside = 0 }
    ' "$1"
}

proton=""
if [ -n "${PROTON:-}" ]; then
    proton=$PROTON
else
    wanted_raw=""
    [ -f "$compat/version" ] && wanted_raw=$(tr -d '[:space:]' < "$compat/version")
    mapped_raw=$(steam_tool_mapping "$root/config/config.vdf" "$APP_ID")
    for wanted_raw_try in "$wanted_raw" "$mapped_raw"; do
        [ -n "$wanted_raw_try" ] || continue
        wanted=$(normalize_tool_name "$wanted_raw_try")
        for dir in "${tool_dirs[@]}"; do
            if tool_matches "$dir" "$wanted" "$wanted_raw_try"; then
                proton=$dir
                break 2
            fi
        done
    done
fi
if [ -z "$proton" ] || [ ! -f "$proton/proton" ]; then
    say "compatibility tools found:"
    for dir in "${tool_dirs[@]}"; do
        name=""
        [ -f "$dir/toolmanifest.vdf" ] && name=$(sed -n \
            's/.*"compatmanager_layer_name"[[:space:]]*"\([^"]*\)".*/\1/p' \
            "$dir/toolmanifest.vdf" 2>/dev/null | head -n 1)
        tversion=$(tool_version_file "$dir")
        say "  $dir${name:+  (layer: $name)}${tversion:+  (version: $tversion)}"
    done
    if [ -f "$compat/version" ]; then
        say "Deadlock's prefix records: $(tr -d '[:space:]' < "$compat/version")"
    else
        say "Deadlock's prefix has no version file; run Deadlock through Proton once"
    fi
    if [ -n "$mapped_raw" ]; then
        say "Steam's compatibility mapping for Deadlock names: $mapped_raw"
    else
        say "Steam's config.vdf has no compatibility mapping for Deadlock"
    fi
    # Never start Dolly with a different Proton than the game: that rewrites the
    # shared prefix. Stop and let the user name the tool instead.
    die "could not identify Deadlock's Proton. Set PROTON=\"/path/to/that/tool\"."
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
