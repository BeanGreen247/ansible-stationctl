#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""
battery-status-tray — iOS-style battery percentage tray icon (bare-metal only)

Draws a pill-shaped battery icon (outline + proportional charge fill + a
nub) with the percentage rendered inside it, matching iOS 16+'s "battery
percentage inside icon" look. Hovering shows a tooltip with charge state
and upower's estimated time to empty/full — deliberately NOT shown on the
icon itself, only on hover, per how this was asked for.

Requirements:
    python3-pyqt6, upower (both ensured by setup-dev-applets.yml)
"""
from __future__ import annotations

import subprocess
import sys

from PyQt6.QtCore import QProcess, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

# upower --monitor drives instant updates on plug/unplug/charge-level
# change; this is just a safety-net poll in case that process ever dies
# and the "finished" handler's restart races something.
POLL_INTERVAL_MS = 30_000

GREEN    = QColor("#34c759")  # charging
RED      = QColor("#ff3b30")  # low, on battery
NORMAL   = QColor("#f2f2f7")  # normal charge level (near-white, iOS default)
EMPTY_BG = QColor("#c7c7cc")  # unfilled portion — light, so dark text stays readable on it
OUTLINE  = QColor("#48484a")
TEXT_ON_LIGHT = QColor("#1c1c1e")


def _read_battery() -> dict:
    """Return {percent, state, time} from upower, or {} if no battery present."""
    try:
        devs = subprocess.run(
            ["upower", "-e"], capture_output=True, text=True, timeout=5
        ).stdout.splitlines()
    except Exception:
        return {}
    dev = next((d for d in devs if "battery_BAT" in d), None)
    if not dev:
        return {}
    try:
        info = subprocess.run(
            ["upower", "-i", dev], capture_output=True, text=True, timeout=5
        ).stdout
    except Exception:
        return {}

    out: dict[str, str] = {}
    for line in info.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if key == "percentage":
            out["percent"] = val.rstrip("%")
        elif key == "state":
            out["state"] = val
        elif key.startswith("time to"):
            out["time"] = val
    return out


def _battery_icon(percent: int, state: str) -> QIcon:
    """Horizontal battery icon (on its side, terminal nub on the right):
    charge fills from the LEFT — so on discharge the empty area grows
    from the right, retreating toward the nub. The unfilled portion is
    light (not dark) specifically so dark text reads directly on top of
    it everywhere, with no separate background band needed.
    """
    w, h = 120, 72
    pix = QPixmap(w, h)
    pix.fill(QColor(0, 0, 0, 0))
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    if state == "charging":
        fill_color = GREEN
    elif state == "discharging" and percent <= 20:
        fill_color = RED
    else:
        fill_color = NORMAL

    pct = max(0, min(100, percent))
    body = QRectF(6, 8, 96, 56)
    nub = QRectF(body.right(), body.center().y() - 13, 12, 26)
    body_path = QPainterPath()
    body_path.addRoundedRect(body, 12, 12)

    # Terminal nub
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(OUTLINE)
    nub_path = QPainterPath()
    nub_path.addRoundedRect(nub, 3, 3)
    p.drawPath(nub_path)

    # Unfilled body background — light, so the dark percentage text stays
    # readable here too, not just over the colored fill.
    p.setBrush(EMPTY_BG)
    p.drawPath(body_path)

    # Charge fill, clipped to the body's rounded shape, growing from the
    # left — the unfilled remainder stays on the right, toward the nub.
    fill_w = body.width() * (pct / 100.0)
    fill_rect = QRectF(body.x(), body.y(), fill_w, body.height())
    p.save()
    p.setClipPath(body_path)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fill_color)
    p.drawRect(fill_rect)
    p.restore()

    # Crisp body outline over both empty + fill
    p.setPen(OUTLINE)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(body_path)

    # Percentage, dark, drawn straight on the body — no background band.
    font = QFont("Sans", 34, QFont.Weight.Black)
    p.setFont(font)
    p.setPen(TEXT_ON_LIGHT)
    p.drawText(body, Qt.AlignmentFlag.AlignCenter, str(pct))

    p.end()
    return QIcon(pix)


def _tooltip(info: dict) -> str:
    if not info:
        return "No battery detected"
    state = info.get("state", "unknown")
    pct = info.get("percent", "?")
    time = info.get("time")
    label = {
        "charging": "Charging",
        "fully-charged": "Fully charged",
        "discharging": "On battery",
    }.get(state, state.replace("-", " ").title())
    if time:
        verb = "until full" if state == "charging" else "remaining"
        return f"{label} — {pct}% ({time} {verb})"
    return f"{label} — {pct}%"


class BatteryTray:
    def __init__(self, app: QApplication) -> None:
        self._app = app
        self._tray = QSystemTrayIcon(_battery_icon(100, "unknown"))
        self._tray.setContextMenu(self._build_menu())
        self._attempt_show_tray()

        # Safety-net poll — normal updates come from the upower --monitor
        # process below, this just covers the gap if that process ever
        # dies between its own restart attempts.
        self._timer = QTimer()
        self._timer.timeout.connect(self._refresh)
        self._timer.start(POLL_INTERVAL_MS)

        self._monitor: QProcess | None = None
        self._start_monitor()
        self._refresh()

    def _attempt_show_tray(self) -> None:
        """Retry showing the tray icon until the StatusNotifier host is ready."""
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray.show()
        else:
            QTimer.singleShot(1_000, self._attempt_show_tray)

    def _start_monitor(self) -> None:
        """Run `upower --monitor` so plug/unplug/charge changes refresh the
        icon immediately instead of waiting for the next poll tick."""
        self._monitor = QProcess()
        self._monitor.setProgram("upower")
        self._monitor.setArguments(["--monitor"])
        self._monitor.readyReadStandardOutput.connect(self._on_monitor_event)
        self._monitor.finished.connect(self._on_monitor_exited)
        self._monitor.start()

    def _on_monitor_event(self) -> None:
        self._monitor.readAllStandardOutput()  # drain, content doesn't matter
        self._refresh()

    def _on_monitor_exited(self) -> None:
        # upower itself restarting, a device replug, etc. — try again
        # shortly rather than silently falling back to poll-only forever.
        QTimer.singleShot(5_000, self._start_monitor)

    def _refresh(self) -> None:
        info = _read_battery()
        if not info:
            self._tray.setToolTip("No battery detected")
            return
        percent = int(float(info.get("percent", "0")))
        state = info.get("state", "unknown")
        self._tray.setIcon(_battery_icon(percent, state))
        self._tray.setToolTip(_tooltip(info))

    def _build_menu(self) -> QMenu:
        menu = QMenu()
        act_quit = menu.addAction("Quit")
        act_quit.triggered.connect(self._app.quit)
        return menu


def main() -> None:
    # Qt6 auto-detects wayland vs xcb from the environment — never override
    # QT_QPA_PLATFORM here (see dotfile-sync-tray.py for why).
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("battery-status-tray")
    app.setDesktopFileName("battery-status-tray")

    _tray = BatteryTray(app)  # noqa: F841 — keep alive for event loop
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
