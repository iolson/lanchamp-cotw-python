"""Cut transitions: short effects applied as extra filters on one concatenated video stream.

A transition is centred on a cut time t on the video's own clock; it never changes the stream's
length or frame size. Only filters that ship with FFmpeg >= 4.4 on macOS and Windows are used.
Ramps are built from the overflow-safe shape `lt(t,a)*(rise)+gte(t,a)*exp(-K*max(0,t-a))`: the
exp() argument is never positive, because exp of a large positive number is inf and inf*0 is NaN,
which renders black frames. `noise` strengths are static (the option takes no `t`), so bursts are
gated with `enable='between(t,a,b)'`; `blend` opacity expressions see the frame time as `T`.
"""

from __future__ import annotations

import random

from cotw.compose import _Graph, _num

KINDS = ("film-burn", "no-signal", "glitch", "old-film", "grunge", "pixel", "anamorphic")

# (seconds before the cut, seconds after the cut) each look covers.
WINDOWS = {
    "film-burn": (0.35, 0.25),
    "no-signal": (0.15, 0.35),
    "glitch": (0.15, 0.35),
    "old-film": (0.3, 0.3),
    "grunge": (0.3, 0.3),
    "pixel": (0.3, 0.3),
    "anamorphic": (0.3, 0.3),
}

PIXEL_BLOCKS = (8, 24, 48)  # block sizes of the three pixel sub-windows, rising into the cut
PIXEL_OFFSETS = (-0.3, -0.1, 0.1)  # start of each sub-window, relative to the cut


def plan_transitions(cuts: list[float], mode: str, seed: int | None = None, clean: float = 0.0) -> list[tuple[float, str]]:
    """(cut time, kind) per cut. "none" drops all; a kind from KINDS puts it on every cut; "random"
    picks a seeded kind per cut, never the same one twice in a row when more than one is available;
    a comma list such as "glitch,pixel" picks among those. `clean` is the fraction of cuts left
    without any transition (seeded)."""
    if mode == "none":
        return []
    if mode == "random":
        pool = list(KINDS)
    else:
        pool = [p.strip() for p in mode.split(",") if p.strip()]
        bad = [p for p in pool if p not in KINDS]
        if bad:
            raise ValueError(f"unknown transition(s) {', '.join(repr(b) for b in bad)}; valid: {', '.join(KINDS)}")
    rng = random.Random(seed)
    out: list[tuple[float, str]] = []
    prev: str | None = None
    for t in cuts:
        if pool and rng.random() < clean:
            prev = None
            continue
        options = [k for k in pool if k != prev] or pool
        prev = rng.choice(options)
        out.append((t, prev))
    return sorted(out)


def window(kind: str) -> tuple[float, float]:
    """(seconds before the cut, seconds after the cut) that the effect covers."""
    if kind not in WINDOWS:
        raise ValueError(f"unknown transition {kind!r}; valid: {', '.join(KINDS)}")
    return WINDOWS[kind]


def _between(a: float, b: float) -> str:
    return f"between(t,{_num(a)},{_num(b)})"


def _ramp(t: float, before: float, after: float, decay: float = 10.0, clock: str = "t") -> str:
    """0 -> 1 linearly over the window before the cut, then exp decay after it."""
    a, b = _num(t), _num(t - before)
    rise = f"({clock}-{b})/{_num(before)}"
    return (f"lt({clock},{b})*{rise}"
            f"+gte({clock},{b})*lt({clock},{a})*exp(-{_num(decay)}*max(0,{clock}-{b}))")


def _stage(g: _Graph, prev: str, nxt: str, filters: list[str]) -> str:
    g.chain(f"[{prev}]{','.join(filters)}[{nxt}]")
    return nxt


def _film_burn(g: _Graph, prev: str, label: str, t: float) -> str:
    before, after = WINDOWS["film-burn"]
    e = _ramp(t, before, after)
    return _stage(g, prev, label, [
        f"eq=brightness='0.32*{e}':saturation='1+0.5*{e}':eval=frame",
        f"colorbalance=rm=0.25:rh=0.3:gm=0.1:bm=-0.25:enable='{_between(t - before, t + after)}'",
        f"noise=alls=18:allf=t+u:enable='{_between(t - before, t + after)}'",
    ])


def _no_signal(g: _Graph, prev: str, label: str, t: float) -> str:
    before, after = WINDOWS["no-signal"]
    win = _between(t - before, t + after)
    return _stage(g, prev, label, [
        f"noise=alls=100:allf=t+u:enable='{win}'",
        f"eq=brightness='0.12*sin(2*PI*t*13)+0.08*sin(2*PI*t*31)':eval=frame:enable='{win}'",
        f"hue=s=0.2:enable='{win}'",
        f"drawbox=x=0:y='mod(t*220,ih)':w=iw:h=6:color=white@0.25:t=fill:enable='{win}'",
    ])


def _glitch(g: _Graph, prev: str, label: str, t: float) -> str:
    before, after = WINDOWS["glitch"]
    win = _between(t - before, t + after)
    thirds = [(t - before + i * (before + after) / 3, t - before + (i + 1) * (before + after) / 3)
              for i in range(3)]
    shifts = ["rh=-8:bh=8", "rh=6:bh=-6", "rh=-8:bh=8"]
    return _stage(g, prev, label,
                  [f"rgbashift={s}:enable='{_between(a, b)}'" for s, (a, b) in zip(shifts, thirds)] + [
        f"noise=alls=35:allf=t+u:enable='{win}'",
        f"eq=contrast=1.3:eval=frame:enable='{win}'",
        f"crop=iw-16:ih-8:x='8+10*sin(2*PI*t*47)':y='4+3*sin(2*PI*t*29)',"
        f"pad=iw+16:ih+8:8:4:color=black",
    ])


def _old_film(g: _Graph, prev: str, label: str, t: float) -> str:
    before, after = WINDOWS["old-film"]
    win = _between(t - before, t + after)
    return _stage(g, prev, label, [
        f"colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131:enable='{win}'",
        f"eq=brightness='0.04*sin(t*70)':contrast=0.95:saturation=0.75:eval=frame:enable='{win}'",
        f"noise=alls=25:allf=t+u:enable='{win}'",
        f"vignette=PI/4:enable='{win}'",
    ])


def _grunge(g: _Graph, prev: str, label: str, t: float) -> str:
    before, after = WINDOWS["grunge"]
    win = _between(t - before, t + after)
    return _stage(g, prev, label, [
        f"eq=contrast=1.45:saturation=0.55:brightness=-0.05:eval=frame:enable='{win}'",
        f"noise=alls=45:allf=t+u:enable='{win}'",
        f"unsharp=5:5:1.2:enable='{win}'",
        f"crop=iw-8:ih-8:x='4+4*sin(2*PI*t*37)':y='4+3*sin(2*PI*t*29)',"
        f"pad=iw+8:ih+8:4:4:color=black",
    ])


def _pixel(g: _Graph, prev: str, label: str, t: float, width: int, height: int) -> str:
    # one pixelated copy per sub-window, overlaid only while it is on; the base keeps the length
    n = len(PIXEL_BLOCKS)
    g.chain(f"[{prev}]split={n + 1}[{label}_b]" + "".join(f"[{label}_p{i}]" for i in range(n)))
    cur = f"{label}_b"
    for i, (block, off) in enumerate(zip(PIXEL_BLOCKS, PIXEL_OFFSETS)):
        a = t + off
        g.chain(
            f"[{label}_p{i}]scale=trunc(iw/{block}):trunc(ih/{block}):flags=neighbor,"
            f"scale={width}:{height}:flags=neighbor[{label}_px{i}]")
        nxt = f"{label}_m{i}" if i < n - 1 else label
        g.chain(f"[{cur}][{label}_px{i}]overlay=enable='{_between(a, a + 0.2)}'[{nxt}]")
        cur = nxt
    return cur


def _anamorphic(g: _Graph, prev: str, label: str, t: float) -> str:
    before, after = WINDOWS["anamorphic"]
    w = _ramp(t, before, after, clock="T")
    g.chain(f"[{prev}]split=2[{label}_base][{label}_src]")
    g.chain(
        f"[{label}_src]lutrgb=r='if(gt(val,180),val,0)':g='if(gt(val,180),val,0)':b='if(gt(val,180),val,0)',"
        f"gblur=sigma=80:sigmaV=0.1:steps=2,colorbalance=rs=-0.2:bs=0.3:rm=-0.15:bm=0.25[{label}_glow]")
    g.chain(f"[{label}_base][{label}_glow]blend=all_expr='A+B*0.8*({w})'[{label}]")
    return label


def add_transitions(g: _Graph, src: str, transitions: list[tuple[float, str]], *,
                    width: int, height: int, label: str = "vtr") -> str:
    """Append filter chains to `g` that read `[src]` and apply every transition; return the label of
    the result. With no transitions return `src` and add nothing."""
    cur = src
    for n, (t, kind) in enumerate(transitions, start=1):
        nxt = f"{label}{n}"
        if kind == "pixel":
            cur = _pixel(g, cur, nxt, t, width, height)
        elif kind == "anamorphic":
            cur = _anamorphic(g, cur, nxt, t)
        else:
            cur = _STAGES[kind](g, cur, nxt, t)
    return cur


_STAGES = {
    "film-burn": _film_burn,
    "no-signal": _no_signal,
    "glitch": _glitch,
    "old-film": _old_film,
    "grunge": _grunge,
}
