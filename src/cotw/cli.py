"""cotw: add COTW overlays, title card and end card to a cs2-highlights video."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import click

from cotw import graphics
from cotw.compose import (
    Assets,
    FfmpegError,
    Options,
    build_args,
    find_ffmpeg,
    probe,
    run_ffmpeg,
)
from cotw.plan import PlanError, Timeline, build_timeline, load_report


def render_assets(
    timeline: Timeline,
    meta: graphics.EpisodeMeta,
    opts: Options,
    work: Path,
    *,
    team: str,
    title_style: str,
    bug: bool,
    lower_thirds: bool,
    punches: bool,
    title: bool,
    end: bool,
) -> Assets:
    assets = Assets()
    save = lambda img, name: _save(img, work / name)  # noqa: E731

    if title:
        assets.title = save(graphics.title_card(meta, title_style), "title.png")
    if end:
        assets.end = save(graphics.end_card(meta), "end.png")
    if bug:
        assets.bug = save(graphics.corner_bug(meta), "bug.png")

    for clip in timeline.clips:
        if lower_thirds:
            img = graphics.lower_third(clip.player, team, clip.stat, clip.context)
            assets.lower_thirds[clip.number] = save(img, f"lower_third_{clip.number:02d}.png")
        if punches and clip.punch:
            frames = work / f"punch_{clip.number:02d}"
            frames.mkdir(exist_ok=True)
            n = graphics.stat_frame_count(opts.punch_fps)
            click.echo(f"  stat punch for clip {clip.number} ({clip.punch.value}): {n} frames")
            for i in range(n):
                p = graphics.stat_progress(i, opts.punch_fps)
                _save(
                    graphics.stat_frame(clip.punch.label, clip.punch.value, clip.punch.context, p),
                    frames / f"{i:03d}.png",
                )
            assets.punches[clip.number] = frames
    return assets


def _save(img, path: Path) -> Path:
    img.save(path, optimize=False, compress_level=1)
    return path


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("video", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--report", "report_path", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="highlights JSON (default: <video>.json next to the video).")
@click.option("-o", "--output", type=click.Path(dir_okay=False, path_type=Path),
              help="Output MP4 (default: <video>-cotw.mp4).")
@click.option("--episode", type=int, required=True, help="Episode number (EP.07).")
@click.option("--week", default="", help='Title card "Week of ..." text, e.g. "Oct 20".')
@click.option("--featuring", default="", help="Title card featuring line, e.g. 'Saltcrew · Free Agents'.")
@click.option("--league", default="", help="Title card league tag, e.g. ESEA.")
@click.option("--channel", default="FuzzyHatTV", show_default=True, help="Channel name on the end card.")
@click.option("--next-up", default="", help='End card "Next up" label, e.g. EP.08.')
@click.option("--team", default="", help="Team shown on every lower third (not in the JSON).")
@click.option("--title-style", type=click.Choice(["center", "cinematic"]), default="center", show_default=True)
@click.option("--title-seconds", type=float, default=5.0, show_default=True)
@click.option("--end-seconds", type=float, default=15.0, show_default=True)
@click.option("--end-card", "end_mode", type=click.Choice(["append", "overlay", "off"]), default="append",
              show_default=True, help="append: add the card after the video; overlay: cover its last seconds.")
@click.option("--no-title", is_flag=True, help="Skip the title card.")
@click.option("--no-bug", is_flag=True, help="Skip the corner bug.")
@click.option("--no-lower-thirds", is_flag=True)
@click.option("--no-punch", is_flag=True, help="Skip the animated stat punch on clutches / 4K / aces.")
@click.option("--clips-dir", type=click.Path(exists=True, file_okay=False, path_type=Path),
              help="Folder with clip_NN.mp4 files; their real lengths replace the JSON's rounded durations.")
@click.option("--encoder", type=click.Choice(["libx264", "h264_nvenc"]), default="libx264", show_default=True)
@click.option("--quality", type=int, default=18, show_default=True, help="CRF (libx264) / CQ (nvenc).")
@click.option("--ffmpeg", "ffmpeg_path", type=click.Path(dir_okay=False, path_type=Path), help="Path to ffmpeg.")
@click.option("--graphics-only", type=click.Path(file_okay=False, path_type=Path),
              help="Render the graphics into this folder and stop (visual check).")
@click.option("--dry-run", is_flag=True, help="Print the timeline and FFmpeg command without encoding.")
def main(video: Path, report_path: Path | None, output: Path | None, episode: int, week: str, featuring: str,
         league: str, channel: str, next_up: str, team: str, title_style: str, title_seconds: float,
         end_seconds: float, end_mode: str, no_title: bool, no_bug: bool, no_lower_thirds: bool, no_punch: bool,
         clips_dir: Path | None, encoder: str, quality: int, ffmpeg_path: Path | None,
         graphics_only: Path | None, dry_run: bool) -> None:
    """Overlay LANChamp COTW graphics onto VIDEO (a cs2-highlights MP4)."""
    report_path = report_path or video.with_suffix(".json")
    output = output or video.with_name(video.stem + "-cotw.mp4")
    if output.resolve() == video.resolve():
        raise click.UsageError("output would overwrite the input video")

    try:
        ffmpeg = find_ffmpeg(ffmpeg_path)
        info = probe(ffmpeg, video)
        report = load_report(report_path)
        clip_durations = None
        if clips_dir:
            clip_durations = {
                int(p.stem.split("_")[1]): probe(ffmpeg, p).duration for p in sorted(clips_dir.glob("clip_*.mp4"))
            }
        timeline = build_timeline(report, video_duration=info.duration, clip_durations=clip_durations)
    except (FfmpegError, PlanError) as exc:
        raise click.ClickException(str(exc)) from exc

    if abs(timeline.duration - info.duration) > 1.0:
        click.echo(f"warning: JSON clips total {timeline.duration:.1f}s but the video is {info.duration:.1f}s; "
                   "overlay times may be off.", err=True)

    click.echo(f"{video.name}: {info.width}x{info.height} @ {float(info.fps):.2f}fps, {info.duration:.1f}s, "
               f"{len(timeline.clips)} clips")
    for c in timeline.clips:
        punch = f"  punch {c.punch.value}" if c.punch else ""
        click.echo(f"  #{c.number:<2} {c.start:7.1f}s +{c.duration:5.1f}s  {c.player} · {c.stat}{punch}")

    meta = graphics.EpisodeMeta(episode, week, featuring, league, channel, next_up)
    opts = Options(title_seconds=title_seconds, end_seconds=end_seconds,
                   end_mode="append" if end_mode == "off" else end_mode, encoder=encoder, quality=quality)

    keep = graphics_only is not None
    work = graphics_only or Path(tempfile.mkdtemp(prefix="cotw-"))
    work.mkdir(parents=True, exist_ok=True)
    try:
        click.echo("Rendering graphics...")
        assets = render_assets(
            timeline, meta, opts, work, team=team, title_style=title_style, bug=not no_bug,
            lower_thirds=not no_lower_thirds, punches=not no_punch, title=not no_title, end=end_mode != "off",
        )
        if graphics_only:
            click.echo(f"Graphics written to {work}")
            return
        args = build_args(video, info, timeline, assets, opts, output)
        if dry_run:
            click.echo(subprocess.list2cmdline([str(ffmpeg), *args]))
            return
        click.echo(f"Encoding {output} ...")
        try:
            run_ffmpeg(ffmpeg, args)
        except FfmpegError as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(f"Done: {output}")
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
