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
| `--kill-padding N` | Trim each clip to N seconds before its first kill and after its last (from the report's `beat_targets` plus the featured player's kills in its events, so a 1vX keeps every kill and a won clutch runs to its end) |
| `--lull-speed X` | Fast-forward the lulls between kills (over 3 s of nothing, keeping 1.5 s after a kill and 2 s before the next) at X with eased ramps (default 4; 1 = off) |
| `--jump-cuts N` | Lulls between kills longer than N seconds (default 10) are jump-cut instead of fast-forwarded: 1.5 s kept after the kill, 2.5 s of set-up before the next, tiny audio fades at the cut; 0 fast-forwards every lull |
| `--music FILE` | Music bed: starts after the title card, plays once, ducked under the game audio (`--music-volume`, default 0.25; `--music-start` to move it) |
| `--slowmo last\|special\|headshots\|all` | Slow motion around kills, eased in and out (off by default). `last` = each clip's final kill; `special`/`headshots` add those kills; `all` slows every kill. Needs 120 fps sources |
| `--slowmo-clips auto\|all\|3,5` | Which clips get it (default `auto`: signature clips, i.e. clutch/ace/4K, best first, at most a third of the episode) |
| `--slowmo-speed X` | Speed in the slowest part (default 0.5, from 120 fps footage that stays smooth at 60 fps) |
| `--transitions none\|random\|<look>` | Cut effects on the joins between clips: one of the seven shared looks below, or `random` for a seeded mix (default `none`) |
| `--snap-cuts` | Move each cut onto the nearest beat of `--music` (`pip install -e '.[beats]'`) |
| `--ai URL` / `--plan FILE` / `--ai-fast` | Let a local LLM suggest clip order and slow-motion clips, save/load the plan (see below) |
| `--music-dir DIR` | Choose the track from a folder: with `--ai` the model picks the best fit from each track's length, tempo and energy curve (cached as `<track>.analysis.json`); otherwise the one closest in length to the video; if no single track covers the video, all of them are used, quietest first |
| `--music FILE` (repeat) | Several songs: each next one starts at a new section (or the nearest clip cut) and crossfades with the one before |
| `--sections map` | Group the clips by map: each map is a section; the section holding the best clip comes last |
| `--broll DIR` / `--broll-seconds N` | Open each section with N seconds (default 4) of empty-map b-roll for that map, taken from the cs2-highlights b-roll exports under DIR (needs `--sections map`) |
| `--fps N` | Output frame rate (default 60; 120 fps sources are just headroom for slow motion) |
| `--channel NAME` | Handle on the end card (default `@LANChampGG`) |
| `--lower-third-scale X` | Lower-third size (default 0.65; 1.0 is the original, larger plate) |
| `--lower-third-seconds N` | How long each lower third stays up (default 7) |
| `--title-style center\|cinematic` | Title card style |
| `--intro VIDEO` | Intro video with alpha (e.g. the Blender `LANChamp_Clips_Reveal.mov`) over the title card's background, with the episode line faded in after the logo lands (timed from its `_events.json`); the title card runs as long as the video |
| `--intro-over cold\|card` | `cold` (default): the intro plays over the cold open, starting just after its last kill, with the footage blurred, dimmed and running on underneath and the game sound giving way to the music; `card`: after the cold open, over the title card's background |
| `--preview-intro` | Render only the cold open, the intro and the first clip, to check an intro without the whole episode |
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
cotw-frag files --music "files/FLAMEBOY - Echo.mp3" --name FuzzyHat -o "output/FuzzyHat - Echo.mp4"
cotw-frag files --player fuzzyhat --music "files/song.mp3"      # only that player's clips
```

- Kills are grouped into **bursts**; each becomes a segment whose last (hero) kill lands exactly on a
  beat of the music, so cuts and kills hit the rhythm. Bursts are ordered weakest to best
  (`--order score`) and the weakest are dropped first when the track is too short.
- **Eased slow motion** on the hero kill of each clip's final burst when the clip has 3+ kills
  (`--slowmo auto|all|none`, tape-style audio as above).
- **Effects** on kills: zoom punch, plus screen shake and a flash on hero kills (`--no-effects` to skip).
- **Transitions** at the cuts (`--transitions auto|none|flash|ghost|whip|random|<look>`): a white flash, a fading
  ghost echo of the previous shot, or a horizontal whip blur. `auto` mixes them, leaves about a third
  of the cuts clean, and flashes into the finale of big (4+ kill) clips. `random` or one of the seven
  shared looks below picks among those instead (build-up cuts stay clean).
- **Optional looks**, both off by default: `--grade vivid|warm` (punchy saturation / warm tint) and
  `--letterbox` (cinematic bars, `--letterbox-ratio 2.35`).
- **Looser sync** (`--sync loose`, the default): cuts may also land on half beats while hero kills
  always land on a beat; `--sync tight` keeps everything on beats. **Pacing** (`--pace auto`): longer
  shots where the music is quiet and quick cuts where it is loud; `--pace flat` for even pacing.
  **Build-up** (`--build-up 8`): open with about 8 seconds of b-roll (footage away from the kills, taken from
  the clips' lead-in and gaps) cut to the music with clean cuts and no kill effects; the kills start on the
  next beat.
  `--broll DIR` takes the build-up footage from a separate folder of cs2-highlights MP4s + JSON instead.
  **POV only** (`--pov-only --ai http://host:8000/v1`): a vision model looks at a frame per second of every
  video and anything that is not a player's first-person view (cinematic cutaways, free camera, intros) is
  kept out of the kill shots and the build-up. Results are cached next to each video as `<video>.pov.json`.
- **Several songs**: repeat `--music`; the tracks play in order and overlap by a second, and cuts
  keep following the beats across the change.
- There is no title card or kill counter: the edit goes straight into the clips.
- The output file name is `<name> - <track>.mp4`; the track comes from the file's tags or an
  `Artist - Title.mp3` file name (`--name` / `--track-name` override). The game audio is mixed under the music (`--game-volume`, `--music-volume`).
- Output is 60 fps from the 120 fps sources, like `cotw`. `--max-seconds` caps the length (for shorts).

## Paced episodes, description and thumbnails

```bash
cotw files --episode 3 --week "Nov 10" --league ESEA --structure paced --kill-padding 2.5 \
     --music-dir files --submit-url lanchamp.to/cotw --next-up EP.04 \
     --description output/ep03.txt --thumbnail output/ep03.jpg --ai http://dgx:8000/v1 -o output/cotw-ep03.mp4
```

`--structure paced` replaces `--order`: a **cold open** (default 9 s, `--cold-open-seconds`) teases the best
moment, then a fast 4 s intro, a **strong opening group**, a **varied middle** (no back-to-back repeats of
player, team, map, division or kind of play) and the **biggest moments** (top 2-3 by score, best last), then the
end card with the `--submit-url`. The music starts on the intro. `--skip` still works, and
`--pick 3,18,...` keeps your own order instead: the last two or three clips become the biggest moments and the
cold open teases the last one.

`--description FILE` writes the YouTube description from the real final timeline: title options, intro, chapters
(cold open, intro, the three blocks, outro), per-clip credits with timestamps (player, team, opponent,
division, map/round) and match links.

**Titles** are built to be clicked, and only from the episode's stats, so they can't overstate it. The hook comes
in the first 40 characters (all a phone shows), with a real number or stake, and the series name goes last:
`Three Aces & Two 1v4 Clutches in One Week | LANChamp EP.03`, `Down 1v4. Then This Happened. | CS2 Clips of the
Week EP.03`, `Three Aces From ESEA Open Players This Week`, then a clutch count and a plain fallback, each kept
within 70 characters. The description opens with the same numbers (`Three aces, two 1v4 clutches and six 1v3
clutches: the 35 best plays ...`). With `--ai URL`, or `--copy-ai URL` for copy only (no edit decisions), the
model adds up to three titles under the same rules. Any title that gets a number wrong (a clutch size the episode
doesn't have, an ace or clutch count that isn't the real one) is dropped.

`--thumbnail FILE.jpg` writes 1280x720 thumbnails (`-2`, `-3` ...; `--thumbnail-count`): a real frame at the last
kill, the LANChamp mark and episode number in a fixed corner, the brand colours (cyan accent, a gold tick), and a
callout computed from the stats (`1V4?!`, `ACE?!`, `1V3?!`, `4K CLUTCH`; override the first with `--thumb-text`).
They lead with the most striking feat (1v5 > 1v4 > ace > 1v3 > 4K), not the highlight score, and each shows a
different callout while there are different ones, so the three suit YouTube's thumbnail test (Test & Compare).
The model's thumbnail picks, when there are any, come first. The callout is never the model's.

`--thumb-style punch` is the louder alternative, in the look of the most-viewed game highlight channels: a
saturated frame, a huge brand-gold 3D callout top left on blue-deep sides with a thick ink edge, a gold arrow at
the crosshair, and a compact `CLIPS OF THE WEEK · LANCHAMP · #3` badge bottom left with the player bottom right.
The frame is taken at the moment of the last kill, so the crosshair is usually on the enemy. If it isn't (a
kill through smoke, say), use `--no-thumb-arrow`. Render both styles to compare, e.g. `--thumb-style punch
--thumbnail output/ep03-punch.jpg --thumbnails-only`.

The callout is rendered as extruded, bevelled 3D text in Blender (`src/cotw/blender_text.py`: Space Grotesk
Bold, a white face and cyan sides, about 3 s for all three) and laid on with a drop shadow and accent
glow. Blender is found via `--blender`, `$BLENDER`, `PATH` or `/Applications/Blender.app`; without it, or with
`--flat-thumbnails`, the flat text is used. The shading behind the callout and the branding corner gets darker
on bright frames. `--thumbnails-only` writes the thumbnails (and `--description`) without rendering the video.

### Match data (FACEIT)

The lower third, description and thumbnails use an optional `match` block in the highlights JSON:

```json
"match": {
  "match_id": "1-...", "url": "https://www.faceit.com/en/cs2/room/1-...",
  "division": "ESEA Open", "teams": ["Saltcrew", "Rivals"],
  "players": { "<steamid64>": { "nickname": "FuzzyHat", "team": "Saltcrew" } }
}
```

Every key is optional; the opponent is the other entry in `teams` (or set `"opponent"` per player). Priority for
names and teams: `names.json`, then this block, then the in-game name.

## Cut transition looks

Both commands share seven FFmpeg-only looks (`cotw --transitions`, `cotw-frag --transitions`); each is a
short effect centred on the cut, never changing the clip length or frame size. `random` picks a seeded
look per cut without repeating the same one twice in a row.

| Look | Effect |
| --- | --- |
| `film-burn` | Warm light leak and grain swelling into the cut, like a burned frame |
| `no-signal` | TV static, rolling bar and desaturation over the cut |
| `glitch` | RGB channel shifts, contrast jump and a frame jitter |
| `old-film` | Sepia tone, flicker, grain and vignette |
| `grunge` | High-contrast bleach, heavy grain and shake |
| `pixel` | Mosaic pixelation in three steps into the cut |
| `anamorphic` | Horizontal anamorphic bloom flaring up through the cut |

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
