#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""
dotfile-sync-tray — Dotfile Sync status window + KDE tray indicator

A system-tray icon that opens a Breeze Dark status window showing:
  • Current sync state (up-to-date / syncing / error)
  • Last sync timestamp and which files were updated
  • Per-file status rows (name, destination, last-updated time)
  • Manual "Sync Now" and "Force Sync" buttons
  • Scrollable sync history log

Left-click or double-click the tray icon to open/raise the window.
Right-click for the context menu (sync, force-sync, quit).

Checks for upstream changes on the interval set in dotfile-sync config
(default 12 h).  On new commits it runs dotfile-sync and sends a KDE
notification.

Requirements:
    python3-pyqt6   (apt install python3-pyqt6)
    dotfile-sync    (~/.local/bin/dotfile-sync — deployed by setup-kubuntu.sh)
"""
from __future__ import annotations

import configparser
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import (
    QPoint, Qt, QThread, QTimer, QUrl, pyqtSignal,
)
from PyQt6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap, QTextCursor
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu,
    QPushButton, QScrollArea, QSystemTrayIcon,
    QTextEdit, QVBoxLayout, QWidget,
)

# ── Paths ─────────────────────────────────────────────────────────────────────
CONFIG_DIR  = Path.home() / ".config"  / "dotfile-sync"
CONFIG_FILE = CONFIG_DIR  / "config.ini"
STATUS_FILE = Path.home() / ".local" / "share" / "dotfile-sync" / "last-sync.txt"
SYNC_BIN    = Path.home() / ".local" / "bin" / "dotfile-sync"

REPO        = "BeanGreen247/dotfiles"
BRANCH      = "master"
REF_API     = f"https://api.github.com/repos/{REPO}/git/refs/heads/{BRANCH}"
API_TIMEOUT = 10  # seconds

WIN_W, WIN_H = 560, 520

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mK]")

_ICON_OK      = "vcs-normal"
_ICON_SYNCING = "view-refresh"
_ICON_ERROR   = "dialog-error"

# ── Stylesheet (Breeze Dark — matches setup-installer / post-login) ───────────
QSS = """
QMainWindow, QWidget#root { background: #232629; }
QWidget#titlebar  { background: #1b1e20; border-bottom: 1px solid #3a3f44; }
QFrame#divider    { background: #3a3f44; max-height: 1px; }
QFrame#divider_v  { background: #3a3f44; max-width:  1px; }

QScrollArea           { background: transparent; border: none; }
QScrollBar:vertical   { background: #1b1e20; width: 8px; }
QScrollBar::handle:vertical { background: #4a5056; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

QLabel#title    { color: #eff0f1; font-size: 15px; font-weight: bold; }
QLabel#author   { color: #7f8c8d; font-size: 11px; }
QLabel#section  { color: #3daee9; font-size: 12px; font-weight: bold; }
QLabel#body     { color: #bdc3c7; font-size: 12px; }
QLabel#caption  { color: #4a5056; font-size: 10px; }

QLabel#status_ok   { color: #27ae60; font-size: 22px; }
QLabel#status_spin { color: #f1c40f; font-size: 22px; }
QLabel#status_err  { color: #da4453; font-size: 22px; }
QLabel#status_text { color: #eff0f1; font-size: 13px; font-weight: bold; }
QLabel#status_sub  { color: #7f8c8d; font-size: 11px; }

QLabel#file_name    { color: #eff0f1; font-size: 12px; }
QLabel#file_path    { color: #4a5056; font-size: 10px; }
QLabel#file_ok      { color: #27ae60; font-size: 13px; }
QLabel#file_pending { color: #4a5056; font-size: 13px; }
QLabel#file_err     { color: #da4453; font-size: 13px; }

QTextEdit#log {
    background: #1b1e20; color: #bdc3c7;
    border: 1px solid #3a3f44;
    font-family: "JetBrains Mono","Fira Code","Courier New",monospace;
    font-size: 10px;
}

QPushButton#primary {
    background: #3daee9; color: #1b1e20;
    border: none; border-radius: 3px;
    padding: 8px 20px; font-size: 12px; font-weight: bold;
}
QPushButton#primary:hover    { background: #56b9ec; }
QPushButton#primary:pressed  { background: #2b9dc8; }
QPushButton#primary:disabled { background: #3a3f44; color: #4a5056; }

QPushButton#secondary {
    background: transparent; color: #3daee9;
    border: 1px solid #3a3f44; border-radius: 3px;
    padding: 7px 16px; font-size: 12px;
}
QPushButton#secondary:hover    { background: #2d3136; border-color: #3daee9; }
QPushButton#secondary:pressed  { background: #1e3040; }
QPushButton#secondary:disabled { color: #4a5056; border-color: #3a3f44; }

QPushButton#tb_close {
    background: transparent; color: #7f8c8d; border: none;
    font-size: 14px; padding: 0 4px;
}
QPushButton#tb_close:hover { color: #da4453; }
"""

# ── Helpers ───────────────────────────────────────────────────────────────────

def _divider() -> QFrame:
    f = QFrame(); f.setObjectName("divider"); f.setFixedHeight(1); return f

def _vdivider() -> QFrame:
    f = QFrame(); f.setObjectName("divider_v"); f.setFixedWidth(1); return f

def _icon(name: str, app: QApplication) -> QIcon:
    ico = QIcon.fromTheme(name)
    if not ico.isNull():
        return ico
    from PyQt6.QtWidgets import QStyle
    return app.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)

def _tray_icon(colour: str) -> QIcon:
    """Painted colored circle icon for the system tray — always visible."""
    pix = QPixmap(22, 22)
    pix.fill(QColor(0, 0, 0, 0))
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(colour))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(3, 3, 16, 16)
    p.end()
    return QIcon(pix)

def _load_status() -> dict:
    if not STATUS_FILE.exists():
        return {}
    data = {}
    for line in STATUS_FILE.read_text().splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            data[k.strip()] = v.strip()
    return data

def _load_config_files() -> list[dict]:
    if not CONFIG_FILE.exists():
        return []
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_FILE)
    out = []
    for section in cfg.sections():
        if section.startswith("file."):
            name = section[len("file."):]
            dest = cfg.get(section, "dest", fallback="").strip()
            url  = cfg.get(section, "url",  fallback="").strip()
            if dest:
                out.append({"name": name, "dest": dest, "url": url})
    return out

def _check_interval_ms() -> int:
    if not CONFIG_FILE.exists():
        return 12 * 60 * 60 * 1000
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_FILE)
    hours = cfg.getfloat("general", "interval_hours", fallback=12)
    return int(hours * 3600 * 1000)


# ── Background worker ─────────────────────────────────────────────────────────

class SyncWorker(QThread):
    finished = pyqtSignal(str, str, str)   # (status, detail, remote_sha)
    log_line = pyqtSignal(str)

    def __init__(self, known_sha: str, force: bool = False) -> None:
        super().__init__()
        self._known_sha = known_sha
        self._force = force

    def run(self) -> None:
        if self._force:
            self._run_sync(["--force"])
            return
        try:
            remote_sha = self._remote_sha()
        except Exception as exc:
            self.finished.emit("error", f"GitHub API: {exc}", "")
            return
        if remote_sha == self._known_sha:
            self.finished.emit("ok", "Already up to date", remote_sha)
            return
        self._run_sync([], remote_sha)

    def _run_sync(self, extra_args: list[str], remote_sha: str = "") -> None:
        if not SYNC_BIN.exists():
            self.finished.emit("error", "dotfile-sync not installed", remote_sha)
            return
        try:
            proc = subprocess.Popen(
                [str(SYNC_BIN)] + extra_args,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            stdout_lines: list[str] = []
            assert proc.stdout is not None
            for raw in proc.stdout:
                clean = ANSI_RE.sub("", raw.rstrip())
                stdout_lines.append(clean)
                self.log_line.emit(clean)
            proc.wait(timeout=90)
        except subprocess.TimeoutExpired:
            self.finished.emit("error", "dotfile-sync timed out after 90 s", "")
            return
        except Exception as exc:
            self.finished.emit("error", str(exc), "")
            return
        if proc.returncode != 0:
            self.finished.emit("error", f"dotfile-sync failed (exit {proc.returncode})", remote_sha)
            return
        detail_lines = [
            ln.strip().lstrip("✓→ ").strip()
            for ln in stdout_lines
            if any(tok in ln for tok in ("✓", "updated", "up to date", "force"))
        ]
        detail = "; ".join(detail_lines) if detail_lines else "Dotfiles synced"
        self.finished.emit("updated", detail, remote_sha)

    @staticmethod
    def _remote_sha() -> str:
        req = urllib.request.Request(REF_API, headers={"User-Agent": "dotfile-sync-tray/2.0"})
        with urllib.request.urlopen(req, timeout=API_TIMEOUT) as r:
            return json.loads(r.read())["object"]["sha"]


# ── Custom title bar ──────────────────────────────────────────────────────────

class TitleBar(QWidget):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("titlebar")
        self.setFixedHeight(36)
        self._drag: QPoint | None = None

        h = QHBoxLayout(self)
        h.setContentsMargins(12, 0, 8, 0)
        h.setSpacing(8)

        lbl = QLabel(title)
        lbl.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        h.addWidget(lbl)
        h.addStretch()

        gh = QLabel('<a href="https://github.com/BeanGreen247/kubuntu-setup" style="color:#4a5056;text-decoration:none;font-size:10px;">github</a>')
        gh.setOpenExternalLinks(False)
        gh.linkActivated.connect(lambda url: QDesktopServices.openUrl(QUrl(url)))
        h.addWidget(gh)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("tb_close")
        close_btn.clicked.connect(lambda: self.window().hide())
        h.addWidget(close_btn)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag = ev.globalPosition().toPoint() - self.window().frameGeometry().topLeft()

    def mouseMoveEvent(self, ev):
        if self._drag and ev.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(ev.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, ev):
        self._drag = None


# ── Main window ───────────────────────────────────────────────────────────────

class SyncWindow(QMainWindow):
    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Window
        )
        self.setFixedSize(WIN_W, WIN_H)
        self.setWindowTitle("Dotfile Sync")

        self._worker: SyncWorker | None = None
        self._last_sha   = ""
        self._last_check = "never"

        root = QWidget(); root.setObjectName("root")
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        v.addWidget(TitleBar("Dotfile Sync"))
        v.addWidget(self._build_body(), 1)
        v.addWidget(_divider())
        v.addWidget(self._build_footer())

        self._refresh_file_rows()

    # ── Body ──────────────────────────────────────────────────────────────────

    def _build_body(self) -> QWidget:
        w = QWidget(); w.setObjectName("root")
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._build_left())
        h.addWidget(_vdivider())
        h.addWidget(self._build_right(), 1)
        return w

    def _build_left(self) -> QWidget:
        panel = QWidget(); panel.setObjectName("root")
        panel.setFixedWidth(220)
        v = QVBoxLayout(panel)
        v.setContentsMargins(20, 20, 16, 16)
        v.setSpacing(0)

        # Status block
        hrow = QHBoxLayout()
        self._status_icon = QLabel("✓")
        self._status_icon.setObjectName("status_ok")
        self._status_icon.setFixedWidth(30)
        scol = QVBoxLayout(); scol.setSpacing(0)
        self._status_text = QLabel("Up to date")
        self._status_text.setObjectName("status_text")
        self._status_sub  = QLabel("Not yet checked")
        self._status_sub.setObjectName("status_sub")
        scol.addWidget(self._status_text)
        scol.addWidget(self._status_sub)
        hrow.addWidget(self._status_icon)
        hrow.addSpacing(8)
        hrow.addLayout(scol, 1)
        v.addLayout(hrow)

        v.addSpacing(16)
        v.addWidget(_divider())
        v.addSpacing(14)

        # Last sync info
        sec = QLabel("Last sync")
        sec.setObjectName("section")
        v.addWidget(sec)
        v.addSpacing(6)

        self._lbl_time    = QLabel("—")
        self._lbl_updated = QLabel("—")
        self._lbl_total   = QLabel("—")
        for lbl in (self._lbl_time, self._lbl_updated, self._lbl_total):
            lbl.setObjectName("body")
            lbl.setWordWrap(True)
            v.addWidget(lbl)
            v.addSpacing(2)

        v.addSpacing(14)
        v.addWidget(_divider())
        v.addSpacing(14)

        # Tracked files list
        sec2 = QLabel("Tracked files")
        sec2.setObjectName("section")
        v.addWidget(sec2)
        v.addSpacing(8)

        self._files_container = QWidget()
        self._files_container.setObjectName("root")
        self._files_layout = QVBoxLayout(self._files_container)
        self._files_layout.setContentsMargins(0, 0, 0, 0)
        self._files_layout.setSpacing(8)
        self._files_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self._files_container)
        v.addWidget(scroll, 1)
        return panel

    def _build_right(self) -> QWidget:
        panel = QWidget(); panel.setObjectName("root")
        v = QVBoxLayout(panel)
        v.setContentsMargins(16, 20, 20, 16)
        v.setSpacing(8)

        sec = QLabel("Sync log")
        sec.setObjectName("section")
        v.addWidget(sec)

        self._log = QTextEdit()
        self._log.setObjectName("log")
        self._log.setReadOnly(True)
        v.addWidget(self._log, 1)
        return panel

    def _build_footer(self) -> QWidget:
        w = QWidget(); w.setObjectName("root")
        h = QHBoxLayout(w)
        h.setContentsMargins(20, 10, 20, 14)
        h.setSpacing(10)

        self._btn_sync = QPushButton("Sync Now")
        self._btn_sync.setObjectName("primary")
        self._btn_sync.clicked.connect(self.run_check)

        self._btn_force = QPushButton("Force Sync")
        self._btn_force.setObjectName("secondary")
        self._btn_force.clicked.connect(self.run_force_sync)

        self._last_check_lbl = QLabel("Checked: never")
        self._last_check_lbl.setObjectName("caption")

        h.addWidget(self._btn_sync)
        h.addWidget(self._btn_force)
        h.addStretch()
        h.addWidget(self._last_check_lbl)

        btn_close = QPushButton("Close")
        btn_close.setObjectName("secondary")
        btn_close.clicked.connect(self.hide)
        h.addWidget(btn_close)
        return w

    # ── File rows ─────────────────────────────────────────────────────────────

    def _refresh_file_rows(self) -> None:
        while self._files_layout.count() > 1:
            item = self._files_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        files = _load_config_files()
        if not files:
            lbl = QLabel("No config found")
            lbl.setObjectName("body")
            self._files_layout.insertWidget(0, lbl)
            self._file_icons: dict[str, QLabel] = {}
            return

        self._file_icons = {}
        for i, f in enumerate(files):
            row = QWidget(); row.setObjectName("root")
            rh = QHBoxLayout(row)
            rh.setContentsMargins(0, 0, 0, 0)
            rh.setSpacing(6)

            ico = QLabel("○")
            ico.setObjectName("file_pending")
            ico.setFixedWidth(16)
            ico.setAlignment(Qt.AlignmentFlag.AlignTop)

            col = QVBoxLayout(); col.setSpacing(0)
            nm = QLabel(f["name"]); nm.setObjectName("file_name")
            pt = QLabel(f["dest"]); pt.setObjectName("file_path")
            pt.setWordWrap(True)
            col.addWidget(nm); col.addWidget(pt)
            rh.addWidget(ico); rh.addLayout(col, 1)
            self._files_layout.insertWidget(i, row)
            self._file_icons[f["name"]] = ico

        self._update_file_icons_from_status()

    def _update_file_icons_from_status(self) -> None:
        status  = _load_status()
        updated = int(status.get("files_updated", "0"))
        total   = int(status.get("files_total",   "0"))
        ts      = status.get("last_sync", None)

        if ts:
            self._lbl_time.setText(f"🕒  {ts}")
            self._lbl_updated.setText(f"↑  {updated} of {total} updated")
            self._lbl_total.setText(f"📄  {total} file(s) tracked")
        else:
            self._lbl_time.setText("Never synced")
            self._lbl_updated.setText("—")
            self._lbl_total.setText("—")

        for ico in self._file_icons.values():
            if ts:
                ico.setText("✓"); ico.setObjectName("file_ok")
            else:
                ico.setText("○"); ico.setObjectName("file_pending")
            ico.setStyleSheet(""); ico.style().polish(ico)

    # ── Sync operations ───────────────────────────────────────────────────────

    def run_check(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self._set_syncing("Checking upstream…")
        self._worker = SyncWorker(self._last_sha)
        self._worker.log_line.connect(self._append_log_line)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def run_force_sync(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self._set_syncing("Force syncing all files…")
        self._worker = SyncWorker(self._last_sha, force=True)
        self._worker.log_line.connect(self._append_log_line)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _set_syncing(self, msg: str) -> None:
        self._btn_sync.setEnabled(False)
        self._btn_force.setEnabled(False)
        self._status_icon.setText("□")
        self._status_icon.setObjectName("status_spin")
        self._status_text.setText(msg)
        self._status_icon.setStyleSheet("")
        self._status_icon.style().polish(self._status_icon)
        self._append_log_line(f"── {msg} ──")

    def _on_finished(self, status: str, detail: str, remote_sha: str) -> None:
        self._last_check = datetime.now().strftime("%H:%M")
        self._last_check_lbl.setText(f"Checked: {self._last_check}")
        self._btn_sync.setEnabled(True)
        self._btn_force.setEnabled(True)
        if remote_sha:
            self._last_sha = remote_sha

        if status == "updated":
            self._status_icon.setText("✓")
            self._status_icon.setObjectName("status_ok")
            self._status_text.setText("Synced")
            self._status_sub.setText(detail[:60])
        elif status == "error":
            self._status_icon.setText("✗")
            self._status_icon.setObjectName("status_err")
            self._status_text.setText("Error")
            self._status_sub.setText(detail[:60])
        else:  # ok / up to date
            self._status_icon.setText("✓")
            self._status_icon.setObjectName("status_ok")
            self._status_text.setText("Up to date")
            self._status_sub.setText(f"Checked {self._last_check}")
        self._refresh_file_rows()

        self._status_icon.setStyleSheet("")
        self._status_icon.style().polish(self._status_icon)
        self._append_log_line(f"── Done: {status} — {detail} ──")

    # ── Log ───────────────────────────────────────────────────────────────────

    def _append_log_line(self, line: str) -> None:
        if not line.strip():
            return
        if "✓" in line or "updated" in line.lower() or "done" in line.lower():
            colour = "#a6e3a1"
        elif "⚠" in line or "warn" in line.lower():
            colour = "#fab387"
        elif "✗" in line or "error" in line.lower() or "fail" in line.lower():
            colour = "#f38ba8"
        elif "──" in line:
            colour = "#3daee9"
        elif "→" in line:
            colour = "#89dceb"
        else:
            colour = "#cdd6f4"

        cur = self._log.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        fmt = cur.charFormat()
        fmt.setForeground(QColor(colour))
        cur.setCharFormat(fmt)
        ts = datetime.now().strftime("%H:%M:%S")
        cur.insertText(f"[{ts}] {line}\n")
        self._log.setTextCursor(cur)
        self._log.ensureCursorVisible()


# ── System tray wrapper ───────────────────────────────────────────────────────

class DotfileTray:
    def __init__(self, app: QApplication) -> None:
        self._app = app

        if not QSystemTrayIcon.isSystemTrayAvailable():
            # KDE StatusNotifier host not ready yet — retry in 1 s
            pass

        self._win = SyncWindow(app)

        self._tray = QSystemTrayIcon(_tray_icon("#f1c40f"))
        self._tray.setToolTip("Dotfile Sync — checking…")
        self._tray.setContextMenu(self._build_menu())
        self._tray.activated.connect(self._on_tray_activated)

        # Retry showing the tray until StatusNotifier host is available
        self._attempt_show_tray()

        # Patch _on_finished to also update tray icon + send KDE notification
        _orig = self._win._on_finished

        def _patched(status: str, detail: str, sha: str) -> None:
            _orig(status, detail, sha)
            if status == "updated":
                self._tray.setIcon(_tray_icon("#27ae60"))
                self._tray.setToolTip("Dotfile Sync — synced")
                self._tray.showMessage(
                    "Dotfiles updated", detail,
                    QSystemTrayIcon.MessageIcon.Information, 6_000,
                )
            elif status == "error":
                self._tray.setIcon(_tray_icon("#da4453"))
                self._tray.setToolTip(f"Dotfile Sync — error: {detail[:60]}")
                self._tray.showMessage(
                    "Dotfile Sync — error", detail,
                    QSystemTrayIcon.MessageIcon.Warning, 8_000,
                )
            else:
                self._tray.setIcon(_tray_icon("#27ae60"))
                self._tray.setToolTip("Dotfile Sync — up to date")

        self._win._on_finished = _patched  # type: ignore[method-assign]

        # Mark tray as pending (yellow) until first check completes
        # icon colour already set at construction above

        # Initial check 5 s after startup, then on configured interval
        QTimer.singleShot(5_000, self._win.run_check)
        self._timer = QTimer()
        self._timer.setInterval(_check_interval_ms())
        self._timer.timeout.connect(self._win.run_check)
        self._timer.start()

    def _attempt_show_tray(self) -> None:
        """Retry showing the tray icon until the StatusNotifier host is ready."""
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray.show()
        else:
            QTimer.singleShot(1_000, self._attempt_show_tray)

    def _build_menu(self) -> QMenu:
        menu = QMenu()
        act_open = menu.addAction(QIcon.fromTheme("utilities-terminal"), "Open status window")
        act_open.triggered.connect(self._show_window)
        menu.addSeparator()
        act_sync  = menu.addAction(_icon(_ICON_SYNCING, self._app), "Sync now")
        act_sync.triggered.connect(self._win.run_check)
        act_force = menu.addAction(_icon(_ICON_SYNCING, self._app), "Force sync")
        act_force.triggered.connect(self._win.run_force_sync)
        menu.addSeparator()
        act_quit = menu.addAction(QIcon.fromTheme("application-exit"), "Quit")
        act_quit.triggered.connect(self._app.quit)
        return menu

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._show_window()

    def _show_window(self) -> None:
        if self._win.isVisible():
            self._win.raise_()
            self._win.activateWindow()
        else:
            screen = self._app.primaryScreen().availableGeometry()
            self._win.move(
                (screen.width()  - WIN_W) // 2,
                (screen.height() - WIN_H) // 2,
            )
            self._win.show()
            self._win.raise_()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    # Qt6 auto-detects wayland vs xcb from the environment.
    # Never override QT_QPA_PLATFORM here — forcing xcb breaks the SNI tray
    # on Wayland; forcing wayland breaks X11-only sessions.

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("dotfile-sync-tray")
    app.setDesktopFileName("dotfile-sync-tray")
    app.setStyleSheet(QSS)

    _tray = DotfileTray(app)  # noqa: F841 — keep alive for event loop
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
