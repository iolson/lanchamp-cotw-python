"""Turn a cs2-highlights `<video>.json` report into an overlay timeline for the finished MP4."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path


class PlanError(ValueError):
    pass


@dataclass(frozen=True)
class Punch:
    """Content of a stat-punch card shown at the start of a signature clip."""

    label: str
    value: str
    context: str


@dataclass(frozen=True)
class ClipPlan:
    number: int
    start: float  # seconds into the highlights video
    duration: float
    player: str
    stat: str  # gold text on the lower third, e.g. "1V3 CLUTCH"
    context: str  # e.g. "Mirage · Round 12"
    punch: Punch | None
    team: str = ""  # from names.json; empty means use the CLI default


@dataclass(frozen=True)
class Timeline:
    clips: list[ClipPlan]
    duration: float


@dataclass(frozen=True)
class PlayerInfo:
    """Per-player overrides from names.json; either field may be None (keep the default)."""

    name: str | None = None
    team: str | None = None


def load_names(path: Path) -> dict[str, PlayerInfo]:
    """Read names.json: {"<steamid64 or in-game name>": "Display" | {"name": ..., "team": ...}}."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanError(f"cannot read names file {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PlanError(f"{path} must be a JSON object keyed by SteamID64 or in-game name")
    players: dict[str, PlayerInfo] = {}
    for key, value in raw.items():
        if isinstance(value, str):  # shorthand: just a display name
            players[key] = PlayerInfo(name=value)
        elif isinstance(value, dict) and all(isinstance(v, str) for v in value.values()):
            unknown = set(value) - {"name", "team"}
            if unknown:
                raise PlanError(f"{path}: {key!r} has unknown field(s) {sorted(unknown)} (use name, team)")
            players[key] = PlayerInfo(name=value.get("name"), team=value.get("team"))
        else:
            raise PlanError(f'{path}: {key!r} must be a display name or {{"name": ..., "team": ...}}')
    return players


def _lookup(player: str, steamid: str, names: dict[str, PlayerInfo]) -> PlayerInfo:
    """Match by SteamID64, then exact in-game name, then case-insensitively."""
    if steamid in names:
        return names[steamid]
    if player in names:
        return names[player]
    lowered = {k.lower(): v for k, v in names.items()}
    return lowered.get(player.lower(), PlayerInfo())


def load_report(path: Path) -> dict:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanError(f"cannot read highlights report {path}: {exc}") from exc
    if not report.get("sequences"):
        raise PlanError(f"{path} has no sequences")
    return report


def _map_label(map_name: str) -> str:
    return re.sub(r"^(de|cs|ar)_", "", map_name or "").replace("_", " ").title()


def _kills_label(kills: int) -> str:
    return "ACE" if kills >= 5 else f"{kills}K"


def describe_moment(moment: dict) -> tuple[str, Punch | None]:
    """Return (lower-third stat text, stat-punch or None); punch context is filled in by the caller."""
    kills = int(moment.get("kills") or 0)
    clutch = moment.get("clutch")
    desc = (moment.get("description") or "").lower()

    parts: list[str] = []
    if kills:
        parts.append(_kills_label(kills))
    won_clutch = bool(clutch and clutch.get("won"))
    if won_clutch:
        parts.append(f"1V{clutch['opponents']} CLUTCH")
    for flag, text in (("knife", "KNIFE"), ("zeus", "ZEUS"), ("noscope", "NOSCOPE")):
        if flag in desc:
            parts.append(text)
    stat = " + ".join(parts) if parts else "HIGHLIGHT"

    punch: Punch | None = None
    if won_clutch:
        punch = Punch("CLUTCH", f"1v{clutch['opponents']}", "")
    elif kills >= 5:
        punch = Punch("", "ACE", "")
    elif kills == 4:
        punch = Punch("QUAD KILL", "4K", "")
    return stat, punch


def build_timeline(
    report: dict,
    *,
    video_duration: float | None = None,
    clip_durations: dict[int, float] | None = None,
    names: dict[str, PlayerInfo] | None = None,
) -> Timeline:
    """Lay the report's sequences end to end.

    `duration_seconds` in the report is rounded to 0.1s, so when the real video length is known
    the whole timeline is rescaled to it; `clip_durations` (probed per-clip files) win outright.
    """
    seqs = sorted(report["sequences"], key=lambda s: s["number"])
    durations = [float((clip_durations or {}).get(s["number"], s["duration_seconds"])) for s in seqs]
    if not clip_durations and video_duration:
        total = sum(durations)
        if total <= 0:
            raise PlanError("report sequences have no duration")
        durations = [d * video_duration / total for d in durations]

    map_name = _map_label(report.get("map", ""))
    clips: list[ClipPlan] = []
    t = 0.0
    for seq, dur in zip(seqs, durations):
        moments = seq.get("moments") or []
        best = max(moments, key=lambda m: m.get("score", 0)) if moments else {}
        stat, punch = describe_moment(best) if best else (seq.get("label", "HIGHLIGHT").upper(), None)
        context = " · ".join(p for p in (map_name, f"Round {seq['round']}" if "round" in seq else "") if p)
        if punch and not punch.context:
            punch = Punch(punch.label, punch.value, context)
        cam = (seq.get("cameras") or [{}])[0]
        player = best.get("player") or cam.get("name", "")
        info = _lookup(player, str(best.get("steamid") or cam.get("steamid") or ""), names or {})
        clips.append(ClipPlan(seq["number"], t, dur, info.name or player, stat, context, punch, info.team or ""))
        t += dur
    return Timeline(clips, t)


def merge_timelines(timelines: list[Timeline]) -> Timeline:
    """Chain timelines end to end, renumbering clips 1..N so numbers stay unique across videos."""
    clips: list[ClipPlan] = []
    offset = 0.0
    for tl in timelines:
        for clip in tl.clips:
            clips.append(replace(clip, number=len(clips) + 1, start=clip.start + offset))
        offset += tl.duration
    return Timeline(clips, offset)
