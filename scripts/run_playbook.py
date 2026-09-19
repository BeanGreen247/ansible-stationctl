#!/usr/bin/env python3
"""
Wrapper around ansible-playbook: runs an IP-drift preflight, the real
apply, and an idempotency postflight — halting loudly with instructions
at any stage that fails, rather than silently warning or silently fixing.

Usage:
  scripts/run_playbook.py <playbook.yml> [ansible-playbook args...]
  scripts/run_playbook.py --ack-idempotency "reason"   # clear a stuck idempotency lock
  scripts/run_playbook.py --status                     # show any active locks
"""
import argparse
import fcntl
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Some shells/sandboxes leave stdio in non-blocking mode, which
# ansible-playbook refuses to run under. Force blocking mode.
for _fd in (0, 1, 2):
    try:
        _flags = fcntl.fcntl(_fd, fcntl.F_GETFL)
        fcntl.fcntl(_fd, fcntl.F_SETFL, _flags & ~os.O_NONBLOCK)
    except OSError:
        pass

REPO = Path(__file__).resolve().parent.parent
IDEMPOTENCY_LOCK = REPO / ".idempotency-drift.lock"
IP_LOCK = REPO / ".ip-drift.lock"
LOG = REPO / "docs" / "run-log.md"

RECAP_RE = re.compile(
    r"^(?P<host>\S+)\s*:\s*ok=(?P<ok>\d+)\s+changed=(?P<changed>\d+)\s+"
    r"unreachable=(?P<unreachable>\d+)\s+failed=(?P<failed>\d+)"
)


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(line):
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a") as f:
        f.write(f"- {now()} {line}\n")


def halt(msg):
    print("=" * 70, file=sys.stderr)
    print(msg, file=sys.stderr)
    print("=" * 70, file=sys.stderr)


def check_locks():
    if IP_LOCK.exists():
        halt("STOP: an unresolved IP-drift lock exists (.ip-drift.lock).\n"
             "Run scripts/check_ip_drift.py --status for details, then either\n"
             "scripts/check_ip_drift.py --apply or --ack \"reason\".")
        return True
    if IDEMPOTENCY_LOCK.exists():
        halt("STOP: an unresolved idempotency-drift lock exists (.idempotency-drift.lock).\n"
             f"{IDEMPOTENCY_LOCK.read_text()}\n"
             "Review the playbook, fix the non-idempotent task, and re-run — or run\n"
             'scripts/run_playbook.py --ack-idempotency "reason" to proceed anyway.')
        return True
    return False


def run_ip_preflight():
    rc = subprocess.run([sys.executable, str(REPO / "scripts" / "check_ip_drift.py")]).returncode
    return rc == 0


def run_apply(playbook, extra_args):
    cmd = ["ansible-playbook", playbook] + extra_args
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd).returncode


def run_idempotency_check(playbook, extra_args):
    cmd = ["ansible-playbook", playbook, "--check", "--diff"] + extra_args
    print(f"\nVerifying idempotency: $ {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)

    per_host = {}
    for line in proc.stdout.splitlines():
        m = RECAP_RE.match(line.strip())
        if m:
            per_host[m.group("host")] = {
                "ok": int(m.group("ok")),
                "changed": int(m.group("changed")),
                "unreachable": int(m.group("unreachable")),
                "failed": int(m.group("failed")),
            }
    return per_host, proc.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("playbook", nargs="?")
    ap.add_argument("--ack-idempotency", metavar="REASON")
    ap.add_argument("--status", action="store_true")
    args, extra = ap.parse_known_args()

    if args.status:
        for lock in (IP_LOCK, IDEMPOTENCY_LOCK):
            print(f"-- {lock.name} --")
            print(lock.read_text() if lock.exists() else "(none)")
        return 0

    if args.ack_idempotency:
        if IDEMPOTENCY_LOCK.exists():
            log(f"idempotency lock ACKNOWLEDGED without fix: {args.ack_idempotency}\n{IDEMPOTENCY_LOCK.read_text()}")
            IDEMPOTENCY_LOCK.unlink()
            print("Idempotency lock cleared (acknowledged, not fixed).")
        else:
            print("No idempotency lock to clear.")
        return 0

    if not args.playbook:
        ap.error("playbook required")

    if check_locks():
        return 1

    print("Running IP-drift preflight...")
    if not run_ip_preflight():
        return 1  # check_ip_drift.py already printed the halt message

    rc = run_apply(args.playbook, extra)
    if rc != 0:
        print(f"\nPlaybook run failed (exit {rc}) — skipping idempotency check.", file=sys.stderr)
        log(f"{args.playbook} apply FAILED (exit {rc})")
        return rc

    per_host, check_rc = run_idempotency_check(args.playbook, extra)
    non_idempotent = {h: s for h, s in per_host.items() if s["changed"] > 0 or s["failed"] > 0}

    if non_idempotent:
        details = "\n".join(f"  {h}: {s}" for h, s in non_idempotent.items())
        IDEMPOTENCY_LOCK.write_text(
            f"Idempotency check failed at {now()} for {args.playbook}\n{details}\n"
        )
        halt(
            f"STOP: {args.playbook} applied successfully, but is not idempotent —\n"
            f"a follow-up --check run shows further changes:\n{details}\n\n"
            "This usually means a task's change-detection condition doesn't\n"
            "correctly recognize the already-applied state.\n\n"
            "What to do:\n"
            "  1. Review the --check --diff output above to see exactly what it\n"
            "     would change again.\n"
            "  2. Fix the underlying task, then re-run this playbook to confirm\n"
            "     a clean (changed=0) check.\n"
            f'  3. Or, to proceed anyway: scripts/run_playbook.py --ack-idempotency "reason"\n'
            "     (no further playbook will run through this wrapper until you do)."
        )
        log(f"{args.playbook} apply OK, idempotency FAILED:\n{details}")
        return 1

    if IDEMPOTENCY_LOCK.exists():
        IDEMPOTENCY_LOCK.unlink()
    print(f"\n{args.playbook}: applied cleanly and verified idempotent (changed=0 on re-check).")
    log(f"{args.playbook} apply OK, idempotency OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
