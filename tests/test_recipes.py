# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# Content recipes: the ffmpeg command lines for each field set.

import dataclasses
from fractions import Fraction

import pytest

from moq_test_tracks.content.recipes import RecipeError, loop_seconds, source_command
from moq_test_tracks.spec import decode, from_slash

BASE = "p/mtt_00/clock/loc/h264/720/30/aac/2"


def _spec(**changes):
    return dataclasses.replace(decode(from_slash(BASE)).spec, **changes)


def _value(argv, flag):
    return argv[argv.index(flag) + 1]


def test_live_h264_aac():
    argv = source_command(_spec(), "mpegts", "out/s.ts")
    assert argv[0] == "ffmpeg" and argv[-1] == "out/s.ts"
    assert argv.count("-re") == 2
    assert _value(argv, "-c:v") == "libx264" and _value(argv, "-bf") == "0"
    assert _value(argv, "-profile:v") == "high"
    assert _value(argv, "-g") == "60" and _value(argv, "-keyint_min") == "60"
    assert _value(argv, "-sc_threshold") == "0"
    assert _value(argv, "-r") == "30/1"
    assert _value(argv, "-b:v") == "2500k" and _value(argv, "-maxrate") == "2500k"
    assert _value(argv, "-c:a") == "aac" and _value(argv, "-ar") == "48000"
    assert _value(argv, "-ac") == "2"
    assert _value(argv, "-f") == "lavfi"
    assert argv[argv.index("-muxdelay") - 1] == "mpegts"
    assert "testsrc2=size=1280x720:rate=30/1" in argv[argv.index("-i") + 1]


def test_fractional_rate_and_group():
    argv = source_command(_spec(frame_rate="29_97", group_ms=2002), "mpegts", "o.ts")
    assert _value(argv, "-r") == "30000/1001"
    assert _value(argv, "-g") == "60"


@pytest.mark.parametrize(
    ("height", "frame_rate", "group_ms", "kbps", "bufsize"),
    [
        (720, "30", 2000, 2500, "417k"),
        (720, "29_97", 2002, 2500, "418k"),
        (1080, "60", 2000, 6750, "563k"),
    ],
)
def test_vbv_is_five_frames(height, frame_rate, group_ms, kbps, bufsize):
    spec = _spec(video_height=height, frame_rate=frame_rate, group_ms=group_ms)
    argv = source_command(spec, "mpegts", "o.ts")
    assert _value(argv, "-b:v") == _value(argv, "-maxrate") == f"{kbps}k"
    assert _value(argv, "-bufsize") == bufsize


def test_codecs():
    h264cb = source_command(_spec(video_codec="h264cb"), "mpegts", "o.ts")
    assert _value(h264cb, "-c:v") == "libx264" and _value(h264cb, "-profile:v") == "baseline"
    h265 = source_command(_spec(video_codec="h265"), "mp4", "o.mp4")
    assert _value(h265, "-c:v") == "libx265" and _value(h265, "-tag:v") == "hvc1"
    av1 = source_command(_spec(video_codec="av1"), "mp4", "o.mp4")
    assert _value(av1, "-c:v") == "libsvtav1"
    opus = source_command(_spec(audio_codec="opus"), "mpegts", "o.ts")
    assert _value(opus, "-c:a") == "libopus" and _value(opus, "-frame_duration") == "20"
    assert _value(opus, "-b:a") == "64k"  # draft §Bitrates: opus stereo
    assert _value(opus, "-vbr") == "off"  # libopus VBR overshoots tonal content by ~50%


@pytest.mark.parametrize(
    "content, video, audio",
    [("bars", "smptehdbars", "sine=frequency=1000"), ("noise", "noise=", "anoisesrc")],
)
def test_content(content, video, audio):
    argv = source_command(_spec(content=content), "mpegts", "o.ts")
    graphs = [argv[i + 1] for i, a in enumerate(argv) if a == "-i"]
    assert video in graphs[0] and audio in graphs[1]


def test_mono_and_video_only():
    mono = source_command(_spec(audio_channels=1), "mpegts", "o.ts")
    assert "c=mono" in [mono[i + 1] for i, a in enumerate(mono) if a == "-i"][1]
    video_only = source_command(
        _spec(
            audio_codec="none",
            audio_channels=None,
            audio_delivery=None,
        ),
        "mpegts",
        "o.ts",
    )
    assert "-c:a" not in video_only and video_only.count("-map") == 1


def test_mp4_loop_is_whole_groups():
    argv = source_command(_spec(frame_rate="29_97", group_ms=2002), "mp4", "o.mp4")
    assert "-re" not in argv
    seconds = Fraction(_value(argv, "-t"))
    assert (
        seconds == loop_seconds(_spec(frame_rate="29_97", group_ms=2002)) == Fraction(64064, 1000)
    )
    assert (seconds * 1000) % 2002 == 0 and (seconds * 48000) % 1024 == 0


@pytest.mark.parametrize(
    "changes, target, seconds",
    [
        ({}, None, 56),  # 2 s groups and 1024-sample frames: whole 8 s
        ({"audio_codec": "opus"}, None, 60),  # 20 ms frames divide 2 s groups
        ({"audio_delivery": 7}, None, 56),  # audio groups may straddle the seam
        ({"audio_delivery": 41}, None, 56),
        ({}, 150, 144),  # a clip window: the longest loop that fits
        ({}, 5, 8),  # never less than one unit
        ({"audio_codec": "none", "audio_channels": None, "audio_delivery": None}, None, 60),
        ({"video_codec": "none", "video_height": None, "frame_rate": None, "group_ms": None,
          "video_tracks": None}, None, Fraction(1024 * 2812, 48000)),
        ({"frame_rate": "24", "group_ms": 9875}, None, Fraction(237, 4)),  # unit > max
    ],
)  # fmt: skip
def test_loop_is_whole_video_groups_and_audio_frames(changes, target, seconds):
    assert loop_seconds(_spec(**changes), target) == seconds


def test_mp4_timescales_and_audio_timing():
    argv = source_command(_spec(), "mp4", "o.mp4")
    assert _value(argv, "-video_track_timescale") == "30000"
    assert _value(argv, "-filter:a") == "asetpts=N/SR/TB"
    ntsc = source_command(_spec(frame_rate="29_97", group_ms=2002), "mp4", "o.mp4")
    assert _value(ntsc, "-video_track_timescale") == "30000"
    assert _value(source_command(_spec(frame_rate="60"), "mp4", "o.mp4"), "-r") == "60/1"
    assert _value(source_command(_spec(frame_rate="60"), "mp4", "o.mp4"),
                  "-video_track_timescale") == "60000"  # fmt: skip
    live = source_command(_spec(), "mpegts", "o.ts")
    assert "-video_track_timescale" not in live and "-filter:a" not in live


def test_clock_shows_the_mapped_time():
    # 12:40:06.002 UTC: the flash and the beep fall on the whole seconds of that time.
    argv = source_command(
        _spec(frame_rate="29_97", group_ms=2002), "mpegts", "o.ts", start_us=1793623206002000
    )
    video, audio = (argv[i + 1] for i, a in enumerate(argv) if a == "-i")
    assert "text='%{pts\\:hms\\:45606.002000\\:24HH} f %{n}'" in video
    assert "enable='lt(mod(t+0.002000,1),0.033367)'" in video
    assert "if(lt(mod(t+0.002000\\,1)\\,0.1)" in audio
    wall = source_command(_spec(), "mpegts", "o.ts")
    assert "text='%{gmtime\\:%T} f %{n}'" in wall[wall.index("-i") + 1]


def test_clip_needs_mezzanine():
    with pytest.raises(RecipeError):
        source_command(_spec(content="clip"), "mpegts", "o.ts")


CLIP = {"id": "demo", "path": "/cache/clips/demo-abc.mkv", "attribution": "Demo credit"}


def test_clip_encodes_the_mezzanine_to_the_shape():
    argv = source_command(_spec(content="clip"), "mp4", "o.mp4", CLIP)
    assert "lavfi" not in argv
    i = argv.index("-i")
    assert argv[i - 2 : i + 2] == ["-stream_loop", "-1", "-i", CLIP["path"]]
    assert argv[argv.index("-map") + 1] == "0:v:0" and "0:a:0" in argv
    graph = argv[argv.index("-filter:v") + 1]
    assert graph.startswith("fps=30/1,scale=1280:720:force_original_aspect_ratio=decrease")
    assert "pad=1280:720" in graph and "text='Demo credit'" in graph
    assert _value(argv, "-c:v") == "libx264" and _value(argv, "-g") == "60"
    assert _value(argv, "-c:a") == "aac" and _value(argv, "-t") == "56.000"
    live = source_command(_spec(content="clip"), "mpegts", "o.ts", CLIP)
    assert live[live.index("-stream_loop") - 1] == "-re"
    other = source_command(_spec(content="clip"), "mp4", "o.mp4", {**CLIP, "path": "/x.mkv"})
    assert other != argv  # a new recording gets a new cache key
    long = source_command(_spec(content="clip"), "mp4", "o.mp4", {**CLIP, "loop_s": 150})
    assert _value(long, "-t") == "144.000"


SET = "p/mtt_00/clock/loc/h264/720/30/aac/2/msf/catalog/2000/1/0/"


def _set(tracks, **changes):
    return dataclasses.replace(decode(from_slash(SET + tracks)).spec, **changes)


def test_live_set_carries_every_video_track():
    spec = _set("alt3")
    argv = source_command(spec, "mpegts", "o.ts", start_us=1793623206000000)
    graphs = [argv[i + 1] for i, a in enumerate(argv) if a == "-i"]
    assert len(graphs) == 4 and graphs[3].startswith("aevalsrc")
    assert all("testsrc2=size=1280x720" in g for g in graphs[:3])  # one picture, scaled
    assert (
        "scale" not in graphs[0] and "scale=960:540" in graphs[1] and "scale=640:360" in graphs[2]
    )
    assert [g.rsplit("text=", 1)[1] for g in graphs[:3]] == ["'720p'", "'540p'", "'360p'"]
    assert argv[argv.index("-map") :].count("-map") == 4
    assert [_value(argv, f"-b:v:{i}") for i in range(3)] == ["2500k", "1500k", "600k"]
    assert [_value(argv, f"-g:v:{i}") for i in range(3)] == ["60"] * 3
    assert _value(argv, "-c:v:2") == "libx264" and "-b:v" not in argv


def test_views_are_pictures_of_their_own():
    graphs = [g for g in source_command(_set("view3"), "mpegts", "o.ts") if "testsrc2" in g]
    assert [("hue=h=0" in g, "hue=h=120" in g, "hue=h=240" in g) for g in graphs] == [
        (True, False, False), (False, True, False), (False, False, True),
    ]  # fmt: skip
    assert [g.rsplit("text=", 1)[1] for g in graphs] == ["'view 1'", "'view 2'", "'view 3'"]
    noise = source_command(_set("view2", content="noise"), "mpegts", "o.ts")
    assert [g.count("all_seed=") for g in noise if "noise=" in g] == [1, 1]
    assert "all_seed=0" in noise[noise.index("-i") + 1] and "all_seed=1" in " ".join(noise)


def test_set_mp4_holds_one_track():
    spec = _set("alt2", packaging="cmaf")
    with pytest.raises(RecipeError, match="its own mp4"):
        source_command(spec, "mp4", "o.mp4")
    first, second = (source_command(spec, "mp4", "o.mp4", track=t) for t in spec.video_set())
    assert "-c:a" in first and "-filter:a" in first
    assert "-c:a" not in second and "-filter:a" not in second
    assert _value(first, "-b:v") == "2500k" and _value(second, "-b:v") == "1500k"
    assert _value(first, "-t") == _value(second, "-t")
    assert "%{pts" in first[first.index("-i") + 1]  # the loop position, the same in both


def test_clip_ladder_scales_the_mezzanine():
    spec = _set("alt2", content="clip", clip="film", packaging="cmaf")
    clip = {"path": "/m/film.mp4", "attribution": "Film"}
    argv = source_command(spec, "mp4", "o.mp4", clip, track=spec.video_set()[1])
    assert "scale=960:540" in _value(argv, "-filter:v") and "-c:a" not in argv
    with pytest.raises(RecipeError, match="mp4 loops"):
        source_command(spec, "mpegts", "o.ts", clip)
