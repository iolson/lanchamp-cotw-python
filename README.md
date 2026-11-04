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

## Install

Needs Python 3.11+ and FFmpeg. It runs on Windows 11 and macOS (the CS2 recording step in
cs2-highlights is Windows-only, but this tool only post-processes the finished MP4 and JSON, so
it works anywhere).

**Windows 11 (PowerShell)**

```powershell
python -m venv .venv
.venv\Scripts\pip install -e .[dev]
.venv\Scripts\cotw --help
```

**macOS (Terminal)**

```bash
brew install ffmpeg python@3.12      # skip whatever you already have
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'    # quotes needed in zsh
.venv/bin/cotw --help
```

Activate the venv (`.venv\Scripts\Activate.ps1` on Windows, `source .venv/bin/activate` on
macOS) to type plain `cotw` instead of the full path.

FFmpeg is found via `--ffmpeg`, then `COTW_FFMPEG` (`$env:COTW_FFMPEG` in PowerShell,
`export COTW_FFMPEG=...` on macOS), then, on Windows, the copy `highlights update-tools`
downloads to `~\.cs2-highlights\tools\ffmpeg\bin`, then `PATH`. A Homebrew install is on
`PATH`, so on macOS there's nothing to configure.

## Usage

One highlights video (Windows PowerShell uses a backtick for line continuation, macOS a
backslash):

```bash
cotw match-highlights.mp4 --episode 7 --week "Oct 20" --featuring "Saltcrew · Free Agents" \
     --league ESEA --team Saltcrew --next-up EP.08
```

Reads `match-highlights.json` next to the video and writes `match-highlights-cotw.mp4`.

A whole episode: pass several videos, or a folder of them. Each video needs its own JSON beside
it. They are joined in filename order and the output defaults to `cotw-epNN.mp4` (use `-o`,
e.g. `-o output/cotw-ep01.mp4`; `output/` is git-ignored):

```bash
cotw files --episode 1 -o output/cotw-ep01.mp4
```

| Option | Purpose |
| --- | --- |
| `--names PATH` | Per-player name/team overrides (default `./names.json`) |
| `--team NAME` | Default team for players without one in `names.json` |
| `--report PATH` | JSON elsewhere than `<video>.json` |
| `--clips-dir DIR` | `clip_NN.mp4` files from the highlights work dir; exact clip lengths replace the JSON's 0.1 s-rounded ones |
| `--end-card append\|overlay\|off` | `append` (default) adds the 15 s card after the video; `overlay` covers the video's last 15 s |
| `--order chronological\|score\|shuffle` | Clip order across all videos (default `chronological`) |
| `--reverse` | Flip the order, e.g. `--order score --reverse` for best first |
| `--seed N` | Repeat a `shuffle` cut |
| `--pick 3,1,5` / `--skip 2,4` | Use only these clips in this order / leave clips out |
| `--kill-padding N` | Trim each clip to N seconds before its first kill and after its last (uses the report's `beat_targets`) |
| `--music FILE` | Music bed: starts after the title card, plays once, ducked under the game audio (`--music-volume`, default 0.25; `--music-start` to move it) |
| `--slowmo last\|special\|headshots\|all` | Slow motion around kills, eased in and out (off by default). `last` = each clip's final kill; `special`/`headshots` add those kills; `all` slows every kill. Needs 120 fps sources |
| `--slowmo-clips auto\|all\|3,5` | Which clips get it (default `auto`: signature clips, i.e. clutch/ace/4K, best first, at most a third of the episode) |
| `--slowmo-speed X` | Speed in the slowest part (default 0.5, from 120 fps footage that stays smooth at 60 fps) |
| `--snap-cuts` | Move each cut onto the nearest beat of `--music` (`pip install -e '.[beats]'`) |
| `--ai URL` / `--plan FILE` / `--ai-fast` | Let a local LLM suggest clip order and slow-motion clips, save/load the plan (see below) |
| `--music-dir DIR` | Choose the track from a folder: with `--ai` the model picks the best fit from each track's length, tempo and energy curve (cached as `<track>.analysis.json`); otherwise the one closest in length to the video |
| `--fps N` | Output frame rate (default 60; 120 fps sources are just headroom for slow motion) |
| `--channel NAME` | Handle on the end card (default `@LANChampGG`) |
| `--lower-third-scale X` | Lower-third size (default 0.65; 1.0 is the original, larger plate) |
| `--lower-third-seconds N` | How long each lower third stays up (default 7) |
| `--title-style center\|cinematic` | Title card style |
| `--no-title / --no-bug / --no-lower-thirds / --no-punch` | Drop individual graphics |
| `--encoder h264_nvenc` | NVIDIA GPU encode, Windows/Linux only (default `libx264`, CRF `--quality 18`) |
| `--graphics-only DIR` | Render the PNGs/frames to `DIR` and stop, to eyeball the look |
| `--dry-run` | Print the timeline and the FFmpeg command only |

## Ordering clips

By default clips play in recording order. `--order score` builds up from the lowest-scoring clip
to the best; `--order shuffle` randomises (seeded with `--seed`, and avoids back-to-back clips
from one player when it can). Run once with `--dry-run` to see the numbered clip list, then use
`--pick 4,2,1` to choose exactly which clips play and in what order, or `--skip 3` to drop some.
`--pick` overrides `--order`.

```bash
cotw files --episode 1 --order score -o output/cotw-ep01.mp4
cotw files --episode 1 --pick 4,2,1 -o output/cotw-ep01.mp4
```

## Music

```bash
cotw files --episode 1 --order score --kill-padding 2.5 --music "files/song.mp3" -o output/cotw-ep01.mp4
```

The track starts when the title card ends and fades in. If it is shorter than the episode it plays
once and ends on its own (the end card carries on without it); if it is longer it is cut with a
3 s fade-out. It is ducked automatically whenever the game audio is loud. Needs FFmpeg 4.4+.

## Slow motion and beat-snapped cuts

```bash
cotw files --episode 1 --order score --kill-padding 2.5 --slowmo last --snap-cuts \
     --music "files/song.mp3" -o output/cotw-ep01.mp4
```

Slow motion eases from full speed down to 0.5x just before a kill and back up after it, using the
120 fps source so it stays smooth in a 60 fps video. The game audio in that stretch is slowed like a
tape (the pitch drops) and low-passed, which avoids the smearing of time-stretching. Use
`--slowmo-clips 3,5` to pick clips yourself (clip numbers as in the clip list from `--dry-run`).

`--snap-cuts` lengthens or shortens the end of each clip so the cut lands on a beat, never cutting off
a kill. It needs `librosa` (`pip install -e '.[beats]'`); beats are cached next to the track as
`<track>.beats.json`.

## AI edit plan (optional)

A local OpenAI-compatible LLM (tested with vLLM serving Qwen) can suggest the clip order and which
clips get slow motion:

```bash
cotw files --episode 1 --kill-padding 2.5 --ai http://192.168.1.231:8000/v1 --plan output/ep01.plan.json \
     --snap-cuts --music "files/song.mp3" -o output/cotw-ep01.mp4
```

The model only picks clip numbers (it sees display names, scores and kill times, never SteamIDs).
Its answer is validated (real clip numbers, no repeats, nothing missing, at most a third of the
clips in slow motion) and saved to `--plan`; if that file already exists it is loaded instead, so you
can edit it by hand and re-render the same cut without calling the model again. Explicit `--order`,
`--pick`, `--skip` and `--slowmo-clips` always win over the model. Set `COTW_AI_URL` instead of
`--ai` if you like. The model reasons first, which takes about 80 s but follows the rules much better
(it ends on the best clip); `--ai-fast` skips that and answers in a few seconds.

## Frag-movie edits: `cotw-frag`

A second command that makes a beat-synced highlight edit ("FUZZYHAT: Echo"), not a Clip of the Week:

```bash
cotw-frag files --music "files/FLAMEBOY - Echo.mp3" --title FuzzyHat -o "output/FuzzyHat - Echo.mp4"
cotw-frag files --player fuzzyhat --music "files/song.mp3"      # only that player's clips
```

- Kills are grouped into **bursts**; each becomes a segment whose last (hero) kill lands exactly on a
  beat of the music, so cuts and kills hit the rhythm. Bursts are ordered weakest to best
  (`--order score`) and the weakest are dropped first when the track is too short.
- **Eased slow motion** on the hero kill of each clip's final burst when the clip has 3+ kills
  (`--slowmo auto|all|none`, tape-style audio as above).
- **Effects** on kills: zoom punch, plus screen shake, a flash and a colour grade on hero kills
  (`--no-effects` to skip).
- **Motion graphics**: an animated title (letters pop in, RGB-split glitch, light sweep, track and artist
  line) and a kill counter that ticks up per kill in clips with 2+ kills (`--no-title`, `--no-counter`).
- The track name and artist come from the file's tags, or from an `Artist - Title.mp3` file name
  (`--track-name` overrides). The game audio is mixed under the music (`--game-volume`, `--music-volume`).
- Output is 60 fps from the 120 fps sources, like `cotw`. `--max-seconds` caps the length (for shorts).

## Player names and teams

Lower thirds show the in-game name from the JSON exactly as recorded (e.g. `-kiwi`) and the
`--team` value as the team. To override either per player, create a **`names.json`** in the
folder you run `cotw` from. It is git-ignored, so each person keeps their own:

```json
{
  "76561198073450718": { "name": "Kiwi", "team": "Saltcrew" },
  "76561197971721260": { "team": "Free Agents" },
  "-kiwi": "Kiwi"
}
```

- **Keys** are a SteamID64 (recommended: it never changes) or an in-game name
  (case-insensitive). The SteamID wins if both match.
- **Values** are `{"name": ..., "team": ...}`; both fields are optional. A plain string is
  shorthand for `{"name": "..."}`.
- A missing `name` keeps the in-game name; a missing `team` falls back to `--team` (and to no
  team at all if that isn't given either).

`cotw` picks up `./names.json` automatically, or point at another file with `--names PATH`.
SteamIDs are in the highlights JSON (`moments[].steamid`).

## How timing works

The highlights JSON lists each clip's `duration_seconds`; clips are laid end to end to find
each one's start in the video. Because those are rounded, the timeline is rescaled to the
video's probed length (or use `--clips-dir` for exact values). Per clip:

- lower third slides in 1.0 s after the clip starts, holds ~7 s in total (`--lower-third-seconds`), fades out
- stat punch plays 0.3 s in (over the clip's lead-in, before the first kill)

The featured player/stat per clip is the highest-scoring moment in that sequence. The JSON has
no team; teams come from `names.json`, with `--team` as the default.

Everything is re-encoded in one FFmpeg pass (overlays + concat), so expect roughly real-time
encode speed on CPU; use `--encoder h264_nvenc` to go faster.

## Tests

```bash
.venv/bin/python -m pytest -q          # macOS
.venv\Scripts\python -m pytest -q      # Windows
```
