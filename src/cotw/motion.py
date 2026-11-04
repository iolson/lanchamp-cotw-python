"""Animated graphics for the frag-movie edits: kinetic title and kill counter (transparent RGBA frames)."""

from __future__ import annotations

import math

from PIL import Image

from cotw.canvas import Canvas, color
from cotw.graphics import CYAN, GOLD, H, INK, MUTED, W, WHITE, _clamp01, _ease_out_back, _ease_out_cubic, _seg

TITLE_SECONDS = 3.6
TITLE_FPS = 30
COUNTER_SECONDS = 1.1
COUNTER_FPS = 30


def _fade(img: Image.Image, factor: float) -> Image.Image:
    if factor >= 0.999:
        return img
    alpha = img.getchannel("A").point(lambda a: round(a * factor))
    img.putalpha(alpha)
    return img


def _bump(p: float, centre: float, width: float) -> float:
    return _clamp01(1 - abs(p - centre) / width)


def title_frame(name: str, subtitle: str, p: float) -> Image.Image:
    """One frame of the title lockup; p runs 0..1. The name pops in letter by letter, glitches with an
    RGB split, a light sweep crosses it, the subtitle slides up and everything fades out at the end."""
    c = Canvas()
    cx, cy = W / 2, H / 2
    shade = 0.62 * _ease_out_cubic(_seg(p, 0.0, 0.12)) * (1 - _ease_out_cubic(_seg(p, 0.86, 1.0)))
    if shade > 0:
        c.fill(color(INK, shade))

    name = name.upper()
    size = c.fit_size(name, "display", 700, 230, 4, W * 0.78)
    total = c.measure(name, "display", 700, size, 4)
    x0 = cx - total / 2
    base_y = cy + size * 0.30
    glitch = max(_bump(p, 0.30, 0.05), _bump(p, 0.52, 0.04))
    shift = 16 * glitch * (1 if int(p * 90) % 2 else -1)

    for i, ch in enumerate(name):
        lp = _seg(p, 0.04 + i * 0.03, 0.22 + i * 0.03)
        if lp <= 0 or ch == " ":
            continue
        s = 0.55 + 0.45 * _ease_out_back(_clamp01(lp))
        x = x0 + c.measure(name[:i], "display", 700, size, 4)
        w = c.measure(ch, "display", 700, size, 4)
        y = base_y + (1 - _ease_out_cubic(lp)) * 70
        a = _ease_out_cubic(lp)
        if glitch > 0.01:
            for dx, col in ((-shift, CYAN), (shift, GOLD)):
                c.text(ch, x + w / 2 + dx, y + size * 0.3 * (s - 1) * -1, family="display", weight=700,
                       size=size * s, ls=0, fill=color(col, 0.7 * a), align="center")
        c.text(ch, x + w / 2, y + size * 0.3 * (s - 1) * -1, family="display", weight=700, size=size * s,
               ls=0, fill=color(WHITE, a), align="center", glow=(color(CYAN, 0.35 * a), 26))

    lw = total * _ease_out_cubic(_seg(p, 0.30, 0.5))
    if lw > 1:
        c.rect(cx - lw / 2, base_y + size * 0.12, lw, 6, color(GOLD))

    sp = _seg(p, 0.34, 0.54)
    if sp > 0 and subtitle:
        c.text(subtitle, cx, base_y + size * 0.12 + 62 + (1 - _ease_out_cubic(sp)) * 24, family="mono", weight=600,
               size=36, ls=16 + (1 - sp) * 20, fill=color(MUTED, _ease_out_cubic(sp)), align="center", upper=True)

    sweep = _seg(p, 0.40, 0.62)
    if 0 < sweep < 1:
        y = base_y - size * 0.95 + sweep * size * 1.4
        c.vband(x0 - 40, y - 40, total + 80, 80, color(WHITE), math.sin(sweep * math.pi) * 0.22)
    return _fade(c.finish(), 1.0)


def title_frame_count() -> int:
    return round(TITLE_SECONDS * TITLE_FPS)


def counter_frame(n: int, p: float) -> Image.Image:
    """Kill counter ('3' + KILLS, with a pip per kill): pops in with overshoot, holds, fades."""
    c = Canvas()
    pop = _ease_out_back(_clamp01(_seg(p, 0.0, 0.3)))
    a = _clamp01(_seg(p, 0.0, 0.12)) * (1 - _ease_out_cubic(_seg(p, 0.75, 1.0)))
    if a <= 0:
        return c.finish()
    x, y = 96, H * 0.52
    size = 150 * (0.6 + 0.4 * pop)
    c.text(str(n), x, y + 60 * (0.6 + 0.4 * pop), family="display", weight=700, size=size, fill=color(GOLD, a),
           glow=(color(GOLD, 0.4 * a), 30))
    c.text("KILL" if n == 1 else "KILLS", x + 6, y + 112, family="mono", weight=600, size=30, ls=14,
           fill=color(WHITE, a))
    for i in range(n):
        c.rect(x + 6 + i * 34, y + 134, 26, 6, color(CYAN, a))
    return c.finish()


def counter_frame_count() -> int:
    return round(COUNTER_SECONDS * COUNTER_FPS)
