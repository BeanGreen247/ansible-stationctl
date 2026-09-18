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
    """Square color-coded badge with the percentage as large as the icon
    allows, plus a thin charge-level bar along the bottom.

    A wide pill-shaped icon (the first version of this) gets forced into
    whatever square-ish slot the tray host allocates for SNI icons — the
    outline/nub ate most of that space and the number was unreadably
    small. Square avoids the squish entirely and spends the pixel budget
    on the digits instead.
    """
    size = 64  # oversampled square; downscales cleanly to actual tray size
    pix = QPixmap(size, size)
    pix.fill(QColor(0, 0, 0, 0))
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    if state == "charging":
        bg_color = GREEN
        text_color = TEXT_ON_DARK
    elif state == "discharging" and percent <= 20:
        bg_color = RED
        text_color = TEXT_ON_DARK
    else:
        bg_color = NORMAL
        text_color = TEXT_ON_LIGHT

    badge = QRectF(2, 2, size - 4, size - 4)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(bg_color)
    badge_path = QPainterPath()
    badge_path.addRoundedRect(badge, 14, 14)
    p.drawPath(badge_path)

    # Thin charge-level bar along the bottom — the one remaining "battery"
    # cue now that the outline/pill shape is gone.
    bar = badge.adjusted(8, badge.height() - 10, -8, -6)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(OUTLINE)
    bar_bg_path = QPainterPath()
    bar_bg_path.addRoundedRect(bar, 2, 2)
    p.drawPath(bar_bg_path)
    fill_w = max(2.0, bar.width() * (max(0, min(100, percent)) / 100.0))
    fill_bar = QRectF(bar.x(), bar.y(), fill_w, bar.height())
    p.setBrush(text_color)
    bar_fill_path = QPainterPath()
    bar_fill_path.addRoundedRect(fill_bar, 2, 2)
    p.drawPath(bar_fill_path)

    # Percentage, large and bold, filling most of the badge above the bar.
    font = QFont("Sans", 24, QFont.Weight.Bold)
    p.setFont(font)
    p.setPen(text_color)
    text_area = badge.adjusted(0, 0, 0, -12)
    p.drawText(text_area, Qt.AlignmentFlag.AlignCenter, str(percent))

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
