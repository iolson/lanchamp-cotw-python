from pathlib import Path

from cotw import frag
from cotw.plan import ClipPlan

GRID = [0.0] + [0.28 + i * 0.65 for i in range(200)]


def _clip(number=1, kills=((5.0, False, False), (6.0, True, False), (20.0, True, False)), src_len=40.0, score=9.0):
    return ClipPlan(number, 0.0, src_len, "p", "4K", "c", None, score=score, src_start=100.0, src_len=src_len, kills=kills)


def test_kills_group_into_bursts_with_clip_wide_counts():
    b = frag.bursts_from([_clip()])
    assert [len(x.kills) for x in b] == [2, 1] and [x.base for x in b] == [0, 2]
    assert b[0].total == 3 and b[1].finale and not b[0].finale and b[0].hero == 106.0


def test_hero_kill_lands_exactly_on_a_beat_and_segments_chain_on_the_grid():
    segs = frag.place(frag.bursts_from([_clip()]), GRID, 100.0, "none", 0.5)
    assert len(segs) == 2
    for s in segs:
        hero_out = s.start + (s.burst.hero - s.src_start)
        assert min(abs(hero_out - g) for g in GRID) < 1e-6
        assert s.end in GRID and s.start in GRID
        assert s.src_start >= 100.0 and s.src_start + s.src_len <= 140.0
    assert segs[1].start == segs[0].end


def test_slow_motion_still_puts_the_hero_kill_on_a_beat():
    segs = frag.place(frag.bursts_from([_clip()]), GRID, 100.0, "all", 0.5)
    assert all(s.slow for s in segs)
    for (t, hero, _, _), s in zip([k for k in frag.kill_times(segs) if k[1]], segs):
        assert min(abs(t - g) for g in GRID) < 1e-3
        assert abs(sum(l / sp for _, l, sp in frag.build_segments(s.src_len, s.slow, s.speed)) - (s.end - s.start)) < 1e-6


def test_weakest_bursts_are_dropped_when_the_music_is_too_short():
    bursts = frag.bursts_from([_clip(1, score=3.0), _clip(2, score=9.0)])
    full = frag.arrange_bursts(bursts, GRID, 100.0, "score", None, "none", 0.5)
    tight = frag.arrange_bursts(bursts, GRID, full[-1].end - 1.0, "score", None, "none", 0.5)
    assert len(tight) < len(full) and all(s.burst.score == 9.0 for s in tight[-1:])


def _track(start=0.0, secs=100.0, energy=()):
    beats = tuple(start + 0.28 + i * 0.65 for i in range(int(secs / 0.65) - 1))
    return frag.Track(Path("t.mp3"), secs, start, beats, energy)


def _place(bursts, tracks, loose, slowmo="none", factor=None):
    grid, flags = frag.build_grid(tracks, loose)
    return frag.place(bursts, grid, 100.0, slowmo, 0.5, flags, factor)


def test_loose_grid_adds_half_beats_but_only_beats_can_take_the_hero_kill():
    grid, flags = frag.build_grid([_track()], loose=True)
    tight, tflags = frag.build_grid([_track()], loose=False)
    assert len(grid) > 1.8 * len(tight) and all(tflags) and not all(flags)
    segs = frag.place(frag.bursts_from([_clip()]), grid, 100.0, "none", 0.5, flags)
    for s in segs:
        hero_out = s.start + (s.burst.hero - s.src_start)
        assert any(abs(hero_out - t) < 1e-6 and f for t, f in zip(grid, flags))


def test_quiet_music_gets_longer_shots_than_loud_music():
    loud, quiet = _track(energy=(9,) * 10), _track(energy=(1,) * 10)
    bursts = frag.bursts_from([_clip()])
    a, b = (_place(bursts, [tr], False, factor=frag.pace_factor([tr], "auto"))[0] for tr in (loud, quiet))
    assert (b.end - b.start) > (a.end - a.start)


def test_second_track_starts_before_the_first_ends():
    a, b = _track(secs=60.0), _track(start=60.0 - frag.XFADE, secs=60.0)
    grid, _ = frag.build_grid([a, b], loose=False)
    assert grid == sorted(grid) and grid[-1] > 110


def test_transition_plan_is_seeded_and_flashes_into_finales():
    segs = _place(frag.bursts_from([_clip(1), _clip(2)]), [_track()], True)
    assert frag.plan_transitions(segs, "none", 1) == []
    assert all(k == "ghost" for _, k in frag.plan_transitions(segs, "ghost", 1))
    assert frag.plan_transitions(segs, "auto", 3) == frag.plan_transitions(segs, "auto", 3)
    big = [(5.0, False, False), (6.0, True, False), (20.0, True, False), (30.0, True, False)]
    segs = _place(frag.bursts_from([_clip(1), _clip(2, kills=tuple(big))]), [_track()], True)
    flashes = [t for t, k in frag.plan_transitions(segs, "auto", 3) if k == "flash"]
    assert any(s.start in flashes for s in segs[1:] if s.burst.finale and s.burst.total >= 4)


def test_build_args_has_the_optional_looks_only_when_asked():
    from fractions import Fraction
    from cotw.compose import MediaInfo
    mains = [(Path("a.mp4"), MediaInfo(1920, 1080, Fraction(120), 100.0, True))]
    segs = _place(frag.bursts_from([_clip(1), _clip(2)]), [_track()], True)
    def g(look):
        args = frag.build_args(mains, segs, [_track()], look, Path("o.mp4"))
        return args[args.index("-filter_complex") + 1]

    plain = g(frag.Look())
    assert "drawbox" not in plain and "colorbalance" not in plain
    fancy = g(frag.Look(letterbox=2.35, grade="vivid", transitions="ghost"))
    assert "drawbox=x=0:y=0:w=iw:h=131" in fancy and "colorbalance" in fancy and "blend=all_expr" in fancy
    assert "gblur" in g(frag.Look(transitions="whip")) and "gblur" not in plain


def test_effect_expressions_cannot_overflow_for_late_kills():
    z, br, sx, sy = frag._punch_exprs([(120.0, True, 3, 3)])
    assert "max(0,t-120)" in br and "exp(-16*max(0,t-120))" in br and "exp(16" not in br
