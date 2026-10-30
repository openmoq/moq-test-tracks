# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# moq_test_tracks_00 codec, MoQT name representation, and the mtt ns CLI.

import json
import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from moq_test_tracks.cli import main
from moq_test_tracks.spec import (
    NamespaceError,
    TrackSpec,
    decode,
    encode,
    from_slash,
    from_text,
    parse_rendered,
    render,
    to_slash,
)
from moq_test_tracks.spec.fields import (
    AUDIO_CODECS,
    CATALOG_INIT_PACKAGING,
    CATALOG_PACKAGING,
    CATALOGS,
    CONTENTS,
    DATAGRAM_PACKAGING,
    DGRAM,
    FIELDS,
    FRACTIONAL_RATES,
    HEIGHTS,
    INIT_DATA,
    REQUIRED,
    VERSION,
    VIDEO_CODECS,
    group_ms_step,
)

PREFIX = "openmoq/mtt/moq_test_tracks_00/"

# The examples appendix of draft-marzot-moq-test-tracks, in their shortest form.
DRAFT_EXAMPLES = [
    PREFIX + "clock/msf/loc/h264/720/30/aac/2",
    PREFIX + "clock/msf/loc/av1/1080/60/opus/2/track/2000/dgram",
    PREFIX + "clock/msf/loc/h264cb/360/30/aac/2/object",
    PREFIX + "clock/msf/cmaf/h264/720/29_97/none/_/catalog/2002/_/600",
    PREFIX + "clock/none/loc/none/_/_/opus/1/_/_/10",
    PREFIX + "clock/msf/loc/h264/1080/30/aac/2/catalog/2000/1/0/alt3",
    PREFIX + "clock/msf/loc/h264/720/30/aac/2/catalog/2000/1/0/view4",
]
CLIP_EXAMPLE = "openmoq/mtt/clip_a/moq_test_tracks_00/clip/msf/cmaf/h264/1080/30/aac/2"
FULL_FORM = PREFIX + "clock/msf/loc/h264/720/30/aac/2/catalog/2000/1/0/1"
BASE = DRAFT_EXAMPLES[0]
CMAF_EXAMPLE = DRAFT_EXAMPLES[3]
DRAFT_RENDERED = "openmoq-mtt-moq_test_tracks_00-clock-msf-loc-h264-720-30-aac-2--video"


def _ns(text):
    return from_slash(text)


def _base(**changes):
    fields = dict(
        content="clock",
        catalog="msf",
        packaging="loc",
        video_codec="h264",
        video_height=720,
        frame_rate="30",
        audio_codec="aac",
        audio_channels=2,
        init_data="catalog",
        group_ms=2000,
        audio_delivery=1,
        duration_s=0,
        video_tracks="1",
    )
    fields.update(changes)
    return TrackSpec(**fields)


def test_only_trailing_fields_are_optional():
    assert all(f.omitted is None for f in FIELDS[:REQUIRED])
    assert all(f.omitted is not None for f in FIELDS[REQUIRED:])
    assert [f.index for f in FIELDS] == list(range(1, len(FIELDS) + 1))


@pytest.mark.parametrize("text", DRAFT_EXAMPLES)
def test_draft_examples_round_trip(text):
    decoded = decode(_ns(text))
    assert decoded.protocol == VERSION
    assert decoded.prefix == (b"openmoq", b"mtt")
    assert to_slash(encode(decoded.spec, decoded.prefix)) == text


def test_clip_example():
    decoded = decode(_ns(CLIP_EXAMPLE))
    assert decoded.prefix == (b"openmoq", b"mtt", b"clip_a")
    assert decoded.spec.content == "clip" and decoded.spec.video_kbps == 4500
    assert to_slash(encode(decoded.spec, decoded.prefix)) == CLIP_EXAMPLE


def test_full_form_names_the_same_tracks():
    full = decode(_ns(FULL_FORM))
    assert full.spec == decode(_ns(BASE)).spec
    assert to_slash(encode(full.spec, full.prefix)) == BASE  # publishers use the shortest form


def test_omitted_values():
    spec = decode(_ns(BASE)).spec
    assert (spec.init_data, spec.group_ms, spec.audio_delivery) == ("catalog", 2000, 1)
    assert (spec.duration_s, spec.video_tracks) == (0, "1")
    assert decode(_ns(PREFIX + "clock/msf/loc/h264/720/29_97/aac/2")).spec.group_ms == 2002
    audio_only = decode(_ns(DRAFT_EXAMPLES[4])).spec
    assert audio_only.init_data is None and audio_only.group_ms is None
    assert audio_only.video_tracks is None and audio_only.audio_delivery == 10


def test_bitrates():
    assert [_base(video_height=h).video_kbps for h in HEIGHTS] == [
        200, 600, 1000, 1500, 2500, 4500, 9000, 16000
    ]  # fmt: skip
    assert _base(frame_rate="60").video_kbps == 3750
    assert _base(frame_rate="29_97").video_kbps == 2500
    assert _base().audio_kbps == 128 and _base(audio_channels=1).audio_kbps == 64
    assert _base(audio_codec="opus").audio_kbps == 64
    assert _base(audio_codec="opus", audio_channels=1).audio_kbps == 32


def test_draft_example_values():
    spec = decode(_ns(CMAF_EXAMPLE)).spec
    assert spec.catalog == "msf" and spec.packaging == "cmaf" and spec.init_data == "catalog"
    assert spec.frames_per_group == 60
    assert spec.video_width == 1280
    assert not spec.has_audio and spec.audio_kbps is None
    assert spec.tracks == ("catalog", "video")
    assert spec.duration_s == 600


def test_datagram_audio():
    spec = decode(_ns(DRAFT_EXAMPLES[1])).spec
    assert spec.audio_datagram and spec.audio_frames_per_group == 1
    assert spec.audio_delivery_mode == DGRAM and _base().audio_delivery_mode == "stream"
    assert spec.video_kbps == 6750 and spec.audio_kbps == 64


def test_ladder_and_views():
    ladder = decode(_ns(DRAFT_EXAMPLES[5])).spec
    assert ladder.ladder() == (1080, 720, 540)
    assert ladder.tracks == ("catalog", "video", "video_1", "video_2", "audio")
    assert [ladder.kbps_at(h) for h in ladder.ladder()] == [4500, 2500, 1500]
    assert _base(video_height=1080, video_tracks="alt4").ladder() == (1080, 720, 540, 360)
    assert _base(video_height=2160, video_tracks="alt7").ladder()[-1] == 180
    views = decode(_ns(DRAFT_EXAMPLES[6])).spec
    assert views.is_views and views.ladder() is None
    assert views.video_track_names == ("video", "video_1", "video_2", "video_3")


def test_draft_rendered_example():
    namespace = _ns(BASE)
    assert render(namespace, "video") == DRAFT_RENDERED
    assert parse_rendered(DRAFT_RENDERED) == (namespace, b"video")


def test_timing():
    spec = decode(_ns(CMAF_EXAMPLE)).spec
    vt = spec.video_timing()
    assert (vt.timescale, vt.object_multiplier, vt.group_multiplier) == (30000, 1001, 60060)
    at = _base(audio_codec="opus", audio_delivery=10).audio_timing()
    assert (at.timescale, at.object_multiplier, at.group_multiplier) == (48000, 960, 9600)
    assert _base().video_timing().timescale == 30000


def test_widths():
    widths = {h: _base(video_height=h).video_width for h in HEIGHTS}
    assert widths == {
        180: 320,
        360: 640,
        480: 854,
        540: 960,
        720: 1280,
        1080: 1920,
        1440: 2560,
        2160: 3840,
    }


def _replace(text, index, token):
    parts = text.split("/")
    parts[2 + index] = token  # parts[2] is the version field
    return "/".join(parts)


@pytest.mark.parametrize(
    "text, index",
    [
        (_replace(FULL_FORM, 1, "film"), 1),
        (_replace(FULL_FORM, 2, "cmsf"), 2),  # CMSF is an MSF catalog
        (PREFIX + "clock/none/cmaf/h264/720/30/aac/2", 3),
        (_replace(FULL_FORM, 4, "vp9"), 4),
        (_replace(FULL_FORM, 4, "h264h"), 4),
        (_replace(FULL_FORM, 5, "0720"), 5),  # leading zero
        (_replace(FULL_FORM, 5, "+720"), 5),
        (_replace(FULL_FORM, 5, "700"), 5),  # not a defined height
        (_replace(FULL_FORM, 5, "_"), 5),  # applies, so not _
        (PREFIX + "clock/none/loc/none/720/_/aac/2", 5),  # n/a must be _
        (_replace(FULL_FORM, 6, "121"), 6),
        (_replace(FULL_FORM, 6, "29.97"), 6),
        (_replace(FULL_FORM, 8, "3"), 8),
        (_replace(FULL_FORM, 9, "inline"), 9),
        (_replace(FULL_FORM, 9, "_"), 9),  # a catalog exists, so init data applies
        (_replace(_replace(FULL_FORM, 3, "cmaf"), 9, "track"), 9),  # CMSF: init in the catalog
        (PREFIX + "clock/none/loc/h264/720/30/aac/2/catalog", 9),
        (_replace(_replace(FULL_FORM, 6, "29_97"), 10, "2000"), 10),  # not whole frames
        (_replace(FULL_FORM, 11, "0"), 11),
        (_replace(FULL_FORM, 11, "101"), 11),
        (_replace(FULL_FORM, 11, "datagram"), 11),
        (_replace(_replace(FULL_FORM, 3, "locmaf"), 11, "dgram"), 11),
        (_replace(FULL_FORM, 12, "86401"), 12),
        (_replace(FULL_FORM, 13, "2"), 13),
        (_replace(FULL_FORM, 13, "alt1"), 13),
        (_replace(FULL_FORM, 13, "alt8"), 13),
        (_replace(FULL_FORM, 13, "view17"), 13),
        (PREFIX + "clock/msf/loc/h264/180/15/aac/2/catalog/2000/1/0/alt2", 13),  # no lower rung
        (PREFIX + "clock/none/loc/h264/720/30/aac/2/_/2000/1/0/alt2", 13),  # needs msf
    ],
)
def test_invalid_fields(text, index):
    with pytest.raises(NamespaceError) as e:
        decode(_ns(text))
    assert index in [err.index for err in e.value.errors]


@pytest.mark.parametrize(
    "namespace",
    [
        _ns(BASE)[:-1],  # too few fields
        _ns(FULL_FORM) + (b"x",),  # too many
        _ns(BASE.replace(VERSION, "moq_test_tracks_01")),  # unknown version
        tuple(f"p{i}".encode() for i in range(24)) + _ns(BASE)[2:],  # over 32 fields
        _ns(BASE)[:1] + (b"",) + _ns(BASE)[2:],  # empty field
        (b"openmoq", b"mtt", VERSION.encode()) + (b"\xff",) + _ns(BASE)[4:],
    ],
)
def test_invalid_namespaces(namespace):
    with pytest.raises(NamespaceError):
        decode(namespace)


def test_prefix_cannot_hold_a_version():
    with pytest.raises(NamespaceError):
        encode(_base(), prefix=("mtt", VERSION))


def test_check_rejects_bad_spec():
    with pytest.raises(NamespaceError):
        encode(_base(video_codec="none"))  # video fields set but do not apply


def _rungs(height):
    """Renditions the 3/4 rule yields from a height."""
    n = 1
    while lower := [h for h in HEIGHTS if 4 * h <= 3 * height]:
        height, n = max(lower), n + 1
    return n


@st.composite
def specs(draw):
    catalog = draw(st.sampled_from(CATALOGS))
    packaging = draw(st.sampled_from(CATALOG_PACKAGING[catalog]))
    if catalog == "none":
        init_data = None
    elif packaging in CATALOG_INIT_PACKAGING:
        init_data = "catalog"
    else:
        init_data = draw(st.sampled_from(INIT_DATA))
    video_codec = draw(st.sampled_from(VIDEO_CODECS))
    audio_codec = draw(st.sampled_from(AUDIO_CODECS))
    video = dict.fromkeys(("video_height", "frame_rate", "group_ms", "video_tracks"))
    if video_codec != "none":
        rate = draw(st.sampled_from([*FRACTIONAL_RATES, *(str(n) for n in range(1, 121))]))
        step = group_ms_step(rate)
        k = draw(st.integers(math.ceil(100 / step), 10000 // step))
        height = draw(st.sampled_from(HEIGHTS))
        tracks = ["1"]
        if catalog == "msf":
            tracks += ["view2", "view16"]
            tracks += [f"alt{n}" for n in range(2, min(_rungs(height), 7) + 1)]
        video = dict(
            video_height=height,
            frame_rate=rate,
            group_ms=k * step,
            video_tracks=draw(st.sampled_from(tracks)),
        )
    audio = dict.fromkeys(("audio_channels", "audio_delivery"))
    if audio_codec != "none":
        delivery = st.integers(1, 100)
        if packaging in DATAGRAM_PACKAGING:
            delivery = st.one_of(delivery, st.just(DGRAM))
        audio = dict(
            audio_channels=draw(st.sampled_from((1, 2))),
            audio_delivery=draw(delivery),
        )
    return TrackSpec(
        content=draw(st.sampled_from(CONTENTS)),
        catalog=catalog,
        packaging=packaging,
        video_codec=video_codec,
        audio_codec=audio_codec,
        init_data=init_data,
        duration_s=draw(st.integers(0, 86400)),
        **video,
        **audio,
    )


prefixes = st.lists(
    st.from_regex(r"[a-z0-9_]{1,12}", fullmatch=True).filter(lambda s: s != VERSION),
    max_size=5,
)


@given(specs(), prefixes)
def test_round_trip(spec, prefix):
    namespace = encode(spec, prefix)
    decoded = decode(namespace)
    assert decoded.spec == spec
    assert decoded.prefix == tuple(p.encode() for p in prefix)
    assert from_slash(to_slash(namespace)) == namespace
    assert parse_rendered(render(namespace)) == (namespace, None)
    assert "." not in render(namespace)  # every field renders verbatim


@given(specs())
def test_every_length_decodes_to_the_same_spec(spec):
    shortest = encode(spec)
    tokens = [getattr(spec, f.name) for f in FIELDS]
    full = (VERSION.encode(), *(b"_" if t is None else str(t).encode() for t in tokens))
    for n in range(len(shortest), len(full) + 1):
        assert decode(full[:n]).spec == spec


@given(
    st.lists(st.binary(min_size=1, max_size=16), max_size=8),
    st.one_of(st.none(), st.binary(max_size=16)),
)
def test_rendered_round_trip_any_bytes(namespace, track):
    assert parse_rendered(render(namespace, track)) == (tuple(namespace), track)


def test_moqt_rendering_example():
    namespace, track = parse_rendered("example.2enet-team2-project_x--report")
    assert namespace == (b"example.net", b"team2", b"project_x")
    assert track == b"report"


@pytest.mark.parametrize(
    "text",
    ["a.61", "a.2E", "a.2", "a.", "a-", "-a", "a--b-c", "a b", "café", "a.g0"],
)
def test_rendered_rejects_non_canonical(text):
    with pytest.raises(ValueError):
        parse_rendered(text)


def test_from_text_accepts_both_notations():
    namespace = _ns(BASE)
    assert from_text(BASE) == namespace
    assert from_text(render(namespace)) == namespace


ENCODE_ARGS = [
    "ns",
    "encode",
    "--prefix",
    "openmoq/mtt",
    "--content", "clock", "--cat", "msf", "--pkg", "loc",
    "--vc", "h264", "--h", "720", "--fps", "30", "--ac", "aac", "--ch", "2",
]  # fmt: skip


def test_cli_encode(capsys):
    assert main(ENCODE_ARGS) == 0
    assert capsys.readouterr().out.strip() == BASE
    assert main([*ENCODE_ARGS, "--render", "--track", "video"]) == 0
    assert capsys.readouterr().out.strip() == DRAFT_RENDERED
    assert main([*ENCODE_ARGS, "--adel", "dgram"]) == 0
    assert capsys.readouterr().out.strip() == BASE + "/catalog/2000/dgram"


def test_cli_encode_requires_required_fields(capsys):
    args = [a for a in ENCODE_ARGS if a not in ("--h", "720")]
    assert main(args) == 2
    assert "--h is required" in capsys.readouterr().err


def test_cli_decode(capsys):
    assert main(["ns", "decode", DRAFT_RENDERED.removesuffix("--video")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["protocol"] == VERSION
    assert out["prefix"] == ["openmoq", "mtt"]
    assert out["tracks"] == ["catalog", "video", "audio"]
    assert out["video"]["kbps"] == 2500 and out["audio"]["kbps"] == 128
    assert out["video"]["timing"] == {
        "timescale": 30000,
        "object_multiplier": 1000,
        "group_multiplier": 60000,
    }


def test_cli_decode_ladder(capsys):
    assert main(["ns", "decode", DRAFT_EXAMPLES[5]]) == 0
    ladder = json.loads(capsys.readouterr().out)["video"]["ladder"]
    assert [(r["track"], r["height"], r["kbps"]) for r in ladder] == [
        ("video", 1080, 4500), ("video_1", 720, 2500), ("video_2", 540, 1500)
    ]  # fmt: skip


def test_cli_decode_reports_field(capsys):
    assert main(["ns", "decode", _replace(BASE, 5, "700")]) == 2
    assert "tuple field 5 (video_height)" in capsys.readouterr().err


def test_cli_render(capsys):
    assert main(["ns", "render", BASE, "--track", "video"]) == 0
    assert capsys.readouterr().out.strip() == DRAFT_RENDERED
