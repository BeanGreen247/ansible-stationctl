"""Polls MATE power-management/lock settings and systemd-logind inhibitors
to determine whether screen-lock and sleep timers are actually active."""
from __future__ import annotations

from dataclasses import dataclass, field

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

POWER_SCHEMA = "org.mate.power-manager"

# Inhibitor modes/types that count as "blocking" idle/sleep behavior.
_BLOCKING_TYPES = {"sleep", "idle"}


@dataclass
class Inhibitor:
    who: str
    why: str
    what: str


@dataclass
class LockState:
    lock_on_blank: bool = False
    display_sleep_ac: int = 0  # seconds, 0 = disabled
    computer_sleep_ac: int = 0  # seconds, 0 = disabled
    inhibitors: list[Inhibitor] = field(default_factory=list)
    error: str | None = None

    @property
    def display_sleep_enabled(self) -> bool:
        return self.display_sleep_ac > 0

    @property
    def computer_sleep_enabled(self) -> bool:
        return self.computer_sleep_ac > 0

    @property
    def inhibited(self) -> bool:
        return bool(self.inhibitors)

    @property
    def all_active(self) -> bool:
        """True = normal padlock. False = crossed-out padlock."""
        if self.error:
            return False
        return (
            self.lock_on_blank
            and self.display_sleep_enabled
            and self.computer_sleep_enabled
            and not self.inhibited
        )

    def tooltip_text(self) -> str:
        def onoff(flag: bool) -> str:
            return "Enabled" if flag else "Disabled"

        def timer(seconds: int) -> str:
            if seconds <= 0:
                return "Disabled"
            minutes = seconds // 60
            return f"{minutes} min"

        if self.error:
            return f"Lock Status Applet\nError reading settings: {self.error}"

        lines = [
            "Lock Status",
            f"Screen Lock: {onoff(self.lock_on_blank)}",
            f"Display Sleep Timer: {timer(self.display_sleep_ac)}",
            f"Computer Sleep Timer: {timer(self.computer_sleep_ac)}",
        ]
        if self.inhibitors:
            lines.append("Inhibited by:")
            for inh in self.inhibitors:
                lines.append(f"  • {inh.who} ({inh.what}): {inh.why}")
        else:
            lines.append("Inhibited by: none")
        return "\n".join(lines)


def _read_power_settings(state: LockState) -> None:
    settings = Gio.Settings.new(POWER_SCHEMA)
    state.lock_on_blank = settings.get_boolean("lock-blank-screen")
    state.display_sleep_ac = settings.get_int("sleep-display-ac")
    state.computer_sleep_ac = settings.get_int("sleep-computer-ac")


def _read_inhibitors(state: LockState) -> None:
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    result = bus.call_sync(
        "org.freedesktop.login1",
        "/org/freedesktop/login1",
        "org.freedesktop.login1.Manager",
        "ListInhibitors",
        None,
        GLib.VariantType.new("(a(ssssuu))"),
        Gio.DBusCallFlags.NONE,
        -1,
        None,
    )
    (entries,) = result.unpack()
    for what, who, why, mode, uid, pid in entries:
        if mode != "block":
            continue
        types = set(what.split(":"))
        if types & _BLOCKING_TYPES:
            state.inhibitors.append(Inhibitor(who=who, why=why, what=what))


def read_state() -> LockState:
    state = LockState()
    try:
        _read_power_settings(state)
        _read_inhibitors(state)
    except GLib.Error as exc:
        state.error = exc.message
    return state
