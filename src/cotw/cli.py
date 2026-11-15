"""cotw: add COTW overlays, title card and end card to a cs2-highlights video."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import click

from cotw import graphics
from cotw import transitions as fx
from cotw.compose import (
    Assets,
    FfmpegError,
    Options,
    audio_duration,
    build_args,
    find_ffmpeg,
    probe,
    run_ffmpeg,
)
from cotw.plan import PlanError, Timeline, build_timeline, load_names, load_report, merge_timelines
from cotw import episode as ep
from cotw import thumbnail as thumb
from cotw.ai import AiError, load_plan, suggest_copy, suggest_music, suggest_plan
from cotw.beats import BeatError, analyze_track, detect_beats
from cotw.plan import (
    ORDERS, _map_slug, SLOWMO_MODES, add_broll, apply_fastforward, apply_slowmo, arrange, parse_numbers, relayout, sections, snap_cuts_multi,
    song_switches,
)


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
    lt_scale: float,
    punches: bool,
    title: bool,
    end: bool,
) -> Assets:
    assets = Assets()
    save = lambda img, name: _save(img, work / name)  # noqa: E731

    if title and opts.intro:
        assets.title = save(graphics.intro_backdrop(), "title.png")
        assets.intro_meta = save(graphics.intro_meta(meta), "intro_meta.png")
    elif title:
        assets.title = save(graphics.title_card(meta, title_style), "title.png")
    if end:
        assets.end = save(graphics.end_card(meta), "end.png")
    if bug:
        assets.bug = save(graphics.corner_bug(meta), "bug.png")

    for clip in timeline.clips:
        if lower_thirds and not clip.broll:
            img = graphics.lower_third(clip.player, clip.team or team, clip.stat, clip.context, lt_scale,
                                       opponent=clip.opponent, division=clip.division)
            assets.lower_thirds[clip.number] = save(img, f"lower_third_{clip.number:02d}.png")
        if clip.rank:
            assets.rank_plates[clip.number] = save(graphics.rank_plate(clip.rank), f"rank_{clip.number:02d}.png")
            assets.rank_badges[clip.number] = save(graphics.rank_badge(clip.rank, len(timeline.clips)),
                                                   f"rankbadge_{clip.number:02d}.png")
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


def ctx_source(ctx: click.Context, name: str) -> bool:
    """True when the option still has its default value (the user did not pass it)."""
    return ctx.get_parameter_source(name) == click.core.ParameterSource.DEFAULT


def _save(img, path: Path) -> Path:
    img.save(path, optimize=False, compress_level=1)
    return path


def _edit_plan(timeline: Timeline, ai_url: str | None, model: str | None, plan_path: Path | None, think: bool = True):
    numbers = [c.number for c in timeline.clips]
    try:
        if plan_path and plan_path.is_file():
            plan = load_plan(plan_path, numbers)
            click.echo(f"Edit plan loaded from {plan_path}")
        else:
            click.echo(f"Asking the model at {ai_url} for an edit plan ...")
            plan = suggest_plan(timeline, ai_url, model, think=think)
            if plan_path:
                plan_path.write_text(json.dumps(plan.to_json(), indent=2), encoding="utf-8")
                click.echo(f"Edit plan saved to {plan_path}")
    except AiError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"  order: {' '.join(f'#{n}' for n in plan.order)}")
    click.echo(f"  slow motion: {' '.join(f'#{n}' for n in plan.slowmo) or 'none'}")
    if plan.reasons:
        click.echo(f"  why: {plan.reasons}")
    for note in plan.notes:
        click.echo(f"  note: {note}", err=True)
    return plan


def _pick_music(timeline: Timeline, folder: Path, ai_url: str | None, model: str | None, think: bool,
                title: float = 0.0) -> list[Path]:
    tracks = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".flac", ".ogg"))
    if not tracks:
        raise click.UsageError(f"no audio files in {folder}")
    try:
        facts = [analyze_track(p) for p in tracks]
        need = timeline.duration + title - 5.0
        if len(facts) > 1 and not any(f["seconds"] >= need for f in facts):
            facts.sort(key=lambda f: sum(f["energy_per_10s"]) / max(1, len(f["energy_per_10s"])))
            click.echo(f"Music: no single track covers {need + 5:.0f}s, so all {len(facts)} are used, quietest first: "
                       + " -> ".join(f["file"] for f in facts))
            return [folder / f["file"] for f in facts]
        if ai_url:
            click.echo(f"Asking the model to pick one of {len(tracks)} tracks ...")
            name, why = suggest_music(timeline, facts, ai_url, model, think=think)
            click.echo(f"Music: {name}\n  why: {why}")
        else:
            name = min(facts, key=lambda f: abs(f["seconds"] - timeline.duration))["file"]
            click.echo(f"Music: {name} (closest in length to the video)")
    except (AiError, BeatError) as exc:
        raise click.ClickException(str(exc)) from exc
    return [folder / name]


def _load_broll(ffmpeg: Path, folder: Path, mains: list) -> dict[str, list[tuple[int, float]]]:
    """Empty-map footage under `folder`, by map name. The videos are added to `mains` (the clips' sources)."""
    footage: dict[str, list[tuple[int, float]]] = {}
    for video in sorted(folder.rglob("*.mp4")):
        if "review" in video.parts[len(folder.parts):]:
            continue
        try:
            report = json.loads(video.with_suffix(".json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            click.echo(f"warning: skipping {video.name}: no readable JSON next to it", err=True)
            continue
        if report.get("kind") != "broll" or not report.get("map"):
            continue
        info = probe(ffmpeg, video)
        mains.append((video, info))
        footage.setdefault(_map_slug(report["map"]), []).append((len(mains) - 1, info.duration))
    for name, files in footage.items():
        click.echo(f"b-roll {name}: {len(files)} shot(s)")
    return footage


INTRO_OVERLAP = 1.0  # the intro over the cold open starts this long before the teaser would have ended


def _intro_meta_times(intro: Path, length: float) -> tuple[float, float]:
    """When the episode line shows over the intro: from the Blender render's `<name>_events.json` (after the
    divider lands, until the fade-out), else from 65% of the intro to 1.3 s before its end."""
    on, off = length * 0.65, length - 1.3
    try:
        events = json.loads(intro.with_name(f"{intro.stem}_events.json").read_text(encoding="utf-8"))["events"]
    except (OSError, ValueError, KeyError, TypeError):
        return on, max(on, off)
    ends = {e.get("type"): e.get("end_time") or e.get("time") for e in events}
    starts = {e.get("type"): e.get("time") for e in events}
    on = float(ends.get("divider_dots") or ends.get("subtitle_in") or on)
    off = float(starts.get("fade_out") or off)
    return min(on, length), min(max(on, off), length)


def _write_thumbnails(ffmpeg: Path, mains: list, timeline: Timeline, meta: graphics.EpisodeMeta, path: Path,
                      count: int, text: str | None, preferred: list[int], blender: Path | None = None,
                      style: str = "classic", arrow: bool = True) -> None:
    """Hero thumbnails from the best clips: `path` for the first, `<stem>-2`, `<stem>-3` ... for the others.
    With `blender`, the callouts are 3D text rendered there instead of flat text."""
    picks = ep.thumbnail_picks(timeline.clips, count, preferred)
    labels = [text if (text and i == 0) else ep.callout(clip) for i, clip in enumerate(picks)]
    callouts: list = [None] * len(picks)
    if blender:
        try:
            if style == "punch":
                callouts = thumb.render_callouts(blender, [(t, thumb.PUNCH_SIDE) for t in labels], face=thumb.PUNCH_FACE)
            else:
                callouts = thumb.render_callouts(blender, [(t, thumb.accent_for(meta.episode)) for t in labels])
        except (FfmpegError, OSError) as exc:
            click.echo(f"warning: 3D thumbnail text failed, using flat text: {exc}", err=True)
    for i, clip in enumerate(picks):
        ks = sorted(t for t, _, _ in clip.kills)
        at = clip.src_start + (ks[-1] if ks else (clip.src_len or clip.duration) / 2) - 0.05
        out = path if i == 0 else path.with_name(f"{path.stem}-{i + 1}{path.suffix}")
        try:
            frame = thumb.extract_frame(ffmpeg, mains[clip.source][0], at)
        except FfmpegError as exc:
            click.echo(f"warning: thumbnail for clip #{clip.number} skipped: {exc}", err=True)
            continue
        label = labels[i]
        if style == "punch":
            img = thumb.render_punch(frame, meta, label, clip.player, clip.team, callout=callouts[i], arrow=arrow)
        else:
            img = thumb.render(frame, meta, label, clip.player, clip.team, callout=callouts[i])
        thumb.save(img, out)
        click.echo(f"Thumbnail {out}: clip #{clip.number} {clip.player} \"{label}\"{' (3D)' if callouts[i] else ''}")


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("videos", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--report", "report_path", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="highlights JSON (single video only; default: <video>.json next to each video).")
@click.option("-o", "--output", type=click.Path(dir_okay=False, path_type=Path),
              help="Output MP4 (default: <video>-cotw.mp4, or cotw-epNN.mp4 for several videos).")
@click.option("--episode", type=int, required=True, help="Episode number (EP.07).")
@click.option("--week", default="", help='Title card "Week of ..." text, e.g. "Oct 20".')
@click.option("--featuring", default="", help="Title card featuring line, e.g. 'Saltcrew · Free Agents'.")
@click.option("--league", default="", help="Title card league tag, e.g. ESEA.")
@click.option("--channel", default="@LANChampGG", show_default=True, help="Channel name on the end card.")
@click.option("--next-up", default="", help='End card "Next up" label, e.g. EP.08.')
@click.option("--team", default="", help="Default team for players without one in names.json.")
@click.option("--names", "names_path", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="JSON of per-player name/team overrides (default: ./names.json if present).")
@click.option("--title-style", type=click.Choice(["center", "cinematic"]), default="center", show_default=True)
@click.option("--title-seconds", type=float, default=5.0, show_default=True)
@click.option("--intro", "intro_path", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Intro video with alpha (e.g. the Blender LANChamp reveal, ProRes 4444) played over the title card's "
                   "background instead of its text, with the episode line faded in after the logo lands. Sets "
                   "--title-seconds to its length unless given.")
@click.option("--intro-over", type=click.Choice(["cold", "card"]), default="cold", show_default=True,
              help="cold: the --intro plays over the cold open's footage, starting just after its last kill, with the "
                   "footage dimmed and running on underneath, straight into the first clip. card: over the title "
                   "card's background, after the cold open. Without a cold open it is always the card.")
@click.option("--preview-intro", is_flag=True,
              help="Render only the cold open, the intro and the first clip (to check the intro without the episode).")
@click.option("--lower-third-seconds", type=float, default=Options.lower_third_seconds, show_default=True,
              help="How long each lower third stays up (including its fades).")
@click.option("--lower-third-scale", type=float, default=0.65, show_default=True,
              help="Size of the lower third (1.0 is the original 750 px wide plate).")
@click.option("--end-seconds", type=float, default=15.0, show_default=True)
@click.option("--end-card", "end_mode", type=click.Choice(["append", "overlay", "off"]), default="append",
              show_default=True, help="append: add the card after the video; overlay: cover its last seconds.")
@click.option("--order", type=click.Choice(ORDERS), default="chronological", show_default=True,
              help="Clip order: recording order, lowest to highest score, or random.")
@click.option("--reverse", is_flag=True, help="Flip the order (e.g. --order score --reverse for best first).")
@click.option("--seed", type=int, help="Seed for --order shuffle, to repeat a cut.")
@click.option("--pick", help="Only these clip numbers, in this order, e.g. 3,1,5 (numbers from the clip list).")
@click.option("--skip", help="Leave these clip numbers out, e.g. 2,4.")
@click.option("--kill-padding", type=float, help="Trim each clip to this many seconds before its first kill and "
              "after its last (e.g. 2.5). Default: keep the whole clip.")
@click.option("--slowmo", type=click.Choice(SLOWMO_MODES), default="none", show_default=True,
              help="Slow motion around kills: the last kill of each clip, plus special kills / headshots, or every kill.")
@click.option("--slowmo-clips", default="auto", show_default=True,
              help="Which clips get slow motion: auto (signature clips: clutch/ace/4K, at most a third), "
                   "all, or clip numbers like 3,5.")
@click.option("--slowmo-speed", type=click.FloatRange(0.5, 0.95), default=0.5, show_default=True,
              help="Playback speed inside slow motion (0.5 is smooth from a 120 fps source at 60 fps output).")
@click.option("--lull-speed", type=click.FloatRange(1.0, 8.0), default=4.0, show_default=True,
              help="Fast-forward the lulls between kills (e.g. a 1v3 walking between fights) at this speed, with eased "
                   "ramps, so the whole play stays in; 1 turns it off. Needs the kill positions of --kill-padding reports.")
@click.option("--jump-cuts", type=click.FloatRange(min=0), default=10.0, show_default=True,
              help="Lulls between kills longer than this many seconds are jump-cut (1.5 s kept after the kill, 2.5 s "
                   "before the next) instead of fast-forwarded; 0 fast-forwards them all.")
@click.option("--transitions", type=click.Choice(["none", "random", *fx.KINDS]), default="none", show_default=True,
              help="Cut effects on the joins between clips: film burn, TV no-signal, RGB glitch, sepia old film, "
                   "grunge, pixelate and anamorphic flare; random picks a seeded look per cut.")
@click.option("--snap-cuts", "snap_cuts_on", is_flag=True,
              help="Move each cut onto the nearest beat of --music (needs librosa).")
@click.option("--music", "music_files", multiple=True, type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Music track (MP3/WAV), played once from after the title card (faded out if longer than the episode) "
                   "and ducked under the game audio. Repeat it for several songs: the next one starts with a new section.")
@click.option("--music-dir", type=click.Path(exists=True, file_okay=False, path_type=Path),
              help="Folder of tracks to choose from (with --ai the model picks the best fit; "
                   "otherwise the one closest in length to the video). When no single track covers the "
                   "video, all of them are used, quietest first.")
@click.option("--sections", "sections_by", type=click.Choice(["none", "map"]), default="none", show_default=True,
              help="map: group the clips by map, so each map is a section.")
@click.option("--broll", "broll_dir", type=click.Path(exists=True, file_okay=False, path_type=Path),
              help="Folder of empty-map b-roll (cs2-highlights exports, any sub-folders); each section opens with "
                   "a shot of its map. Needs --sections map.")
@click.option("--broll-seconds", type=float, default=4.0, show_default=True, help="Length of each b-roll shot.")
@click.option("--music-volume", type=float, default=Options.music_volume, show_default=True,
              help="Music level before ducking (1.0 = the track's own volume).")
@click.option("--music-start", type=float,
              help="Seconds into the episode when the music starts (default: right after the title card).")
@click.option("--ai", "ai_url", envvar="COTW_AI_URL", metavar="URL",
              help="OpenAI-compatible LLM (e.g. http://host:8000/v1) that suggests clip order and slow-motion clips.")
@click.option("--ai-model", help="Model name for --ai (default: the server's first model).")
@click.option("--ai-fast", is_flag=True, help="Skip the model's reasoning pass: a few seconds instead of ~80 s, "
              "but it follows the editing rules less reliably.")
@click.option("--plan", "plan_path", type=click.Path(dir_okay=False, path_type=Path),
              help="Edit plan JSON: loaded if it exists, otherwise written after --ai asks the model.")
@click.option("--structure", type=click.Choice(["none", "paced", "countdown"]), default="none", show_default=True,
              help="paced: cold open teasing the best clip, fast intro, a strong opening group, a varied middle and "
                   "the biggest moments last (with --pick, your order is kept and the cold open teases the last clip). "
                   "countdown: the best --top N clips ranked like `highlights top` (by score) "
                   "and played from the lowest rank up, with a big number on each. Both replace --order; --skip works.")
@click.option("--top", "top_n", type=click.IntRange(1), help="Countdown size: keep only the best N clips (default all).")
@click.option("--cold-open-seconds", type=float, default=9.0, show_default=True,
              help="Length of the best-moment teaser before the intro with --structure paced (0 turns it off).")
@click.option("--submit-url", default="", help="Where to submit clips: shown on the end card and in the description.")
@click.option("--description", "description_path", type=click.Path(dir_okay=False, path_type=Path),
              help="Write the YouTube description (chapters, credits, match links) to this text file.")
@click.option("--thumbnail", "thumbnail_path", type=click.Path(dir_okay=False, path_type=Path),
              help="Write a thumbnail JPEG from the best clip (and <name>-2, <name>-3 from the next).")
@click.option("--thumbnail-count", type=click.IntRange(1, 6), default=3, show_default=True)
@click.option("--thumb-style", type=click.Choice(["classic", "punch"]), default="classic", show_default=True,
              help="classic: white/cyan callout bottom left, branding top left. punch: huge gold 3D callout top left "
                   "with a thick dark edge, a gold arrow at the crosshair, a saturated frame and a series badge "
                   "bottom left (the look of the most-viewed game highlight channels).")
@click.option("--no-thumb-arrow", is_flag=True, help="With --thumb-style punch, leave out the arrow.")
@click.option("--flat-thumbnails", is_flag=True, help="Flat callout text on thumbnails instead of 3D text from Blender.")
@click.option("--blender", "blender_path", type=click.Path(dir_okay=False, path_type=Path),
              help="Blender for 3D thumbnail text (default: $BLENDER, PATH, then /Applications/Blender.app).")
@click.option("--thumbnails-only", is_flag=True, help="Write the --thumbnail images (and --description) and stop.")
@click.option("--thumb-text", help="Callout text for the first thumbnail instead of the computed one, e.g. 'HE WON THIS?!'.")
@click.option("--copy-ai", "copy_ai_url", metavar="URL",
              help="LLM for titles, description intro and thumbnail picks only, without --ai's edit decisions "
                   "(order, slow motion, music). Its titles are added after the stat-based ones and dropped if "
                   "they claim a feat the episode does not have.")
@click.option("--no-ai-copy", is_flag=True, help="With --ai, do not ask the model for titles, intro and thumbnail picks.")
@click.option("--no-title", is_flag=True, help="Skip the title card.")
@click.option("--no-bug", is_flag=True, help="Skip the corner bug.")
@click.option("--no-lower-thirds", is_flag=True)
@click.option("--no-punch", is_flag=True, help="Skip the animated stat punch on clutches / 4K / aces.")
@click.option("--clips-dir", type=click.Path(exists=True, file_okay=False, path_type=Path),
              help="Folder with clip_NN.mp4 files; their real lengths replace the JSON's rounded durations.")
@click.option("--encoder", type=click.Choice(["libx264", "h264_nvenc"]), default="libx264", show_default=True)
@click.option("--fps", type=int, default=Options.fps, show_default=True,
              help="Output frame rate (sources can be 120 fps for slow motion).")
@click.option("--quality", type=int, default=18, show_default=True, help="CRF (libx264) / CQ (nvenc).")
@click.option("--ffmpeg", "ffmpeg_path", type=click.Path(dir_okay=False, path_type=Path), help="Path to ffmpeg.")
@click.option("--graphics-only", type=click.Path(file_okay=False, path_type=Path),
              help="Render the graphics into this folder and stop (visual check).")
@click.option("--dry-run", is_flag=True, help="Print the timeline and FFmpeg command without encoding.")
def main(videos: tuple[Path, ...], report_path: Path | None, output: Path | None, episode: int, week: str, featuring: str,
         league: str, channel: str, next_up: str, team: str, names_path: Path | None, title_style: str, title_seconds: float, intro_path: Path | None, intro_over: str, preview_intro: bool,
         ai_url: str | None, ai_model: str | None, ai_fast: bool, plan_path: Path | None,
         structure: str, top_n: int | None, cold_open_seconds: float, submit_url: str, description_path: Path | None,
         thumbnail_path: Path | None, thumbnail_count: int, flat_thumbnails: bool, thumb_style: str, no_thumb_arrow: bool,
         blender_path: Path | None, thumbnails_only: bool, thumb_text: str | None, copy_ai_url: str | None, no_ai_copy: bool,
         kill_padding: float | None, slowmo: str, slowmo_clips: str, slowmo_speed: float, lull_speed: float, jump_cuts: float, transitions: str, snap_cuts_on: bool, music_files: tuple[Path, ...], music_dir: Path | None, sections_by: str, broll_dir: Path | None,
         broll_seconds: float, music_volume: float, music_start: float | None,
         order: str, reverse: bool, seed: int | None, pick: str | None, skip: str | None,
         end_seconds: float, lower_third_seconds: float, lower_third_scale: float, end_mode: str, no_title: bool, no_bug: bool, no_lower_thirds: bool, no_punch: bool,
         clips_dir: Path | None, encoder: str, fps: int, quality: int, ffmpeg_path: Path | None,
         graphics_only: Path | None, dry_run: bool) -> None:
    """Overlay LANChamp COTW graphics onto VIDEOS (cs2-highlights MP4s, each with its JSON).

    Several videos, or one folder of them, are joined in filename order into one episode.
    """
    if len(videos) == 1 and videos[0].is_dir():
        videos = tuple(sorted(videos[0].glob("*.mp4")))
        if not videos:
            raise click.UsageError("no .mp4 files in that folder")
    if any(v.is_dir() for v in videos):
        raise click.UsageError("pass video files, or a single folder")
    if len(videos) > 1 and (report_path or clips_dir):
        raise click.UsageError("--report and --clips-dir only work with a single video")
    if structure != "none" and (order != "chronological" or reverse or sections_by != "none"
                                or (pick and structure != "paced")):
        raise click.UsageError("--structure sets the order itself: drop --order/--reverse/--sections, and --pick "
                               "unless it is paced (--skip is fine)")
    if top_n and structure != "countdown":
        raise click.UsageError("--top only works with --structure countdown")
    video = videos[0]
    output = output or (video.with_name(video.stem + "-cotw.mp4") if len(videos) == 1
                        else video.with_name(f"cotw-ep{episode:02d}.mp4"))
    if output.resolve() in {v.resolve() for v in videos}:
        raise click.UsageError("output would overwrite an input video")

    try:
        ffmpeg = find_ffmpeg(ffmpeg_path)
        names_path = names_path or (Path("names.json") if Path("names.json").is_file() else None)
        names = load_names(names_path) if names_path else {}
        if names_path:
            click.echo(f"Player overrides: {names_path} ({len(names)} entries)")
        mains, timelines = [], []
        for v in videos:
            info = probe(ffmpeg, v)
            report = load_report(report_path or v.with_suffix(".json"))
            clip_durations = None
            if clips_dir:
                clip_durations = {
                    int(p.stem.split("_")[1]): probe(ffmpeg, p).duration
                    for p in sorted(clips_dir.glob("clip_*.mp4"))
                }
            tl = build_timeline(report, video_duration=info.duration, clip_durations=clip_durations, names=names,
                                kill_padding=kill_padding)
            if slowmo != "none" and float(info.fps) < fps / slowmo_speed - 0.5:
                click.echo(f"warning: {v.name} is {float(info.fps):.0f} fps; slow motion at {slowmo_speed}x needs "
                           f"{fps / slowmo_speed:.0f} fps to stay smooth.", err=True)
            if kill_padding is None and abs(tl.duration - info.duration) > 1.0:
                click.echo(f"warning: {v.name}: JSON clips total {tl.duration:.1f}s but the video is "
                           f"{info.duration:.1f}s; overlay times may be off.", err=True)
            click.echo(f"{v.name}: {info.width}x{info.height} @ {float(info.fps):.2f}fps, {info.duration:.1f}s, "
                       f"{len(tl.clips)} clips")
            mains.append((v, info))
            timelines.append(tl)
        timeline = merge_timelines(timelines)
        timeline = apply_fastforward(timeline, lull_speed, jump_cuts)
        if any(c.fast for c in timeline.clips):
            click.echo(f"Fast-forward x{lull_speed:g} through lulls on clip(s): "
                       + ", ".join(f"#{c.number}" for c in timeline.clips if c.fast))
        if any(c.jumps for c in timeline.clips):
            click.echo("Jump cuts over long lulls: " + ", ".join(
                f"#{c.number} (-{sum(b - a for a, b in c.jumps):.0f}s)" for c in timeline.clips if c.jumps))
        if ai_url or (plan_path and plan_path.is_file()):
            ctx = click.get_current_context()
            plan = _edit_plan(timeline, ai_url, ai_model, plan_path, think=not ai_fast)
            if structure == "none" and not (order != "chronological" or pick or skip or reverse):
                pick = ",".join(map(str, plan.order))
            if plan.slowmo and ctx.get_parameter_source("slowmo_clips") == click.core.ParameterSource.DEFAULT:
                slowmo_clips = ",".join(map(str, plan.slowmo))
                if slowmo == "none":
                    slowmo = "last"
        if order != "chronological" or reverse or pick or skip:
            click.echo("Clips as recorded:")
            for c in timeline.clips:
                click.echo(f"  #{c.number:<2} {c.player} · {c.stat} (score {c.score:g})")
            timeline = arrange(timeline, order=order, reverse=reverse, seed=seed,
                               pick=parse_numbers(pick) if pick else None,
                               skip=parse_numbers(skip) if skip else None)
            click.echo("Final order:")
        labels: dict[int, str] | None = None
        cold_open: tuple[int, float, float] | None = None
        if structure == "countdown":
            timeline = relayout(ep.countdown_order(timeline.clips, top_n))
            click.echo("Countdown: " + " ".join(f"#{c.rank}=clip {c.number}" for c in timeline.clips))
        if structure == "paced":
            if pick:  # keep the hand-picked order; only the blocks are worked out
                labels = ep.paced_labels(timeline.clips)
            else:
                ordered, labels = ep.paced_order(timeline.clips)
                timeline = relayout(ordered)
            click.echo("Paced order: " + " ".join(f"#{c.number}({labels[c.number][0]})" for c in timeline.clips)
                       + "   (o = opening group, m = middle, p = biggest moments)")
        if structure != "none":
            if cold_open_seconds > 0:
                # a countdown must not give away #1: tease the runner-up; a picked paced order teases its finale
                by_score = sorted(timeline.clips, key=lambda c: -c.score)
                best = by_score[1] if structure == "countdown" and len(by_score) > 2 else by_score[0]
                if structure == "paced" and pick:
                    best = timeline.clips[-1]
                st, ln = ep.cold_open_window(best, max_len=cold_open_seconds)
                cold_open = (best.source, st, ln)
                click.echo(f"Cold open: {ln:.1f}s from clip #{best.number} ({best.player} · {best.stat})")
            if ctx_source(click.get_current_context(), "title_seconds"):
                title_seconds = 4.0  # a fast intro
        intro_meta_at = (0.0, 0.0)
        if intro_path:
            if ctx_source(click.get_current_context(), "title_seconds"):
                title_seconds = probe(ffmpeg, intro_path).duration
            intro_meta_at = _intro_meta_times(intro_path, title_seconds)
            click.echo(f"Intro: {intro_path.name} ({title_seconds:.1f}s), episode line at "
                       f"{intro_meta_at[0]:.1f}-{intro_meta_at[1]:.1f}s")
        intro_at: float | None = None  # where the intro starts inside the cold open when it plays over it
        if intro_path and cold_open and intro_over == "cold" and not no_title:
            src, st, ln = cold_open
            intro_at = max(0.0, ln - INTRO_OVERLAP)
            cold_open = (src, st, intro_at + title_seconds)
            title_seconds = 0.0  # no separate card: the cold open carries the intro
            click.echo(f"Intro over the cold open from {intro_at:.1f}s ({cold_open[2]:.1f}s in all)")
        if preview_intro:
            timeline = relayout(timeline.clips[:1])
            end_mode, description_path, thumbnail_path = "off", None, None
            click.echo("Preview: cold open, intro and the first clip only")
        if broll_dir and sections_by != "map":
            raise click.UsageError("--broll needs --sections map")
        if sections_by == "map":
            footage = _load_broll(ffmpeg, broll_dir, mains) if broll_dir else {}
            timeline = add_broll(timeline, footage, broll_seconds) if footage else relayout(
                [c for sec in sections(timeline) for c in sec])
            click.echo("Sections: " + " · ".join(
                f"{sec[0].map or '?'} ({sum(not c.broll for c in sec)} clips)" for sec in sections(timeline)))
            if broll_dir and not footage:
                click.echo("warning: no b-roll footage matched the maps in this episode", err=True)
        title = 0.0 if no_title else title_seconds
        cold = cold_open[2] if cold_open else 0.0
        lead = cold + title  # where the first clip starts in the finished video
        songs = list(music_files)
        if music_dir and not songs:
            songs = _pick_music(timeline, music_dir, ai_url, ai_model, not ai_fast, lead)
        if slowmo != "none":
            sel = slowmo_clips if slowmo_clips in ("auto", "all") else parse_numbers(slowmo_clips)
            timeline = apply_slowmo(timeline, slowmo, slowmo_speed, sel)
            click.echo("Slow motion on clip(s): " + (", ".join(f"#{c.number}" for c in timeline.clips if c.slow) or "none"))
        song_len = [audio_duration(ffmpeg, p) for p in songs]
        first = (intro_at if intro_at is not None else cold or title) if music_start is None else music_start  # music starts on the intro, or after the title
        switches = song_switches(timeline, song_len, offset=lead, start=first) if len(songs) > 1 else []
        if len(songs) > 1:
            if len(switches) + 1 < len(songs):
                click.echo(f"note: {len(songs) - len(switches) - 1} song(s) left out: no later section start fits", err=True)
            songs, song_len = songs[:len(switches) + 1], song_len[:len(switches) + 1]
        if snap_cuts_on:
            if not songs:
                raise click.UsageError("--snap-cuts needs --music")
            all_beats = [detect_beats(p) for p in songs]
            if music_start is None and all_beats[0]:  # put the track's first beat on the first cut
                music_start = max(0.0, lead - all_beats[0][0])
            timeline, song_starts = snap_cuts_multi(timeline, all_beats, switches, offset=lead,
                                                    music_start=first if music_start is None else music_start)
        else:
            song_starts = [first] + [lead + timeline.clips[i].start for i in switches]
        for p, st in zip(songs, song_starts):
            if len(songs) > 1:
                click.echo(f"Song at {st:6.1f}s: {p.name}")
    except (FfmpegError, PlanError, BeatError) as exc:
        raise click.ClickException(str(exc)) from exc

    for c in timeline.clips:
        punch = f"  punch {c.punch.value}" if c.punch else ""
        slow = f"  slow-mo x{len(c.slow)}" if c.slow else ""
        fast = (f"  fast x{len(c.fast)}" if c.fast else "") + (f"  jump x{len(c.jumps)}" if c.jumps else "")
        click.echo(f"  #{c.number:<2} {c.start:7.1f}s +{c.duration:5.1f}s  {c.player} · {c.stat}{punch}{slow}{fast}")

    # Cut effects on the final timeline; a window must not reach before 0, so early cuts stay clean.
    planned = fx.plan_transitions([c.start for c in timeline.clips[1:] if c.start >= 0.35],
                                  transitions, seed if seed is not None else 7)
    if transitions != "none":
        kinds = ", ".join(sorted({k for _, k in planned})) or "none"
        click.echo(f"Transitions: {len(planned)} cuts ({kinds})")

    meta = graphics.EpisodeMeta(episode, week, featuring, league, channel, next_up, submit_url)
    opts = Options(title_seconds=title_seconds, end_seconds=end_seconds, lower_third_seconds=lower_third_seconds,
                   end_mode="append" if end_mode == "off" else end_mode, encoder=encoder, quality=quality, fps=fps,
                   music=songs[0] if len(songs) == 1 else None, music_volume=music_volume,
                   music_start=music_start if len(songs) <= 1 else None,
                   music_duration=song_len[0] if len(songs) == 1 else None,
                   tracks=tuple(zip(songs, song_starts, song_len)) if len(songs) > 1 else (),
                   transitions=tuple(planned), cold=cold_open, intro=intro_path, intro_meta_at=intro_meta_at,
                   intro_at=intro_at)

    if description_path or thumbnail_path:
        copy = None
        if (copy_ai_url or ai_url) and not no_ai_copy:
            try:
                copy = suggest_copy(timeline, episode, league, copy_ai_url or ai_url, ai_model)
                click.echo("AI copy: " + " | ".join(copy.titles))
            except AiError as exc:
                click.echo(f"warning: no AI copy ({exc}); using the stat-based titles", err=True)
        if description_path:
            outro_at = None if end_mode == "off" else (
                lead + timeline.duration if end_mode == "append" else lead + max(0.0, timeline.duration - end_seconds))
            info = ep.DescriptionInfo(episode, week, league, channel, submit_url, next_up,
                                      top_n=len(timeline.clips) if structure == "countdown" else 0)
            facts = ep.episode_facts(timeline, league)
            titles = ep.title_options(timeline, info)
            extra = []
            for t in copy.titles if copy else []:
                if not ep.title_is_honest(t, facts):
                    click.echo(f"note: dropped AI title that gets a number wrong: {t}", err=True)
                elif t not in titles:
                    extra.append(t)
            titles += extra[:3]
            click.echo("Titles:\n  " + "\n  ".join(titles))
            text = ep.description(timeline, info, lead=lead, cold=cold if intro_at is None else intro_at, labels=labels, outro_at=outro_at,
                                  titles=titles)  # the stat-led intro line beats the model's
            description_path.parent.mkdir(parents=True, exist_ok=True)
            description_path.write_text(text, encoding="utf-8")
            click.echo(f"Description: {description_path}")
        if thumbnail_path:
            _write_thumbnails(ffmpeg, mains, timeline, meta, thumbnail_path, thumbnail_count, thumb_text,
                              copy.thumbnail_clips if copy else [],
                              None if flat_thumbnails else thumb.find_blender(blender_path),
                              style=thumb_style, arrow=not no_thumb_arrow)
        if thumbnails_only:
            return

    keep = graphics_only is not None
    work = graphics_only or Path(tempfile.mkdtemp(prefix="cotw-"))
    work.mkdir(parents=True, exist_ok=True)
    try:
        click.echo("Rendering graphics...")
        assets = render_assets(
            timeline, meta, opts, work, team=team, title_style=title_style, bug=not no_bug,
            lower_thirds=not no_lower_thirds, lt_scale=lower_third_scale, punches=not no_punch, title=not no_title, end=end_mode != "off",
        )
        if graphics_only:
            click.echo(f"Graphics written to {work}")
            return
        args = build_args(mains, timeline, assets, opts, output)
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
