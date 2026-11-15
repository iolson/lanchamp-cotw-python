"""YouTube thumbnails: a real frame from a standout clip, one big callout, and the LANChamp mark and
episode number in the same corner every week. Only the frame and the text change; the colours are the
brand's (white type, cyan accent, a gold tick), never a per-episode accent."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageStat

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


def render_callouts(blender: Path, items: list[tuple[str, str]]) -> list[Image.Image]:
    """Extruded 3D renders of each (text, accent colour) from Blender, cropped to the text (RGBA)."""
    with tempfile.TemporaryDirectory(prefix="cotw-3d-") as tmp:
        jobs = [{"text": t.upper(), "accent": a, "out": str(Path(tmp) / f"{i}.png")} for i, (t, a) in enumerate(items)]
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
