"""YouTube thumbnails: a real frame from a standout clip, one big callout, and the LANChamp mark and
episode number in the same corner every week. Only the frame, the accent colour and the text change."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

from PIL import Image, ImageChops

from cotw.canvas import Canvas, color
from cotw.compose import FfmpegError
from cotw.graphics import CYAN, GOLD, H, INK, MUTED, W, WHITE, EpisodeMeta, _mark

ACCENTS = (CYAN, GOLD, "#FF4D5A", "#7CFF6B")  # rotates with the episode number
OUT_SIZE = (1280, 720)  # YouTube's recommended size; JPEG keeps it under the 2 MB limit


def accent_for(episode: int) -> str:
    return ACCENTS[(episode - 1) % len(ACCENTS)]


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


def _shade() -> Image.Image:
    """Dark gradients from the left and the bottom so text reads on any frame."""
    ramp_x = Image.linear_gradient("L").rotate(90).resize((W, H))  # white left -> black right
    ramp_y = Image.linear_gradient("L").resize((W, H))  # black top -> white bottom
    left = ramp_x.point(lambda v: int(v * 0.78))
    bottom = ramp_y.point(lambda v: max(0, int((v - 110) * 0.9)))
    alpha = ImageChops.lighter(left, bottom)
    shade = Image.new("RGBA", (W, H), color(INK))
    shade.putalpha(alpha)
    return shade


def render(frame: Image.Image, meta: EpisodeMeta, text: str, player: str = "", team: str = "",
           accent: str | None = None) -> Image.Image:
    """The 1920x1080 thumbnail; `text` is the callout (e.g. "1V5?!")."""
    accent = accent or accent_for(meta.episode)
    base = _cover(frame).convert("RGBA")
    base.alpha_composite(_shade())

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
    size = c.fit_size(text.upper(), "display", 700, 330, 0, W * 0.62)
    base_y = H - 190
    c.text(text, 84, base_y, family="display", weight=700, size=size, fill=color(WHITE), upper=True,
           glow=(color(accent, 0.75), 46))
    c.rect(88, base_y + 26, 220, 10, color(accent))
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
