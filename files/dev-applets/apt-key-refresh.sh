#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# apt-key-refresh — validate and refresh third-party apt repository signing keys
# by BeanGreen247
#
# Runs as root (via systemd apt-key-refresh.timer, weekly + OnBootSec=10min).
# For each registered repo: fetches the canonical key, normalises it to binary,
# compares SHA-256 with the installed keyring file.  If different (key rotation
# or corrupted file), replaces it.  Notifies the active desktop session via
# notify-send and runs apt-get update if any key changed.
#
# Usage:
#   apt-key-refresh              run once (check + update)
#   apt-key-refresh --check      dry-run: report status, make no changes
#   apt-key-refresh --list       print the registered key table and exit
#
# Add / remove repos by editing the KEY_TABLE array below.
################################################################################

set -euo pipefail

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✓${NC}  $*"; }
info() { echo -e "${CYAN}  →${NC}  $*"; }
warn() { echo -e "${YELLOW}  !${NC}  $*" >&2; }
err()  { echo -e "${RED}  ✗${NC}  $*" >&2; }

# ── Options ───────────────────────────────────────────────────────────────────
DRY_RUN=false
case "${1:-}" in
    --check) DRY_RUN=true ;;
    --list)  ;;
    "")      ;;
    *) echo "Usage: apt-key-refresh [--check|--list]" >&2; exit 1 ;;
esac

# ── Key table ─────────────────────────────────────────────────────────────────
# Format: "name|fetch_url|dest_keyring_path"
# fetch_url:  canonical public URL for the current signing key (ASCII or binary)
# dest:       absolute path to the binary .gpg / .key file used in sources.list
#             (must already exist; apt-key-refresh won't create new repos)
#
# Kept as the full kubuntu-setup key table, not just the repos stationctl adds
# today — any entry whose dest keyring isn't present is silently skipped (see
# the "repo not installed" check below), so this stays correct if a future
# playbook change adds one of these repos (docker, VS Code, k8s, etc). Only
# brave-browser and nodesource paths are corrected to match where
# setup-mate-desktop.yml / setup-dev-tools.yml actually write them.
declare -a KEY_TABLE=(
    "google-chrome|https://dl.google.com/linux/linux_signing_key.pub|/usr/share/keyrings/google-chrome.gpg"
    "signal-desktop|https://updates.signal.org/desktop/apt/keys.asc|/usr/share/keyrings/signal-desktop-keyring.gpg"
    "nodesource|https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key|/etc/apt/keyrings/nodesource.gpg"
    "docker|https://download.docker.com/linux/ubuntu/gpg|/etc/apt/keyrings/docker.gpg"
    "winehq|https://dl.winehq.org/wine-builds/winehq.key|/etc/apt/keyrings/winehq-archive.key"
    "kubernetes|https://pkgs.k8s.io/core:/stable:/v1.32/deb/Release.key|/etc/apt/keyrings/kubernetes-apt-keyring.gpg"  # bump version path when upgrading clusters
    "brave-browser|https://brave-browser-apt-release.s3.brave.com/brave-browser-archive-keyring.gpg|/etc/apt/keyrings/brave-browser-archive-keyring.gpg"
    "spotify|https://download.spotify.com/debian/pubkey_5384CE82BA52C83A.asc|/etc/apt/trusted.gpg.d/spotify.gpg"
)

# ── Handle --list (no root needed) ────────────────────────────────────────────
if [[ "${1:-}" == "--list" ]]; then
    echo "Registered apt signing keys:"
    printf "  %-20s  %-55s  %s\n" "NAME" "URL" "KEYRING FILE"
    for entry in "${KEY_TABLE[@]}"; do
        IFS='|' read -r name url dest <<< "$entry"
        printf "  %-20s  %-55s  %s\n" "$name" "$url" "$dest"
    done
    exit 0
fi

# ── Root check ────────────────────────────────────────────────────────────────
if [[ "$(id -u)" -ne 0 ]]; then
    err "Must run as root"; exit 1
fi

# ── Helpers ───────────────────────────────────────────────────────────────────

# Notify the active desktop session; runs as the logged-in user via its D-Bus socket.
# Safe to call even when no desktop is running (notify-send will silently fail).
_notify() {
    local urgency="${1:-normal}" summary="$2" body="${3:-}"
    local uid dbus_addr xdg_runtime

    # Find the first non-root user with a running session bus socket
    for run_dir in /run/user/[0-9]*/; do
        uid=$(basename "$run_dir")
        [[ "$uid" == "0" ]] && continue
        [[ -S "${run_dir}bus" ]] || continue
        dbus_addr="unix:path=${run_dir}bus"
        xdg_runtime="${run_dir%/}"
        break
    done

    [[ -z "${dbus_addr:-}" ]] && return 0  # no desktop session active

    sudo -u "#${uid}" \
        env DBUS_SESSION_BUS_ADDRESS="$dbus_addr" \
            XDG_RUNTIME_DIR="$xdg_runtime" \
        notify-send \
            --urgency="$urgency" \
            --icon="system-software-update" \
            --app-name="apt-key-refresh" \
            "$summary" "$body" 2>/dev/null || true
}

# Normalise a GPG key file (ASCII-armored or binary) to a binary .gpg file.
# Writes binary output to $2.  Returns 1 on failure.
_to_binary() {
    local src="$1" dst="$2"
    # Detect ASCII-armor by header rather than relying on gpg exit code —
    # gpg can also fail for unrelated reasons (not installed, corrupt data, etc.).
    if head -c 27 "$src" | grep -q -- '-----BEGIN PGP'; then
        gpg --dearmor < "$src" > "$dst" 2>/dev/null
    else
        cp "$src" "$dst"
    fi
}

# Return the SHA-256 hex digest of a file ($1).
_sha256() { sha256sum "$1" | cut -d' ' -f1; }

# ── Main refresh loop ─────────────────────────────────────────────────────────
TMPDIR_KEYS=$(mktemp -d)
trap 'rm -rf "$TMPDIR_KEYS"' EXIT

UPDATED=0
FAILED=0
SKIPPED=0
UNCHANGED=0
REMOVED=0
CHANGED_NAMES=()
FAILED_NAMES=()
REMOVED_NAMES=()

for entry in "${KEY_TABLE[@]}"; do
    IFS='|' read -r name url dest <<< "$entry"

    # Spotify: auto-discover the current pubkey URL from their repo index
    # (the key ID is embedded in the filename; rotate silently when they publish a new one)
    if [[ "$name" == "spotify" ]]; then
        _spot=$(curl -sLm 15 https://download.spotify.com/debian/ \
            | grep -oP 'href="\Kpubkey_[A-Fa-f0-9]+\.asc(?=")' | tail -1) || true
        [[ -n "$_spot" ]] && url="https://download.spotify.com/debian/${_spot}"
    fi

    # (repo not installed on this machine — don't create orphan key files)
    if [[ ! -f "$dest" ]]; then
        info "${name}: keyring not present (${dest}) — skipping"
        (( SKIPPED++ )) || true
        continue
    fi

    info "Checking ${name}…"

    # Fetch the remote key
    tmp_raw="${TMPDIR_KEYS}/${name}.raw"
    if ! curl -fsSLm 20 -o "$tmp_raw" "$url" 2>/dev/null; then
        warn "${name}: fetch failed (${url}) — skipping"
        FAILED_NAMES+=("$name")
        (( FAILED++ )) || true
        continue
    fi

    # Normalise to binary
    tmp_bin="${TMPDIR_KEYS}/${name}.gpg"
    if ! _to_binary "$tmp_raw" "$tmp_bin"; then
        warn "${name}: key normalisation failed — skipping"
        FAILED_NAMES+=("$name")
        (( FAILED++ )) || true
        continue
    fi

    # Compare SHA-256 of normalised remote vs installed
    remote_sha=$(_sha256 "$tmp_bin")
    local_sha=$(_sha256  "$dest")

    if [[ "$remote_sha" == "$local_sha" ]]; then
        ok "${name}: up to date"
        (( UNCHANGED++ )) || true
        continue
    fi

    # Keys differ — a rotation or corrupted local file
    if [[ "$DRY_RUN" == "true" ]]; then
        warn "${name}: KEY MISMATCH (local ≠ remote) — would update  [dry-run]"
        CHANGED_NAMES+=("$name")
        continue
    fi

    # Backup old key and install new one; keep only the 2 most recent backups
    cp "$dest" "${dest}.bak.$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
    ls -1t "${dest}.bak."* 2>/dev/null | tail -n +3 | xargs rm -f --
    install -m 644 -o root -g root "$tmp_bin" "$dest"
    ok "${name}: KEY UPDATED  (${dest})"
    CHANGED_NAMES+=("$name")
    (( UPDATED++ )) || true
done

# ── Orphan-key cleanup ────────────────────────────────────────────────────────
# Remove keyring files that exist on disk but are no longer referenced by any
# apt source (repo was removed but its key file was left behind).
for entry in "${KEY_TABLE[@]}"; do
    IFS='|' read -r name url dest <<< "$entry"
    [[ -f "$dest" ]] || continue  # absent — nothing to remove

    if ! grep -rqF "$dest" /etc/apt/sources.list /etc/apt/sources.list.d/ 2>/dev/null; then
        if [[ "$DRY_RUN" == "true" ]]; then
            warn "${name}: keyring unreferenced by apt sources — would remove (${dest})  [dry-run]"
            REMOVED_NAMES+=("$name")
        else
            warn "${name}: keyring unreferenced by apt sources — removing ${dest}"
            rm -f "$dest"
            REMOVED_NAMES+=("$name")
            (( REMOVED++ )) || true
        fi
    fi
done

# ── Run apt-get update if keys changed or removed ─────────────────────────────
if (( ${#CHANGED_NAMES[@]} > 0 || REMOVED > 0 )) && [[ "$DRY_RUN" == "false" ]]; then
    info "Running apt-get update to apply refreshed keys…"
    apt-get update -qq 2>&1 | grep -E '^(E:|W:.*NO_PUBKEY)' || true
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}apt-key-refresh summary${NC}"
echo "  Updated  : ${UPDATED}"
echo "  Removed  : ${REMOVED}"
echo "  Failed   : ${FAILED}"
echo "  Skipped  : ${SKIPPED}"
echo "  Unchanged: ${UNCHANGED}"
if [[ "$DRY_RUN" == "true" ]]; then
    echo "  Would update: ${CHANGED_NAMES[*]:-none}"
    (( ${#REMOVED_NAMES[@]} > 0 )) && echo "  Would remove: ${REMOVED_NAMES[*]}" || true
fi

# ── Desktop notification ──────────────────────────────────────────────────────
if (( UPDATED > 0 )) || (( REMOVED > 0 )) || (( FAILED > 0 )); then
    _title="apt-key-refresh"
    _body=""
    if (( UPDATED > 0 )); then
        changed_list=$(printf '%s\n' "${CHANGED_NAMES[@]}" | paste -sd', ')
        _title="apt signing keys refreshed (${UPDATED} rotated)"
        _body="Rotated: ${changed_list}"
    fi
    if (( REMOVED > 0 )); then
        removed_list=$(printf '%s\n' "${REMOVED_NAMES[@]}" | paste -sd', ')
        [[ -n "$_body" ]] && _body+=$'\n'
        _body+="Removed orphaned: ${removed_list}"
        [[ "$UPDATED" -eq 0 ]] && _title="apt-key-refresh: ${REMOVED} orphaned key(s) removed"
    fi
    if (( FAILED > 0 )); then
        failed_list=$(printf '%s\n' "${FAILED_NAMES[@]:-}" | paste -sd', ')
        [[ -n "$_body" ]] && _body+=$'\n'
        _body+="Failed: ${failed_list:-${FAILED} key(s)} — check journalctl -u apt-key-refresh"
        [[ "$UPDATED" -eq 0 && "$REMOVED" -eq 0 ]] && _title="apt-key-refresh: ${FAILED} key(s) failed"
    fi
    # Successful rotations/removals are routine (low); any failure needs attention (normal).
    urgency=$( (( FAILED == 0 )) && echo low || echo normal )
    _notify "$urgency" "$_title" "$_body"
else
    # Quiet on full success — no notification spam on weekly runs
    :
fi
