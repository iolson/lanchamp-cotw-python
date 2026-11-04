"""Ask a local OpenAI-compatible LLM (e.g. vLLM) to make editing choices: clip order and slow-motion picks.

The model only chooses clip numbers. Everything is validated here, and kill/beat timing stays in code.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from cotw.plan import ClipPlan, Timeline

SYSTEM = (
    "You are the editor of LANChamp Clips of the Week, a weekly Counter-Strike 2 highlights video for a "
    "league community. You decide clip order and which clips get slow motion. Only use the clip numbers you "
    "are given."
)

RULES = """Decide:
1. "order": every clip number once, in the order they should play. Open with a strong clip as a hook, keep
   the pacing varied (do not put weak clips in a row), never play two clips by the same player back to back
   when you can avoid it, and save the very best clip for last.
2. "slowmo": the clips whose final kill deserves slow motion. Be selective: only genuine signature moments
   (clutches, aces, 4Ks, special kills), at most a third of the clips, possibly none.
3. "reasons": one or two short sentences explaining the order and the slow-motion picks.
In the data, "score" is the highlight detector's rating (higher is better), "kills" lists when each kill
lands in seconds from the clip's start, and "seconds" is the clip length."""

SCHEMA = {
    "type": "object",
    "properties": {
        "order": {"type": "array", "items": {"type": "integer"}},
        "slowmo": {"type": "array", "items": {"type": "integer"}},
        "reasons": {"type": "string"},
    },
    "required": ["order", "slowmo", "reasons"],
}


class AiError(RuntimeError):
    pass


@dataclass
class EditPlan:
    order: list[int]
    slowmo: list[int]
    reasons: str = ""
    notes: list[str] = field(default_factory=list)  # things we corrected in the model's answer

    def to_json(self) -> dict:
        return {"order": self.order, "slowmo": self.slowmo, "reasons": self.reasons}


def clip_brief(clip: ClipPlan) -> dict:
    """What the model gets to see about a clip (display names only, no Steam IDs)."""
    return {
        "clip": clip.number,
        "player": clip.player,
        "stat": clip.stat,
        "detail": clip.detail,
        "context": clip.context,
        "score": round(clip.score, 2),
        "seconds": round(clip.src_len or clip.duration, 1),
        "kills": [{"at": round(t, 1), "headshot": hs, "special": sp} for t, hs, sp in clip.kills],
    }


def normalize_plan(raw: dict, numbers: list[int]) -> EditPlan:
    """Make a model answer (or a saved plan) safe: real clip numbers only, no repeats, nothing missing."""
    notes: list[str] = []
    valid = set(numbers)

    def clean(values, what: str) -> list[int]:
        out: list[int] = []
        for v in values if isinstance(values, list) else []:
            if isinstance(v, int) and v in valid and v not in out:
                out.append(v)
            else:
                notes.append(f"ignored {v!r} in {what}")
        return out

    order = clean(raw.get("order"), "order")
    for n in numbers:  # anything the model forgot goes last, in recording order
        if n not in order:
            notes.append(f"clip #{n} was missing from the order; added at the end")
            order.append(n)
    slowmo = clean(raw.get("slowmo"), "slowmo")
    cap = max(1, round(len(numbers) / 3))
    if len(slowmo) > cap:
        notes.append(f"kept the first {cap} slow-motion picks")
        slowmo = slowmo[:cap]
    return EditPlan(order, slowmo, str(raw.get("reasons") or ""), notes)


def _post(url: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, json.dumps(payload).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise AiError(f"cannot reach the model at {url}: {exc}") from exc


def _first_model(base: str, timeout: float) -> str:
    try:
        with urllib.request.urlopen(base + "/models", timeout=timeout) as resp:
            return json.loads(resp.read())["data"][0]["id"]
    except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError) as exc:
        raise AiError(f"cannot list models at {base}/models: {exc}") from exc


def suggest_plan(
    timeline: Timeline, base_url: str, model: str | None = None, timeout: float = 400.0, think: bool = True
) -> EditPlan:
    """Ask the model at `base_url` (an OpenAI-style `/v1` root) for an order and slow-motion picks.

    With `think` the model reasons first (about 80 s on a local Qwen) and follows the rules noticeably
    better, e.g. ending on the best clip; without it the answer takes a few seconds.
    """
    base = base_url.rstrip("/")
    model = model or _first_model(base, timeout)
    clips = [clip_brief(c) for c in timeline.clips]
    reply = _post(
        base + "/chat/completions",
        {
            "model": model,
            "temperature": 0.2,
            "max_tokens": 16000,  # reasoning models spend a lot of this thinking before the JSON
            "chat_template_kwargs": {"enable_thinking": think},  # vLLM/Qwen: reasoning pass on or off
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"{RULES}\n\nClips:\n{json.dumps(clips, indent=1)}"},
            ],
            "response_format": {"type": "json_schema", "json_schema": {"name": "edit_plan", "schema": SCHEMA}},
        },
        timeout,
    )
    try:
        raw = json.loads(reply["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        finish = (reply.get("choices") or [{}])[0].get("finish_reason")
        raise AiError(f"the model did not return a usable plan (finish_reason={finish}): {reply!r:.200}") from exc
    if not isinstance(raw, dict):
        raise AiError("the model's plan was not a JSON object")
    plan = normalize_plan(raw, [c.number for c in timeline.clips])
    scores = {c.number: c.score for c in timeline.clips}
    ranked = sorted(scores, key=lambda n: -scores[n])
    if plan.order and plan.order[-1] not in ranked[: max(1, round(len(ranked) / 3))]:
        plan.notes.append(f"the plan ends on clip #{plan.order[-1]}, which is not one of the best clips")
    return plan


def load_plan(path: Path, numbers: list[int]) -> EditPlan:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AiError(f"cannot read plan {path}: {exc}") from exc
    return normalize_plan(raw, numbers)


MUSIC_SCHEMA = {
    "type": "object",
    "properties": {"file": {"type": "string"}, "reasons": {"type": "string"}},
    "required": ["file", "reasons"],
}


def suggest_music(
    timeline: Timeline, tracks: list[dict], base_url: str, model: str | None = None,
    timeout: float = 400.0, think: bool = True,
) -> tuple[str, str]:
    """Pick the best-fitting track from `tracks` (facts from beats.analyze_track); returns (file, reasons)."""
    base = base_url.rstrip("/")
    model = model or _first_model(base, timeout)
    episode = {
        "video_seconds": round(timeline.duration),
        "clips": [clip_brief(c) | {"kills": len(c.kills)} for c in timeline.clips],
    }
    prompt = (
        "Choose the music track for this Counter-Strike highlights video. Fit matters more than taste: the "
        "track should be about as long as the video or longer (a shorter one leaves silence), its energy "
        "should build toward the loudest part near the end where the biggest clips play, and a higher tempo "
        "suits fast multi-kill clips. 'energy_per_10s' is loudness per 10 seconds from 0 to 9 and "
        "'biggest_drop_at' is when it gets suddenly louder (null if there is no clear drop). Answer with the "
        "exact 'file' of one track and short reasons.\n\n"
        f"Video:\n{json.dumps(episode)}\n\nTracks:\n{json.dumps(tracks, indent=1)}"
    )
    reply = _post(
        base + "/chat/completions",
        {
            "model": model, "temperature": 0.2, "max_tokens": 16000,
            "chat_template_kwargs": {"enable_thinking": think},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "music_pick", "schema": MUSIC_SCHEMA}},
        },
        timeout,
    )
    try:
        raw = json.loads(reply["choices"][0]["message"]["content"])
        file, reasons = str(raw["file"]), str(raw.get("reasons") or "")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise AiError(f"the model did not return a usable music pick: {reply!r:.200}") from exc
    if file not in {t["file"] for t in tracks}:
        raise AiError(f"the model picked {file!r}, which is not one of the candidate tracks")
    return file, reasons
