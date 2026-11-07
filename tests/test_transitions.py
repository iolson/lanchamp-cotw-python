import subprocess
from pathlib import Path

import pytest

from cotw.compose import _Graph, find_ffmpeg
from cotw.transitions import KINDS, add_transitions, plan_transitions, window

CUTS = [0.5, 1.0, 1.5, 2.0, 2.5]


def test_none_mode_returns_no_transitions():
    assert plan_transitions(CUTS, "none") == []
    assert plan_transitions(CUTS, "none", seed=7, clean=0.5) == []


def test_single_kind_mode_puts_it_on_every_cut():
    assert plan_transitions(CUTS, "glitch") == [(t, "glitch") for t in CUTS]


def test_random_mode_is_seeded_and_reproducible():
    a = plan_transitions(CUTS, "random", seed=42)
    b = plan_transitions(CUTS, "random", seed=42)
    c = plan_transitions(CUTS, "random", seed=43)
    assert a == b and [k for _, k in a] != [k for _, k in c]
    assert all(t == u and k in KINDS for (t, k), u in zip(a, CUTS))


def test_random_mode_never_repeats_a_kind_twice_in_a_row():
    for seed in range(25):
        kinds = [k for _, k in plan_transitions(list(range(1, 30)), "random", seed=seed)]
        assert all(x != y for x, y in zip(kinds, kinds[1:]))


def test_comma_list_mode_picks_only_among_those_kinds():
    picked = plan_transitions(list(range(1, 15)), "glitch,pixel", seed=3)
    assert {k for _, k in picked} <= {"glitch", "pixel"} and len({k for _, k in picked}) == 2


def test_clean_fraction_leaves_some_cuts_without_a_transition():
    many = [i * 0.5 for i in range(1, 81)]
    assert plan_transitions(many, "glitch", seed=1, clean=0.0) == [(t, "glitch") for t in many]
    half = plan_transitions(many, "glitch", seed=1, clean=0.5)
    assert 0.2 * len(many) < len(half) < 0.8 * len(many)
    assert plan_transitions(many, "glitch", seed=1, clean=1.0) == []


def test_unknown_names_raise_value_error_listing_the_valid_ones():
    with pytest.raises(ValueError) as exc:
        plan_transitions(CUTS, "warp")
    assert "warp" in str(exc.value) and all(k in str(exc.value) for k in KINDS)
    with pytest.raises(ValueError):
        plan_transitions(CUTS, "glitch,warp")


def test_result_is_sorted_by_time():
    shuffled = [1.5, 0.5, 2.5, 1.0, 2.0]
    assert [t for t, _ in plan_transitions(shuffled, "random", seed=5)] == sorted(shuffled)


def test_window_returns_two_positive_floats_for_every_kind():
    for kind in KINDS:
        before, after = window(kind)
        assert isinstance(before, float) and isinstance(after, float)
        assert before > 0 and after > 0


def test_window_rejects_unknown_kind():
    with pytest.raises(ValueError):
        window("warp")


def test_no_transitions_returns_src_and_adds_nothing():
    g = _Graph()
    assert add_transitions(g, "vcat", [], width=640, height=360) == "vcat"
    assert g.chains == []


def test_each_transition_gets_its_own_chain_with_unique_labels():
    g = _Graph()
    out = add_transitions(g, "vcat", [(0.5, "glitch"), (1.5, "pixel")], width=640, height=360)
    assert out == "vtr2"
    assert any(spec.startswith("[vcat]") and spec.endswith("[vtr1]") for spec in g.chains)
    assert any(f"[{out}]" in spec for spec in g.chains)
    assert len({spec.split("]")[0] for spec in g.chains if spec.startswith("[vtr")}) > 1


def test_filter_strings_reference_the_cut_times_and_frame_size():
    g = _Graph()
    add_transitions(g, "vcat", [(1.5, "film-burn"), (2.5, "glitch"), (3.0, "pixel")],
                    width=640, height=360)
    joined = ";".join(g.chains)
    assert "1.5" in joined and "2.5" in joined and "3" in joined
    assert "scale=640:360" in joined


def _run_ffmpeg(transitions: list[tuple[float, str]], seconds: float) -> subprocess.CompletedProcess:
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        pytest.skip("ffmpeg not available")
    g = _Graph()
    out = add_transitions(g, "0:v", transitions, width=640, height=360)
    source = f"testsrc2=s=640x360:r=30:d={seconds}"
    cmd = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-stats", "-f", "lavfi", "-i", source,
           "-filter_complex", ";".join(g.chains), "-map", f"[{out}]", "-f", "null", "-"]
    return subprocess.run(cmd, capture_output=True, text=True)


@pytest.mark.parametrize("kind", KINDS)
def test_every_kind_renders_under_real_ffmpeg(kind):
    result = _run_ffmpeg([(1.5, kind)], 3)
    assert result.returncode == 0, result.stderr
    assert "Error" not in result.stderr and "Invalid" not in result.stderr


def test_all_seven_kinds_in_a_row_on_one_source():
    plan = [(t, kind) for t, kind in zip([0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5], KINDS)]
    result = _run_ffmpeg(plan, 5)
    assert result.returncode == 0, result.stderr
    assert "Error" not in result.stderr and "Invalid" not in result.stderr


@pytest.mark.parametrize("kind", KINDS)
def test_every_kind_keeps_the_stream_length(kind):
    result = _run_ffmpeg([(1.5, kind)], 3)
    assert result.returncode == 0, result.stderr
    assert "frame=   90" in result.stderr.replace("\r", "\n"), result.stderr[-300:]


@pytest.mark.parametrize("kind", KINDS)
def test_footage_far_from_a_cut_is_untouched(kind):
    ffmpeg = find_ffmpeg()
    g = _Graph()
    out = add_transitions(g, "0:v", [(80.0, kind)], width=320, height=180)
    chains = ";".join(["[0:v]split[ref][in]", *(c.replace("[0:v]", "[in]") for c in g.chains),
                       f"[ref]trim=duration=2,setpts=PTS-STARTPTS[r];[{out}]trim=duration=2,setpts=PTS-STARTPTS[o]",
                       "[r][o]psnr"])
    res = subprocess.run([str(ffmpeg), "-hide_banner", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=3",
                          "-filter_complex", chains, "-f", "null", "-"], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr[-400:]
    avg = res.stderr.rsplit("average:", 1)[1].split()[0]
    assert avg == "inf" or float(avg) > 45, res.stderr[-300:]
