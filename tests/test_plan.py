import pytest

from cotw.plan import PlanError, build_timeline, describe_moment, load_report, merge_timelines, load_names, PlayerInfo, arrange, parse_numbers, snap_cuts, clip_segments, apply_slowmo


def _report():
    return {
        "map": "de_mirage",
        "sequences": [
            {"number": 2, "round": 12, "duration_seconds": 20.0, "cameras": [{"name": "cam"}],
             "moments": [{"player": "saltboi", "kills": 4, "description": "4K (m4a1), 1v3 clutch", "score": 30,
                          "clutch": {"opponents": 3, "won": True, "kills": 3}}]},
            {"number": 1, "round": 3, "duration_seconds": 10.0, "label": "3K", "cameras": [{"name": "cam"}],
             "moments": [{"player": "fuzzyhat", "kills": 3, "description": "3K (ak47)", "score": 10, "clutch": None},
                         {"player": "low", "kills": 1, "description": "1K", "score": 1, "clutch": None}]},
        ],
    }


def test_timeline_orders_by_number_and_accumulates():
    tl = build_timeline(_report())
    assert [c.number for c in tl.clips] == [1, 2]
    assert [c.start for c in tl.clips] == [0.0, 10.0]
    assert tl.duration == 30.0


def test_timeline_rescales_to_real_video_length():
    tl = build_timeline(_report(), video_duration=33.0)
    assert tl.duration == pytest.approx(33.0)
    assert tl.clips[1].start == pytest.approx(11.0)


def test_probed_clip_durations_win_over_rescale():
    tl = build_timeline(_report(), video_duration=99.0, clip_durations={1: 9.5, 2: 20.25})
    assert tl.clips[1].start == 9.5
    assert tl.duration == pytest.approx(29.75)


def test_featured_moment_is_highest_scoring():
    first = build_timeline(_report()).clips[0]
    assert first.player == "fuzzyhat"
    assert first.stat == "3K"
    assert first.context == "Mirage · Round 3"
    assert first.punch is None


def test_clutch_gets_punch_with_context():
    second = build_timeline(_report()).clips[1]
    assert second.stat == "4K + 1V3 CLUTCH"
    assert (second.punch.label, second.punch.value) == ("CLUTCH", "1v3")
    assert second.punch.context == "Mirage · Round 12"


@pytest.mark.parametrize(
    "moment,stat,punch_value",
    [
        ({"kills": 5, "description": "ACE (awp)"}, "ACE", "ACE"),
        ({"kills": 4, "description": "4K"}, "4K", "4K"),
        ({"kills": 2, "description": "2K, knife"}, "2K + KNIFE", None),
        ({"kills": 0, "clutch": None, "description": "moment"}, "HIGHLIGHT", None),
    ],
)
def test_describe_moment(moment, stat, punch_value):
    got_stat, punch = describe_moment(moment)
    assert got_stat == stat
    assert (punch.value if punch else None) == punch_value


def test_load_report_rejects_empty(tmp_path):
    p = tmp_path / "x.json"
    p.write_text('{"sequences": []}')
    with pytest.raises(PlanError):
        load_report(p)
    with pytest.raises(PlanError):
        load_report(tmp_path / "missing.json")


def test_merge_timelines_offsets_and_renumbers():
    a = build_timeline(_report())  # clips 1, 2 over 30s
    merged = merge_timelines([a, a])
    assert [c.number for c in merged.clips] == [1, 2, 3, 4]
    assert [c.start for c in merged.clips] == [0.0, 10.0, 30.0, 40.0]
    assert merged.duration == 60.0


def test_names_match_steamid_then_name_then_case_insensitively():
    report = _report()
    report["sequences"][0]["moments"][0]["steamid"] = "76561"  # saltboi (sequence 2)
    fuzzy = {"fuzzyhat": PlayerInfo(name="Fuzzy")}
    assert build_timeline(report, names=fuzzy).clips[0].player == "Fuzzy"
    assert build_timeline(report, names=fuzzy).clips[1].player == "saltboi"
    both = {"76561": PlayerInfo(name="Salt"), "fuzzyhat": PlayerInfo(name="Fuzzy")}
    assert [c.player for c in build_timeline(report, names=both).clips] == ["Fuzzy", "Salt"]


def test_team_override_is_per_player_and_independent_of_name():
    report = _report()
    report["sequences"][0]["moments"][0]["steamid"] = "76561"
    names = {"76561": PlayerInfo(team="Saltcrew"), "fuzzyhat": PlayerInfo(name="Fuzzy", team="Free Agents")}
    first, second = build_timeline(report, names=names).clips
    assert (first.player, first.team) == ("Fuzzy", "Free Agents")
    assert (second.player, second.team) == ("saltboi", "Saltcrew")  # name untouched
    assert build_timeline(report).clips[0].team == ""


def test_load_names_accepts_both_formats(tmp_path):
    p = tmp_path / "n.json"
    p.write_text('{"-kiwi": "Kiwi", "7656": {"name": "Fuzzy", "team": "Saltcrew"}, "x": {"team": "T"}}')
    assert load_names(p) == {
        "-kiwi": PlayerInfo(name="Kiwi"),
        "7656": PlayerInfo("Fuzzy", "Saltcrew"),
        "x": PlayerInfo(team="T"),
    }


@pytest.mark.parametrize("bad", ['["-kiwi"]', '{"a": 5}', '{"a": {"nmae": "x"}}', '{"a": {"name": 1}}'])
def test_load_names_rejects_bad_shapes(tmp_path, bad):
    p = tmp_path / "n.json"
    p.write_text(bad)
    with pytest.raises(PlanError):
        load_names(p)


def _tl():
    from cotw.plan import ClipPlan, Timeline
    clips = [ClipPlan(n, 0.0, d, p, "x", "c", None, score=sc) for n, d, p, sc in
             [(1, 10, "a", 30), (2, 20, "b", 10), (3, 5, "a", 20), (4, 5, "c", 40)]]
    return Timeline(clips, 40.0)


def test_arrange_by_score_ascending_and_reversed():
    assert [c.number for c in arrange(_tl(), order="score").clips] == [2, 3, 1, 4]
    assert [c.number for c in arrange(_tl(), order="score", reverse=True).clips] == [4, 1, 3, 2]


def test_arrange_recomputes_starts():
    tl = arrange(_tl(), order="score")
    assert [c.start for c in tl.clips] == [0, 20, 25, 35] and tl.duration == 40


def test_arrange_pick_and_skip():
    assert [c.number for c in arrange(_tl(), pick=[4, 1]).clips] == [4, 1]
    assert [c.number for c in arrange(_tl(), skip=[2]).clips] == [1, 3, 4]
    for bad in (dict(pick=[9]), dict(pick=[1, 1]), dict(skip=[1, 2, 3, 4])):
        try:
            arrange(_tl(), **bad)
        except PlanError:
            continue
        raise AssertionError(bad)


def test_shuffle_is_seeded_and_avoids_same_player_neighbours():
    a = [c.number for c in arrange(_tl(), order="shuffle", seed=7).clips]
    assert a == [c.number for c in arrange(_tl(), order="shuffle", seed=7).clips]
    players = [c.player for c in arrange(_tl(), order="shuffle", seed=7).clips]
    assert all(x != y for x, y in zip(players, players[1:]))


def test_parse_numbers():
    assert parse_numbers("3, 1,5") == [3, 1, 5]


def test_sequences_not_in_the_video_are_ignored_and_timing_is_exact():
    report = {
        "map": "de_nuke",
        "sequences": [
            {"number": 1, "round": 3, "duration_seconds": 12.0, "clip_duration_seconds": 12.04,
             "video_start_seconds": 0.0, "moments": []},
            {"number": 2, "round": 3, "duration_seconds": 12.0, "cinematic": {"campath": "A4.dz"}, "moments": []},
        ],
    }
    tl = build_timeline(report, video_duration=12.04)
    assert [c.number for c in tl.clips] == [1]
    assert tl.clips[0].duration == 12.04 and tl.duration == 12.04


def test_kill_padding_trims_to_the_kills_and_keeps_source_positions():
    report = {
        "map": "de_nuke",
        "sequences": [
            {"number": 1, "round": 3, "duration_seconds": 30.0, "clip_duration_seconds": 30.0,
             "video_start_seconds": 0.0, "moments": []},
            {"number": 2, "round": 4, "duration_seconds": 10.0, "clip_duration_seconds": 10.0,
             "video_start_seconds": 30.0, "moments": []},
        ],
        "beat_targets": [
            {"sequence": 1, "clip_time_seconds": 4.0}, {"sequence": 1, "clip_time_seconds": 20.0},
            {"sequence": 2, "clip_time_seconds": 5.0},
        ],
    }
    tl = build_timeline(report, kill_padding=2.5)
    a, b = tl.clips
    assert (a.src_start, a.duration, a.start, a.trimmed) == (1.5, 21.0, 0.0, True)
    assert (b.src_start, b.duration, b.start) == (32.5, 5.0, 21.0)
    assert tl.duration == 26.0
    whole = build_timeline(report)
    assert whole.duration == 40.0 and not whole.clips[0].trimmed


def _kill_report():
    return {
        "map": "de_nuke",
        "sequences": [{"number": 1, "round": 3, "duration_seconds": 20.0, "clip_duration_seconds": 20.0,
                       "video_start_seconds": 0.0, "moments": []}],
        "beat_targets": [
            {"sequence": 1, "clip_time_seconds": 4.0, "headshot": True, "special": False},
            {"sequence": 1, "clip_time_seconds": 10.0, "headshot": False, "special": False},
            {"sequence": 1, "clip_time_seconds": 10.2, "headshot": False, "special": False},
        ],
    }


def test_slowmo_last_slows_only_the_final_kill_with_ramps_in_and_out():
    tl = build_timeline(_kill_report(), kill_padding=2.5)
    c = apply_slowmo(tl, "last", select="all").clips[0]
    # last kill at 10.2s is 8.7s into the trimmed clip: full slow-mo from 0.3s before to 0.45s after
    assert len(c.slow) == 1 and abs(c.slow[0][0] - 8.4) < 1e-6 and abs(c.slow[0][1] - 9.15) < 1e-6
    segs = clip_segments(c)
    speeds = [round(sp, 2) for _, _, sp in segs]
    assert speeds[0] == 1.0 and speeds[-1] == 1.0 and min(speeds) == 0.5
    down, up = speeds[: speeds.index(0.5)], speeds[speeds.index(0.5) + 1 :]
    assert down[1:] == sorted(down[1:], reverse=True) and up[:-1] == sorted(up[:-1])  # smooth in and out
    assert abs(sum(l / sp for _, l, sp in segs) - c.duration) < 1e-6 and c.duration > c.src_len
    assert sum(l for _, l, _ in segs) == c.src_len or abs(sum(l for _, l, _ in segs) - c.src_len) < 1e-6


def test_slowmo_headshots_adds_headshot_windows_and_none_adds_nothing():
    tl = build_timeline(_kill_report(), kill_padding=2.5)
    assert len(apply_slowmo(tl, "headshots", select="all").clips[0].slow) == 2
    assert apply_slowmo(tl, "none", select="all").clips[0].slow == ()


def test_slowmo_clip_selection_is_selective():
    from cotw.plan import ClipPlan, Punch, Timeline, select_slowmo_clips
    clips = [ClipPlan(n, 0.0, 10.0, "p", "s", "c", Punch("", "4K", "") if pun else None, score=sc)
             for n, pun, sc in [(1, True, 5), (2, False, 9), (3, True, 8), (4, True, 7), (5, False, 1), (6, False, 2)]]
    tl = Timeline(clips, 60.0)
    assert select_slowmo_clips(tl) == [3, 4]  # signature clips only, best first, at most a third (2 of 6)
    assert select_slowmo_clips(tl, [5, 1]) == [5, 1] and select_slowmo_clips(tl, "all") == [1, 2, 3, 4, 5, 6]
    try:
        select_slowmo_clips(tl, [9])
    except PlanError:
        pass
    else:
        raise AssertionError


def test_snap_cuts_moves_clip_ends_onto_beats_without_cutting_kills():
    tl = build_timeline(_kill_report(), kill_padding=2.5)  # 11.2s clip, last kill 8.7s into it
    beats = [i * 0.6 for i in range(100)]
    snapped = snap_cuts(tl, beats, offset=5.0, music_start=5.0)
    end = 5.0 + snapped.clips[0].duration
    assert min(abs(end - b - 5.0) for b in beats) < 1e-6
    assert snapped.clips[0].src_len >= tl.clips[0].min_src - 1e-9
    assert abs(snapped.clips[0].duration - tl.clips[0].duration) <= 0.3 + 1e-9


def test_snap_cuts_keeps_slow_motion_intact():
    tl = apply_slowmo(build_timeline(_kill_report(), kill_padding=2.5), "last", select="all")
    beats = [i * 0.6 for i in range(100)]
    snapped = snap_cuts(tl, beats, offset=5.0, music_start=5.0).clips[0]
    assert snapped.slow == tl.clips[0].slow
    assert snapped.src_len >= snapped.slow[-1][1] + 0.4 - 1e-9  # the ramp out still fits
    assert min(abs(5.0 + snapped.duration - b - 5.0) for b in beats) < 1e-6


def _mapped(number, map_name, duration=20.0, score=1.0):
    from cotw.plan import ClipPlan
    return ClipPlan(number, 0.0, duration, "p", "3K", "c", None, score=score, src_len=duration, src_max=duration,
                    min_src=duration, map=map_name)


def test_sections_group_by_map_and_the_best_section_comes_last():
    from cotw.plan import Timeline, relayout, sections
    tl = relayout([_mapped(1, "dust2", score=9), _mapped(2, "anubis", score=1), _mapped(3, "dust2", score=2),
                   _mapped(4, "anubis", score=3)])
    secs = sections(tl)
    assert [[c.number for c in s] for s in secs] == [[1, 3], [2, 4]]  # in the order their last clip appears


def test_add_broll_opens_each_section_with_its_maps_footage():
    from cotw.plan import add_broll, relayout
    tl = relayout([_mapped(1, "dust2"), _mapped(2, "anubis"), _mapped(3, "mirage")])
    out = add_broll(tl, {"dust2": [(5, 8.0)], "anubis": [(6, 2.5)]}, 4.0)
    assert [(c.broll, c.map) for c in out.clips] == [
        (True, "dust2"), (False, "dust2"), (True, "anubis"), (False, "anubis"), (False, "mirage")]
    shots = [c for c in out.clips if c.broll]
    assert [(c.source, c.src_len, c.duration) for c in shots] == [(5, 4.0, 4.0), (6, 2.5, 2.5)]
    assert out.duration == sum(c.duration for c in out.clips) and out.clips[1].start == 4.0


def test_songs_change_at_a_section_start_when_one_is_near_the_end_of_the_song():
    from cotw.plan import add_broll, song_switches
    tl = add_broll(relayout_for_songs(), {"a": [(1, 5.0)], "b": [(2, 5.0)]}, 4.0)
    # sections: a-broll(0-4) a1 a2 (4-44) | b-broll (44-48) b1 b2 (48-88)
    assert [c.broll for c in tl.clips] == [True, False, False, True, False, False]
    assert song_switches(tl, [50.0, 60.0], offset=0.0, start=0.0) == [3]  # the b section starts at 44 <= 49


def test_songs_change_at_the_nearest_clip_when_no_section_starts_near_the_song_end():
    from cotw.plan import song_switches
    tl = relayout_for_songs()
    assert song_switches(tl, [30.0, 60.0], offset=0.0, start=0.0) == [1]  # clips start at 0, 20, 40, 60


def relayout_for_songs():
    from cotw.plan import relayout
    return relayout([_mapped(1, "a"), _mapped(2, "a"), _mapped(3, "b"), _mapped(4, "b")])


def test_snap_cuts_multi_starts_the_next_song_on_its_first_beat():
    from cotw.plan import relayout, snap_cuts_multi
    tl = relayout([_mapped(i, "a") for i in range(1, 4)])
    beats1 = [i * 0.5 for i in range(200)]
    beats2 = [0.3 + i * 0.5 for i in range(200)]
    out, starts = snap_cuts_multi(tl, [beats1, beats2], [2], offset=0.0, music_start=0.0)
    assert starts[0] == 0.0 and starts[1] == pytest.approx(out.clips[2].start - 0.3)
