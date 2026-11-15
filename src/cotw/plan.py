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
    fast: tuple[tuple[float, float], ...] = ()  # fast-forward windows over the lulls between kills
    fast_speed: float = 1.0  # playback speed inside the fast windows
    jumps: tuple[tuple[float, float], ...] = ()  # source stretches cut out (jump cuts over long lulls)
    kills: tuple[tuple[float, bool, bool], ...] = ()  # (seconds after src_start, headshot, special)
    detail: str = ""  # e.g. "4K (m4a1)"; context for people / the LLM, not shown on screen
    map: str = ""  # e.g. "anubis"; clips of one map form a section
    broll: bool = False  # empty-map footage that opens a section: no lower third or stat punch
    opponent: str = ""  # from the report's `match` block (FACEIT); empty when it has none
    division: str = ""  # e.g. "Open", "Intermediate"
    match_id: str = ""
    match_url: str = ""
    won_clutch: int = 0  # opponents beaten in a won clutch (1v3 -> 3); 0 when there was none
    kills_n: int = 0  # kills in the featured moment
    round: int = 0
    rank: int = 0  # countdown position, 1 = best; 0 when the episode is not a countdown


SLOW_BEFORE, SLOW_AFTER = 0.30, 0.45  # full slow motion runs from just before a kill to just after it
RAMP_IN, RAMP_OUT, RAMP_STEPS = 0.30, 0.40, 4  # source seconds easing into / out of slow motion
TAIL_AFTER_KILL = 0.8  # never cut closer than this to the last kill
FAST_AFTER, FAST_BEFORE = 1.5, 2.0  # normal speed kept after a kill and before the next one
FAST_MIN = 3.0  # lulls shorter than this (after those margins) play at normal speed
FAST_RAMP = 0.6  # source seconds easing into / out of fast-forward, inside the lull
JUMP_MIN = 10.0  # lulls longer than this (after the margins) are jump-cut instead of fast-forwarded
JUMP_BEFORE = 2.5  # normal speed kept before the next kill after a jump cut (the set-up for the fight)
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


def build_segments(src_len: float, slow: tuple[Window, ...], speed: float, fast: tuple[Window, ...] = (),
                   fast_speed: float = 1.0, jumps: tuple[Window, ...] = ()) -> list[tuple[float, float, float]]:
    """Split `src_len` source seconds into (offset, length, speed) pieces: normal speed, an ease down to
    `speed` before each slow window, the window itself, and an ease back up after it. Fast windows ease
    up to `fast_speed` and back down inside the window, so the moments around it stay at normal speed."""
    wins: list[list[float]] = []  # [start, end, speed, ramp in, ramp out]; ramps sit outside start..end
    for a, b in sorted(slow):
        a, b = max(0.0, a), min(src_len, b)
        if b - a < 1e-3:
            continue
        if wins and a - wins[-1][1] < RAMP_IN + RAMP_OUT:  # ramps would collide: one longer window
            wins[-1][1] = max(wins[-1][1], b)
        else:
            wins.append([a, b, speed, RAMP_IN, RAMP_OUT])
    if abs(fast_speed - 1.0) > 1e-6:
        for a, b in fast:
            a, b = max(0.0, a) + FAST_RAMP, min(src_len, b) - FAST_RAMP
            if b - a >= 1e-3:
                wins.append([a, b, fast_speed, FAST_RAMP, FAST_RAMP])
        wins.sort()
    out: list[tuple[float, float, float]] = []
    cursor = 0.0
    for a, b, sp, r_in, r_out in wins:
        if a < cursor:  # overlaps the previous window's ramp out; skip rather than run backwards
            continue
        ramp_start = max(cursor, a - r_in)
        if ramp_start - cursor > 1e-3:
            out.append((cursor, ramp_start - cursor, 1.0))
        if a - ramp_start > 1e-3:
            out += _ramp(ramp_start, a - ramp_start, sp, True)
        out.append((a, b - a, sp))
        ramp_end = min(src_len, b + r_out)
        if ramp_end - b > 1e-3:
            out += _ramp(b, ramp_end - b, sp, False)
        cursor = ramp_end
    if src_len - cursor > 1e-3 or not out:
        out.append((cursor, src_len - cursor, 1.0))
    return _cut(out, jumps)


def _cut(pieces: list[tuple[float, float, float]], jumps: tuple[Window, ...]) -> list[tuple[float, float, float]]:
    """`pieces` without the source stretches in `jumps`; the gaps left are the jump cuts."""
    for a, b in jumps:
        kept = []
        for off, length, sp in pieces:
            end = off + length
            if end <= a or off >= b:
                kept.append((off, length, sp))
                continue
            if a - off > 1e-3:
                kept.append((off, a - off, sp))
            if end - b > 1e-3:
                kept.append((b, end - b, sp))
        pieces = kept
    return pieces


def clip_length(src_len: float, slow: tuple[Window, ...], speed: float, fast: tuple[Window, ...] = (),
                fast_speed: float = 1.0, jumps: tuple[Window, ...] = ()) -> float:
    """Seconds a clip takes on screen: its source length stretched by slow motion, squeezed by fast-forward,
    less its jump cuts."""
    return sum(length / sp for _, length, sp in build_segments(src_len, slow, speed, fast, fast_speed, jumps))


def _length(clip: "ClipPlan", src_len: float, slow: tuple[Window, ...] | None = None) -> float:
    return clip_length(src_len, clip.slow if slow is None else slow, clip.speed, clip.fast, clip.fast_speed,
                       clip.jumps)


def clip_segments(clip: "ClipPlan") -> list[tuple[float, float, float]]:
    return build_segments(clip.src_len or clip.duration, clip.slow, clip.speed, clip.fast, clip.fast_speed,
                          clip.jumps)


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
        elif isinstance(value, dict) and all(v is None or isinstance(v, str) for v in value.values()):
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


@dataclass(frozen=True)
class MatchContext:
    """Who a player was playing for and against, from the report's optional `match` block.

    The block is written by the highlights tool's FACEIT enrichment:
    {"match_id", "url", "division", "competition", "teams": [A, B],
     "players": {"<steamid64>": {"nickname", "team", "opponent"?}}}; every key is optional.
    """

    nickname: str = ""
    team: str = ""
    opponent: str = ""
    division: str = ""
    match_id: str = ""
    url: str = ""


def match_context(report: dict, steamid: str, player: str) -> MatchContext:
    match = report.get("match")
    if not isinstance(match, dict):
        return MatchContext()
    players = match.get("players") or {}
    entry = players.get(steamid)
    if entry is None and player:  # fall back to the nickname / in-game name, ignoring case
        low = player.lower()
        entry = next((e for e in players.values()
                      if low in (str(e.get("nickname", "")).lower(), str(e.get("name", "")).lower())), None)
    entry = entry if isinstance(entry, dict) else {}
    team = str(entry.get("team") or "")
    teams = [str(t.get("name") if isinstance(t, dict) else t) for t in match.get("teams") or []]
    opponent = str(entry.get("opponent") or "")
    if not opponent and team and len(teams) == 2 and team in teams:
        opponent = teams[1 - teams.index(team)]
    return MatchContext(
        nickname=str(entry.get("nickname") or ""), team=team, opponent=opponent,
        division=str(match.get("division") or match.get("competition") or ""),
        match_id=str(match.get("match_id") or match.get("id") or ""), url=str(match.get("url") or ""),
    )


def load_report(path: Path) -> dict:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanError(f"cannot read highlights report {path}: {exc}") from exc
    if not report.get("sequences"):
        raise PlanError(f"{path} has no sequences")
    return report


def _map_slug(map_name: str) -> str:
    return re.sub(r"^(de|cs|ar)_", "", map_name or "").lower()


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


def _featured_kills(seq: dict, steamid: str) -> list[dict]:
    """The featured player's kills from the sequence's event list.

    A report's moment (and so its `beat_targets`) can miss kills: a 3K 1v3 clutch has been listed as
    a 1K, which trimmed the clip to its first kill.
    """
    out = []
    for e in seq.get("events") or []:
        if e.get("type") != "kill" or "seconds_from_clip_start" not in e:
            continue
        attacker = str((e.get("attacker") or {}).get("steamid") or "")
        if (attacker != steamid) if steamid else not e.get("is_pov_kill"):
            continue
        special = any(e.get(f) for f in ("noscope", "thrusmoke", "attackerblind")) or e.get("weapon") in ("knife", "taser")
        out.append({"t": float(e["seconds_from_clip_start"]), "headshot": bool(e.get("headshot")), "special": special})
    return out


def _clutch_end(seq: dict, steamid: str) -> float | None:
    """When the featured player's won clutch ends (a defuse can come after the last kill)."""
    for e in seq.get("events") or []:
        if (e.get("type") == "clutch_end" and "seconds_from_clip_start" in e
                and str(e.get("steamid") or steamid) == steamid):
            return float(e["seconds_from_clip_start"])
    return None


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
        moments = seq.get("moments") or []
        best = max(moments, key=lambda m: m.get("score", 0)) if moments else {}
        cam = (seq.get("cameras") or [{}])[0]
        player = best.get("player") or cam.get("name", "")
        steamid = str(best.get("steamid") or cam.get("steamid") or "")
        lead, trimmed = 0.0, False
        kl = list(kills.get(seq["number"]) or []) if exact else None
        own = _featured_kills(seq, steamid)
        if kl is not None:
            kl += [k for k in own if all(abs(k["t"] - x["t"]) > 0.1 for x in kl)]
            kl.sort(key=lambda k: k["t"])
        ks = [k["t"] for k in kl or []]
        clutch = best.get("clutch")
        won = bool(clutch and clutch.get("won"))
        end = _clutch_end(seq, steamid) if won and ks else None
        if kill_padding is not None and ks:
            last = max(ks + ([end] if end is not None else []))
            lo, hi = max(0.0, min(ks) - kill_padding), min(dur, last + kill_padding)
            lead, trimmed = lo, hi - lo < dur - 0.05
            seq_dur, dur = dur, hi - lo
        else:
            seq_dur = dur
        src_len = dur
        src_max = seq_dur - lead
        min_src = min(src_len, max(ks + ([end] if end is not None else [])) - lead + TAIL_AFTER_KILL) if ks else src_len
        kills_n = max(int(best.get("kills") or 0), len(own))
        detail = str(best.get("description") or "")
        if best and kills_n > int(best.get("kills") or 0):
            best = {**best, "kills": kills_n}
            detail = re.sub(r"^\d+K\b( \([^)]*\))?", _kills_label(kills_n), detail)
        stat, punch = describe_moment(best) if best else (seq.get("label", "HIGHLIGHT").upper(), None)
        context = " · ".join(p for p in (map_name, f"Round {seq['round']}" if "round" in seq else "") if p)
        if punch and not punch.context:
            punch = Punch(punch.label, punch.value, context)
        info = _lookup(player, steamid, names or {})
        mc = match_context(report, steamid, player)  # names.json overrides FACEIT, which beats the in-game name
        clips.append(
            ClipPlan(seq["number"], out, dur, info.name or mc.nickname or player, stat, context, punch,
                     info.team or mc.team or "",
                     opponent=mc.opponent, division=mc.division, match_id=mc.match_id, match_url=mc.url,
                     won_clutch=int(clutch["opponents"]) if won else 0,
                     kills_n=kills_n, round=int(seq.get("round") or 0),
                     score=float(best.get("score", 0)), src_start=t + lead, trimmed=trimmed,
                     src_len=src_len, src_max=src_max, min_src=min_src,
                     kills=tuple((k["t"] - lead, k["headshot"], k["special"]) for k in kl or []),
                     detail=detail, map=_map_slug(report.get("map", "")))
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
    return snap_cuts_multi(timeline, [beats], [], offset=offset, music_start=music_start)[0]


def snap_cuts_multi(timeline: Timeline, beats: list[list[float]], switches: list[int], *, offset: float,
                    music_start: float) -> tuple[Timeline, list[float]]:
    """`snap_cuts` for several songs: song k+1 starts with clip `switches[k]`, with its first beat on that
    clip's start. Returns the timeline and where each song starts on the episode clock."""
    starts = [music_start]
    grid = [music_start + b for b in beats[0]]
    out: list[ClipPlan] = []
    t = 0.0
    for i, clip in enumerate(timeline.clips):
        at = offset + t
        if len(starts) < len(beats) and i == switches[len(starts) - 1]:
            nxt = beats[len(starts)]
            starts.append(max(0.0, at - (nxt[0] if nxt else 0.0)))
            grid = [starts[-1] + b for b in nxt]
        src_len = clip.src_len or clip.duration
        src_max = max(clip.src_max, src_len)
        min_src = min(clip.min_src or src_len, src_len)
        lo = _length(clip, min_src)
        hi = _length(clip, src_max)
        want = at + clip.duration
        beat = min((b for b in grid if at + lo <= b <= at + hi), key=lambda b: abs(b - want), default=None)
        if beat is not None:
            new_len = beat - at
            extra = clip.duration - src_len  # what slow motion adds (fast-forward takes); the tail is normal speed
            new_src = min(src_max, max(min_src, new_len - extra))
            clip = replace(clip, src_len=new_src, duration=_length(clip, new_src),
                           trimmed=clip.trimmed or new_src < clip.src_max - 0.05)
        out.append(replace(clip, start=t))
        t += clip.duration
    return Timeline(out, t), starts


def relayout(clips: list[ClipPlan]) -> Timeline:
    """A timeline of `clips` laid end to end."""
    out, t = [], 0.0
    for c in clips:
        out.append(replace(c, start=t))
        t += c.duration
    return Timeline(out, t)


def sections(timeline: Timeline) -> list[list[ClipPlan]]:
    """Clips grouped by map. Clips keep their order inside a section; sections are ordered by where their
    last clip sits, so with `--order score` the section holding the best clip comes last."""
    groups: dict[str, list[tuple[int, ClipPlan]]] = {}
    for i, c in enumerate(timeline.clips):
        groups.setdefault(c.map, []).append((i, c))
    return [[c for _, c in g] for g in sorted(groups.values(), key=lambda g: g[-1][0])]


BROLL_NUMBER = 1000  # b-roll "clips" are numbered from here so they never clash with real clip numbers


def add_broll(timeline: Timeline, footage: dict[str, list[tuple[int, float]]], seconds: float) -> Timeline:
    """Group the clips into map sections and open each with a b-roll shot of that map.

    `footage` maps a map name to its b-roll as (index of the source video, its length in seconds).
    """
    out: list[ClipPlan] = []
    used: dict[str, int] = {}
    for sec in sections(timeline):
        files = footage.get(sec[0].map)
        if files:
            source, length = files[used.get(sec[0].map, 0) % len(files)]
            used[sec[0].map] = used.get(sec[0].map, 0) + 1
            shot = min(seconds, length)
            out.append(ClipPlan(BROLL_NUMBER + len(out), 0.0, shot, "b-roll", "", sec[0].context, None,
                                source=source, trimmed=True, src_len=shot, src_max=length,
                                min_src=min(shot, 2.0), map=sec[0].map, broll=True))
        out.extend(sec)
    return relayout(out)


def song_switches(timeline: Timeline, lengths: list[float], *, offset: float, start: float, overlap: float = 1.0,
                  min_play: float = 20.0, max_early: float = 25.0) -> list[int]:
    """Clip indexes where songs 2.. begin, as late as possible without the song before running out.

    A song changes at the start of a section (its b-roll shot, or the first clip of a new map) when one falls
    within `max_early` seconds of where the song ends; otherwise at the latest clip boundary that fits.
    """
    clips = timeline.clips
    starts = {i for i, c in enumerate(clips) if i > 0 and (c.broll or c.map != clips[i - 1].map)}
    out: list[int] = []
    song_start = start
    for length in lengths[:-1]:
        want = song_start + length - overlap
        ok = [(i, offset + clips[i].start) for i in range(1, len(clips))
              if offset + clips[i].start <= want and offset + clips[i].start >= song_start + min_play
              and i > (out[-1] if out else 0)]
        if not ok:
            break
        near = [(i, t) for i, t in ok if i in starts and want - t <= max_early]
        i, song_start = (near or ok)[-1]
        out.append(i)
    return out


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
            clip = replace(clip, src_len=src_len, slow=slow, speed=speed, min_src=min_src)
            clip = replace(clip, duration=_length(clip, src_len))
        out.append(replace(clip, start=t))
        t += clip.duration
    return Timeline(out, t)


def _lull_windows(kills: tuple[tuple[float, bool, bool], ...], src_len: float,
                  jump_min: float = 0.0) -> tuple[tuple[Window, ...], tuple[Window, ...]]:
    """(fast, jumps): the lulls between kills, less a margin of normal speed on each side. Lulls longer than
    `jump_min` (0: never) are jump cuts instead, keeping a little more set-up before the next kill."""
    ts = sorted(t for t, _, _ in kills)
    fast, jumps = [], []
    for a, b in zip(ts, ts[1:]):
        lo, hi = a + FAST_AFTER, min(src_len, b - FAST_BEFORE)
        if hi - lo < FAST_MIN:
            continue
        if jump_min and hi - lo > jump_min:
            jumps.append((lo, min(src_len, b - JUMP_BEFORE)))
        else:
            fast.append((lo, hi))
    return tuple(fast), tuple(jumps)


def _fast_windows(kills: tuple[tuple[float, bool, bool], ...], src_len: float) -> tuple[Window, ...]:
    return _lull_windows(kills, src_len)[0]


def apply_fastforward(timeline: Timeline, speed: float, jump_min: float = 0.0) -> Timeline:
    """Speed through the lulls between kills (a 1v3 walking between fights) with eased ramps, so the
    whole play fits in the episode instead of being cut after the first kill; lulls longer than `jump_min`
    seconds (0: none) are jump-cut instead, since even at 4x they drag."""
    if speed <= 1.0 and not jump_min:
        return timeline
    out: list[ClipPlan] = []
    t = 0.0
    for clip in timeline.clips:
        src_len = clip.src_len or clip.duration
        fast, jumps = _lull_windows(clip.kills, src_len, jump_min)
        if speed <= 1.0:
            fast = ()
        if fast or jumps:
            clip = replace(clip, src_len=src_len, fast=fast, fast_speed=speed if fast else 1.0, jumps=jumps)
            clip = replace(clip, duration=_length(clip, src_len))
        out.append(replace(clip, start=t))
        t += clip.duration
    return Timeline(out, t)
