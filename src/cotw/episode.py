"""Episode structure and write-ups: the paced clip order, the cold-open window, the callout text for
thumbnails and the YouTube description with chapters and credits."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace

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
    if clip.won_clutch >= 3:
        return f"1V{clip.won_clutch}?!"
    if clip.won_clutch == 2 and clip.kills_n >= 4:  # "1V2" undersells a 4K that ends in a clutch
        return f"{_kills(clip.kills_n)} CLUTCH"
    if clip.won_clutch == 2:
        return "1V2?!"
    if clip.kills_n >= 5:
        return "ACE?!"
    for flag, text in (("knife", "KNIFE?!"), ("zeus", "ZEUS?!"), ("noscope", "NOSCOPE?!")):
        if flag in desc:
            return text
    if clip.kills_n >= 3:
        return f"{clip.kills_n}K?!"
    return "HOW?!"


def _kills(n: int) -> str:
    return "ACE" if n >= 5 else f"{n}K"


def feat_rank(c: ClipPlan) -> tuple[float, int, float]:
    """How striking a clip's feat is on a thumbnail or in a title (not its highlight score): a 1v5 beats
    a 1v4, which beats an ace, then 1v3s, 4Ks, 4K-into-1v2s and the rest; kills, then score break ties."""
    if c.won_clutch >= 5:
        f = 6.0
    elif c.won_clutch == 4:
        f = 5.0
    elif c.kills_n >= 5:
        f = 4.5
    elif c.won_clutch == 3:
        f = 4.0
    elif c.kills_n >= 4:
        f = 3.0 + (0.5 if c.won_clutch else 0.0)
    else:
        f = c.kills_n / 2 + c.won_clutch / 4
    return f, c.kills_n, c.score


def thumbnail_picks(clips: list[ClipPlan], count: int, preferred: list[int] | None = None) -> list[ClipPlan]:
    """The clips for the thumbnails: `preferred` (e.g. the model's picks) first, then the most striking
    feats, one per callout while there are different ones, so the variants suit a 3-way thumbnail test."""
    pool = [c for c in clips if not c.broll]
    by_number = {c.number: c for c in pool}
    ranked = sorted(pool, key=feat_rank, reverse=True)
    out = [by_number[n] for n in preferred or [] if n in by_number][:count]
    for c in ranked:
        if len(out) >= count:
            break
        if c not in out and callout(c) not in {callout(o) for o in out}:
            out.append(c)
    out += [c for c in ranked if c not in out]
    return out[:count]


_NUMBER_WORDS = {2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven", 8: "Eight", 9: "Nine"}


@dataclass(frozen=True)
class Facts:
    """What an episode can honestly claim, for titles (and for the model writing them)."""

    clips: int
    aces: int
    clutches: dict[int, int] = field(default_factory=dict)  # won 1vN clutch size -> how many
    quads: int = 0  # 4Ks that are not part of an ace
    ace_divisions: tuple[str, ...] = ()  # e.g. ("ESEA Open",) for each ace, league range dropped

    @property
    def biggest_clutch(self) -> int:
        return max(self.clutches, default=0)

    def as_dict(self) -> dict:
        return {"clips": self.clips, "aces": self.aces, "quad_kills": self.quads,
                "won_clutches": {f"1v{n}": k for n, k in sorted(self.clutches.items(), reverse=True)},
                "ace_divisions": list(self.ace_divisions)}


def _division(c: ClipPlan, league: str) -> str:
    d = re.sub(r"\s*\d+\s*-\s*\d+$", "", c.division or "").strip()  # "ESEA Open 1-8" -> "ESEA Open"
    if d and league and not d.lower().startswith(league.lower()):
        d = f"{league} {d}"
    return d


def episode_facts(timeline: Timeline, league: str = "") -> Facts:
    clips = [c for c in timeline.clips if not c.broll]
    clutches: dict[int, int] = {}
    for c in clips:
        if c.won_clutch >= 2:
            clutches[c.won_clutch] = clutches.get(c.won_clutch, 0) + 1
    aces = [c for c in clips if c.kills_n >= 5]
    return Facts(len(clips), len(aces), clutches, sum(c.kills_n == 4 for c in clips),
                 tuple(d for d in (_division(c, league) for c in aces) if d))


def _count(n: int, noun: str) -> str:
    """'an Ace', 'Three Aces', 'a 1v4 Clutch', 'Two 1v4 Clutches'."""
    if n == 1:
        return ("an " if noun[0] in "AEIOU" else "a ") + noun
    plural = noun + ("es" if noun.endswith(("ch", "s")) else "s")
    return f"{_NUMBER_WORDS.get(n, str(n))} {plural}"


def title_options(timeline: Timeline, info: "DescriptionInfo", limit: int = 70) -> list[str]:
    """Hook-first titles built only from the episode's stats, strongest first; the series name goes last so
    the hook survives truncation. They name what the episode holds without repeating one thumbnail's callout."""
    f = episode_facts(timeline, info.league)
    tag = f"EP.{info.episode:02d}"
    suffixes = (f" | CS2 Clips of the Week {tag}", f" | LANChamp {tag}", "")
    hooks: list[str] = []
    big = f.biggest_clutch
    if f.aces or big >= 3:
        parts = []
        if f.aces:
            parts.append(_count(f.aces, "Ace"))
        if big >= 3:
            parts.append(_count(f.clutches[big], f"1v{big} Clutch"))
        hooks.append(" & ".join(parts) + " in One Week")
    if big >= 3:
        hooks.append(f"Down 1v{big}. Then This Happened.")
    if f.aces and f.ace_divisions:
        div = max(set(f.ace_divisions), key=f.ace_divisions.count)
        n = f.ace_divisions.count(div)
        hooks.append(f"{_count(n, 'Ace')} From {div} Players This Week")
    if sum(f.clutches.values()) >= 3:
        hooks.append(f"{sum(f.clutches.values())} Clutches, {f.clips} Plays, One Week")
    if info.top_n:
        hooks.append(f"Top {info.top_n} {info.league + ' ' if info.league else ''}CS2 Plays of the Week")
    hooks.append(f"The Best {info.league + ' ' if info.league else ''}Plays This Week")
    out = []
    for h in hooks:
        h = h[0].upper() + h[1:]
        title = next((h + sfx for sfx in suffixes if len(h + sfx) <= limit), h[:limit])
        if title not in out:
            out.append(title)
    return out


def title_is_honest(title: str, facts: Facts) -> bool:
    """False when a title gets a number wrong: a clutch size the episode does not have, or an ace or clutch
    count that is not the real one (fewer is wrong too: "2 aces" in a three-ace week)."""
    t = title.lower()
    for n in re.findall(r"1\s*v\s*(\d)", t):
        if int(n) not in facts.clutches:
            return False
    words = {w.lower(): k for k, w in _NUMBER_WORDS.items()} | {"an": 1, "a": 1, "one": 1}
    for num, _ in re.findall(r"\b(\d+|an?|one|two|three|four|five|six|seven|eight|nine)\s+aces?\b()", t):
        if (int(num) if num.isdigit() else words[num]) != facts.aces:
            return False
    for num, size in re.findall(r"\b(\d+|an?|one|two|three|four|five|six|seven|eight|nine)\s+1\s*v\s*(\d)\s+clutch", t):
        if (int(num) if num.isdigit() else words[num]) != facts.clutches.get(int(size), 0):
            return False
    for num in re.findall(r"\b(\d+|two|three|four|five|six|seven|eight|nine)\s+(?:won\s+)?clutch(?:es)?\b(?!\s+won)", t):
        if (int(num) if num.isdigit() else words[num]) != sum(facts.clutches.values()):
            return False
    if "ace" in re.findall(r"\bace\b", t) and not facts.aces:
        return False
    return True


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
    """The strongest of `title_options`."""
    return title_options(timeline, info)[0]


def intro_line(timeline: Timeline, info: DescriptionInfo) -> str:
    """The description's first line (shown under the title in search): the episode's biggest numbers first."""
    f = episode_facts(timeline, info.league)
    bits = []
    if f.aces:
        bits.append(_count(f.aces, "Ace").lower())
    for n in sorted(f.clutches, reverse=True)[:2]:
        if n >= 3:
            bits.append(_count(f.clutches[n], f"1v{n} Clutch").lower())
    league = f"{info.league} " if info.league else ""
    head = (", ".join(bits[:-1]) + " and " + bits[-1] if len(bits) > 1 else bits[0]) if bits else ""
    lead = f"{head[0].upper() + head[1:]}: " if head else ""
    when = f"{league}matches in the week of {info.week}" if info.week else f"this week's {league}matches"
    return (f"{lead}the {f.clips} best plays from {when}. "
            f"Clips of the Week EP.{info.episode:02d}, every player credited below.")


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
    lines = ["TITLE OPTIONS", *(titles or title_options(timeline, info)), ""]
    lines += [intro_text or intro_line(timeline, info), ""]
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
