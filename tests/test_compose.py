from fractions import Fraction
from pathlib import Path

from cotw.compose import Assets, MediaInfo, Options, build_args
from cotw.plan import ClipPlan, Punch, Timeline

INFO = MediaInfo(1920, 1080, Fraction(60), 30.0, True)
MAIN = [(Path("in.mp4"), INFO)]
TL = Timeline(
    [ClipPlan(1, 0.0, 10.0, "a", "3K", "ctx", None), ClipPlan(2, 10.0, 20.0, "b", "ACE", "ctx", Punch("", "ACE", ""))],
    30.0,
)


def _assets(**kw):
    base = dict(
        bug=Path("bug.png"), title=Path("title.png"), end=Path("end.png"),
        lower_thirds={1: Path("lt1.png"), 2: Path("lt2.png")}, punches={2: Path("punch2")},
    )
    return Assets(**{**base, **kw})


def _graph(args):
    return args[args.index("-filter_complex") + 1]


def test_default_layout_is_title_main_end_concat():
    args = build_args(MAIN, TL, _assets(), Options(), Path("out.mp4"))
    g = _graph(args)
    assert "concat=n=3:v=1:a=1[vout][aout]" in g
    assert "[vtitle][atitle][vmain][amain][vend][aend]" in g
    assert args[-1] == "out.mp4"


def test_lower_third_and_punch_are_timed_to_their_clip():
    g = _graph(build_args(MAIN, TL, _assets(), Options(), Path("out.mp4")))
    assert "setpts=PTS+1/TB[lt1]" in g  # clip 1 starts at 0 + 1.0s delay
    assert "setpts=PTS+11/TB[lt2]" in g  # clip 2 starts at 10 + 1.0s
    assert "setpts=PTS+10.3/TB[st2]" in g  # punch 0.3s into clip 2
    assert "[st1]" not in g  # clip 1 has no punch


def test_overlay_end_card_has_no_extra_segment():
    opts = Options(end_mode="overlay")
    g = _graph(build_args(MAIN, TL, _assets(), opts, Path("out.mp4")))
    assert "concat=n=2" in g
    assert "setpts=PTS+15/TB[endov]" in g  # last 15s of a 30s video


def test_optional_assets_are_skipped():
    a = _assets(title=None, end=None, bug=None, lower_thirds={}, punches={})
    g = _graph(build_args(MAIN, TL, a, Options(), Path("out.mp4")))
    assert "concat=n=1" in g
    assert "overlay" not in g


def test_silent_input_gets_generated_audio():
    info = MediaInfo(1920, 1080, Fraction(60), 30.0, False)
    args = build_args([(Path("in.mp4"), info)], TL, _assets(), Options(), Path("out.mp4"))
    assert "[0:a:0]" not in _graph(args)
    assert "anullsrc=r=48000:cl=stereo" in args


def test_non_1080p_input_scales_graphics():
    info = MediaInfo(2560, 1440, Fraction(60), 30.0, True)
    g = _graph(build_args([(Path("in.mp4"), info)], TL, _assets(), Options(), Path("out.mp4")))
    assert "format=rgba,scale=2560:1440" in g


def test_nvenc_encoder_args():
    args = build_args(MAIN, TL, _assets(), Options(encoder="h264_nvenc", quality=20), Path("o.mp4"))
    assert args[args.index("-c:v") + 1] == "h264_nvenc"
    assert args[args.index("-cq") + 1] == "20"


def test_clips_are_cut_from_their_source_video_in_timeline_order():
    b_info = MediaInfo(1920, 1080, Fraction(60), 10.0, False)
    two = [(Path("a.mp4"), INFO), (Path("b.mp4"), b_info)]
    tl = Timeline(
        [ClipPlan(2, 0.0, 4.0, "b", "3K", "ctx", None, source=1, src_start=1.5),
         ClipPlan(1, 4.0, 10.0, "a", "ACE", "ctx", None, source=0, src_start=0.0)],
        14.0,
    )
    args = build_args(two, tl, _assets(lower_thirds={}, punches={}), Options(), Path("o.mp4"))
    g = _graph(args)
    joined = " ".join(args)
    assert "-ss 1.5 -t 4 -i b.mp4" in joined and "-ss 0 -t 10 -i a.mp4" in joined
    assert joined.index("b.mp4") < joined.index("a.mp4")
    assert "[mv0][ma0][mv1][ma1]concat=n=2:v=1:a=1[v0][amain]" in g
    assert "anullsrc" in joined  # b has no audio
