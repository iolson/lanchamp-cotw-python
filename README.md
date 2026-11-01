# lanchamp-cotw

Takes the output of [cs2-highlights](../cs2-highlights-maker) (`match-highlights.mp4` +
`match-highlights.json`) and produces a finished **Clips of the Week** video with FFmpeg:

- **Title card** at the front (default 5 s)
- **Corner bug** (`COTW · EP.07`) for the whole highlight section
- **Lower third** naming the player, stat and map/round at the start of every clip
- **Stat punch** (animated `1v4` / `ACE` / `4K`) on clutches, aces and 4Ks
- **End card** for the last 15 s

The graphics are Python (Pillow) ports of the templates in
`lanchamp.to/public/tools/cotw/index.html`, using the same colours, layout and the same
fonts (bundled in `src/cotw/fonts`, all SIL OFL). If you change a template in the web tool,
update `src/cotw/graphics.py` to match.

## Install (Windows 11)

```powershell
python -m venv .venv
.venv\Scripts\pip install -e .[dev]
```

FFmpeg is found via `--ffmpeg`, `$env:COTW_FFMPEG`, the copy `highlights update-tools`
downloads to `~\.cs2-highlights\tools\ffmpeg\bin`, then `PATH`.

## Usage

```powershell
cotw match-highlights.mp4 --episode 7 --week "Oct 20" --featuring "Saltcrew · Free Agents" `
     --league ESEA --team Saltcrew --next-up EP.08
```

Reads `match-highlights.json` next to the video and writes `match-highlights-cotw.mp4`.

| Option | Purpose |
| --- | --- |
| `--names PATH` | Display-name overrides (default `./names.json`) |
| `--report PATH` | JSON elsewhere than `<video>.json` |
| `--clips-dir DIR` | `clip_NN.mp4` files from the highlights work dir; exact clip lengths replace the JSON's 0.1 s-rounded ones |
| `--end-card append\|overlay\|off` | `append` (default) adds the 15 s card after the video; `overlay` covers the video's last 15 s |
| `--lower-third-seconds N` | How long each lower third stays up (default 7) |
| `--title-style center\|cinematic` | Title card style |
| `--no-title / --no-bug / --no-lower-thirds / --no-punch` | Drop individual graphics |
| `--encoder h264_nvenc` | GPU encode (default `libx264`, CRF `--quality 18`) |
| `--graphics-only DIR` | Render the PNGs/frames to `DIR` and stop, to eyeball the look |
| `--dry-run` | Print the timeline and the FFmpeg command only |

## Display names

Lower thirds show the in-game name from the JSON exactly as recorded (e.g. `-kiwi`). To show
something else, create a **`names.json`** in the folder you run `cotw` from. It is git-ignored,
so each person keeps their own:

```json
{
  "-kiwi": "Kiwi",
  "76561197971721260": "FuzzyHat"
}
```

Keys are an in-game name (case-insensitive) or a Steam ID; values are what appears on screen.
`cotw` picks up `./names.json` automatically, or point at another file with `--names PATH`.

## How timing works

The highlights JSON lists each clip's `duration_seconds`; clips are laid end to end to find
each one's start in the video. Because those are rounded, the timeline is rescaled to the
video's probed length (or use `--clips-dir` for exact values). Per clip:

- lower third slides in 1.0 s after the clip starts, holds ~7 s in total (`--lower-third-seconds`), fades out
- stat punch plays 0.3 s in (over the clip's lead-in, before the first kill)

The featured player/stat per clip is the highest-scoring moment in that sequence. The JSON has
no team, so `--team` applies to every lower third.

Everything is re-encoded in one FFmpeg pass (overlays + concat), so expect roughly real-time
encode speed on CPU; use `--encoder h264_nvenc` to go faster.

## Tests

```powershell
.venv\Scripts\python -m pytest -q
```
