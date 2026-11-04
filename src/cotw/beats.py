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


def analyze_track(track: Path) -> dict:
    """Facts about a track for choosing music: length, tempo, a coarse energy curve and the biggest drop.

    Cached next to the track as `<track>.analysis.json`; needs librosa like `detect_beats`.
    """
    cache = track.with_name(track.name + ".analysis.json")
    stamp = [track.stat().st_size, int(track.stat().st_mtime)]
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if cached.get("stamp") == stamp:
            return cached["facts"]
    except (OSError, ValueError, KeyError):
        pass
    try:
        import librosa
        import numpy as np
    except ImportError as exc:
        raise BeatError("music analysis needs librosa: pip install -e '.[beats]'") from exc
    samples, rate = librosa.load(str(track), sr=22050)
    tempo, _ = librosa.beat.beat_track(y=samples, sr=rate, units="time")
    rms = librosa.feature.rms(y=samples)[0]
    times = librosa.times_like(rms, sr=rate)
    seconds = len(samples) / rate
    chunks = [float(rms[(times >= t) & (times < t + 10)].mean()) for t in range(0, int(seconds), 10)]
    top = max(chunks) or 1.0
    energy = [round(9 * c / top) for c in chunks]  # 0 (silent) .. 9 (loudest), one value per 10 s
    jumps = [(chunks[i + 1] - chunks[i], (i + 1) * 10) for i in range(len(chunks) - 1)]
    drop = max(jumps)[1] if jumps and max(jumps)[0] > 0.25 * top else None
    facts = {
        "file": track.name,
        "seconds": round(seconds),
        "bpm": round(float(np.atleast_1d(tempo)[0])),
        "energy_per_10s": energy,
        "biggest_drop_at": drop,
    }
    try:
        cache.write_text(json.dumps({"stamp": stamp, "facts": facts}), encoding="utf-8")
    except OSError:
        pass
    return facts
