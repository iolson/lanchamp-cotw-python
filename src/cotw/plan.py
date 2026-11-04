"""Turn a cs2-highlights `<video>.json` report into an overlay timeline for the finished MP4."""

from __future__ import annotations

import json
import random
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
    score: float = 0.0  # best moment's score, used by --order score
    source: int = 0  # index of the highlights video this clip is cut from
    src_start: float = 0.0  # where the clip starts inside that video
    trimmed: bool = False  # cut down to the kills; the audio gets short fades at the cut points
    src_len: float = 0.0  # source seconds used (0: same as duration, i.e. no slow motion)
    src_max: float = 0.0  # source seconds available after src_start (room to extend the tail)
    min_src: float = 0.0  # least source seconds that still keep every kill and a short tail
    slow: tuple[tuple[float, float], ...] = ()  # slow-motion windows, in source seconds from src_start
    speed: float = 0.5  # playback speed inside the slow windows
    kills: tuple[tuple[float, bool, bool], ...] = ()  # (seconds after src_start, headshot, special)
    detail: str = ""  # e.g. "4K (m4a1)"; context for people / the LLM, not shown on screen


SLOW_BEFORE, SLOW_AFTER = 0.30, 0.45  # full slow motion runs from just before a kill to just after it
RAMP_IN, RAMP_OUT, RAMP_STEPS = 0.30, 0.40, 4  # source seconds easing into / out of slow motion
TAIL_AFTER_KILL = 0.8  # never cut closer than this to the last kill
SLOWMO_MODES = ("none", "last", "special", "headshots", "all")
Window = tuple[float, float]


def _smooth(u: float) -> float:
    return u * u * (3 - 2 * u)


def _ramp(start: float, length: float, speed: float, easing_in: bool) -> list[tuple[float, float, float]]:
    """Ease between full speed and `speed` in a few short steps (smoothstep curve)."""
    step = length / RAMP_STEPS
    out = []
    for i in range(RAMP_STEPS):
        u = (i + 0.5) / RAMP_STEPS
        u = u if easing_in else 1 - u
        out.append((start + i * step, step, 1 - (1 - speed) * _smooth(u)))
    return out


def build_segments(src_len: float, slow: tuple[Window, ...], speed: float) -> list[tuple[float, float, float]]:
    """Split `src_len` source seconds into (offset, length, speed) pieces: normal speed, an ease down to
    `speed` before each slow window, the window itself, and an ease back up after it."""
    wins: list[list[float]] = []
    for a, b in sorted(slow):
        a, b = max(0.0, a), min(src_len, b)
        if b - a < 1e-3:
            continue
        if wins and a - wins[-1][1] < RAMP_IN + RAMP_OUT:  # ramps would collide: one longer window
            wins[-1][1] = max(wins[-1][1], b)
        else:
            wins.append([a, b])
    out: list[tuple[float, float, float]] = []
    cursor = 0.0
    for a, b in wins:
        ramp_start = max(cursor, a - RAMP_IN)
        if ramp_start - cursor > 1e-3:
            out.append((cursor, ramp_start - cursor, 1.0))
        if a - ramp_start > 1e-3:
            out += _ramp(ramp_start, a - ramp_start, speed, True)
        out.append((a, b - a, speed))
        ramp_end = min(src_len, b + RAMP_OUT)
        if ramp_end - b > 1e-3:
            out += _ramp(b, ramp_end - b, speed, False)
        cursor = ramp_end
    if src_len - cursor > 1e-3 or not out:
        out.append((cursor, src_len - cursor, 1.0))
    return out


def clip_length(src_len: float, slow: tuple[Window, ...], speed: float) -> float:
    """Seconds a clip takes on screen: its source length stretched by slow motion."""
    return sum(length / sp for _, length, sp in build_segments(src_len, slow, speed))


def clip_segments(clip: "ClipPlan") -> list[tuple[float, float, float]]:
    return build_segments(clip.src_len or clip.duration, clip.slow, clip.speed)


def _slow_windows(kills: tuple[tuple[float, bool, bool], ...], mode: str, src_len: float) -> tuple[Window, ...]:
    if mode == "none" or not kills:
        return ()
    last = max(kills)
    chosen = [
        k for k in kills
        if mode == "all" or k == last
        or (mode == "special" and k[2]) or (mode == "headshots" and k[1])
    ]
    return tuple(
        (max(0.0, t - SLOW_BEFORE), min(src_len, t + SLOW_AFTER)) for t, _, _ in sorted(chosen)
        if min(src_len, t + SLOW_AFTER) - max(0.0, t - SLOW_BEFORE) >= 0.2
    )


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
    kill_padding: float | None = None,
) -> Timeline:
    """Lay the report's sequences end to end.

    `duration_seconds` in the report is rounded to 0.1s, so when the real video length is known
    the whole timeline is rescaled to it; `clip_durations` (probed per-clip files) win outright.

    `kill_padding` trims each clip to that many seconds before its first kill and after its last
    (from the report's `beat_targets`); it needs the exact clip positions of newer reports.
    """
    seqs = sorted(report["sequences"], key=lambda s: s["number"])
    # Newer reports say where each clip sits in the video. Sequences without that (e.g. cinematic
    # variants that were never rendered into this MP4) are not in the video and must be ignored.
    placed = any("video_start_seconds" in s for s in seqs)
    if placed:
        seqs = [s for s in seqs if "video_start_seconds" in s]
    exact = placed and not clip_durations
    durations = [
        float((clip_durations or {}).get(s["number"], s["clip_duration_seconds"] if exact else s["duration_seconds"]))
        for s in seqs
    ]
    if not clip_durations and not exact and video_duration:
        total = sum(durations)
        if total <= 0:
            raise PlanError("report sequences have no duration")
        durations = [d * video_duration / total for d in durations]

    map_name = _map_label(report.get("map", ""))
    clips: list[ClipPlan] = []
    kills: dict[int, list[dict]] = {}
    for b in report.get("beat_targets") or []:
        kills.setdefault(b.get("sequence"), []).append(
            {"t": float(b["clip_time_seconds"]), "headshot": bool(b.get("headshot")), "special": bool(b.get("special"))}
        )

    t = 0.0  # position in the source video
    out = 0.0  # position in the finished episode
    for seq, dur in zip(seqs, durations):
        if exact:
            t = float(seq["video_start_seconds"])
        lead, trimmed = 0.0, False
        kl = kills.get(seq["number"]) if exact else None
        ks = [k["t"] for k in kl or []]
        if kill_padding is not None and ks:
            lo, hi = max(0.0, min(ks) - kill_padding), min(dur, max(ks) + kill_padding)
            lead, trimmed = lo, hi - lo < dur - 0.05
            seq_dur, dur = dur, hi - lo
        else:
            seq_dur = dur
        src_len = dur
        src_max = seq_dur - lead
        min_src = min(src_len, max(ks) - lead + TAIL_AFTER_KILL) if ks else src_len
        moments = seq.get("moments") or []
        best = max(moments, key=lambda m: m.get("score", 0)) if moments else {}
        stat, punch = describe_moment(best) if best else (seq.get("label", "HIGHLIGHT").upper(), None)
        context = " · ".join(p for p in (map_name, f"Round {seq['round']}" if "round" in seq else "") if p)
        if punch and not punch.context:
            punch = Punch(punch.label, punch.value, context)
        cam = (seq.get("cameras") or [{}])[0]
        player = best.get("player") or cam.get("name", "")
        info = _lookup(player, str(best.get("steamid") or cam.get("steamid") or ""), names or {})
        clips.append(
            ClipPlan(seq["number"], out, dur, info.name or player, stat, context, punch, info.team or "",
                     score=float(best.get("score", 0)), src_start=t + lead, trimmed=trimmed,
                     src_len=src_len, src_max=src_max, min_src=min_src,
                     kills=tuple((k["t"] - lead, k["headshot"], k["special"]) for k in kl or []),
                     detail=str(best.get("description") or ""))
        )
        t += seq_dur
        out += dur
    return Timeline(clips, out)


def merge_timelines(timelines: list[Timeline]) -> Timeline:
    """Chain timelines end to end, renumbering clips 1..N so numbers stay unique across videos."""
    clips: list[ClipPlan] = []
    offset = 0.0
    for source, tl in enumerate(timelines):
        for clip in tl.clips:
            clips.append(replace(clip, number=len(clips) + 1, start=clip.start + offset, source=source))
        offset += tl.duration
    return Timeline(clips, offset)


ORDERS = ("chronological", "score", "shuffle")


def parse_numbers(text: str) -> list[int]:
    """'3, 1,5' -> [3, 1, 5]."""
    try:
        return [int(p) for p in text.replace(" ", "").split(",") if p]
    except ValueError as exc:
        raise PlanError(f"expected a comma-separated list of clip numbers, got {text!r}") from exc


def _adjacent_repeats(clips: list[ClipPlan]) -> int:
    return sum(a.player == b.player for a, b in zip(clips, clips[1:]))


def arrange(
    timeline: Timeline,
    *,
    order: str = "chronological",
    reverse: bool = False,
    seed: int | None = None,
    pick: list[int] | None = None,
    skip: list[int] | None = None,
) -> Timeline:
    """Choose and order the clips; start times are recomputed for the new order.

    `pick` keeps only those clip numbers, in the order given (it overrides `order`); `skip` drops
    clips. `score` runs lowest to highest; `shuffle` avoids back-to-back clips of one player when it can.
    """
    by_number = {c.number: c for c in timeline.clips}
    for n in [*(pick or []), *(skip or [])]:
        if n not in by_number:
            raise PlanError(f"no clip #{n} (clips are numbered 1..{len(by_number)})")
    clips = list(timeline.clips)
    if pick:
        if len(set(pick)) != len(pick):
            raise PlanError("--pick lists a clip more than once")
        clips = [by_number[n] for n in pick]
    else:
        clips = [c for c in clips if c.number not in set(skip or [])]
        if order == "score":
            clips.sort(key=lambda c: c.score)  # stable: ties keep recording order
        elif order == "shuffle":
            rng = random.Random(seed)
            best: list[ClipPlan] | None = None
            for _ in range(200):
                trial = clips[:]
                rng.shuffle(trial)
                if best is None or _adjacent_repeats(trial) < _adjacent_repeats(best):
                    best = trial
                if _adjacent_repeats(best) == 0:
                    break
            clips = best or clips
        elif order != "chronological":
            raise PlanError(f"unknown order {order!r}")
        if reverse:
            clips.reverse()
    if not clips:
        raise PlanError("no clips left after --pick/--skip")
    out, t = [], 0.0
    for c in clips:
        out.append(replace(c, start=t))
        t += c.duration
    return Timeline(out, t)


def snap_cuts(timeline: Timeline, beats: list[float], *, offset: float, music_start: float) -> Timeline:
    """Move each clip's end (the cut to the next clip) onto the nearest beat of the music.

    Only the tail of a clip is lengthened or shortened, never so far that it cuts off a kill (or runs
    past the footage). `offset` is when the first clip starts in the episode (the title card length)
    and `music_start` is when the track starts, so beat times can be placed on the episode clock.
    """
    grid = [music_start + b for b in beats]
    out: list[ClipPlan] = []
    t = 0.0
    for clip in timeline.clips:
        src_len = clip.src_len or clip.duration
        src_max = max(clip.src_max, src_len)
        min_src = min(clip.min_src or src_len, src_len)
        at = offset + t
        lo = clip_length(min_src, clip.slow, clip.speed)
        hi = clip_length(src_max, clip.slow, clip.speed)
        want = at + clip.duration
        beat = min((b for b in grid if at + lo <= b <= at + hi), key=lambda b: abs(b - want), default=None)
        if beat is not None:
            new_len = beat - at
            extra = clip.duration - src_len  # what slow motion adds; the tail past the ramps is normal speed
            new_src = min(src_max, max(min_src, new_len - extra))
            clip = replace(clip, src_len=new_src, duration=clip_length(new_src, clip.slow, clip.speed),
                           trimmed=clip.trimmed or new_src < clip.src_max - 0.05)
        out.append(replace(clip, start=t))
        t += clip.duration
    return Timeline(out, t)


def select_slowmo_clips(timeline: Timeline, select: str | list[int] = "auto") -> list[int]:
    """Which clips get slow motion: `all`, explicit clip numbers, or `auto`.

    `auto` keeps the signature clips (the ones with a stat punch: won clutches, aces, 4Ks), best score
    first, and at most a third of the episode so slow motion stays special.
    """
    numbers = [c.number for c in timeline.clips]
    if isinstance(select, list):
        missing = [n for n in select if n not in numbers]
        if missing:
            raise PlanError(f"no clip #{missing[0]} for --slowmo-clips")
        return select
    if select == "all":
        return numbers
    if select != "auto":
        raise PlanError(f"unknown --slowmo-clips value {select!r}")
    ranked = sorted((c for c in timeline.clips if c.punch), key=lambda c: -c.score)
    return [c.number for c in ranked[: max(1, round(len(numbers) / 3))]]


def apply_slowmo(
    timeline: Timeline, mode: str, speed: float = 0.5, select: str | list[int] = "auto"
) -> Timeline:
    """Add slow-motion windows (with speed ramps) around kills on the selected clips."""
    chosen = set(select_slowmo_clips(timeline, select))
    out: list[ClipPlan] = []
    t = 0.0
    for clip in timeline.clips:
        src_len = clip.src_len or clip.duration
        slow = _slow_windows(clip.kills, mode, src_len) if clip.number in chosen else ()
        if slow:
            min_src = max(clip.min_src or src_len, min(src_len, slow[-1][1] + RAMP_OUT))
            clip = replace(clip, src_len=src_len, slow=slow, speed=speed, min_src=min_src,
                           duration=clip_length(src_len, slow, speed))
        out.append(replace(clip, start=t))
        t += clip.duration
    return Timeline(out, t)
