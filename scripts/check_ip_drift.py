#!/usr/bin/env python3
"""
Preflight IP-drift check for inventory/hosts.ini.

Cross-checks each inventory host's static ansible_host against a live
Tailscale lookup (by the host's tailscale_hostname / workstation_hostname
host_var) and, where a mac_address host_var is set, a local ARP/neighbor
table lookup. On any confirmed drift it HALTS LOUDLY and writes a lock
file (.ip-drift.lock) that run_playbook.py refuses to proceed past until
someone applies the fix or explicitly acknowledges it.

Usage:
  scripts/check_ip_drift.py                 # report only, exit non-zero on drift
  scripts/check_ip_drift.py --apply         # patch hosts.ini to the confirmed IP
  scripts/check_ip_drift.py --status        # print the current lock, if any
  scripts/check_ip_drift.py --ack "reason"  # clear the lock without applying
"""
import argparse
import json
import re
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INVENTORY = REPO / "inventory" / "hosts.ini"
HOST_VARS = REPO / "host_vars"
LOCK = REPO / ".ip-drift.lock"
LOG = REPO / "docs" / "ip-resolution-log.md"

HOST_LINE_RE = re.compile(
    r"^(?P<name>\S+)\s+.*\bansible_host=(?P<ip>\S+)\b.*$"
)
SECTION_RE = re.compile(r"^\[(?P<name>[^\]:]+)\]\s*$")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_inventory():
    """Return {host_alias: {'ip': str, 'line': str, 'section': str}} for
    real host entries (skips [group] and [group:children] sections)."""
    hosts = {}
    section = None
    for raw in INVENTORY.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = SECTION_RE.match(line)
        if m:
            section = m.group("name")
            continue
        m = HOST_LINE_RE.match(line)
        if m and section:
            hosts[m.group("name")] = {"ip": m.group("ip"), "line": raw, "section": section}
    return hosts


def host_var(alias, key, default=None):
    f = HOST_VARS / alias / "main.yml"
    if not f.exists():
        return default
    for line in f.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{key}:"):
            val = line.split(":", 1)[1].strip().strip('"').strip("'")
            return val
    return default


def tailscale_map():
    """{tailscale_hostname: ipv4}. Empty dict (with a note) if tailscale
    is unavailable — callers must treat that as inconclusive, not as drift."""
    try:
        out = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=10, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        return None, f"tailscale status unavailable ({e})"
    data = json.loads(out.stdout)
    m = {}
    nodes = [data.get("Self", {})] + list(data.get("Peer", {}).values())
    for node in nodes:
        ips = [ip for ip in node.get("TailscaleIPs", []) if ":" not in ip]
        if node.get("HostName") and ips:
            m[node["HostName"]] = ips[0]
    return m, None


def arp_lookup(mac):
    """Best-effort: current IP owning `mac` per the local neighbor table.
    Only useful for hosts on the same L2 segment as the control node."""
    try:
        out = subprocess.run(["ip", "neigh"], capture_output=True, text=True, timeout=5)
    except FileNotFoundError:
        return None
    mac = mac.lower()
    for line in out.stdout.splitlines():
        if mac in line.lower():
            return line.split()[0]
    return None


def tcp_reachable(ip, port, timeout=3):
    try:
        with socket.create_connection((ip, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def check():
    hosts = parse_inventory()
    ts_map, ts_err = tailscale_map()
    findings = []  # list of dicts describing each host's result
    drift = []

    for alias, info in hosts.items():
        static_ip = info["ip"]
        port = host_var(alias, "ansible_port", "22")
        # ansible_port isn't in host_vars for this repo, it's inline in
        # hosts.ini — pull it from the inventory line instead.
        pm = re.search(r"ansible_port=(\S+)", info["line"])
        if pm:
            port = pm.group(1)

        ts_hostname = host_var(alias, "tailscale_hostname") or host_var(alias, "workstation_hostname") or alias
        mac = host_var(alias, "mac_address")

        entry = {"alias": alias, "static_ip": static_ip, "tailscale_hostname": ts_hostname}

        if ts_map is None:
            entry["status"] = "inconclusive"
            entry["note"] = ts_err
            findings.append(entry)
            continue

        ts_ip = ts_map.get(ts_hostname)
        if ts_ip is None:
            entry["status"] = "inconclusive"
            entry["note"] = f"no Tailscale peer named '{ts_hostname}' found (offline, or hostname mismatch)"
            findings.append(entry)
            continue

        entry["tailscale_ip"] = ts_ip

        if mac:
            arp_ip = arp_lookup(mac)
            entry["arp_ip"] = arp_ip
            if arp_ip and arp_ip != ts_ip:
                entry["arp_note"] = (
                    f"ARP neighbor table shows {arp_ip} for MAC {mac}, "
                    f"which disagrees with Tailscale's {ts_ip} — informational only, "
                    "not used to block (VPN-routed traffic can legitimately differ from ARP)."
                )
        else:
            entry["arp_note"] = (
                "mac_address not set in host_vars — ARP cross-check skipped. "
                f"Add mac_address to host_vars/{alias}/main.yml to enable it."
            )

        if ts_ip == static_ip:
            entry["status"] = "ok"
        else:
            static_reachable = tcp_reachable(static_ip, port)
            ts_reachable = tcp_reachable(ts_ip, port)
            entry["static_reachable"] = static_reachable
            entry["ts_reachable"] = ts_reachable
            entry["status"] = "drift"
            drift.append(entry)

        findings.append(entry)

    return findings, drift


def write_lock(drift):
    lines = [f"IP drift detected at {now()}", ""]
    for d in drift:
        lines.append(f"- {d['alias']}: hosts.ini has {d['static_ip']}, Tailscale ('{d['tailscale_hostname']}') reports {d['tailscale_ip']}")
        lines.append(f"    old IP reachable on SSH port: {d['static_reachable']}")
        lines.append(f"    new IP reachable on SSH port: {d['ts_reachable']}")
        if d.get("arp_note"):
            lines.append(f"    {d['arp_note']}")
    LOCK.write_text("\n".join(lines) + "\n")


def print_halt(drift):
    print("=" * 70, file=sys.stderr)
    print("STOP: IP drift detected between inventory/hosts.ini and Tailscale.", file=sys.stderr)
    print("No playbook will run until this is resolved.", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    for d in drift:
        print(f"\nHost: {d['alias']}", file=sys.stderr)
        print(f"  inventory/hosts.ini : {d['static_ip']}", file=sys.stderr)
        print(f"  Tailscale ('{d['tailscale_hostname']}') : {d['tailscale_ip']}", file=sys.stderr)
        print(f"  old IP SSH-reachable : {d['static_reachable']}", file=sys.stderr)
        print(f"  new IP SSH-reachable : {d['ts_reachable']}", file=sys.stderr)
        if d.get("arp_note"):
            print(f"  note: {d['arp_note']}", file=sys.stderr)
    print("\nWhat to do:", file=sys.stderr)
    print("  1. Confirm the new IP is really this host (not a different machine",
          file=sys.stderr)
    print("     that happens to answer on port 22).", file=sys.stderr)
    print("  2. If correct: scripts/check_ip_drift.py --apply", file=sys.stderr)
    print("     (patches inventory/hosts.ini and logs the change)", file=sys.stderr)
    print("  3. If you need to proceed without applying (e.g. investigating",
          file=sys.stderr)
    print('     manually first): scripts/check_ip_drift.py --ack "reason"', file=sys.stderr)
    print("=" * 70, file=sys.stderr)


def apply(drift):
    text = INVENTORY.read_text()
    changed = []
    for d in drift:
        if not d["ts_reachable"]:
            print(f"Refusing to apply {d['alias']}: new IP {d['tailscale_ip']} is not "
                  f"SSH-reachable. Investigate manually.", file=sys.stderr)
            continue
        old_line = None
        for line in text.splitlines():
            if line.strip().startswith(d["alias"] + " ") and f"ansible_host={d['static_ip']}" in line:
                old_line = line
                break
        if old_line is None:
            print(f"Could not locate the exact hosts.ini line for {d['alias']}, skipping.", file=sys.stderr)
            continue
        new_line = old_line.replace(f"ansible_host={d['static_ip']}", f"ansible_host={d['tailscale_ip']}")
        text = text.replace(old_line, new_line)
        changed.append((d["alias"], d["static_ip"], d["tailscale_ip"]))

    if not changed:
        print("Nothing applied.", file=sys.stderr)
        return 1

    INVENTORY.write_text(text)
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a") as f:
        for alias, old, new in changed:
            f.write(f"- {now()} {alias}: {old} -> {new} (confirmed SSH-reachable via Tailscale lookup)\n")
    for alias, old, new in changed:
        print(f"Applied: {alias} {old} -> {new}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--ack", metavar="REASON")
    args = ap.parse_args()

    if args.status:
        if LOCK.exists():
            print(LOCK.read_text())
        else:
            print("No IP-drift lock present.")
        return 0

    if args.ack:
        if LOCK.exists():
            LOG.parent.mkdir(exist_ok=True)
            with LOG.open("a") as f:
                f.write(f"- {now()} ACKNOWLEDGED without applying: {args.ack}\n")
                f.write(LOCK.read_text())
            LOCK.unlink()
            print("IP-drift lock cleared (acknowledged, not applied).")
        else:
            print("No lock to clear.")
        return 0

    findings, drift = check()

    if not drift:
        if LOCK.exists():
            LOCK.unlink()
        for f in findings:
            if f["status"] == "inconclusive":
                print(f"NOTE {f['alias']}: {f['note']}", file=sys.stderr)
        print("IP check: clean, no drift.")
        return 0

    if args.apply:
        write_lock(drift)  # keep an audit trail even when applying immediately
        rc = apply(drift)
        if rc == 0 and LOCK.exists():
            LOCK.unlink()
        return rc

    write_lock(drift)
    print_halt(drift)
    return 1


if __name__ == "__main__":
    sys.exit(main())
