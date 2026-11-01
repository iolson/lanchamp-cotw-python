import pytest

from cotw.plan import PlanError, build_timeline, describe_moment, load_report, merge_timelines, load_names, PlayerInfo


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
