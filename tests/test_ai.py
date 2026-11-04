import json

from cotw import ai
from cotw.plan import ClipPlan, Timeline

TL = Timeline(
    [ClipPlan(n, 0.0, 10.0, p, "4K", "Cache · Round 7", None, score=s, kills=((3.0, True, False),), detail="4K (ak47)")
     for n, p, s in [(1, "a", 5.0), (2, "b", 9.0), (3, "a", 7.0), (4, "c", 3.0), (5, "d", 6.0), (6, "e", 2.0)]],
    60.0,
)
NUMBERS = [1, 2, 3, 4, 5, 6]


def test_brief_has_display_data_only():
    b = ai.clip_brief(TL.clips[0])
    assert b["player"] == "a" and b["detail"] == "4K (ak47)" and b["kills"] == [{"at": 3.0, "headshot": True, "special": False}]
    assert "steamid" not in json.dumps(b).lower()


def test_normalize_fixes_a_sloppy_answer():
    plan = ai.normalize_plan({"order": [3, 9, 3, 1], "slowmo": [2, 4, 5, 6], "reasons": "x"}, NUMBERS)
    assert plan.order == [3, 1, 2, 4, 5, 6]  # bad/duplicate numbers dropped, forgotten clips appended
    assert plan.slowmo == [2, 4]  # at most a third of six clips
    assert plan.notes


def test_suggest_plan_posts_a_schema_request_and_validates_the_reply(monkeypatch):
    seen = {}

    def fake_post(url, payload, timeout):
        seen.update(url=url, payload=payload)
        content = json.dumps({"order": [4, 1, 3, 5, 6, 2], "slowmo": [2], "reasons": "build up"})
        return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(ai, "_post", fake_post)
    plan = ai.suggest_plan(TL, "http://llm:8000/v1/", model="m")
    assert seen["url"] == "http://llm:8000/v1/chat/completions"
    assert seen["payload"]["response_format"]["json_schema"]["schema"] is ai.SCHEMA
    assert plan.order == [4, 1, 3, 5, 6, 2] and plan.slowmo == [2] and plan.reasons == "build up"


def test_unusable_reply_is_an_error(monkeypatch):
    monkeypatch.setattr(ai, "_post", lambda *a, **k: {"choices": [{"message": {"content": "nope"}}]})
    try:
        ai.suggest_plan(TL, "http://llm/v1", model="m")
    except ai.AiError:
        return
    raise AssertionError


def test_suggest_music_validates_the_pick(monkeypatch):
    tracks = [{"file": "a.mp3", "seconds": 120}, {"file": "b.mp3", "seconds": 200}]
    monkeypatch.setattr(ai, "_post", lambda *a, **k: {"choices": [{"message": {"content": json.dumps({"file": "b.mp3", "reasons": "longer"})}}]})
    assert ai.suggest_music(TL, tracks, "http://llm/v1", model="m") == ("b.mp3", "longer")
    monkeypatch.setattr(ai, "_post", lambda *a, **k: {"choices": [{"message": {"content": json.dumps({"file": "zzz.mp3", "reasons": ""})}}]})
    try:
        ai.suggest_music(TL, tracks, "http://llm/v1", model="m")
    except ai.AiError:
        return
    raise AssertionError
