"""cotw-frag: cut a beat-synced frag-movie style edit ("FUZZYHAT: track name") from cs2-highlights clips.

Kills are grouped into bursts; every burst becomes a segment whose hero (last) kill lands exactly on a beat
of the music, with an optional eased slow-motion at the hero kill and a zoom punch / shake / flash on kills.
There is no title card: the edit goes straight into the clips.
"""

from __future__ import annotations

import random
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import click

from cotw.beats import BeatError, analyze_track, detect_beats
from cotw.compose import (
    CUT_FADE, SLOW_AUDIO, FfmpegError, MediaInfo, _Graph, _num, audio_duration, find_ffmpeg, probe, run_ffmpeg,
)
from cotw.graphics import H, W
from cotw.plan import (
    PlanError, RAMP_IN, SLOW_AFTER, SLOW_BEFORE, RAMP_OUT, ClipPlan, build_segments, build_timeline, clip_length,
    load_names, load_report, merge_timelines,
)

BURST_GAP = 3.0  # kills closer than this belong to the same burst
PRE_ROLL = 1.2  # seconds before a burst's first kill
TAIL = 1.3  # least seconds kept after the hero kill
MAX_LEAD_STEPS = 40
MAX_LEAD_SECONDS = 12.0


@dataclass(frozen=True)
class Burst:
    clip: ClipPlan
    kills: tuple[tuple[float, bool, bool], ...]  # (source seconds, headshot, special)
    score: float
    base: int = 0  # kills of this clip before the burst
    total: int = 0  # kills in the whole clip
    finale: bool = False  # the clip's last burst

    @property
    def first(self) -> float:
        return self.kills[0][0]

    @property
    def hero(self) -> float:
        return self.kills[-1][0]


@dataclass(frozen=True)
class Seg:
    burst: Burst
    start: float  # on the episode clock (= the music clock)
    end: float
    src_start: float
    src_len: float
    slow: tuple[tuple[float, float], ...] = ()
    speed: float = 0.5


@dataclass
class Look:
    effects: bool = True
    game_volume: float = 0.35
    music_volume: float = 0.9
    fps: int = 60
    encoder: str = "libx264"
    quality: int = 18
    letterbox: float | None = None  # e.g. 2.35 for cinematic bars; None = full frame
    grade: str = "none"  # none | vivid | warm
    transitions: str = "auto"  # none | auto | flash | ghost | whip
    seed: int | None = None


@dataclass(frozen=True)
class Track:
    path: Path
    seconds: float
    start: float  # where it starts on the edit's clock
    beats: tuple[float, ...]  # beat times on the edit's clock
    energy: tuple[int, ...] = ()  # 0..9 loudness per 10 s of the track


XFADE = 1.0  # seconds two tracks overlap when the music changes
GRADES = {
    "vivid": "eq=saturation=1.5:contrast=1.07,colorbalance=rs=0.03:rh=0.04:bh=-0.05",
    "warm": "eq=saturation=1.3:contrast=1.04,colorbalance=rh=0.06:bh=-0.08",
}
TRANSITION_KINDS = ("flash", "ghost", "whip")
GHOST_DELAY, GHOST_LEN = 0.2, 0.3


def _rank(b: Burst) -> tuple:
    return (b.score, b.clip.number, b.first)  # weakest first; a clip's bursts stay in recording order


def bursts_from(clips: list[ClipPlan]) -> list[Burst]:
    out: list[Burst] = []
    for clip in clips:
        ks = sorted((clip.src_start + t, hs, sp) for t, hs, sp in clip.kills)
        groups: list[list[tuple[float, bool, bool]]] = []
        for k in ks:
            if groups and k[0] - groups[-1][-1][0] <= BURST_GAP:
                groups[-1].append(k)
            else:
                groups.append([k])
        done = 0
        for gi, group in enumerate(groups):
            out.append(Burst(clip, tuple(group), clip.score, done, len(ks), gi == len(groups) - 1))
            done += len(group)
    return out


def src_to_out(segments: list[tuple[float, float, float]], offset: float) -> float:
    """Episode seconds at which `offset` source seconds into a clip are shown, given its speed pieces."""
    out = cum = 0.0
    for _, length, speed in segments:
        if offset <= cum + length:
            return out + (offset - cum) / speed
        out += length / speed
        cum += length
    return out


def _fit(b: Burst, lead: float, total: float, slow: bool, speed: float):
    """Source window so the hero kill is `lead` seconds into a segment of `total` seconds on screen.
    Returns (src_start, src_len, slow windows) or None when the footage does not allow it."""
    lo, hi, hero = b.clip.src_start, b.clip.src_start + b.clip.src_len, b.hero
    if not slow:
        start, length = hero - lead, total
        return (start, length, ()) if start >= lo - 1e-6 and start + length <= hi + 1e-6 else None
    l0, t0 = 2.0, SLOW_AFTER + RAMP_OUT + 0.2
    win0 = ((l0 - SLOW_BEFORE, l0 + SLOW_AFTER),)
    k = src_to_out(build_segments(l0 + t0, win0, speed), l0) - l0  # extra time slow motion adds before the hero
    pre = lead - k
    if pre < SLOW_BEFORE + RAMP_IN or hero - pre < lo - 1e-6:
        return None
    win = ((pre - SLOW_BEFORE, pre + SLOW_AFTER),)
    post = t0 + (total - clip_length(pre + t0, win, speed))
    if post < SLOW_AFTER + RAMP_OUT or hero + post > hi + 1e-6:
        return None
    return hero - pre, pre + post, win


def build_grid(tracks: list[Track], loose: bool) -> tuple[list[float], list[bool]]:
    """Cut points on the edit's clock and which of them are beats (the hero kill must land on one).
    With `loose` the half-beats in between are available for cuts too."""
    pts: list[tuple[float, bool]] = [(0.0, True)]
    for tr in tracks:
        beats = list(tr.beats)
        for i, t in enumerate(beats):
            pts.append((t, True))
            if loose and i + 1 < len(beats):
                pts.append(((t + beats[i + 1]) / 2, False))
    pts.sort()
    out: list[tuple[float, bool]] = []
    for t, is_beat in pts:
        if out and t - out[-1][0] < 0.05:
            out[-1] = (out[-1][0], out[-1][1] or is_beat)
        else:
            out.append((t, is_beat))
    return [t for t, _ in out], [f for _, f in out]


def pace_factor(tracks: list[Track], pace: str):
    """Longer pre-roll and tail where the music is quiet, tight cuts where it is loud."""
    if pace == "flat":
        return None

    def factor(t: float) -> float:
        for tr in reversed(tracks):
            if t >= tr.start:
                i = int((t - tr.start) // 10)
                if tr.energy and i < len(tr.energy):
                    return 1.0 + 0.8 * (1 - tr.energy[i] / 9)
                break
        return 1.0

    return factor


def place(bursts: list[Burst], grid: list[float], limit: float, slowmo: str, speed: float,
          flags: list[bool] | None = None, factor=None) -> list[Seg]:
    """Lay bursts on the cut grid in order; stop when the next one would run past `limit`."""
    flags = flags or [True] * len(grid)
    segs: list[Seg] = []
    j = 0
    for b in bursts:
        f = factor(grid[j]) if factor else 1.0
        want_slow = slowmo == "all" or (slowmo == "auto" and b.finale and b.total >= 3)
        placed = None
        for slow in ([True, False] if want_slow else [False]):
            for m in range(1, MAX_LEAD_STEPS + 1):
                if j + m >= len(grid):
                    break
                lead = grid[j + m] - grid[j]
                if lead > MAX_LEAD_SECONDS:
                    break
                if not flags[j + m] or lead < (b.hero - b.first) + PRE_ROLL * f:
                    continue
                for n in range(1, 17):
                    if j + m + n >= len(grid):
                        break
                    total = grid[j + m + n] - grid[j]
                    if total - lead < TAIL * f:
                        continue
                    fit = _fit(b, lead, total, slow, speed)
                    if fit:
                        placed = (m, n, fit)
                        break
                if placed:
                    break
            if placed:
                break
        if not placed:
            continue
        m, n, (start, length, win) = placed
        end = grid[j + m + n]
        if end > limit:
            break
        segs.append(Seg(b, grid[j], end, start, length, win, speed))
        j += m + n
    return segs


def arrange_bursts(bursts: list[Burst], grid: list[float], limit: float, order: str, seed: int | None,
                   slowmo: str, speed: float, flags: list[bool] | None = None, factor=None) -> list[Seg]:
    """Order the bursts and drop the weakest until what is left fits the music."""
    if order == "score":
        bursts = sorted(bursts, key=_rank)
    elif order == "shuffle":
        bursts = bursts[:]
        random.Random(seed).shuffle(bursts)
    keep = bursts[:]
    while keep:
        segs = place(keep, grid, limit, slowmo, speed, flags, factor)
        if len(segs) == len(keep):
            return segs
        weakest = min(keep, key=_rank)
        keep.remove(weakest)  # not everything fits: lose the weakest burst and try again
    return []


def plan_transitions(segs: list[Seg], mode: str, seed: int | None) -> list[tuple[float, str]]:
    """(time, kind) for each cut between segments. `auto` mixes the kinds, leaves some cuts clean and
    puts a flash on the cut into the finale of a big (4+ kill) clip."""
    if mode == "none":
        return []
    rng = random.Random(seed if seed is not None else 7)
    out = []
    for s in segs[1:]:
        if mode in TRANSITION_KINDS:
            out.append((s.start, mode))
        elif s.burst.finale and s.burst.total >= 4:
            out.append((s.start, "flash"))
        else:
            kind = rng.choices([None, "flash", "ghost", "whip"], [0.35, 0.2, 0.2, 0.25])[0]
            if kind:
                out.append((s.start, kind))
    return out


def kill_times(segs: list[Seg]) -> list[tuple[float, bool, int, int]]:
    """(episode time, is hero, kill number within its clip, kills in the clip) for every kill."""
    out = []
    for s in segs:
        pieces = build_segments(s.src_len, s.slow, s.speed)
        for i, (t, _, _) in enumerate(s.burst.kills):
            out.append((s.start + src_to_out(pieces, t - s.src_start), i == len(s.burst.kills) - 1,
                        s.burst.base + i + 1, s.burst.total))
    return out


def _punch_exprs(kills: list[tuple[float, bool, int, int]]) -> tuple[str, str, str, str]:
    """FFmpeg per-frame expressions of time for the zoom punch, flash and shake around kills.

    `max(0,t-a)` keeps every exp() argument non-positive: exp(K*(a-t)) overflows to inf for late kills,
    and 0*inf is NaN, which turned the whole frame black.
    """
    zoom, brightness, sx, sy = [], [], [], []
    for t, hero, _, _ in kills:
        a = _num(t)
        age = f"max(0,t-{a})"
        win = f"gte(t,{a})*lt(t,{_num(t + 0.7)})"
        zoom.append(f"{0.11 if hero else 0.05}*exp(-9*{age})*{win}")
        if hero:
            brightness.append(f"0.22*exp(-16*{age})*{win}")
            sx.append(f"16*exp(-8*{age})*sin(75*{age})*{win}")
            sy.append(f"11*exp(-8*{age})*sin(61*{age})*{win}")
    join = lambda parts: "+".join(parts) or "0"  # noqa: E731
    return join(zoom), join(brightness), join(sx), join(sy)


def _flash_expr(cuts: list[float]) -> str:
    """White-out around a cut: ramps up in 0.05 s before it, then decays."""
    parts = []
    for tc in cuts:
        a = _num(tc)
        parts.append(f"0.8*(gte(t,{_num(tc - 0.05)})*lt(t,{a})*(1-({a}-t)/0.05)+gte(t,{a})*lt(t,{_num(tc + 0.5)})*exp(-11*max(0,t-{a})))")
    return "+".join(parts) or "0"


def build_args(mains: list[tuple[Path, MediaInfo]], segs: list[Seg], tracks: list[Track], look: Look,
               output: Path) -> list[str]:
    g = _Graph()
    info = mains[0][1]
    fps = str(look.fps)
    size = f"{info.width}:{info.height}"
    end = segs[-1].end
    pieces: list[str] = []
    for k, s in enumerate(segs):
        path, mi = mains[s.burst.clip.source]
        parts = build_segments(s.src_len, s.slow, s.speed)
        for j, (off, length, speed) in enumerate(parts):
            tag, slow = f"{k}_{j}", abs(speed - 1.0) > 1e-6
            idx = g.add_input("-ss", _num(s.src_start + off), "-t", _num(length), "-i", str(path))
            retime = f"setpts=(PTS-STARTPTS)/{_num(speed)}," if slow else ""
            g.chain(f"[{idx}:v]{retime}fps={fps},scale={size},setsar=1[v{tag}]")
            shown = length / speed
            if mi.has_audio:
                audio = f"[{idx}:a:0]aresample=48000,aformat=channel_layouts=stereo" + (
                    SLOW_AUDIO.format(speed=_num(speed)) if slow else "")
            else:
                sil = g.add_input("-f", "lavfi", "-t", _num(shown), "-i", "anullsrc=r=48000:cl=stereo")
                audio = f"[{sil}:a]anull"
            fades = []
            if j == 0:
                fades.append(f"afade=t=in:d={CUT_FADE}")
            if j == len(parts) - 1:
                fades.append(f"afade=t=out:st={_num(max(0.0, shown - CUT_FADE))}:d={CUT_FADE}")
            g.chain(audio + "".join("," + f for f in fades) + f"[a{tag}]")
            pieces.append(f"[v{tag}][a{tag}]")
    g.chain(f"{''.join(pieces)}concat=n={len(pieces)}:v=1:a=1[vcat][agame]")

    kills = kill_times(segs)
    cuts = plan_transitions(segs, look.transitions, look.seed)
    flash_cuts = [t for t, kind in cuts if kind == "flash"]
    ghost_cuts = [t for t, kind in cuts if kind == "ghost"]
    whip_cuts = [t for t, kind in cuts if kind == "whip"]

    steps: list[str] = []  # filters applied in order to the picture
    if look.effects:
        z, br, sx, sy = _punch_exprs(kills)
        steps.append(
            f"scale=w='trunc({info.width}*(1+{z})/2)*2':h='trunc({info.height}*(1+{z})/2)*2':eval=frame,"
            f"crop={info.width}:{info.height}:x='(in_w-out_w)/2+{sx}':y='(in_h-out_h)/2+{sy}'")
    else:
        br = "0"
    if look.grade in GRADES:
        steps.append(GRADES[look.grade])
    flash = "+".join(x for x in (br if br != "0" else "", _flash_expr(flash_cuts) if flash_cuts else "") if x)
    if flash:
        steps.append(f"eq=brightness='{flash}':eval=frame")
    for tc in whip_cuts:
        steps.append(f"gblur=sigma=45:sigmaV=0.1:steps=2:enable='between(t,{_num(tc - 0.06)},{_num(tc + 0.08)})'")
    if look.effects:
        steps.append("vignette=PI/6")

    cur = "vcat"
    if steps:
        g.chain(f"[vcat]{','.join(steps)}[vfx]")
        cur = "vfx"
    if ghost_cuts:  # fading echo of the previous shot over the start of the next one
        w = "+".join(f"0.55*max(0,1-(T-{_num(t)})/{GHOST_LEN})*gte(T,{_num(t)})" for t in ghost_cuts)
        g.chain(f"[{cur}]split=2[gm][gd]")
        g.chain(f"[gd]tpad=start_mode=clone:start_duration={GHOST_DELAY}[gdd]")
        g.chain(f"[gm][gdd]blend=all_expr='A*(1-({w}))+B*({w})':shortest=1[vgh]")
        cur = "vgh"
    if look.letterbox:
        bar = round(info.height * (1 - (16 / 9) / look.letterbox) / 2)
        if bar > 0:
            g.chain(f"[{cur}]drawbox=x=0:y=0:w=iw:h={bar}:color=black:t=fill,"
                    f"drawbox=x=0:y=ih-{bar}:w=iw:h={bar}:color=black:t=fill[vlb]")
            cur = "vlb"
    g.chain(f"[{cur}]format=yuv420p[vout]")

    labels = []
    tracks = [t for t in tracks if t.start < end - 0.5]  # a later song the edit never reaches is not mixed in
    for n, tr in enumerate(tracks):
        i = g.add_input("-i", str(tr.path))
        natural_end = tr.start + tr.seconds
        track_end = min(natural_end, end)
        last = n == len(tracks) - 1
        fade_out = min(2.5 if last else XFADE, track_end - tr.start)
        g.chain(
            f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,volume={look.music_volume},"
            f"afade=t=in:d={0.4 if n == 0 else XFADE},adelay={int(tr.start * 1000)}:all=1,"
            f"afade=t=out:st={_num(track_end - fade_out)}:d={_num(fade_out)},atrim=end={_num(end)}[mus{n}]")
        labels.append(f"[mus{n}]")
    g.chain(f"[agame]volume={look.game_volume}[ag]")
    g.chain(f"[ag]{''.join(labels)}amix=inputs={1 + len(labels)}:duration=first:normalize=0[aout]")

    venc = (["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(look.quality), "-b:v", "0"]
            if look.encoder == "h264_nvenc" else ["-c:v", "libx264", "-preset", "medium", "-crf", str(look.quality)])
    return ["-y", *g.inputs, "-filter_complex", ";".join(g.chains), "-map", "[vout]", "-map", "[aout]", *venc,
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output)]


def track_labels(ffmpeg: Path, track: Path) -> tuple[str, str]:
    """(track name, artist) from the file's tags, else from an 'Artist - Title.mp3' file name."""
    tags: dict[str, str] = {}
    result = subprocess.run(
        [str(ffmpeg.with_name("ffprobe" + ffmpeg.suffix)) if ffmpeg.with_name("ffprobe" + ffmpeg.suffix).is_file() else "ffprobe",
         "-v", "error", "-show_entries", "format_tags=title,artist", "-of", "default=nw=1", str(track)],
        capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            tags[k.replace("TAG:", "").lower()] = v.strip()
    stem = track.stem
    artist, _, title = stem.partition(" - ")
    if not title:
        artist, title = "", stem
    return tags.get("title") or title, tags.get("artist") or artist


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("videos", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--music", "music_paths", required=True, multiple=True,
              type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Music track; repeat it to change songs during the edit (they overlap by a second).")
@click.option("--name", help="Name used in the output file name (default: the player in the clips).")
@click.option("--track-name", help="Track name used in the output file name (default: from the file's tags / name).")
@click.option("--player", help="Only use clips by this player (display name, case-insensitive).")
@click.option("-o", "--output", type=click.Path(dir_okay=False, path_type=Path), help="Output MP4 (default: output/<name> - <track>.mp4).")
@click.option("--order", type=click.Choice(["score", "chronological", "shuffle"]), default="score", show_default=True,
              help="Segment order; with score the best burst comes last (and the weakest are dropped first).")
@click.option("--seed", type=int, help="Seed for --order shuffle.")
@click.option("--slowmo", type=click.Choice(["auto", "all", "none"]), default="auto", show_default=True,
              help="Eased slow motion on the hero kill: auto = bursts of 3+ kills.")
@click.option("--slowmo-speed", type=click.FloatRange(0.5, 0.95), default=0.5, show_default=True)
@click.option("--max-seconds", type=float, help="Cap the edit's length (default: the music's length).")
@click.option("--no-effects", is_flag=True, help="Skip the per-kill zoom punch, shake, flash and vignette.")
@click.option("--letterbox", is_flag=True, help="Cinematic black bars (see --letterbox-ratio).")
@click.option("--letterbox-ratio", type=float, default=2.35, show_default=True)
@click.option("--grade", type=click.Choice(["none", "vivid", "warm"]), default="none", show_default=True,
              help="Colour grade: vivid (punchy saturation) or warm.")
@click.option("--transitions", type=click.Choice(["auto", "none", "flash", "ghost", "whip"]), default="auto",
              show_default=True, help="Cut transitions: white flash, ghost echo of the previous shot, whip blur; "
              "auto mixes them and leaves some cuts clean.")
@click.option("--sync", type=click.Choice(["loose", "tight"]), default="loose", show_default=True,
              help="loose: cuts may also land on half beats (hero kills always on a beat); tight: beats only.")
@click.option("--pace", type=click.Choice(["auto", "flat"]), default="auto", show_default=True,
              help="auto: longer shots where the music is quiet, quick cuts where it is loud.")
@click.option("--game-volume", type=float, default=Look.game_volume, show_default=True)
@click.option("--music-volume", type=float, default=Look.music_volume, show_default=True)
@click.option("--names", "names_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--fps", type=int, default=Look.fps, show_default=True)
@click.option("--encoder", type=click.Choice(["libx264", "h264_nvenc"]), default="libx264", show_default=True)
@click.option("--quality", type=int, default=Look.quality, show_default=True)
@click.option("--ffmpeg", "ffmpeg_path", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--dry-run", is_flag=True, help="Print the plan and FFmpeg command only.")
def main(videos, music_paths, name, track_name, player, output, order, seed, slowmo, slowmo_speed, max_seconds,
         no_effects, letterbox, letterbox_ratio, grade, transitions, sync, pace, game_volume, music_volume,
         names_path, fps, encoder, quality, ffmpeg_path, dry_run) -> None:
    """Cut a beat-synced highlight edit from VIDEOS (cs2-highlights MP4s with their JSON)."""
    if len(videos) == 1 and videos[0].is_dir():
        videos = tuple(sorted(videos[0].glob("*.mp4")))
    try:
        ffmpeg = find_ffmpeg(ffmpeg_path)
        names_path = names_path or (Path("names.json") if Path("names.json").is_file() else None)
        names = load_names(names_path) if names_path else {}
        mains, timelines = [], []
        for v in videos:
            info = probe(ffmpeg, v)
            tl = build_timeline(load_report(v.with_suffix(".json")), video_duration=info.duration, names=names)
            mains.append((v, info))
            timelines.append(tl)
        clips = merge_timelines(timelines).clips
        if player:
            clips = [c for c in clips if c.player.lower() == player.lower()]
            if not clips:
                raise click.UsageError(f"no clips by {player!r}")
        bursts = bursts_from(clips)
        if not bursts:
            raise click.UsageError("the reports have no per-kill data (beat_targets)")
        tracks: list[Track] = []
        for path in music_paths:
            start = tracks[-1].start + tracks[-1].seconds - XFADE if tracks else 0.0
            facts = analyze_track(path)
            tracks.append(Track(path, audio_duration(ffmpeg, path), start,
                                tuple(start + b for b in detect_beats(path)), tuple(facts["energy_per_10s"])))
    except (FfmpegError, PlanError, BeatError) as exc:
        raise click.ClickException(str(exc)) from exc

    music_seconds = tracks[-1].start + tracks[-1].seconds
    grid, flags = build_grid(tracks, loose=sync == "loose")
    limit = min(music_seconds - 1.0, max_seconds or 1e9)
    segs = arrange_bursts(bursts, grid, limit, order, seed, slowmo, slowmo_speed, flags, pace_factor(tracks, pace))
    if not segs:
        raise click.ClickException("no bursts fit on the beat grid")
    click.echo(f"{len(segs)} of {len(bursts)} bursts, {segs[-1].end:.1f}s, music "
               f"{' + '.join(t.path.name for t in tracks)} ({music_seconds:.0f}s)")
    for s in segs:
        c = s.burst.clip
        click.echo(f"  {s.start:6.1f}-{s.end:6.1f}s  {c.player} · {len(s.burst.kills)} of {s.burst.total} kills · score {s.burst.score:g}"
                   f"{'  slow-mo' if s.slow else ''}")

    name = name or max({c.player for c in clips}, key=lambda p: sum(c.player == p for c in clips))
    track = track_name or track_labels(ffmpeg, tracks[0].path)[0]
    output = output or Path("output" if Path("output").is_dir() else ".") / f"{name} - {track}.mp4"
    output = Path(re.sub(r'[<>:"|?*]', "", str(output)))
    look = Look(not no_effects, game_volume, music_volume, fps, encoder, quality,
                letterbox_ratio if letterbox else None, grade, transitions, seed)
    args = build_args(mains, segs, tracks, look, output)
    if dry_run:
        click.echo(subprocess.list2cmdline([str(ffmpeg), *args]))
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    click.echo(f"Encoding {output} ...")
    try:
        run_ffmpeg(ffmpeg, args)
    except FfmpegError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Done: {output}")


if __name__ == "__main__":
    sys.exit(main())
