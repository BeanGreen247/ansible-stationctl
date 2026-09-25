"""Shared Gtk widget: a padlock image that polls lock_status_state and
updates its icon + hover tooltip. Used by both the standalone test runner
and the real MatePanelApplet."""
from __future__ import annotations

import os
import re
import subprocess

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from lock_status_icon import render_lock_surface, surface_to_pixbuf
from lock_status_state import POWER_SCHEMA, read_state

POLL_INTERVAL_SECONDS = 2
ICON_SIZE = 22
INHIBIT_WHO = "Lock Status Applet"
INHIBIT_WHY = "Paused by user via panel applet"

_XSET_TIMEOUT_RE = re.compile(r"timeout:\s*(\d+)\s*cycle:\s*(\d+)")


def _get_xscreensaver_timeout() -> tuple[int, int]:
    out = subprocess.run(["xset", "q"], capture_output=True, text=True, timeout=5).stdout
    match = _XSET_TIMEOUT_RE.search(out)
    return (int(match.group(1)), int(match.group(2))) if match else (600, 600)


def _set_xscreensaver_off() -> None:
    subprocess.run(["xset", "s", "off"], timeout=5)


def _set_xscreensaver_timeout(timeout: int, cycle: int) -> None:
    subprocess.run(["xset", "s", str(timeout), str(cycle)], timeout=5)


class LockStatusWidget(Gtk.EventBox):
    def __init__(self, icon_size: int = ICON_SIZE) -> None:
        super().__init__()
        self._icon_size = icon_size
        self._inhibit_fd: int | None = None
        self._saved_display_sleep: int | None = None
        self._saved_lock_on_blank: bool | None = None
        self._saved_xset_timeout: tuple[int, int] | None = None
        self._saved_idle_dim_ac: bool | None = None
        self._power_settings = Gio.Settings.new(POWER_SCHEMA)
        self.image = Gtk.Image()
        self.add(self.image)
        self.set_visible_window(False)
        self.set_events(self.get_events() | Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("button-press-event", self._on_click)
        self.connect("destroy", self._on_destroy)
        self.refresh()
        GLib.timeout_add_seconds(POLL_INTERVAL_SECONDS, self._on_timeout)

    def _on_timeout(self) -> bool:
        self.refresh()
        return True  # keep repeating

    def _on_destroy(self, _widget) -> None:
        self._release_inhibitor()

    def _on_click(self, _widget, event) -> bool:
        if event.button != 1:
            return False
        if self._inhibit_fd is None:
            self._acquire_inhibitor()
        else:
            self._release_inhibitor()
        self.refresh()
        return True

    def _acquire_inhibitor(self) -> None:
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            result, fdlist = bus.call_with_unix_fd_list_sync(
                "org.freedesktop.login1",
                "/org/freedesktop/login1",
                "org.freedesktop.login1.Manager",
                "Inhibit",
                GLib.Variant("(ssss)", ("idle:sleep", INHIBIT_WHO, INHIBIT_WHY, "block")),
                GLib.VariantType.new("(h)"),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
                None,
            )
            (idx,) = result.unpack()
            self._inhibit_fd = fdlist.get(idx)
        except GLib.Error:
            self._inhibit_fd = None
            return
        self._saved_display_sleep = self._power_settings.get_int("sleep-display-ac")
        self._power_settings.set_int("sleep-display-ac", 0)
        self._saved_lock_on_blank = self._power_settings.get_boolean("lock-blank-screen")
        self._power_settings.set_boolean("lock-blank-screen", False)
        # mate-power-manager's own timers don't control the X server's
        # built-in screensaver blanking -- that runs on its own idle timer
        # (xset q) independent of gsettings/logind, so it must be disabled
        # separately or the screen blanks anyway while "paused".
        self._saved_xset_timeout = _get_xscreensaver_timeout()
        _set_xscreensaver_off()
        self._saved_idle_dim_ac = self._power_settings.get_boolean("idle-dim-ac")
        self._power_settings.set_boolean("idle-dim-ac", False)

    def _release_inhibitor(self) -> None:
        if self._inhibit_fd is not None:
            os.close(self._inhibit_fd)
            self._inhibit_fd = None
        if self._saved_display_sleep is not None:
            self._power_settings.set_int("sleep-display-ac", self._saved_display_sleep)
            self._saved_display_sleep = None
        if self._saved_lock_on_blank is not None:
            self._power_settings.set_boolean("lock-blank-screen", self._saved_lock_on_blank)
            self._saved_lock_on_blank = None
        if self._saved_xset_timeout is not None:
            _set_xscreensaver_timeout(*self._saved_xset_timeout)
            self._saved_xset_timeout = None
        if self._saved_idle_dim_ac is not None:
            self._power_settings.set_boolean("idle-dim-ac", self._saved_idle_dim_ac)
            self._saved_idle_dim_ac = None

    def refresh(self) -> None:
        state = read_state()
        surface = render_lock_surface(self._icon_size, state.all_active)
        self.image.set_from_pixbuf(surface_to_pixbuf(surface))
        self.image.queue_draw()
        self.set_tooltip_text(state.tooltip_text())
        # set_tooltip_text() alone doesn't refresh an already-visible tooltip
        # popup in GTK3 -- force a re-query so a tooltip open during a poll
        # tick shows the new text instead of stale text until re-hovered.
        self.trigger_tooltip_query()
