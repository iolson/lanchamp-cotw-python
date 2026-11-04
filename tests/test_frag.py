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


def test_effect_expressions_cannot_overflow_for_late_kills():
    z, br, sx, sy = frag._punch_exprs([(120.0, True, 3, 3)])
    assert "max(0,t-120)" in br and "exp(-16*max(0,t-120))" in br and "exp(16" not in br
