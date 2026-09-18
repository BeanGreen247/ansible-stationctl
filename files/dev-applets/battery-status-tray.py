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

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

POLL_INTERVAL_MS = 15_000

GREEN   = QColor("#34c759")  # charging
RED     = QColor("#ff3b30")  # low, on battery
NORMAL  = QColor("#f2f2f7")  # normal charge level (near-white, iOS default)
OUTLINE = QColor("#e5e5ea")
TEXT_ON_LIGHT = QColor("#000000")
TEXT_ON_DARK  = QColor("#ffffff")


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
    """Render the pill-shaped battery icon at 2x for crisp panel scaling."""
    w, h = 64, 32
    pix = QPixmap(w, h)
    pix.fill(QColor(0, 0, 0, 0))
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    body = QRectF(2, 4, 52, 24)
    nub = QRectF(55, 11, 6, 10)

    if state == "charging":
        fill_color = GREEN
    elif state == "discharging" and percent <= 20:
        fill_color = RED
    else:
        fill_color = NORMAL

    # Outline (body + nub)
    p.setPen(OUTLINE)
    p.setBrush(Qt.BrushStyle.NoBrush)
    outline_path = QPainterPath()
    outline_path.addRoundedRect(body, 6, 6)
    p.drawPath(outline_path)
    p.drawRoundedRect(nub, 2, 2)

    # Proportional fill, inset from the outline
    inset = body.adjusted(3, 3, -3, -3)
    fill_w = max(2.0, inset.width() * (max(0, min(100, percent)) / 100.0))
    fill_rect = QRectF(inset.x(), inset.y(), fill_w, inset.height())
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fill_color)
    fill_path = QPainterPath()
    fill_path.addRoundedRect(fill_rect, 3, 3)
    p.drawPath(fill_path)

    # Percentage, centered over the whole body — readable against both the
    # light NORMAL/GREEN fills and the darker RED low-battery fill.
    font = QFont("Sans", 12, QFont.Weight.Bold)
    p.setFont(font)
    p.setPen(TEXT_ON_DARK if fill_color == RED else TEXT_ON_LIGHT)
    p.drawText(body, Qt.AlignmentFlag.AlignCenter, str(percent))

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

        self._timer = QTimer()
        self._timer.timeout.connect(self._refresh)
        self._timer.start(POLL_INTERVAL_MS)
        self._refresh()

    def _attempt_show_tray(self) -> None:
        """Retry showing the tray icon until the StatusNotifier host is ready."""
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray.show()
        else:
            QTimer.singleShot(1_000, self._attempt_show_tray)

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
