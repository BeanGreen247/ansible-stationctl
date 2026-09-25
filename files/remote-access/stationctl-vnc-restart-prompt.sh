#!/bin/bash
# stationctl-vnc-restart-prompt.sh — pop a real GUI confirmation dialog into
# an end-user's live MATE session before setup-remote-access.yml is allowed
# to restart the VNC server out from under them.
#
# Why this exists (2026-09-21): the old safety gate only checked for an
# established TCP connection on the VNC port, then used ansible's `pause`
# module to ask for confirmation on the controlling terminal. That silently
# does nothing useful when the playbook is run non-interactively (no TTY
# attached) and misses "logged in but idle" sessions the TCP check can't
# see — both true here, and both combined to restart VNC mid-session
# without ever actually asking. This reaches the user directly, on their
# own screen, regardless of how the playbook was invoked.
#
# Exit 0  = user explicitly clicked "Restart now" — restart may proceed.
# Any other exit code (no session, dialog closed/declined, timeout, missing
# tooling) = restart must be skipped. Fail-safe by design.
#
# Usage: stationctl-vnc-restart-prompt.sh <user> <hostname> <display> <timeout-seconds>
set -euo pipefail

user="${1:?usage: $0 <user> <hostname> <display> <timeout-seconds>}"
host="${2:?}"
display="${3:?}"
timeout="${4:-45}"

pid=$(pgrep -u "$user" -x mate-session | head -1) || {
  echo "stationctl-vnc-restart-prompt: no mate-session running for $user" >&2
  exit 1
}

# Pull this session's real DISPLAY/DBUS/XAUTHORITY out of its own process
# environment rather than assuming a fixed path — the VNC session runs on
# its own private D-Bus bus (see the dbus-run-session fix in
# setup-remote-access.yml), not the systemd user bus at /run/user/<uid>/bus.
mapfile -t envvars < <(tr '\0' '\n' < "/proc/$pid/environ" | grep -E '^(DISPLAY|DBUS_SESSION_BUS_ADDRESS|XAUTHORITY)=')
eval "$(printf '%s\n' "${envvars[@]}")"

if [ -z "${DISPLAY:-}" ]; then
  echo "stationctl-vnc-restart-prompt: could not resolve DISPLAY for $user's session" >&2
  exit 1
fi

run_as_user() {
  sudo -u "$user" env DISPLAY="$DISPLAY" DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-}" XAUTHORITY="${XAUTHORITY:-}" "$@"
}

# Heads-up toast first — catches anyone glancing at the screen even before
# the dialog finishes mapping.
run_as_user notify-send -u critical "stationctl" \
  "VNC restart requested on ${host} ${display} — a confirmation dialog is opening." || true

# XDG_CONFIG_HOME scoped to just this one process picks up the gradient
# gtk.css deployed to /etc/stationctl/vnc-restart-prompt-theme — this yad
# build has no --css flag, GTK3's own config-dir CSS autoload is what
# actually renders the color.
run_as_user env XDG_CONFIG_HOME=/etc/stationctl/vnc-restart-prompt-theme \
  yad --title="stationctl-vnc-restart" \
      --text="<span size=\"x-large\" weight=\"bold\">⚠ VNC restart requested on ${host} ${display}</span>\nThis will disconnect the current VNC session immediately.\nRestart now?" \
      --button="Restart now:0" --button="Skip:1" \
      --width=480 --height=200 --center --on-top --sticky \
      --window-icon="dialog-warning" \
      --timeout="$timeout" --timeout-indicator=bottom &
dialog_pid=$!

# Give the dialog a moment to map, then center + raise + flash it. Best
# effort only: the dialog still works fine even if xdotool/wmctrl can't
# find the window in time.
sleep 0.6
wid=$(run_as_user xdotool search --sync --name "stationctl-vnc-restart" 2>/dev/null | head -1) || true
if [ -n "${wid:-}" ]; then
  run_as_user xdotool windowactivate "$wid" || true
  run_as_user wmctrl -i -r "$wid" -b add,demands_attention || true
fi
run_as_user xkbbell || true

wait "$dialog_pid"
