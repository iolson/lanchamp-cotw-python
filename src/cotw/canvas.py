"""Small 2D drawing layer over Pillow that mimics the canvas calls used by the COTW web tool.

Everything is drawn in logical 1920x1080 coordinates at `scale`x resolution and
downsampled in `finish()`, which gives anti-aliased shapes (ImageDraw has none).
Each primitive is drawn on its own transparent layer and alpha-composited, so
semi-transparent colours blend correctly.
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT_DIR = Path(__file__).parent / "fonts"
FONT_FILES = {
    "display": "SpaceGrotesk.ttf",
    "mono": "GeistMono.ttf",
    "sans": "Inter.ttf",
}

RGBA = tuple[int, int, int, int]


def color(hex_str: str, alpha: float = 1.0) -> RGBA:
    h = hex_str.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), round(max(0.0, min(1.0, alpha)) * 255)


@lru_cache(maxsize=256)
def _font(family: str, weight: int, px: float) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(FONT_DIR / FONT_FILES[family]), px)
    font.set_variation_by_axes([weight])  # every bundled font has a single wght axis
    return font


class Layer:
    """A transparent patch covering `box` (logical coords); draw methods take logical coords."""

    def __init__(self, canvas: Canvas, box: tuple[float, float, float, float], seed: RGBA = (0, 0, 0, 0)):
        s = canvas.scale
        self.canvas = canvas
        self.x0 = max(0, math.floor(box[0] * s))
        self.y0 = max(0, math.floor(box[1] * s))
        x1 = min(canvas.img.width, math.ceil(box[2] * s))
        y1 = min(canvas.img.height, math.ceil(box[3] * s))
        self.empty = x1 <= self.x0 or y1 <= self.y0
        self.img = Image.new("RGBA", (max(1, x1 - self.x0), max(1, y1 - self.y0)), (*seed[:3], 0))
        self.draw = ImageDraw.Draw(self.img)

    def _p(self, x: float, y: float) -> tuple[float, float]:
        s = self.canvas.scale
        return x * s - self.x0, y * s - self.y0

    def rect(self, x: float, y: float, w: float, h: float, fill: RGBA, radius: float = 0) -> None:
        a, b = self._p(x, y), self._p(x + w, y + h)
        if radius:
            self.draw.rounded_rectangle([a, (b[0] - 1, b[1] - 1)], radius * self.canvas.scale, fill=fill)
        else:
            self.draw.rectangle([a, (b[0] - 1, b[1] - 1)], fill=fill)

    def outline(self, x: float, y: float, w: float, h: float, line: RGBA, lw: float, radius: float = 0) -> None:
        # Canvas strokes are centred on the path edge; grow the box by half the width.
        half = lw / 2
        a, b = self._p(x - half, y - half), self._p(x + w + half, y + h + half)
        self.draw.rounded_rectangle(
            [a, (b[0] - 1, b[1] - 1)],
            (radius + half) * self.canvas.scale,
            outline=line,
            width=max(1, round(lw * self.canvas.scale)),
        )

    def polygon(self, pts: list[tuple[float, float]], fill: RGBA) -> None:
        self.draw.polygon([self._p(*p) for p in pts], fill=fill)

    def polyline(self, pts: list[tuple[float, float]], line: RGBA, lw: float, round_caps: bool = True) -> None:
        s = self.canvas.scale
        px = [self._p(*p) for p in pts]
        self.draw.line(px, fill=line, width=max(1, round(lw * s)), joint="curve")
        if round_caps:
            r = lw * s / 2
            for cx, cy in (px[0], px[-1]):
                self.draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=line)

    def circle(self, cx: float, cy: float, r: float, fill: RGBA) -> None:
        (px, py), pr = self._p(cx, cy), r * self.canvas.scale
        self.draw.ellipse([px - pr, py - pr, px + pr, py + pr], fill=fill)


class Canvas:
    def __init__(self, width: int = 1920, height: int = 1080, scale: int = 2):
        self.w, self.h, self.scale = width, height, scale
        self.img = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))

    # ---- compositing ----
    @contextmanager
    def layer(self, box: tuple[float, float, float, float], seed: RGBA = (0, 0, 0, 0)):
        lay = Layer(self, box, seed)
        yield lay
        if not lay.empty:
            self._composite(lay.img, lay.x0, lay.y0)

    def _composite(self, patch: Image.Image, x0: int, y0: int, blur: float = 0) -> None:
        if blur:
            pad = math.ceil(blur * 3)
            grown = Image.new("RGBA", (patch.width + 2 * pad, patch.height + 2 * pad), (*patch.getpixel((0, 0))[:3], 0))
            grown.paste(patch, (pad, pad))
            patch, x0, y0 = grown.filter(ImageFilter.GaussianBlur(blur)), x0 - pad, y0 - pad
        # alpha_composite needs a non-negative dest and a source that fits; crop to the canvas.
        sx, sy = max(0, -x0), max(0, -y0)
        dx, dy = max(0, x0), max(0, y0)
        w = min(patch.width - sx, self.img.width - dx)
        h = min(patch.height - sy, self.img.height - dy)
        if w > 0 and h > 0:
            self.img.alpha_composite(patch, (dx, dy), (sx, sy, sx + w, sy + h))

    # ---- shapes ----
    def fill(self, c: RGBA) -> None:
        self._composite(Image.new("RGBA", self.img.size, c), 0, 0)

    def rect(self, x: float, y: float, w: float, h: float, fill: RGBA, radius: float = 0) -> None:
        with self.layer((x, y, x + w, y + h)) as lay:
            lay.rect(x, y, w, h, fill, radius)

    def outline(self, x: float, y: float, w: float, h: float, line: RGBA, lw: float = 1.5, radius: float = 0) -> None:
        with self.layer((x - lw, y - lw, x + w + lw, y + h + lw)) as lay:
            lay.outline(x, y, w, h, line, lw, radius)

    def circle(self, cx: float, cy: float, r: float, fill: RGBA) -> None:
        with self.layer((cx - r - 1, cy - r - 1, cx + r + 1, cy + r + 1)) as lay:
            lay.circle(cx, cy, r, fill)

    def dots(self, pts: list[tuple[float, float]], r: float, fill: RGBA) -> None:
        with self.layer((0, 0, self.w, self.h)) as lay:
            for cx, cy in pts:
                lay.circle(cx, cy, r, fill)

    def polyline(self, pts: list[tuple[float, float]], line: RGBA, lw: float, round_caps: bool = True) -> None:
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        m = lw + 1
        with self.layer((min(xs) - m, min(ys) - m, max(xs) + m, max(ys) + m)) as lay:
            lay.polyline(pts, line, lw, round_caps)

    def vgradient_line(self, x: float, y0: float, y1: float, rgb: RGBA, peak_alpha: float, lw: float = 2) -> None:
        """Vertical line fading 0 -> peak -> 0 along its length (canvas linear gradient)."""
        with self.layer((x - lw, y0, x + lw, y1), seed=rgb) as lay:
            rows = lay.img.height
            cx, half = lay.img.width / 2, lw * self.scale / 2
            for r in range(rows):
                t = r / max(1, rows - 1)
                a = (1 - abs(t - 0.5) * 2) * peak_alpha
                lay.draw.rectangle([cx - half, r, cx + half - 1, r], fill=(*rgb[:3], round(a * 255)))

    def vband(self, x: float, y: float, w: float, h: float, rgb: RGBA, peak_alpha: float) -> None:
        """Horizontal band fading 0 -> peak -> 0 top to bottom (the stat 'scan flash')."""
        with self.layer((x, y, x + w, y + h), seed=rgb) as lay:
            rows = lay.img.height
            for r in range(rows):
                t = r / max(1, rows - 1)
                a = (1 - abs(t - 0.5) * 2) * peak_alpha
                lay.draw.line([(0, r), (lay.img.width, r)], fill=(*rgb[:3], round(a * 255)))

    def radial(
        self, cx: float, cy: float, r0: float, r1: float, c0: RGBA, c1: RGBA, down: int = 4
    ) -> None:
        """Radial gradient c0 (inside r0) -> c1 (outside r1), like createRadialGradient on one centre.

        Built at 1/`down` resolution (gradients are smooth) and upscaled just where it lands.
        """
        s = self.scale
        tile_px = max(2, math.ceil(2 * r1 * s / down))
        # Pillow's gradient is 0 at the centre and 255 at the tile *corners* (distance r1 * sqrt 2).
        dist = Image.radial_gradient("L").resize((tile_px, tile_px), Image.BILINEAR)

        def lut(i: int) -> list[int]:
            out = []
            for v in range(256):
                d = v / 255 * r1 * math.sqrt(2)
                t = 0.0 if d <= r0 else min(1.0, (d - r0) / (r1 - r0))
                out.append(round(c0[i] + (c1[i] - c0[i]) * t))
            return out

        tile = Image.merge("RGBA", [dist.point(lut(i)) for i in range(4)])
        # Destination rect in canvas px, clipped; source rect in tile px.
        ox, oy = (cx - r1) * s, (cy - r1) * s
        dx0, dy0 = max(0, math.floor(ox)), max(0, math.floor(oy))
        dx1 = min(self.img.width, math.ceil(ox + 2 * r1 * s))
        dy1 = min(self.img.height, math.ceil(oy + 2 * r1 * s))
        if dx1 <= dx0 or dy1 <= dy0:
            return
        k = tile_px / (2 * r1 * s)
        box = (
            max(0.0, (dx0 - ox) * k),
            max(0.0, (dy0 - oy) * k),
            min(float(tile_px), (dx1 - ox) * k),
            min(float(tile_px), (dy1 - oy) * k),
        )
        patch = tile.resize((dx1 - dx0, dy1 - dy0), Image.BICUBIC, box=box)
        # Beyond r1 the gradient is c1; every caller's c1 is transparent or fully covered by the tile.
        self._composite(patch, dx0, dy0)

    # ---- text ----
    def measure(self, text: str, family: str, weight: int, size: float, ls: float = 0.0) -> float:
        font = _font(family, weight, size * self.scale)
        if not ls:
            return font.getlength(text) / self.scale
        return (sum(font.getlength(ch) for ch in text) + ls * self.scale * len(text)) / self.scale

    def fit_size(self, text: str, family: str, weight: int, size: float, ls: float, max_w: float) -> float:
        while size > 12 and self.measure(text, family, weight, size, ls) > max_w:
            size -= 2
        return size

    def text(
        self,
        text: str,
        x: float,
        y: float,
        *,
        family: str = "sans",
        weight: int = 400,
        size: float = 40,
        ls: float = 0.0,
        fill: RGBA = (247, 250, 252, 255),
        align: str = "left",
        upper: bool = False,
        glow: tuple[RGBA, float] | None = None,
    ) -> None:
        """Draw text with its baseline at y. `glow` = (shadow colour, blur) like canvas shadowBlur."""
        if upper:
            text = text.upper()
        s = self.scale
        w = self.measure(text, family, weight, size, ls)
        left = x - (w / 2 if align == "center" else w if align == "right" else 0)
        font = _font(family, weight, size * s)
        pad = size * 0.6 + (glow[1] * 2 if glow else 0)
        box = (left - pad, y - size * 1.2, left + w + pad, y + size * 0.6)

        def paint(lay: Layer, col: RGBA) -> None:
            px, py = lay._p(left, y)
            if not ls:
                lay.draw.text((px, py), text, font=font, fill=col, anchor="ls")
                return
            for ch in text:
                lay.draw.text((px, py), ch, font=font, fill=col, anchor="ls")
                px += font.getlength(ch) + ls * s

        if glow:
            gcol, blur = glow
            lay = Layer(self, box, seed=gcol)
            if not lay.empty:
                paint(lay, gcol)
                self._composite(lay.img, lay.x0, lay.y0, blur=blur / 2 * s)  # canvas sigma = blur / 2
        with self.layer(box, seed=fill) as lay:
            paint(lay, fill)

    # ---- output ----
    def finish(self) -> Image.Image:
        if self.scale == 1:
            return self.img
        return self.img.resize((self.w, self.h), Image.LANCZOS)
