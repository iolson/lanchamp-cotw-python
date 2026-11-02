from cotw import graphics
from cotw.graphics import EpisodeMeta

META = EpisodeMeta(7, "Oct 20", "Saltcrew", "ESEA", "@LANChampGG", "EP.08")


def test_transparent_overlays_keep_alpha():
    for img in (graphics.corner_bug(META), graphics.lower_third("fuzzyhat", "Saltcrew", "3K", "Mirage"),
                graphics.stat_frame("CLUTCH", "1v4", "ctx", 1.0)):
        assert img.size == (1920, 1080)
        assert img.getpixel((0, 0))[3] == 0
        assert img.getchannel("A").getbbox() is not None


def test_cards_are_opaque_full_frames():
    for img in (graphics.title_card(META), graphics.title_card(META, "cinematic"), graphics.end_card(META)):
        assert img.size == (1920, 1080)
        assert img.getchannel("A").getextrema() == (255, 255)


def test_stat_frame_zero_is_empty_and_final_frame_is_not():
    assert graphics.stat_frame("CLUTCH", "1v4", "ctx", 0.0).getchannel("A").getbbox() is None
    assert graphics.stat_frame("CLUTCH", "1v4", "ctx", 1.0).getchannel("A").getbbox() is not None


def test_stat_frame_count_and_progress():
    assert graphics.stat_frame_count(30) == 50
    assert graphics.stat_progress(0, 30) == 0
    assert graphics.stat_progress(49, 30) == 1.0
