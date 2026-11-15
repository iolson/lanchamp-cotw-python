from dataclasses import replace

from cotw import episode as ep
from cotw.plan import ClipPlan, Timeline, build_timeline, match_context, relayout


def _clip(n, player, score, *, kills_n=3, won=0, team="", map="mirage", **kw):
    return ClipPlan(n, 0.0, 10.0, player, f"{kills_n}K", "ctx", None, team, score=score, src_len=10.0,
                    kills=((4.0, False, False), (6.0, True, False)), map=map, kills_n=kills_n, won_clutch=won, **kw)


def test_paced_order_ends_on_best_and_peak_is_ascending():
    clips = [_clip(i, f"p{i}", s) for i, s in enumerate([5, 40, 12, 30, 8, 25, 18, 50], start=1)]
    order, labels = ep.paced_order(clips)
    assert sorted(c.number for c in order) == list(range(1, 9))
    assert order[-1].score == 50
    peak = [c for c in order if labels[c.number] == "peak"]
    assert [c.score for c in peak] == sorted(c.score for c in peak) and len(peak) == 2
    assert {c.number for c in peak} == {2, 8}
    assert [labels[c.number] for c in order] == sorted((labels[c.number] for c in order),
                                                       key={"opener": 0, "middle": 1, "peak": 2}.get)


def test_paced_order_spreads_players_in_the_middle():
    clips = [_clip(1, "a", 20), _clip(2, "a", 19), _clip(3, "b", 18), _clip(4, "b", 17), _clip(5, "c", 16),
             _clip(6, "d", 90), _clip(7, "e", 80)]
    order, labels = ep.paced_order(clips)
    body = [c for c in order if labels[c.number] != "peak"]
    assert all(x.player != y.player for x, y in zip(body, body[1:]))


def test_paced_labels_keep_a_picked_order():
    clips = [_clip(i, f"p{i}", s) for i, s in enumerate([5, 40, 12, 30, 8, 25, 18, 50], start=1)]
    labels = ep.paced_labels(clips)
    assert [labels[c.number] for c in clips] == ["opener"] * 3 + ["middle"] * 3 + ["peak"] * 2


def test_small_episodes_have_no_peak_block():
    order, labels = ep.paced_order([_clip(1, "a", 3), _clip(2, "b", 9)])
    assert set(labels.values()) <= {"opener", "middle"}
    assert len(order) == 2


def test_cold_open_covers_the_last_kill_and_respects_length():
    clip = replace(_clip(1, "a", 9), src_start=100.0, src_len=20.0,
                   kills=tuple((t, False, False) for t in (3.0, 8.0, 12.0, 14.0)))
    start, length = ep.cold_open_window(clip, max_len=9.0)
    assert length <= 9.0 + 1e-6
    assert start + length >= 100.0 + 14.0 + 0.99
    assert start >= 100.0


def test_callout_is_accurate():
    assert ep.callout(_clip(1, "a", 1, won=5)) == "1V5?!"
    assert ep.callout(_clip(1, "a", 1, kills_n=5)) == "ACE?!"
    assert ep.callout(_clip(1, "a", 1, kills_n=1)) == "HOW?!"
    assert ep.callout(_clip(1, "a", 1, kills_n=4, won=2)) == "4K CLUTCH"  # not the smaller "1V2?!"
    assert ep.callout(_clip(1, "a", 1, kills_n=2, won=2)) == "1V2?!"


def _week():
    return Timeline([
        _clip(1, "a", 21.0, kills_n=4, won=2), _clip(2, "b", 19.0, kills_n=4, won=4),
        _clip(3, "c", 16.0, kills_n=4, won=4), _clip(4, "d", 15.0, kills_n=5, division="ESEA Open 1-8"),
        _clip(5, "e", 13.0, kills_n=5, division="Open 9-10"), _clip(6, "f", 12.0, kills_n=3, won=3),
        _clip(7, "g", 10.0, kills_n=3),
    ], 70.0)


def test_thumbnails_lead_with_the_biggest_feat_and_vary_the_callout():
    picks = ep.thumbnail_picks(_week().clips, 3)
    assert [ep.callout(c) for c in picks] == ["1V4?!", "ACE?!", "1V3?!"]
    assert picks[0].number == 2  # the higher-scoring of the two 1v4s
    assert ep.thumbnail_picks(_week().clips, 2, preferred=[7])[0].number == 7


def test_title_options_lead_with_real_numbers_and_fit():
    info = ep.DescriptionInfo(3, week="Nov 14", league="ESEA")
    titles = ep.title_options(_week(), info)
    assert titles[:3] == ["CS2: Two Aces & Two 1v4 Clutches in One Week! #3",
                          "TOP 7 CS2 Plays This Week That Will Blow Your Mind! #3",
                          "CS2 | Community Highlights Week 03"]
    assert "CS2: Down 1v4. Then This Happened. #3" in titles
    assert "CS2: Two Aces From ESEA Open Players This Week #3" in titles
    assert all(len(t) <= 70 and t.startswith(("CS2", "TOP", "The")) for t in titles)
    facts = ep.episode_facts(_week(), "ESEA")
    assert all(ep.title_is_honest(t, facts) for t in titles)
    assert not ep.title_is_honest("A 1v5 Nobody Saw Coming", facts)
    assert not ep.title_is_honest("Five Aces in One Week", facts)
    assert ep.title_is_honest("Two Aces and a 1v3", facts)
    assert not ep.title_is_honest("One Ace in One Week", facts)  # undercounting is wrong too
    assert not ep.title_is_honest("Three 1v4 Clutches", facts) and ep.title_is_honest("Two 1v4 Clutches", facts)
    assert not ep.title_is_honest("9 Clutches This Week", facts) and ep.title_is_honest("4 Clutches Won", facts)
    assert not ep.title_is_honest("TOP 100 CS2 Plays", facts) and ep.title_is_honest("TOP 7 CS2 Plays", facts)
    assert not ep.title_is_honest("The Insane 1v4 Clutch That Broke The Game", facts)


def test_intro_line_leads_with_the_numbers():
    line = ep.intro_line(_week(), ep.DescriptionInfo(3, week="Nov 14", league="ESEA"))
    assert line.startswith("Two aces, two 1v4 clutches and a 1v3 clutch: the 7 best plays from ESEA matches in the week of Nov 14.")


def test_stamp():
    assert ep.stamp(65.4) == "1:05"
    assert ep.stamp(3725) == "1:02:05"


def test_description_chapters_start_at_zero_and_credit_matches():
    clips = [_clip(1, "a", 5, team="Saltcrew", opponent="Rivals", division="Open", match_url="http://m/1"),
             _clip(2, "b", 9, team="Rivals")]
    tl = relayout(clips)
    text = ep.description(tl, ep.DescriptionInfo(3, league="ESEA"), lead=10.0, cold=6.0,
                          labels={1: "opener", 2: "peak"}, outro_at=30.0)
    chapters = text.split("CHAPTERS\n")[1].split("\n\n")[0].splitlines()
    assert chapters[0] == "0:00 Cold open" and chapters[1] == "0:06 Intro"
    assert "0:10 Opening plays" in chapters and "0:20 Biggest moments" in chapters and "0:30 " in chapters[-1]
    assert "a (Saltcrew) vs Rivals" in text and "http://m/1" in text


def test_match_context_resolves_opponent_and_prefers_steamid():
    report = {"match": {"match_id": "1-x", "url": "u", "division": "Open", "teams": ["A", "B"],
                        "players": {"7": {"nickname": "Nick", "team": "B"}}}}
    mc = match_context(report, "7", "ingame")
    assert (mc.nickname, mc.team, mc.opponent, mc.division, mc.match_id) == ("Nick", "B", "A", "Open", "1-x")
    assert match_context(report, "", "nick").team == "B"  # fallback by nickname
    assert match_context({}, "7", "x").team == ""


def test_timeline_uses_match_block_but_names_json_wins():
    from cotw.plan import PlayerInfo
    report = {"map": "de_mirage", "match": {"teams": ["A", "B"], "players": {"7": {"nickname": "Nick", "team": "A"}}},
              "sequences": [{"number": 1, "round": 2, "duration_seconds": 10.0, "cameras": [{"steamid": "7", "name": "g"}],
                             "moments": [{"player": "g", "steamid": "7", "kills": 3, "score": 5, "description": "3K"}]}]}
    c = build_timeline(report).clips[0]
    assert (c.player, c.team, c.opponent) == ("Nick", "A", "B")
    c = build_timeline(report, names={"7": PlayerInfo(name="Override", team="Z")}).clips[0]
    assert (c.player, c.team) == ("Override", "Z")


def test_countdown_ranks_by_score_and_plays_lowest_first():
    clips = [_clip(i, f"p{i}", s) for i, s in enumerate([5, 40, 12, 30], start=1)]
    order = ep.countdown_order(clips)
    assert [c.rank for c in order] == [4, 3, 2, 1]
    assert [c.number for c in order] == [1, 3, 4, 2]  # worst first, best last
    top2 = ep.countdown_order(clips, top=2)
    assert [(c.rank, c.number) for c in top2] == [(2, 4), (1, 2)]


def test_countdown_description_names_ranks():
    tl = relayout(ep.countdown_order([_clip(1, "a", 5), _clip(2, "b", 9)]))
    text = ep.description(tl, ep.DescriptionInfo(3, league="ESEA", top_n=2), lead=10.0)
    assert "TOP 2 CS2 Plays" in text and "#2 a" in text and "#1 b" in text
