#!/usr/bin/env python3
"""dotfile-sync — pull dotfiles from GitHub on a schedule.

by BeanGreen247  |  https://github.com/BeanGreen247

Config:  ~/.config/dotfile-sync/config.ini
Backups: ~/.config/dotfile-sync/backups/
Log:     ~/.local/share/dotfile-sync/last-sync.txt

Usage:
  dotfile-sync                    sync once and exit (default)
  dotfile-sync --daemon           sync in a loop (interval from config)
  dotfile-sync --install-timer    write systemd user timer + service
  dotfile-sync --status           show last-sync info
  dotfile-sync --config-path      print the config file location
"""

import argparse
import configparser
import hashlib
import os
import shutil
import signal
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
CONFIG_DIR  = Path.home() / ".config"  / "dotfile-sync"
CONFIG_FILE = CONFIG_DIR  / "config.ini"
BACKUP_DIR  = CONFIG_DIR  / "backups"
STATUS_FILE = Path.home() / ".local" / "share" / "dotfile-sync" / "last-sync.txt"

# ── Colour output ─────────────────────────────────────────────────────────────
_TTY = sys.stdout.isatty()

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _TTY else text

def ok(msg: str)   -> None: print(_c("32", f"  ✓  {msg}"))
def info(msg: str) -> None: print(_c("34", f"  →  {msg}"))
def warn(msg: str) -> None: print(_c("33", f"  !  {msg}"), file=sys.stderr)
def err(msg: str)  -> None: print(_c("31", f"  ✗  {msg}"), file=sys.stderr)

# ── Default config (written on first run) ────────────────────────────────────
DEFAULT_CONFIG = """\
[general]
# How often to re-sync in --daemon mode (decimal hours allowed, e.g. 0.5)
interval_hours = 12
# Number of timestamped backups kept per file (oldest pruned automatically)
backup_count = 5

# ── Files to sync ─────────────────────────────────────────────────────────────
# Add one [file.<name>] section per file.
# url   = raw download URL — GitHub, GitLab, Gitea, Codeberg, or any HTTPS URL
# dest  = destination path (~ expands to your home directory)
# token = (optional) personal access token for private repos

[file.bashrc]
url  = https://raw.githubusercontent.com/BeanGreen247/dotfiles/master/bashrc/bashrc
dest = ~/.bashrc

[file.vimrc]
url  = https://raw.githubusercontent.com/BeanGreen247/dotfiles/master/vim/vimrc
dest = ~/.vimrc

[file.claude-md]
url  = https://raw.githubusercontent.com/BeanGreen247/dotfiles/master/claude/CLAUDE.md
dest = ~/.claude/CLAUDE.md
"""

# ── Systemd unit templates ────────────────────────────────────────────────────
_SERVICE_UNIT = """\
[Unit]
Description=Dotfile sync — pull dotfiles from GitHub
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart={exec_path}
StandardOutput=journal
StandardError=journal
"""

_TIMER_UNIT = """\
[Unit]
Description=Dotfile sync timer

[Timer]
OnBootSec=2min
OnUnitActiveSec={interval_hours}h
Persistent=true

[Install]
WantedBy=timers.target
"""

# ── Config loading ────────────────────────────────────────────────────────────
def load_config() -> configparser.ConfigParser:
    if not CONFIG_FILE.exists():
        info(f"no config found — writing default to {CONFIG_FILE}")
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(DEFAULT_CONFIG)
        info("edit the config then re-run dotfile-sync")

    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_FILE)
    return cfg


def file_entries(cfg: configparser.ConfigParser) -> list[dict]:
    entries = []
    for section in cfg.sections():
        if section.startswith("file."):
            name  = section[len("file."):]
            url   = cfg.get(section, "url",   fallback="").strip()
            dest  = cfg.get(section, "dest",  fallback="").strip()
            token = cfg.get(section, "token", fallback="").strip()
            chmod = cfg.get(section, "chmod", fallback="").strip()
            if url and dest:
                entries.append({"name": name, "url": url, "dest": dest, "token": token, "chmod": chmod})
            else:
                warn(f"[{section}] missing url or dest — skipped")
    return entries

# ── File sync helpers ─────────────────────────────────────────────────────────
def _fetch(url: str, token: str = "") -> bytes:
    headers = {"User-Agent": "dotfile-sync/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _prune_backups(name: str, keep: int) -> None:
    existing = sorted(BACKUP_DIR.glob(f"{name}.*.bak"))
    for old in existing[:max(0, len(existing) - keep)]:
        old.unlink()


def sync_one(entry: dict, backup_count: int, force: bool = False) -> bool:
    """Fetch one file; replace dest if content changed. Returns True on update."""
    name = entry["name"]
    dest = Path(entry["dest"]).expanduser()

    info(f"{name}  ←  {entry['url']}")
    try:
        data = _fetch(entry["url"], entry.get("token", "")).replace(b"\r\n", b"\n")
    except Exception as exc:
        warn(f"  {name}: fetch failed — {exc}")
        return False

    if not force and dest.exists() and _sha256(dest.read_bytes()) == _sha256(data):
        info(f"  {name}: up to date")
        return False

    if dest.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts     = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = BACKUP_DIR / f"{name}.{ts}.bak"
        shutil.copy2(dest, backup)
        _prune_backups(name, backup_count)
        info(f"  backed up → {backup.name}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    if entry.get("chmod"):
        import stat as _stat
        mode = int(entry["chmod"], 8)
        dest.chmod(dest.stat().st_mode | mode)
    ok(f"{name}: {'force-refreshed' if force else 'updated'}  ({dest})")
    return True

# ── Sync run ──────────────────────────────────────────────────────────────────
def run_sync(cfg: configparser.ConfigParser, force: bool = False) -> None:
    entries      = file_entries(cfg)
    backup_count = cfg.getint("general", "backup_count", fallback=5)

    if not entries:
        warn("no [file.*] sections in config — nothing to sync")
        return

    updated = sum(sync_one(e, backup_count, force=force) for e in entries)

    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(
        f"last_sync     = {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"files_total   = {len(entries)}\n"
        f"files_updated = {updated}\n"
    )
    ok(f"sync complete: {updated}/{len(entries)} file(s) updated")

# ── Systemd timer install ─────────────────────────────────────────────────────
def install_timer(cfg: configparser.ConfigParser) -> None:
    interval = cfg.getfloat("general", "interval_hours", fallback=24)
    exec_path = shutil.which("dotfile-sync") or str(Path(sys.argv[0]).resolve())

    svc_dir = Path.home() / ".config" / "systemd" / "user"
    svc_dir.mkdir(parents=True, exist_ok=True)

    (svc_dir / "dotfile-sync.service").write_text(
        _SERVICE_UNIT.format(exec_path=exec_path)
    )
    (svc_dir / "dotfile-sync.timer").write_text(
        _TIMER_UNIT.format(interval_hours=int(interval))
    )

    ok(f"service + timer units written to {svc_dir}")
    info("enable with:")
    info("  systemctl --user daemon-reload")
    info("  systemctl --user enable --now dotfile-sync.timer")
    info("  systemctl --user status dotfile-sync.timer")

# ── Daemon loop ───────────────────────────────────────────────────────────────
def run_daemon(cfg: configparser.ConfigParser) -> None:
    interval = cfg.getfloat("general", "interval_hours", fallback=24) * 3600
    stop     = threading.Event()

    def _handle(sig, _frame):
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT,  _handle)

    info(f"daemon mode: syncing every {interval / 3600:.1f}h (Ctrl-C or SIGTERM to stop)")
    while not stop.is_set():
        run_sync(cfg)
        deadline = time.monotonic() + interval
        while not stop.is_set() and time.monotonic() < deadline:
            stop.wait(timeout=10)
    info("daemon stopped")

# ── CLI ───────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(
        prog="dotfile-sync",
        description="Pull dotfiles from GitHub on a schedule.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--daemon",        action="store_true", help="sync in a loop (interval from config)")
    ap.add_argument("--install-timer", action="store_true", help="write systemd user timer + service units")
    ap.add_argument("--status",        action="store_true", help="print last-sync info")
    ap.add_argument("--config-path",   action="store_true", help="print config file path")
    ap.add_argument("--force",         action="store_true", help="re-download all files even if unchanged")
    args = ap.parse_args()

    if args.config_path:
        print(CONFIG_FILE)
        return

    if args.status:
        if STATUS_FILE.exists():
            print(STATUS_FILE.read_text(), end="")
        else:
            info("no sync has run yet")
        return

    cfg = load_config()

    if args.install_timer:
        install_timer(cfg)
        return

    if args.daemon:
        run_daemon(cfg)
        return

    run_sync(cfg, force=args.force)


if __name__ == "__main__":
    main()
