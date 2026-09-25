#!/usr/bin/env python3
"""Real MATE panel applet: shows a padlock icon that reflects whether
screen-lock and sleep timers are active, disabled, or currently inhibited
(crossed-out lock), with a breakdown tooltip on hover -- modeled on KDE's
power-management systray inhibition indicator."""
import os
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("MatePanelApplet", "4.0")
from gi.repository import Gtk, MatePanelApplet

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lock_status_widget import LockStatusWidget

APPLET_IID = "LockStatusApplet"


def applet_fill(applet: MatePanelApplet.Applet) -> None:
    widget = LockStatusWidget()
    applet.add(widget)
    applet.show_all()


def applet_factory(applet: MatePanelApplet.Applet, iid: str, _data) -> bool:
    if iid != APPLET_IID:
        return False
    applet_fill(applet)
    return True


def main() -> int:
    return MatePanelApplet.Applet.factory_main(
        "LockStatusAppletFactory",
        True,
        MatePanelApplet.Applet.__gtype__,
        applet_factory,
        None,
    )


if __name__ == "__main__":
    sys.exit(main())
