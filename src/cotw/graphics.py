"""Python ports of the COTW asset generator templates (lanchamp.to/public/tools/cotw/index.html).

Every function mirrors the matching canvas routine in that page; keep coordinates in sync
with it so the video graphics match what the web tool exports.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image

from cotw.canvas import Canvas, color

W, H = 1920, 1080

INK = "#0B1018"
CYAN = "#00ACE7"
GOLD = "#F6CA26"
WHITE = "#F7FAFC"
MUTED = "#9EADBC"
MUTED2 = "#7D8B9D"

STAT_DUR = 1.3  # seconds of reveal animation
STAT_HOLD = 0.35  # seconds the finished frame holds
STAT_TOTAL = STAT_DUR + STAT_HOLD


@dataclass(frozen=True)
class EpisodeMeta:
    episode: int
    week: str = ""
    featuring: str = ""
    league: str = ""
    channel: str = "FuzzyHatTV"
    next_up: str = ""

    @property
    def ep_tag(self) -> str:
        return f"EP.{self.episode:02d}"


def _mark(c: Canvas, x: float, y: float, size: float) -> None:
    """The Champion Node mark, drawn in a 100x100 box scaled to `size`."""
    k = size / 100

    def t(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
        return [(x + px * k, y + py * k) for px, py in pts]

    c.polyline(t([(23, 22), (10, 22), (10, 38), (26, 47)]), color(CYAN), 7 * k)
    c.polyline(t([(77, 22), (90, 22), (90, 38), (74, 47)]), color(CYAN), 7 * k)
    with c.layer((x, y, x + size, y + size)) as lay:
        lay.polygon(t([(22, 14), (78, 14), (71, 46), (60, 58), (40, 58), (29, 46)]), color(GOLD))
        lay.polygon(t([(30, 20), (70, 20), (66, 40), (34, 40)]), color(WHITE, 0.14))
        lay.polygon(t([(45, 58), (55, 58), (55, 70), (45, 70)]), color(CYAN))
        lay.polygon(t([(30, 70), (70, 70), (74, 86), (26, 86)]), color(CYAN))
        lay.polyline(t([(34, 79), (66, 79)]), color(INK), 2.2 * k, round_caps=False)
        for nx in (36, 50, 64):
            lay.circle(x + nx * k, y + 79 * k, 2.6 * k, color(INK))


def _bg_full(c: Canvas) -> None:
    c.fill(color(INK))
    c.radial(W / 2, -140, 80, 960, color(CYAN, 0.17), color(CYAN, 0))
    c.radial(W * 0.08, H + 120, 60, 760, color(GOLD, 0.07), color(GOLD, 0))
    pts = [(x, y) for y in range(48, int(H * 0.5), 58) for x in range(48, W, 58)]
    c.dots(pts, 1.5, color("#FFFFFF", 0.05))
    c.radial(W / 2, H / 2, H * 0.28, W * 0.72, color("#000000", 0), color("#000000", 0.5))


def _corner_ticks(c: Canvas, m: float, length: float = 46, col=None, lw: float = 3) -> None:
    col = col or color(CYAN, 0.6)
    c.polyline([(m, m + length), (m, m), (m + length, m)], col, lw)
    c.polyline([(W - m - length, H - m), (W - m, H - m), (W - m, H - m - length)], col, lw)


def corner_bug(meta: EpisodeMeta) -> Image.Image:
    """Persistent top-right watermark (transparent)."""
    c = Canvas()
    ep = meta.ep_tag
    mark_s, pad, gap = 54, 18, 14
    a_w = c.measure("COTW", "display", 700, 30, 1)
    b_w = c.measure(ep, "mono", 500, 17, 3)
    box_w = pad + mark_s + gap + max(a_w, b_w) + pad
    box_h = 92
    bx, by = W - 72 - box_w, 64
    c.rect(bx, by, box_w, box_h, color("#080C12", 0.78), radius=14)
    c.outline(bx, by, box_w, box_h, color("#FFFFFF", 0.18), 1.5, radius=14)
    _mark(c, bx + pad, by + (box_h - mark_s) / 2, mark_s)
    tx = bx + pad + mark_s + gap
    c.text("COTW", tx, by + 42, family="display", weight=700, size=30, ls=1, fill=color(WHITE), upper=True)
    c.text(ep, tx, by + 70, family="mono", weight=500, size=17, ls=3, fill=color(MUTED2), upper=True)
    c.rect(tx, by + box_h - pad - 2, 22, 3, color(GOLD))
    return c.finish()


def lower_third(player: str, team: str, stat: str, context: str) -> Image.Image:
    """Player name plate (transparent). The corner bug is a separate overlay."""
    c = Canvas()
    origin_x, box_h, bottom_m = 96, 156, 150
    box_top = H - bottom_m - box_h
    name_size = 66
    name_w = c.measure(player or "player", "display", 700, name_size)

    parts = [p for p in (team, stat, context) if p]
    segs: list[tuple[str, str]] = []
    for i, p in enumerate(parts):
        if i:
            segs.append(("   ·   ", MUTED2))
        segs.append((p.upper(), GOLD if p == stat else (WHITE if i == 0 else MUTED)))
    sub_size, sub_ls = 23, 3
    sub_w = sum(c.measure(t, "mono", 500, sub_size, sub_ls) for t, _ in segs)

    pad_l, pad_r, spine = 34, 40, 8
    body_w = max(name_w, sub_w) + pad_l + pad_r
    body_x = origin_x + spine
    c.rect(origin_x, box_top, spine, box_h, color(CYAN))
    c.rect(body_x, box_top, body_w, box_h, color("#080C12", 0.84))
    c.outline(body_x, box_top, body_w, box_h, color("#FFFFFF", 0.14), 1.5)
    c.text(player or "player", body_x + pad_l, box_top + 86, family="display", weight=700, size=name_size, fill=color(WHITE))
    sx = body_x + pad_l
    for t, col in segs:
        c.text(t, sx, box_top + 128, family="mono", weight=500, size=sub_size, ls=sub_ls, fill=color(col))
        sx += c.measure(t, "mono", 500, sub_size, sub_ls)
    return c.finish()


def title_card(meta: EpisodeMeta, style: str = "center") -> Image.Image:
    c = Canvas()
    _bg_full(c)
    parts = [meta.ep_tag]
    if meta.week:
        parts.append(f"Week of {meta.week}")
    if meta.featuring:
        parts.append(meta.featuring)
    if meta.league:
        parts.append(meta.league)
    meta_line = "    ·    ".join(parts)

    if style == "center":
        _corner_ticks(c, 40, 54)
        cx = W / 2
        _mark(c, cx - 52, 170, 104)
        c.text("LANChamp Presents", cx, 360, family="mono", weight=500, size=24, ls=9, fill=color(CYAN), align="center", upper=True)
        c.text("CLIPS OF THE", cx, 520, family="display", weight=700, size=118, ls=1, fill=color(WHITE), align="center", upper=True)
        c.text("WEEK", cx, 690, family="display", weight=700, size=170, ls=2, fill=color(GOLD), align="center", upper=True)
        c.polyline([(cx - 260, 760), (cx + 260, 760)], color("#FFFFFF", 0.14), 1.5, round_caps=False)
        c.circle(cx, 760, 4, color(CYAN))
        ms = c.fit_size(meta_line.upper(), "mono", 500, 26, 4, W - 360)
        c.text(meta_line, cx, 826, family="mono", weight=500, size=ms, ls=4, fill=color(MUTED), align="center", upper=True)
    else:
        c.rect(0, 0, W, 130, color("#000000", 0.78))
        c.rect(0, H - 130, W, 130, color("#000000", 0.78))
        c.rect(0, 130, W, 3, color(CYAN))
        c.rect(0, H - 133, W, 3, color(CYAN))
        lx = 140
        _mark(c, lx, 300, 86)
        c.text("LANChamp Presents", lx + 120, 340, family="mono", weight=500, size=22, ls=8, fill=color(CYAN), upper=True)
        c.text("Clips of the", lx, 520, family="display", weight=700, size=120, ls=-1, fill=color(WHITE), upper=True)
        c.text("Week", lx, 650, family="display", weight=700, size=120, ls=-1, fill=color(GOLD), upper=True)
        c.rect(lx, 700, 120, 4, color(CYAN))
        ms = c.fit_size(meta_line.upper(), "mono", 500, 26, 4, W - lx - 140)
        c.text(meta_line, lx, 760, family="mono", weight=500, size=ms, ls=4, fill=color(MUTED), upper=True)
    return c.finish()


def end_card(meta: EpisodeMeta) -> Image.Image:
    c = Canvas()
    _bg_full(c)
    _corner_ticks(c, 40, 54)
    cx = W / 2
    _mark(c, cx - 40, 150, 80)
    c.text("SAME SQUAD, SAME TIME", cx, 420, family="display", weight=700, size=88, ls=1, fill=color(WHITE), align="center", upper=True)
    c.text("NEXT WEEK", cx, 540, family="display", weight=700, size=118, ls=2, fill=color(GOLD), align="center", upper=True)
    c.text(f"New clips every week · {meta.channel}", cx, 620, family="mono", weight=500, size=26, ls=6, fill=color(MUTED), align="center", upper=True)
    # Space Grotesk has no play-triangle glyph (the browser falls back to a system font), so draw it.
    tri_w, tri_h, tri_gap = 26, 30, 22
    label_w = c.measure("SUBSCRIBE", "display", 700, 38, 1)
    bw, bh = tri_w + tri_gap + label_w + 72, 84
    bx = cx - bw / 2
    c.rect(bx, 700, bw, bh, color(CYAN), radius=12)
    tx = bx + 36
    with c.layer((tx, 700, tx + tri_w, 700 + bh)) as lay:
        top = 700 + (bh - tri_h) / 2
        lay.polygon([(tx, top), (tx + tri_w, top + tri_h / 2), (tx, top + tri_h)], color("#041018"))
    c.text("SUBSCRIBE", tx + tri_w + tri_gap, 700 + 54, family="display", weight=700, size=38, ls=1, fill=color("#041018"))
    if meta.next_up:
        c.text("Next up", W - 96, H - 150, family="mono", weight=500, size=22, ls=5, fill=color(MUTED2), align="right", upper=True)
        c.text(meta.next_up, W - 96, H - 104, family="display", weight=700, size=46, fill=color(WHITE), align="right", upper=True)
    return c.finish()


# ---- stat punch ----

def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _seg(p: float, a: float, b: float) -> float:
    return _clamp01((p - a) / (b - a))


def _ease_out_cubic(t: float) -> float:
    return 1 - (1 - t) ** 3


def _ease_out_back(t: float) -> float:
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def _mix(a: str, b: str, t: float) -> tuple[int, int, int, int]:
    ca, cb = color(a), color(b)
    return (*(round(ca[i] + (cb[i] - ca[i]) * t) for i in range(3)), 255)


def stat_frame(label: str, value: str, context: str, p: float) -> Image.Image:
    """One frame of the stat punch; p is reveal progress 0..1 (transparent background)."""
    c = Canvas()
    cx, cy = W / 2, H / 2

    fp = _ease_out_cubic(_seg(p, 0, 0.38))
    half, fa = (H * 0.3) * fp, 0.55 * fp
    if half > 1:
        for fx in (W * 0.34, W * 0.66):
            c.vgradient_line(fx, cy - half, cy + half, color(CYAN), fa)

    if label:
        lp = _ease_out_cubic(_seg(p, 0.10, 0.46))
        if lp > 0:
            c.text(label, cx, cy - 150 + (1 - lp) * -26, family="mono", weight=600, size=40,
                   ls=22 + (1 - lp) * 34, fill=color(WHITE, lp), align="center", upper=True)

    impact = _clamp01(1 - abs(p - 0.5) / 0.17)
    na = _ease_out_cubic(_seg(p, 0.18, 0.42))
    scale = 0.35 + _ease_out_back(_clamp01(_seg(p, 0.18, 0.64))) * 0.65
    if na > 0:
        v_size = c.fit_size(value.upper(), "display", 700, 300, 0, W * 0.55)
        col = _mix(GOLD, "#FFFFFF", (impact - 0.55) / 0.45) if impact > 0.55 else color(GOLD)
        col = (*col[:3], round(na * 255))
        shadow = color(GOLD, (0.3 + impact * 0.45) * na)
        c.text(value, cx, cy - 10 + v_size * 0.35 * scale, family="display", weight=700,
               size=v_size * scale, fill=col, align="center", upper=True, glow=(shadow, 22 + impact * 78))

    sp = _seg(p, 0.42, 0.64)
    if 0 < sp < 1:
        y = H * 0.22 + sp * (H * 0.56)
        c.vband(W * 0.2, y - 46, W * 0.6, 92, color(CYAN), math.sin(sp * math.pi) * 0.5)

    if context:
        cp = _ease_out_cubic(_seg(p, 0.56, 0.84))
        if cp > 0:
            c.text(context, cx, cy + 200 + (1 - cp) * 16, family="mono", weight=500, size=28, ls=8,
                   fill=color(MUTED, cp), align="center", upper=True)
    return c.finish()


def stat_frame_count(fps: int) -> int:
    return max(1, round(STAT_TOTAL * fps))


def stat_progress(index: int, fps: int) -> float:
    return min(1.0, (index / fps) / STAT_DUR)
