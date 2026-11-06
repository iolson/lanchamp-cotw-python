"""Build and run the FFmpeg command that overlays graphics and adds the title/end cards."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from cotw.graphics import H, STAT_TOTAL, W
from cotw.plan import Timeline, clip_segments

FADE = 0.5  # title/end card fade length, seconds
LT_FADE = 0.3  # lower-third alpha fade in/out
LT_SLIDE = 0.45  # lower-third slide-in time
LT_SLIDE_PX = 400
PUNCH_FADE_OUT = 0.25
# Slow-motion sound: slowed like a tape (pitch drops with speed, so nothing is stretched or smeared the
# way atempo does to gunfire), then low-passed for the muffled movie-slow-mo feel.
SLOW_AUDIO = ",asetrate=48000*{speed},aresample=48000,lowpass=f=3200"
CUT_FADE = 0.03  # audio fade at the edges of a trimmed clip


class FfmpegError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaInfo:
    width: int
    height: int
    fps: Fraction
    duration: float
    has_audio: bool


@dataclass
class Assets:
    bug: Path | None = None
    title: Path | None = None
    end: Path | None = None
    lower_thirds: dict[int, Path] = field(default_factory=dict)  # clip number -> PNG
    punches: dict[int, Path] = field(default_factory=dict)  # clip number -> dir of %03d.png frames


@dataclass(frozen=True)
class Options:
    title_seconds: float = 5.0
    end_seconds: float = 15.0
    end_mode: str = "append"  # "append" (extra card after the video) or "overlay" (over its last seconds)
    lower_third_delay: float = 1.0
    lower_third_seconds: float = 7.0
    punch_delay: float = 0.3
    punch_fps: int = 30
    encoder: str = "libx264"  # or "h264_nvenc"
    quality: int = 18  # CRF / CQ
    fps: int = 60  # output frame rate; 120 fps sources are only headroom for slow motion
    music: Path | None = None
    music_volume: float = 0.25
    music_duration: float | None = None  # track length; the music plays once and ends on its own if it fits
    music_start: float | None = None  # seconds into the episode; None = right after the title card
    tracks: tuple[tuple[Path, float, float], ...] = ()  # several songs: (file, start on the episode clock, length)
    music_fade_in: float = 1.5
    music_fade_out: float = 3.0


def find_ffmpeg(explicit: Path | None = None) -> Path:
    """--ffmpeg, $COTW_FFMPEG, the cs2-highlights tools folder, then PATH."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get("COTW_FFMPEG"):
        candidates.append(Path(os.environ["COTW_FFMPEG"]))
    candidates.append(Path.home() / ".cs2-highlights" / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe")
    for c in candidates:
        if c.is_file():
            return c
    on_path = shutil.which("ffmpeg")
    if on_path:
        return Path(on_path)
    raise FfmpegError(
        "ffmpeg not found. Pass --ffmpeg, set COTW_FFMPEG, run `highlights update-tools`, or put it on PATH."
    )


def _ffprobe(ffmpeg: Path) -> str:
    sibling = ffmpeg.with_name("ffprobe" + ffmpeg.suffix)
    return str(sibling) if sibling.is_file() else "ffprobe"


def probe(ffmpeg: Path, path: Path) -> MediaInfo:
    result = subprocess.run(
        [_ffprobe(ffmpeg), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise FfmpegError(f"ffprobe failed for {path}: {result.stderr.strip()}")
    info = json.loads(result.stdout)
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    if video is None:
        raise FfmpegError(f"{path} has no video stream")
    return MediaInfo(
        width=int(video["width"]),
        height=int(video["height"]),
        fps=Fraction(video.get("r_frame_rate") or "60/1"),
        duration=float(info["format"]["duration"]),
        has_audio=any(s["codec_type"] == "audio" for s in info["streams"]),
    )


def audio_duration(ffmpeg: Path, path: Path) -> float:
    result = subprocess.run(
        [_ffprobe(ffmpeg), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise FfmpegError(f"ffprobe could not read {path}: {result.stderr.strip()}") from exc


def _num(x: float) -> str:
    return f"{x:.3f}".rstrip("0").rstrip(".")


class _Graph:
    """Collects `-i` inputs and filter chains, handing out stream labels."""

    def __init__(self) -> None:
        self.inputs: list[str] = []
        self.n_inputs = 0
        self.chains: list[str] = []

    def add_input(self, *args: str) -> int:
        self.inputs += list(args)
        self.n_inputs += 1
        return self.n_inputs - 1

    def chain(self, spec: str) -> None:
        self.chains.append(spec)


def _music_bed(g: _Graph, opts: Options, total: float, title: float) -> None:
    """Play the track once from after the title card, ducked under the game audio.

    A track that ends before the episode does ends naturally; a longer one is cut with a fade-out.
    """
    if len(opts.tracks) > 1:
        _music_tracks(g, opts, total)
        return
    start = title if opts.music_start is None else opts.music_start
    start = min(max(0.0, start), total)
    room = max(0.1, total - start)
    cut = opts.music_duration is None or opts.music_duration > room
    length = room if cut else opts.music_duration
    tail = (
        f"atrim=end={_num(room)},asetpts=PTS-STARTPTS,"
        f"afade=t=out:st={_num(room - min(opts.music_fade_out, room))}:d={_num(min(opts.music_fade_out, room))},"
        if cut else ""
    )
    i = g.add_input("-i", str(opts.music))
    g.chain(
        f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,volume={opts.music_volume},{tail}"
        f"afade=t=in:d={_num(min(opts.music_fade_in, length))},"
        f"adelay={int(start * 1000)}:all=1,apad=whole_dur={_num(total)}[mus]"
    )
    g.chain("[agame]asplit=2[agame1][agame2]")
    g.chain("[mus][agame2]sidechaincompress=threshold=0.03:ratio=6:attack=30:release=500[musd]")
    g.chain("[agame1][musd]amix=inputs=2:duration=first:normalize=0[aout]")


SONG_OVERLAP = 1.0  # seconds two songs overlap when the music changes


def _music_tracks(g: _Graph, opts: Options, total: float) -> None:
    """Several songs back to back (each crossfades into the next), ducked under the game audio."""
    labels = []
    for n, (path, start, seconds) in enumerate(opts.tracks):
        last = n == len(opts.tracks) - 1
        start = min(max(0.0, start), total)
        end = min(start + seconds, total) if last else min(start + seconds, opts.tracks[n + 1][1] + SONG_OVERLAP)
        length = max(0.1, end - start)
        cut = not last or start + seconds > total
        fade = min(opts.music_fade_out if last else SONG_OVERLAP, length)
        tail = f"afade=t=out:st={_num(length - fade)}:d={_num(fade)}," if cut else ""
        i = g.add_input("-i", str(path))
        g.chain(
            f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,volume={opts.music_volume},"
            f"atrim=end={_num(length)},asetpts=PTS-STARTPTS,{tail}"
            f"afade=t=in:d={_num(min(opts.music_fade_in if n == 0 else SONG_OVERLAP, length))},"
            f"adelay={int(start * 1000)}:all=1,apad=whole_dur={_num(total)}[mus{n}]"
        )
        labels.append(f"[mus{n}]")
    g.chain(f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:normalize=0[mus]")
    g.chain("[agame]asplit=2[agame1][agame2]")
    g.chain("[mus][agame2]sidechaincompress=threshold=0.03:ratio=6:attack=30:release=500[musd]")
    g.chain("[agame1][musd]amix=inputs=2:duration=first:normalize=0[aout]")


def build_args(
    mains: list[tuple[Path, MediaInfo]],
    timeline: Timeline,
    assets: Assets,
    opts: Options,
    output: Path,
) -> list[str]:
    g = _Graph()
    info = mains[0][1]  # the first video sets the output size and frame rate
    fps = str(opts.fps)
    size = f"{info.width}:{info.height}"
    needs_scale = (info.width, info.height) != (W, H)
    rgba = f"format=rgba,scale={size}" if needs_scale else "format=rgba"
    main_dur = timeline.duration

    def loop_png(path: Path, seconds: float) -> int:
        return g.add_input("-loop", "1", "-framerate", fps, "-t", _num(seconds), "-i", str(path))

    def silence(seconds: float) -> int:
        return g.add_input("-f", "lavfi", "-t", _num(seconds), "-i", "anullsrc=r=48000:cl=stereo")

    # Each clip is cut from its source video (in timeline order, slow-motion pieces at their own
    # speed), normalised, then everything is joined.
    pieces: list[str] = []
    for k, clip in enumerate(timeline.clips):
        path, mi = mains[clip.source]
        segs = clip_segments(clip)
        for j, (off, length, speed) in enumerate(segs):
            tag = f"{k}_{j}"
            idx = g.add_input("-ss", _num(clip.src_start + off), "-t", _num(length), "-i", str(path))
            slow = abs(speed - 1.0) > 1e-6
            retime = f"setpts=(PTS-STARTPTS)/{_num(speed)}," if slow else ""
            g.chain(f"[{idx}:v]{retime}fps={fps},scale={size},setsar=1[mv{tag}]")
            shown = length / speed
            if mi.has_audio:
                audio = f"[{idx}:a:0]aresample=48000,aformat=channel_layouts=stereo" + (SLOW_AUDIO.format(speed=_num(speed)) if slow else "")
            else:
                audio = f"[{silence(shown)}:a]anull"
            fades = []
            if clip.trimmed and j == 0:  # tiny fades so the cuts don't click
                fades.append(f"afade=t=in:d={CUT_FADE}")
            if clip.trimmed and j == len(segs) - 1:
                fades.append(f"afade=t=out:st={_num(max(0.0, shown - CUT_FADE))}:d={CUT_FADE}")
            g.chain(audio + "".join("," + f for f in fades) + f"[ma{tag}]")
            pieces.append(f"[mv{tag}][ma{tag}]")
    g.chain(f"{''.join(pieces)}concat=n={len(pieces)}:v=1:a=1[v0][amain]")
    cur, step = "v0", 0

    def overlay(top: str, enable: tuple[float, float] | None, x: str = "0") -> None:
        nonlocal cur, step
        step += 1
        nxt = f"v{step}"
        en = f":enable='between(t,{_num(enable[0])},{_num(enable[1])})'" if enable else ""
        g.chain(f"[{cur}][{top}]overlay=x={x}:y=0:format=auto:eof_action=pass{en}[{nxt}]")
        cur = nxt

    if assets.bug:
        i = loop_png(assets.bug, main_dur)
        g.chain(f"[{i}:v]{rgba}[bug]")
        overlay("bug", None)

    for clip in timeline.clips:
        start = clip.start
        if clip.number in assets.lower_thirds:
            s = start + opts.lower_third_delay
            d = min(opts.lower_third_seconds, max(1.0, clip.duration - opts.lower_third_delay))
            i = loop_png(assets.lower_thirds[clip.number], d)
            g.chain(
                f"[{i}:v]{rgba},fade=t=in:st=0:d={LT_FADE}:alpha=1,"
                f"fade=t=out:st={_num(d - LT_FADE)}:d={LT_FADE}:alpha=1,setpts=PTS+{_num(s)}/TB[lt{clip.number}]"
            )
            slide = f"-pow(1-min(1,max(0,(t-{_num(s)})/{LT_SLIDE})),3)*{LT_SLIDE_PX}"
            overlay(f"lt{clip.number}", (s, s + d), x=f"'{slide}'")
        if clip.number in assets.punches:
            s = start + opts.punch_delay
            i = g.add_input("-framerate", str(opts.punch_fps), "-i", str(assets.punches[clip.number] / "%03d.png"))
            g.chain(
                f"[{i}:v]{rgba},fade=t=out:st={_num(STAT_TOTAL - PUNCH_FADE_OUT)}:d={PUNCH_FADE_OUT}:alpha=1,"
                f"setpts=PTS+{_num(s)}/TB[st{clip.number}]"
            )
            overlay(f"st{clip.number}", (s, s + STAT_TOTAL))

    if assets.end and opts.end_mode == "overlay":
        d = min(opts.end_seconds, main_dur)
        s = main_dur - d
        i = loop_png(assets.end, d)
        g.chain(f"[{i}:v]{rgba},fade=t=in:st=0:d={FADE}:alpha=1,setpts=PTS+{_num(s)}/TB[endov]")
        overlay("endov", (s, main_dur))

    g.chain(f"[{cur}]format=yuv420p[vmain]")

    # Title + main + (appended) end card, joined with the concat filter.
    segments: list[tuple[str, str]] = []
    if assets.title and opts.title_seconds > 0:
        d = opts.title_seconds
        i, a = loop_png(assets.title, d), silence(d)
        g.chain(
            f"[{i}:v]fps={fps},scale={size},setsar=1,format=yuv420p,"
            f"fade=t=in:st=0:d={FADE},fade=t=out:st={_num(d - FADE)}:d={FADE}[vtitle]"
        )
        g.chain(f"[{a}:a]anull[atitle]")
        segments.append(("vtitle", "atitle"))
    segments.append(("vmain", "amain"))
    if assets.end and opts.end_mode == "append" and opts.end_seconds > 0:
        d = opts.end_seconds
        i, a = loop_png(assets.end, d), silence(d)
        g.chain(
            f"[{i}:v]fps={fps},scale={size},setsar=1,format=yuv420p,"
            f"fade=t=in:st=0:d={FADE}[vend]"
        )
        g.chain(f"[{a}:a]anull[aend]")
        segments.append(("vend", "aend"))

    joined = "".join(f"[{v}][{a}]" for v, a in segments)
    total = (opts.title_seconds if assets.title and opts.title_seconds > 0 else 0.0) + main_dur
    if assets.end and opts.end_mode == "append" and opts.end_seconds > 0:
        total += opts.end_seconds
    if opts.music or opts.tracks:
        g.chain(f"{joined}concat=n={len(segments)}:v=1:a=1[vout][agame]")
        _music_bed(g, opts, total, title=opts.title_seconds if assets.title else 0.0)
    else:
        g.chain(f"{joined}concat=n={len(segments)}:v=1:a=1[vout][aout]")

    if opts.encoder == "h264_nvenc":
        venc = ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(opts.quality), "-b:v", "0"]
    else:
        venc = ["-c:v", "libx264", "-preset", "medium", "-crf", str(opts.quality)]

    return [
        "-y",
        *g.inputs,
        "-filter_complex",
        ";".join(g.chains),
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        *venc,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output),
    ]


def run_ffmpeg(ffmpeg: Path, args: list[str]) -> None:
    # stderr is inherited so ffmpeg's progress line is visible during the (minutes-long) encode.
    cmd = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-stats", *args]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise FfmpegError(f"ffmpeg failed (exit {result.returncode})\ncommand: {subprocess.list2cmdline(cmd)}")
