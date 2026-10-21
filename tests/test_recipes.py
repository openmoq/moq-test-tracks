# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# Content recipes: the ffmpeg command lines for each field set.

import dataclasses
from fractions import Fraction

import pytest

from moq_test_tracks.content.recipes import RecipeError, loop_seconds, source_command
from moq_test_tracks.spec import decode, from_slash

BASE = "p/moq_test_tracks_00/msf/loc/h264/720/30/2500/2000/0/aac/2/128/1/0/ts/0"


def _spec(**changes):
    return dataclasses.replace(decode(from_slash(BASE)).spec, **changes)


def _value(argv, flag):
    return argv[argv.index(flag) + 1]


def test_live_h264_aac():
    argv = source_command(_spec(), "mpegts", "out/s.ts")
    assert argv[0] == "ffmpeg" and argv[-1] == "out/s.ts"
    assert argv.count("-re") == 2
    assert _value(argv, "-c:v") == "libx264" and _value(argv, "-bf") == "0"
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


def test_codecs():
    h265 = source_command(_spec(video_codec="h265"), "mp4", "o.mp4")
    assert _value(h265, "-c:v") == "libx265" and _value(h265, "-tag:v") == "hvc1"
    av1 = source_command(_spec(video_codec="av1"), "mp4", "o.mp4")
    assert _value(av1, "-c:v") == "libsvtav1"
    opus = source_command(_spec(audio_codec="opus", audio_kbps=64), "mpegts", "o.ts")
    assert _value(opus, "-c:a") == "libopus" and _value(opus, "-frame_duration") == "20"


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
            audio_kbps=None,
            audio_frames_per_group=None,
            audio_forwarding=None,
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
        seconds == loop_seconds(_spec(frame_rate="29_97", group_ms=2002)) == Fraction(60060, 1000)
    )
    assert (seconds * 1000) % 2002 == 0


def test_clip_needs_mezzanine():
    with pytest.raises(RecipeError):
        source_command(_spec(content="clip"), "mpegts", "o.ts")
