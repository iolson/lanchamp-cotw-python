"""Beat times of a music track, for snapping cuts to the beat."""

from __future__ import annotations

import json
from pathlib import Path


class BeatError(RuntimeError):
    pass


def detect_beats(track: Path) -> list[float]:
    """Beat times in seconds from the start of `track`.

    Needs librosa (`pip install -e '.[beats]'`). The result is cached next to the track as
    `<track>.beats.json` and reused while the track file is unchanged.
    """
    cache = track.with_name(track.name + ".beats.json")
    stamp = [track.stat().st_size, int(track.stat().st_mtime)]
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if cached.get("stamp") == stamp:
            return [float(b) for b in cached["beats"]]
    except (OSError, ValueError, KeyError):
        pass
    try:
        import librosa
    except ImportError as exc:
        raise BeatError("--snap-cuts needs librosa: pip install -e '.[beats]'") from exc
    samples, rate = librosa.load(str(track), sr=22050)
    _, beats = librosa.beat.beat_track(y=samples, sr=rate, units="time")
    beats = [round(float(b), 3) for b in beats]
    try:
        cache.write_text(json.dumps({"stamp": stamp, "beats": beats}), encoding="utf-8")
    except OSError:
        pass  # a read-only music folder just means no cache
    return beats
