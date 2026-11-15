"""YouTube thumbnails: a real frame from a standout clip, one big callout, and the LANChamp mark and
episode number in the same corner every week. Only the frame and the text change; the colours are the
brand's (white type, cyan accent, a gold tick), never a per-episode accent."""

from __future__ import annotations

import io
import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageStat

from cotw.canvas import Canvas, color
from cotw.compose import FfmpegError
from cotw.graphics import CYAN, GOLD, H, INK, MUTED, W, WHITE, EpisodeMeta, _mark

ACCENT = CYAN  # the brand's primary accent; gold stays a small secondary touch (brand guidelines §4)
OUT_SIZE = (1280, 720)  # YouTube's recommended size; JPEG keeps it under the 2 MB limit


def accent_for(episode: int) -> str:
    """The thumbnail accent. It used to rotate per episode through off-brand red and green; now always cyan."""
    return ACCENT


def extract_frame(ffmpeg: Path, video: Path, seconds: float) -> Image.Image:
    """One full-resolution frame from `video` at `seconds`."""
    cmd = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, seconds):.3f}", "-i", str(video),
           "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0 or not result.stdout:
        raise FfmpegError(f"could not grab a frame at {seconds:.1f}s from {video}: {result.stderr.decode(errors='replace')}")
    return Image.open(io.BytesIO(result.stdout)).convert("RGB")


def _cover(img: Image.Image) -> Image.Image:
    """Scale and centre-crop to fill 1920x1080."""
    k = max(W / img.width, H / img.height)
    img = img.resize((round(img.width * k), round(img.height * k)), Image.LANCZOS)
    x, y = (img.width - W) // 2, (img.height - H) // 2
    return img.crop((x, y, x + W, y + H))


def _brightness(img: Image.Image, box: tuple[int, int, int, int]) -> float:
    """Mean luminance (0..1) of `box` in `img`."""
    return ImageStat.Stat(img.crop(box).convert("L")).mean[0] / 255


def _shade(frame: Image.Image | None = None) -> Image.Image:
    """Dark gradients from the left and the bottom, and behind the branding corner, so text reads on any
    frame; they get stronger where the frame is bright (sky, white walls)."""
    callout = _brightness(frame, (0, H // 2, int(W * 0.65), H)) if frame else 0.0
    corner = _brightness(frame, (0, 0, 760, 240)) if frame else 0.0
    k = 0.78 + 0.2 * min(1.0, max(0.0, (callout - 0.3) / 0.3))
    ramp_x = Image.linear_gradient("L").rotate(90).resize((W, H))  # white left -> black right
    ramp_y = Image.linear_gradient("L").resize((W, H))  # black top -> white bottom
    left = ramp_x.point(lambda v: int(v * k))
    bottom = ramp_y.point(lambda v: max(0, min(255, int((v - 110) * (0.9 + (k - 0.78) * 2)))))
    alpha = ImageChops.lighter(left, bottom)
    if corner > 0.3:  # a soft dark patch under the logo and "Clips of the Week" line
        patch = Image.new("L", (W, H), 0)
        patch.paste(int(min(1.0, corner + 0.35) * 230), (0, 0, 820, 230))
        alpha = ImageChops.lighter(alpha, patch.filter(ImageFilter.GaussianBlur(70)))
    shade = Image.new("RGBA", (W, H), color(INK))
    shade.putalpha(alpha)
    return shade


def find_blender(explicit: Path | None = None) -> Path | None:
    """--blender, $BLENDER, `blender` on PATH, then the macOS app; None when there is none."""
    for c in (explicit, os.environ.get("BLENDER"), shutil.which("blender"),
              "/Applications/Blender.app/Contents/MacOS/Blender"):
        if c and Path(c).is_file():
            return Path(c)
    return None


STYLES = ("classic", "punch")
PUNCH_FACE, PUNCH_SIDE = GOLD, "#11425B"  # brand gold letters on brand blue-deep sides


def render_callouts(blender: Path, items: list[tuple[str, str]], face: str = WHITE) -> list[Image.Image]:
    """Extruded 3D renders of each (text, side colour) from Blender with `face` letter fronts, cropped to the
    text (RGBA)."""
    with tempfile.TemporaryDirectory(prefix="cotw-3d-") as tmp:
        jobs = [{"text": t.upper(), "accent": a, "face": face, "out": str(Path(tmp) / f"{i}.png")}
                for i, (t, a) in enumerate(items)]
        job = Path(tmp) / "job.json"
        job.write_text(json.dumps({"font": str(Path(__file__).parent / "fonts" / "SpaceGrotesk-Bold.ttf"),
                                   "jobs": jobs}), encoding="utf-8")
        result = subprocess.run([str(blender), "-b", "--factory-startup", "--python",
                                 str(Path(__file__).parent / "blender_text.py"), "--", str(job)],
                                capture_output=True, text=True)
        out = []
        for j in jobs:
            if not Path(j["out"]).is_file():
                raise FfmpegError(f"Blender did not render {j['text']!r}: {result.stderr.strip()[-400:]}")
            img = Image.open(j["out"]).convert("RGBA")
            out.append(img.crop(img.getchannel("A").getbbox()))
        return out


def _place_3d(base: Image.Image, img: Image.Image, accent: str, x: int, bottom: int) -> None:
    """Scale the 3D callout into the callout box and lay it on `base` with a drop shadow and accent glow."""
    k = min(W * 0.62 / img.width, 330 / img.height)
    img = img.resize((round(img.width * k), round(img.height * k)), Image.LANCZOS)
    y = bottom - img.height
    a = img.getchannel("A")
    for fill, blur, dx, dy, strength in ((INK, 26, 10, 16, 0.85), (accent, 44, 0, 0, 0.6)):
        pad = blur * 3
        mask = Image.new("L", (img.width + 2 * pad, img.height + 2 * pad), 0)
        mask.paste(a, (pad, pad))
        mask = mask.filter(ImageFilter.GaussianBlur(blur)).point(lambda v: int(v * strength))
        layer = Image.new("RGBA", mask.size, color(fill))
        layer.putalpha(mask)
        base.alpha_composite(layer, (x - pad + dx, y - pad + dy))
    base.alpha_composite(img, (x, y))


def render(frame: Image.Image, meta: EpisodeMeta, text: str, player: str = "", team: str = "",
           accent: str | None = None, callout: Image.Image | None = None) -> Image.Image:
    """The 1920x1080 thumbnail; `text` is the callout (e.g. "1V5?!"). `callout` is a 3D render of it from
    `render_callouts`, used instead of flat text."""
    accent = accent or accent_for(meta.episode)
    base = _cover(frame).convert("RGBA")
    base.alpha_composite(_shade(_cover(frame)))

    c = Canvas()
    # fixed branding corner: the same place and size every episode
    mark_s, bx, by = 74, 64, 56
    _mark(c, bx, by, mark_s)
    c.text("LANChamp", bx + mark_s + 18, by + 36, family="display", weight=700, size=36, ls=1, fill=color(WHITE),
           upper=True)
    c.text(f"Clips of the Week · {meta.ep_tag}", bx + mark_s + 18, by + 68, family="mono", weight=500, size=20, ls=3,
           fill=color(MUTED), upper=True)
    c.rect(bx + mark_s + 18, by + 82, 64, 5, color(accent))

    # callout, bottom left
    base_y = H - 190
    if callout is not None:
        _place_3d(base, callout, accent, 70, base_y + 14)
    else:
        size = c.fit_size(text.upper(), "display", 700, 330, 0, W * 0.62)
        c.text(text, 84, base_y, family="display", weight=700, size=size, fill=color(WHITE), upper=True,
               glow=(color(accent, 0.75), 46))
    c.rect(88, base_y + 26, 168, 10, color(accent))  # cyan + gold, the brand's scoreboard line
    c.rect(262, base_y + 26, 52, 10, color(GOLD))
    sub = " · ".join(p for p in (player, team) if p)
    if sub:
        ss = c.fit_size(sub.upper(), "mono", 600, 40, 5, W * 0.62)
        c.text(sub, 88, base_y + 96, family="mono", weight=600, size=ss, ls=5, fill=color(GOLD), upper=True)
    base.alpha_composite(c.finish())
    return base


def save(img: Image.Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").resize(OUT_SIZE, Image.LANCZOS).save(path, quality=92, optimize=True)
    return path


def _outline(img: Image.Image, width: int, fill: str) -> Image.Image:
    """`img` on a thick rounded outline of `fill` (the sticker edge highlight thumbnails use)."""
    pad = width + 4
    a = Image.new("L", (img.width + 2 * pad, img.height + 2 * pad), 0)
    a.paste(img.getchannel("A"), (pad, pad))
    grown = a.filter(ImageFilter.MaxFilter(2 * (width // 2) + 1))
    if width > 12:  # MaxFilter sizes are capped in practice; grow in two steps for thick edges
        grown = grown.filter(ImageFilter.MaxFilter(2 * ((width - width // 2) // 2) + 1))
    edge = Image.new("RGBA", a.size, color(fill))
    edge.putalpha(grown.filter(ImageFilter.GaussianBlur(1.2)))
    edge.alpha_composite(img, (pad, pad))
    return edge


def _arrow(tail: tuple[float, float], tip: tuple[float, float], fill: str, edge: str) -> Image.Image:
    """A fat arrow from `tail` to `tip` with a dark edge and a drop shadow, on a transparent 1920x1080 layer."""
    (x0, y0), (x1, y1) = tail, tip
    ang = math.atan2(y1 - y0, x1 - x0)
    length = math.hypot(x1 - x0, y1 - y0)
    shaft, head_w, head_l = 48, 142, 124

    def pt(along: float, side: float) -> tuple[float, float]:
        return (x0 + along * math.cos(ang) - side * math.sin(ang), y0 + along * math.sin(ang) + side * math.cos(ang))

    poly = [pt(0, -shaft / 2), pt(length - head_l, -shaft / 2), pt(length - head_l, -head_w / 2), pt(length, 0),
            pt(length - head_l, head_w / 2), pt(length - head_l, shaft / 2), pt(0, shaft / 2)]
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(shadow).polygon([(x + 10, y + 14) for x, y in poly], fill=170)
    sh = Image.new("RGBA", (W, H), color(INK))
    sh.putalpha(shadow.filter(ImageFilter.GaussianBlur(14)))
    layer.alpha_composite(sh)
    d = ImageDraw.Draw(layer)
    d.polygon(poly, fill=color(fill), outline=color(edge), width=10)
    return layer


def render_punch(frame: Image.Image, meta: EpisodeMeta, text: str, player: str = "", team: str = "",
                 callout: Image.Image | None = None, arrow: bool = True) -> Image.Image:
    """The punchier thumbnail, in the shape of the most-viewed game highlight channels: a saturated frame,
    a huge gold callout top left with a thick dark edge, a gold arrow at the crosshair (the frame is the
    moment of the kill, so that is where the enemy is) and a compact series badge bottom left."""
    img = _cover(frame)
    img = ImageEnhance.Contrast(ImageEnhance.Color(img).enhance(1.3)).enhance(1.08)
    base = img.convert("RGBA")
    # a soft dark corner top left (callout) and along the bottom (badge), not the whole left half
    shade = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(shade)
    d.ellipse((-700, -520, 1150, 640), fill=150)
    d.rectangle((0, H - 210, W, H), fill=170)
    tint = Image.new("RGBA", (W, H), color(INK))
    tint.putalpha(shade.filter(ImageFilter.GaussianBlur(120)))
    base.alpha_composite(tint)

    box_w, box_h, x, y = W * 0.52, H * 0.40, 56, 44
    if callout is not None:
        k = min(box_w / callout.width, box_h / callout.height)
        art = _outline(callout.resize((round(callout.width * k), round(callout.height * k)), Image.LANCZOS), 18, INK)
    else:
        c = Canvas()
        size = c.fit_size(text.upper(), "display", 700, 400, 0, box_w)
        c.text(text, 40, 40 + size * 0.8, family="display", weight=700, size=size, fill=color(GOLD), upper=True)
        flat = c.finish()
        flat = flat.crop(flat.getchannel("A").getbbox())
        art = _outline(flat, 18, INK)
    base.alpha_composite(art, (x - 20, y - 20))
    if arrow:  # from just right of the callout towards the crosshair, stopping short of it
        cx, cy = W / 2, H / 2
        tail = (min(W * 0.62, x + art.width + 40), max(y + art.height * 0.55, H * 0.26))
        dx, dy = cx - tail[0], cy - tail[1]
        dist = max(1.0, (dx * dx + dy * dy) ** 0.5)
        if dist < 160:  # too close to aim: come in from above-right instead
            tail = (cx + 260, cy - 260)
            dx, dy, dist = cx - tail[0], cy - tail[1], 367.7
        stop = 70 / dist
        tip = (cx - dx * stop, cy - dy * stop)
        base.alpha_composite(_arrow(tail, tip, GOLD, INK))

    c = Canvas()  # series badge, bottom left
    bx, by, mark_s = 56, H - 150, 78
    c.rect(bx - 18, by - 16, 560, mark_s + 32, color(INK, 0.82), radius=14)
    _mark(c, bx, by, mark_s)
    c.text("Clips of the Week", bx + mark_s + 18, by + 40, family="display", weight=700, size=40, fill=color(WHITE),
           upper=True)
    c.text(f"LANChamp · #{meta.episode}", bx + mark_s + 18, by + 74, family="mono", weight=600, size=22, ls=4,
           fill=color(CYAN), upper=True)
    sub = " · ".join(p for p in (player, team) if p)
    if sub:
        ss = c.fit_size(sub.upper(), "mono", 700, 34, 4, W * 0.5)
        c.text(sub, W - 56, H - 70, family="mono", weight=700, size=ss, ls=4, fill=color(GOLD), upper=True,
               align="right", glow=(color(INK, 0.9), 12))
    base.alpha_composite(c.finish())
    return base
