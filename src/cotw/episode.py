"""Episode structure and write-ups: the paced clip order, the cold-open window, the callout text for
thumbnails and the YouTube description with chapters and credits."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from cotw.plan import ClipPlan, Timeline, relayout

# Share of the non-peak clips that open the episode; the rest are the varied middle.
OPENER_SHARE = 0.35
PEAK_MAX = 3  # the "biggest moments" block: the best two or three plays, best last


def _kind(c: ClipPlan) -> str:
    if c.won_clutch:
        return "clutch"
    if c.kills_n >= 5:
        return "ace"
    return f"{c.kills_n}k" if c.kills_n else "other"


def _variety_order(clips: list[ClipPlan], prev: ClipPlan | None = None) -> list[ClipPlan]:
    """Greedy order that avoids back-to-back repeats of player, team, map, division and kind of play,
    preferring higher scores among equals."""
    left = sorted(clips, key=lambda c: -c.score)
    out: list[ClipPlan] = []
    while left:
        def clash(c: ClipPlan) -> int:
            if not (out or prev):
                return 0
            p = out[-1] if out else prev
            return (3 * (c.player == p.player) + 2 * (bool(c.team) and c.team == p.team)
                    + (c.map == p.map) + (c.division == p.division and bool(c.division)) + (_kind(c) == _kind(p)))

        best = min(left, key=lambda c: (clash(c), -c.score))
        left.remove(best)
        out.append(best)
    return out


def peak_count(n: int) -> int:
    return 0 if n < 4 else max(2, min(PEAK_MAX, n // 4))


def paced_order(clips: list[ClipPlan]) -> tuple[list[ClipPlan], dict[int, str]]:
    """Order clips as: strong opening group, varied middle, then the biggest moments building to the best.

    Returns the order and a clip number -> block label ("opener" / "middle" / "peak").
    """
    ranked = sorted(clips, key=lambda c: -c.score)
    k = peak_count(len(ranked))
    peak = list(reversed(ranked[:k]))  # ascending: the best clip is last
    rest = ranked[k:]
    n_open = math.ceil(len(rest) * OPENER_SHARE)
    chosen: list[ClipPlan] = []  # the strongest of the rest, one per player while that is possible
    for c in rest:
        if len(chosen) < n_open and all(c.player != o.player for o in chosen):
            chosen.append(c)
    chosen += [c for c in rest if c not in chosen][: n_open - len(chosen)]
    opener = _variety_order(chosen)
    middle = _variety_order([c for c in rest if c not in chosen], prev=opener[-1] if opener else None)
    labels = {c.number: "opener" for c in opener} | {c.number: "middle" for c in middle} | {
        c.number: "peak" for c in peak}
    return [*opener, *middle, *peak], labels


def paced_labels(clips: list[ClipPlan]) -> dict[int, str]:
    """Block labels for a hand-picked order (`--pick` with paced): the last two or three clips are the
    biggest moments, the first share of the rest the opening group, everything between the middle."""
    k = peak_count(len(clips))
    n_open = math.ceil((len(clips) - k) * OPENER_SHARE)
    return {c.number: "opener" if i < n_open else "peak" if i >= len(clips) - k else "middle"
            for i, c in enumerate(clips)}


def countdown_order(clips: list[ClipPlan], top: int | None = None) -> list[ClipPlan]:
    """Rank clips the way `highlights top` does (best `score` first, across all videos), keep the best
    `top`, and play them from the lowest rank up so #1 comes last. Each clip gets its `rank`."""
    ranked = sorted(clips, key=lambda c: (-c.score, c.number))[: top or None]
    return [replace(c, rank=i) for i, c in reversed(list(enumerate(ranked, 1)))]


def cold_open_window(clip: ClipPlan, max_len: float = 9.0, min_len: float = 6.0) -> tuple[float, float]:
    """(absolute source start, length) of the clip's best stretch for a teaser: it ends just after the last
    kill and, for long streaks, covers only the last few kills so it fits `max_len`."""
    src_len = clip.src_len or clip.duration
    ks = sorted(t for t, _, _ in clip.kills)
    if not ks:
        length = min(max_len, src_len)
        return clip.src_start, length
    end = min(src_len, ks[-1] + 1.0)
    start = max(0.0, ks[0] - 1.5)
    if end - start > max_len:
        start = end - max_len
        # begin about a second before the first kill that still fits
        fits = [t for t in ks if t - 1.5 >= start]
        if fits:
            start = max(start, fits[0] - 1.5)
    if end - start < min_len:  # a short moment: pad the lead-in rather than cut the tail
        start = max(0.0, end - min_len)
        end = min(src_len, start + min_len)
    return clip.src_start + start, end - start


def callout(clip: ClipPlan) -> str:
    """Short thumbnail text for a clip: the feat, as a question or exclamation."""
    desc = clip.detail.lower()
    if clip.won_clutch >= 2:
        return f"1V{clip.won_clutch}?!"
    if clip.kills_n >= 5:
        return "ACE?!"
    for flag, text in (("knife", "KNIFE?!"), ("zeus", "ZEUS?!"), ("noscope", "NOSCOPE?!")):
        if flag in desc:
            return text
    if clip.kills_n >= 3:
        return f"{clip.kills_n}K?!"
    return "HOW?!"


def stamp(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _what(c: ClipPlan) -> str:
    return c.stat.title().replace("1V", "1v").replace(" + ", " + ") if c.stat else "Highlight"


@dataclass(frozen=True)
class DescriptionInfo:
    episode: int
    week: str = ""
    league: str = ""
    channel: str = "@LANChampGG"
    submit_url: str = ""
    next_up: str = ""
    top_n: int = 0  # countdown size; 0 when the episode is not a countdown


def title_suggestion(timeline: Timeline, info: DescriptionInfo) -> str:
    """Complements the thumbnail instead of repeating its callout: the thumbnail carries the feat, the
    title names the series and where it is from."""
    where = f"{info.league} " if info.league else ""
    if info.top_n:
        return f"Top {info.top_n} {where}CS2 Plays of the Week | LANChamp Clips of the Week EP.{info.episode:02d}"
    return f"The Best {where}CS2 Plays of the Week | LANChamp Clips of the Week EP.{info.episode:02d}"


def description(timeline: Timeline, info: DescriptionInfo, *, lead: float, cold: float = 0.0,
                labels: dict[int, str] | None = None, outro_at: float | None = None,
                titles: list[str] | None = None, intro_text: str | None = None) -> str:
    """YouTube description: chapters from the real timeline, credits per clip, links.

    `lead` is when the first clip starts in the video (cold open + title card); `cold` is the cold-open
    length, listed as its own opening chapter. With `labels` (the paced structure) the chapters are the
    blocks; otherwise each clip is a chapter. `outro_at` is where the end card starts, if appended.
    """
    clips = [c for c in timeline.clips if not c.broll]
    league = f"{info.league} " if info.league else ""
    lines = ["TITLE OPTIONS", *(titles or [title_suggestion(timeline, info)]), ""]
    intro = f"Clips of the Week EP.{info.episode:02d}" + (f" · week of {info.week}" if info.week else "")
    lines += [intro_text or f"{intro}: the best plays from this week's {league}matches, picked from {len(clips)} clips.", ""]
    if info.submit_url:
        lines += [f"Got a clip? Submit it: {info.submit_url}", ""]

    lines.append("CHAPTERS")  # must start at 0:00 and have 3+ entries at least 10 s apart
    lines += ["0:00 Cold open", f"{stamp(cold)} Intro"] if cold > 0 else ["0:00 Intro"]
    block_names = {"opener": "Opening plays", "middle": "More highlights", "peak": "Biggest moments"}
    seen: set[str] = set()
    for c in clips:
        block = (labels or {}).get(c.number)
        if labels is None:
            lines.append(f"{stamp(lead + c.start)} {_chapter(c)}")
        elif block not in seen:
            seen.add(block)
            lines.append(f"{stamp(lead + c.start)} {block_names[block]}")
    if outro_at is not None:
        lines.append(f"{stamp(outro_at)} Submit your clips / next week")

    lines += ["", "CLIP CREDITS"]
    lines += [f"{stamp(lead + c.start)} - {_credit(c)}" for c in clips]
    urls = sorted({c.match_url for c in clips if c.match_url})
    if urls:
        lines += ["", "MATCHES", *urls]
    tags = ["#CS2", "#CounterStrike2", "#LANChamp", "#ClipsOfTheWeek"] + ([f"#{info.league}"] if info.league else [])
    lines += ["", f"Subscribe for new clips every week: {info.channel}"]
    if info.next_up:
        lines.append(f"Next up: {info.next_up}")
    lines += ["", " ".join(tags)]
    return "\n".join(lines) + "\n"


def _chapter(c: ClipPlan) -> str:
    return (f"#{c.rank} " if c.rank else "") + f"{c.player} · {_what(c)}"


def _credit(c: ClipPlan) -> str:
    who = (f"#{c.rank} " if c.rank else "") + c.player
    if c.team:
        who += f" ({c.team})"
    if c.opponent:
        who += f" vs {c.opponent}"
    bits = [who, _what(c)]
    if c.division:
        bits.append(c.division)
    if c.context:
        bits.append(c.context)
    return " · ".join(bits)
