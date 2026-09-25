"""Draws a padlock icon (normal or crossed-out) with Cairo, matching the
style of KDE's power-management tray icon that shows a slashed lock when
sleep/screen-lock is inhibited or disabled."""
from __future__ import annotations

import cairo
import gi

gi.require_version("Gdk", "3.0")
from gi.repository import Gdk

_ACTIVE_COLOR = (0.85, 0.85, 0.85)   # light gray, matches panel icon theme
_CROSSED_COLOR = (0.85, 0.85, 0.85)
_SLASH_COLOR = (0.90, 0.25, 0.20)    # red slash, like KDE's inhibited overlay


def _draw_padlock(ctx: cairo.Context, size: int, color: tuple[float, float, float]) -> None:
    ctx.set_line_width(max(1.5, size * 0.09))
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    ctx.set_source_rgb(*color)

    body_w = size * 0.56
    body_h = size * 0.42
    body_x = (size - body_w) / 2
    body_y = size * 0.46

    # shackle
    shackle_r = body_w * 0.42
    cx = size / 2
    cy = body_y
    ctx.arc(cx, cy, shackle_r, 3.14159, 0)
    ctx.stroke()

    # body (rounded rect)
    radius = size * 0.06
    x, y, w, h = body_x, body_y, body_w, body_h
    ctx.new_sub_path()
    ctx.arc(x + w - radius, y + radius, radius, -1.5708, 0)
    ctx.arc(x + w - radius, y + h - radius, radius, 0, 1.5708)
    ctx.arc(x + radius, y + h - radius, radius, 1.5708, 3.14159)
    ctx.arc(x + radius, y + radius, radius, 3.14159, 4.71239)
    ctx.close_path()
    ctx.set_source_rgb(*color)
    ctx.fill()

    # keyhole
    ctx.set_source_rgb(0.12, 0.12, 0.12)
    khx, khy = size / 2, body_y + body_h * 0.42
    ctx.arc(khx, khy, size * 0.05, 0, 2 * 3.14159)
    ctx.fill()


def render_lock_surface(size: int, active: bool) -> cairo.ImageSurface:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    ctx = cairo.Context(surface)
    _draw_padlock(ctx, size, _ACTIVE_COLOR if active else _CROSSED_COLOR)

    if not active:
        ctx.set_line_width(max(2.0, size * 0.11))
        ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        ctx.set_source_rgb(*_SLASH_COLOR)
        margin = size * 0.08
        ctx.move_to(size - margin, margin)
        ctx.line_to(margin, size - margin)
        ctx.stroke()

    return surface


def surface_to_pixbuf(surface: cairo.ImageSurface):
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, surface.get_width(), surface.get_height())
