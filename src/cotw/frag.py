"""cotw-frag: cut a beat-synced frag-movie style edit ("FUZZYHAT: track name") from cs2-highlights clips.

Kills are grouped into bursts; every burst becomes a segment whose hero (last) kill lands exactly on a beat
of the music, with an optional eased slow-motion at the hero kill, a zoom punch / shake / flash on kills, a
kill counter and an animated title.
"""

from __future__ import annotations

import random
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import click

from cotw import motion
from cotw.beats import BeatError, detect_beats
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
MAX_LEAD_BEATS = 16


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


def place(bursts: list[Burst], grid: list[float], limit: float, slowmo: str, speed: float) -> list[Seg]:
    """Lay bursts on the beat grid in order; stop when the next one would run past `limit`."""
    segs: list[Seg] = []
    j = 0
    for b in bursts:
        want_slow = slowmo == "all" or (slowmo == "auto" and b.finale and b.total >= 3)
        placed = None
        for slow in ([True, False] if want_slow else [False]):
            for m in range(1, MAX_LEAD_BEATS + 1):
                if j + m >= len(grid):
                    break
                lead = grid[j + m] - grid[j]
                if lead < (b.hero - b.first) + PRE_ROLL:
                    continue
                for n in range(1, 9):
                    if j + m + n >= len(grid):
                        break
                    total = grid[j + m + n] - grid[j]
                    if total - lead < TAIL:
                        continue
                    fit = _fit(b, lead, total, slow, speed)
                    if fit:
                        placed = (m, n, fit, slow)
                        break
                if placed:
                    break
            if placed:
                break
        if not placed:
            continue
        m, n, (start, length, win), slow = placed
        end = grid[j + m + n]
        if end > limit:
            break
        segs.append(Seg(b, grid[j], end, start, length, win, speed))
        j += m + n
    return segs


def arrange_bursts(bursts: list[Burst], grid: list[float], limit: float, order: str, seed: int | None,
                   slowmo: str, speed: float) -> list[Seg]:
    """Order the bursts and drop the weakest until what is left fits the music."""
    if order == "score":
        bursts = sorted(bursts, key=_rank)
    elif order == "shuffle":
        bursts = bursts[:]
        random.Random(seed).shuffle(bursts)
    keep = bursts[:]
    while keep:
        segs = place(keep, grid, limit, slowmo, speed)
        if len(segs) == len(keep):
            return segs
        weakest = min(keep, key=_rank)
        keep.remove(weakest)  # not everything fits: lose the weakest burst and try again
    return []


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


def build_args(mains: list[tuple[Path, MediaInfo]], segs: list[Seg], music: Path, music_seconds: float,
               look: Look, assets: dict, output: Path) -> list[str]:
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
    cur = "vcat"
    if look.effects:
        z, br, sx, sy = _punch_exprs(kills)
        g.chain(
            f"[vcat]scale=w='trunc({info.width}*(1+{z})/2)*2':h='trunc({info.height}*(1+{z})/2)*2':eval=frame,"
            f"crop={info.width}:{info.height}:x='(in_w-out_w)/2+{sx}':y='(in_h-out_h)/2+{sy}',"
            f"eq=contrast=1.06:saturation=1.22:brightness='{br}':eval=frame,vignette=PI/6[vfx]"
        )
        cur = "vfx"

    step = 0

    def overlay(top: str, start: float, length: float) -> None:
        nonlocal cur, step
        step += 1
        g.chain(f"[{cur}][{top}]overlay=0:0:format=auto:eof_action=pass:"
                f"enable='between(t,{_num(start)},{_num(start + length)})'[o{step}]")
        cur = f"o{step}"

    rgba = f"format=rgba,scale={size}" if (info.width, info.height) != (W, H) else "format=rgba"
    if "title" in assets:
        i = g.add_input("-framerate", str(motion.TITLE_FPS), "-i", str(assets["title"] / "%03d.png"))
        start = 0.3
        g.chain(f"[{i}:v]{rgba},fps={fps},setpts=PTS+{_num(start)}/TB[title]")
        overlay("title", start, motion.TITLE_SECONDS)
    for t, _, count, size_ in kills:
        if size_ >= 2 and count in assets.get("counters", {}):
            i = g.add_input("-framerate", str(motion.COUNTER_FPS), "-i", str(assets["counters"][count] / "%03d.png"))
            g.chain(f"[{i}:v]{rgba},fps={fps},setpts=PTS+{_num(t)}/TB[c{step}]")
            overlay(f"c{step}", t, motion.COUNTER_SECONDS)
    g.chain(f"[{cur}]format=yuv420p[vout]")

    mi_ = g.add_input("-i", str(music))
    fade = min(2.5, end)
    g.chain(f"[{mi_}:a]aresample=48000,aformat=channel_layouts=stereo,volume={look.music_volume},"
            f"atrim=end={_num(end)},afade=t=in:d=0.4,afade=t=out:st={_num(end - fade)}:d={_num(fade)}[mus]")
    g.chain(f"[agame]volume={look.game_volume}[ag]")
    g.chain("[ag][mus]amix=inputs=2:duration=first:normalize=0[aout]")

    venc = (["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(look.quality), "-b:v", "0"]
            if look.encoder == "h264_nvenc" else ["-c:v", "libx264", "-preset", "medium", "-crf", str(look.quality)])
    return ["-y", *g.inputs, "-filter_complex", ";".join(g.chains), "-map", "[vout]", "-map", "[aout]", *venc,
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output)]


def _title_job(args):
    name, sub, i, n, path = args
    motion.title_frame(name, sub, i / (n - 1)).save(path, compress_level=1)


def _counter_job(args):
    count, i, n, path = args
    motion.counter_frame(count, i / (n - 1)).save(path, compress_level=1)


def render_graphics(work: Path, name: str, subtitle: str, max_count: int) -> dict:
    assets: dict = {"title": work / "title", "counters": {}}
    assets["title"].mkdir()
    jobs = [(name, subtitle, i, motion.title_frame_count(), assets["title"] / f"{i:03d}.png")
            for i in range(motion.title_frame_count())]
    cjobs = []
    for c in range(2, max_count + 1):
        d = work / f"counter{c}"
        d.mkdir()
        assets["counters"][c] = d
        cjobs += [(c, i, motion.counter_frame_count(), d / f"{i:03d}.png") for i in range(motion.counter_frame_count())]
    with ProcessPoolExecutor() as pool:
        list(pool.map(_title_job, jobs, chunksize=4))
        list(pool.map(_counter_job, cjobs, chunksize=4))
    return assets


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
@click.option("--music", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Music track.")
@click.option("--title", help="Name on the title card (default: the player in the clips).")
@click.option("--track-name", help="Track name on the title card (default: from the file's tags / name).")
@click.option("--player", help="Only use clips by this player (display name, case-insensitive).")
@click.option("-o", "--output", type=click.Path(dir_okay=False, path_type=Path), help="Output MP4 (default: output/<title> - <track>.mp4).")
@click.option("--order", type=click.Choice(["score", "chronological", "shuffle"]), default="score", show_default=True,
              help="Segment order; with score the best burst comes last (and the weakest are dropped first).")
@click.option("--seed", type=int, help="Seed for --order shuffle.")
@click.option("--slowmo", type=click.Choice(["auto", "all", "none"]), default="auto", show_default=True,
              help="Eased slow motion on the hero kill: auto = bursts of 3+ kills.")
@click.option("--slowmo-speed", type=click.FloatRange(0.5, 0.95), default=0.5, show_default=True)
@click.option("--max-seconds", type=float, help="Cap the edit's length (default: the music's length).")
@click.option("--no-effects", is_flag=True, help="Skip zoom punch, shake, flash and colour grade.")
@click.option("--no-title", is_flag=True, help="Skip the animated title.")
@click.option("--no-counter", is_flag=True, help="Skip the kill counter.")
@click.option("--game-volume", type=float, default=Look.game_volume, show_default=True)
@click.option("--music-volume", type=float, default=Look.music_volume, show_default=True)
@click.option("--names", "names_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--fps", type=int, default=Look.fps, show_default=True)
@click.option("--encoder", type=click.Choice(["libx264", "h264_nvenc"]), default="libx264", show_default=True)
@click.option("--quality", type=int, default=Look.quality, show_default=True)
@click.option("--ffmpeg", "ffmpeg_path", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--dry-run", is_flag=True, help="Print the plan and FFmpeg command only.")
def main(videos, music, title, track_name, player, output, order, seed, slowmo, slowmo_speed, max_seconds,
         no_effects, no_title, no_counter, game_volume, music_volume, names_path, fps, encoder, quality,
         ffmpeg_path, dry_run) -> None:
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
        beats = detect_beats(music)
        music_seconds = audio_duration(ffmpeg, music)
    except (FfmpegError, PlanError, BeatError) as exc:
        raise click.ClickException(str(exc)) from exc

    grid = ([0.0] if beats[0] > 0.05 else []) + beats
    limit = min(music_seconds - 1.0, max_seconds or 1e9)
    segs = arrange_bursts(bursts, grid, limit, order, seed, slowmo, slowmo_speed)
    if not segs:
        raise click.ClickException("no bursts fit on the beat grid")
    kills = kill_times(segs)
    click.echo(f"{len(segs)} of {len(bursts)} bursts, {segs[-1].end:.1f}s, music {music.name} ({music_seconds:.0f}s)")
    for s in segs:
        c = s.burst.clip
        click.echo(f"  {s.start:6.1f}-{s.end:6.1f}s  {c.player} · {len(s.burst.kills)} of {s.burst.total} kills · score {s.burst.score:g}"
                   f"{'  slow-mo' if s.slow else ''}")

    name = (title or max({c.player for c in clips}, key=lambda p: sum(c.player == p for c in clips))).upper()
    track, artist = track_labels(ffmpeg, music)
    sub = f"{track_name or track} · {artist}" if artist and not track_name else (track_name or track)
    output = output or Path("output" if Path("output").is_dir() else ".") / f"{name.title() if name.isupper() else name} - {track_name or track}.mp4"
    output = Path(re.sub(r'[<>:"|?*]', "", str(output)))
    look = Look(not no_effects, game_volume, music_volume, fps, encoder, quality)
    work = Path(tempfile.mkdtemp(prefix="cotw-frag-"))
    try:
        assets = {"counters": {}}
        if not (no_title and no_counter):
            click.echo("Rendering graphics...")
            max_count = 0 if no_counter else max(s.burst.total for s in segs)
            assets = render_graphics(work, name, sub, max_count)
            if no_title:
                del assets["title"]
        args = build_args(mains, segs, music, music_seconds, look, assets, output)
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
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
