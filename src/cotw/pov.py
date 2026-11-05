"""Find the parts of a highlights video that are not a player's first-person view (cinematic cutaways, free camera,
intros) by asking a local vision model about one frame per second, so a frag edit can leave them out."""

from __future__ import annotations

import base64
import json
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from cotw.ai import AiError, _first_model, _post

QUESTION = (
    "This is one frame from a Counter-Strike 2 highlight video. Answer with exactly one word: 'pov' if it is the "
    "first-person view of a player (their weapon or hands and the game HUD are visible), otherwise 'other' "
    "(cinematic or free camera, third-person, intro or title, scoreboard, menu, black screen, replay overlay)."
)
PAD = 0.3  # seconds of margin kept around every flagged frame

Range = tuple[float, float]


def _is_pov(base_url: str, model: str, frame: Path, timeout: float) -> bool:
    url = "data:image/jpeg;base64," + base64.b64encode(frame.read_bytes()).decode()
    reply = _post(base_url + "/chat/completions", {
        "model": model, "max_tokens": 8, "temperature": 0, "chat_template_kwargs": {"enable_thinking": False},
        "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}},
                                                  {"type": "text", "text": QUESTION}]}],
    }, timeout)
    try:
        return str(reply["choices"][0]["message"]["content"]).strip().lower().startswith("pov")
    except (KeyError, IndexError, TypeError) as exc:
        raise AiError(f"unexpected reply from the model: {reply!r}"[:300]) from exc


def scan(ffmpeg: Path, video: Path, base_url: str, model: str | None = None, step: float = 1.0,
         workers: int = 8, timeout: float = 60.0) -> list[bool]:
    """True/False (first-person or not) for the frame at every `step` seconds of `video`; cached next to it."""
    cache = video.with_suffix(".pov.json")
    stamp = {"size": video.stat().st_size, "mtime": int(video.stat().st_mtime), "step": step}
    try:
        saved = json.loads(cache.read_text())
        if saved.get("stamp") == stamp:
            return saved["samples"]
    except (OSError, ValueError, KeyError):
        pass
    model = model or _first_model(base_url, timeout)
    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run([str(ffmpeg), "-v", "error", "-i", str(video), "-vf", f"fps=1/{step},scale=480:-1",
                                 "-q:v", "4", str(Path(tmp) / "f%05d.jpg")], capture_output=True, text=True)
        if result.returncode:
            raise AiError(f"cannot sample frames from {video.name}: {result.stderr.strip()[-300:]}")
        frames = sorted(Path(tmp).glob("f*.jpg"))
        with ThreadPoolExecutor(workers) as pool:
            samples = list(pool.map(lambda f: _is_pov(base_url, model, f, timeout), frames))
    try:
        cache.write_text(json.dumps({"stamp": stamp, "samples": samples}))
    except OSError:
        pass
    return samples


def bad_ranges(samples: list[bool], step: float = 1.0) -> list[Range]:
    """Source-second ranges to avoid: every non-first-person frame plus a margin, overlaps merged."""
    out: list[Range] = []
    for i, pov in enumerate(samples):
        if pov:
            continue
        a, b = max(0.0, i * step - PAD), (i + 1) * step + PAD
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def good_islands(lo: float, hi: float, bad: list[Range]) -> list[Range]:
    """The parts of [lo, hi] left once the `bad` ranges are taken out."""
    out, cursor = [], lo
    for a, b in sorted(bad):
        if a > cursor:
            out.append((cursor, min(a, hi)))
        cursor = max(cursor, b)
        if cursor >= hi:
            break
    if cursor < hi:
        out.append((cursor, hi))
    return [(a, b) for a, b in out if b > a]
