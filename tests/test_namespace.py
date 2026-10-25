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
    FRACTIONAL_RATES,
    HEIGHTS,
    INIT_DATA,
    VERSION,
    VIDEO_CODECS,
    group_ms_step,
)

PREFIX = "openmoq/mtt/moq_test_tracks_00/"

# The examples appendix of draft-marzot-moq-test-tracks.
DRAFT_EXAMPLES = [
    PREFIX + "msf/loc/catalog/h264/720/30/2500/2000/0/aac/2/128/1/0/clock/0",
    PREFIX + "msf/loc/track/av1/1080/60/6000/2000/0/opus/2/64/1/3/clock/0",
    PREFIX + "msf/loc/object/h264cb/360/30/600/2000/0/aac/2/128/1/0/clock/0",
    PREFIX + "msf/cmaf/catalog/h264/720/29_97/2500/2002/0/none/_/_/_/_/clock/600",
    PREFIX + "none/loc/_/none/_/_/_/_/_/opus/1/32/10/0/clock/0",
]
CMAF_EXAMPLE = DRAFT_EXAMPLES[3]
DRAFT_RENDERED = (
    "openmoq-mtt-moq_test_tracks_00-msf-loc-catalog-h264-720-30-2500-2000-0-aac-2-128-1-0-clock-0"
    "--video"
)


def _ns(text):
    return from_slash(text)


def _base(**changes):
    fields = dict(
        catalog="msf",
        packaging="loc",
        init_data="catalog",
        video_codec="h264",
        video_height=720,
        frame_rate="30",
        video_kbps=2500,
        group_ms=2000,
        video_forwarding=0,
        audio_codec="aac",
        audio_channels=2,
        audio_kbps=128,
        audio_frames_per_group=1,
        audio_forwarding=0,
        content="clock",
        duration_s=0,
    )
    fields.update(changes)
    return TrackSpec(**fields)


@pytest.mark.parametrize("text", DRAFT_EXAMPLES)
def test_draft_examples_round_trip(text):
    decoded = decode(_ns(text))
    assert decoded.protocol == VERSION
    assert decoded.prefix == (b"openmoq", b"mtt")
    assert to_slash(encode(decoded.spec, decoded.prefix)) == text


def test_draft_example_values():
    spec = decode(_ns(CMAF_EXAMPLE)).spec
    assert spec.catalog == "msf" and spec.packaging == "cmaf" and spec.init_data == "catalog"
    assert spec.frames_per_group == 60
    assert spec.video_width == 1280
    assert not spec.has_audio and spec.audio_kbps is None
    assert spec.tracks == ("catalog", "video")
    assert spec.duration_s == 600


def test_draft_rendered_example():
    namespace = _ns(DRAFT_EXAMPLES[0])
    assert render(namespace, "video") == DRAFT_RENDERED
    assert parse_rendered(DRAFT_RENDERED) == (namespace, b"video")


def test_timing():
    spec = decode(_ns(CMAF_EXAMPLE)).spec
    vt = spec.video_timing()
    assert (vt.timescale, vt.object_multiplier, vt.group_multiplier) == (30000, 1001, 60060)
    at = _base(audio_codec="opus", audio_frames_per_group=10).audio_timing()
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


BASE = DRAFT_EXAMPLES[0]


@pytest.mark.parametrize(
    "text, index",
    [
        (_replace(BASE, 1, "cmsf"), 1),  # CMSF is an MSF catalog
        (_replace(BASE, 3, "inline"), 3),
        (_replace(BASE, 3, "_"), 3),  # a catalog exists, so init data applies
        (_replace(_replace(BASE, 2, "cmaf"), 3, "track"), 3),  # CMSF: init in the catalog
        (PREFIX + "none/loc/catalog/h264/720/30/2500/2000/0/aac/2/128/1/0/clock/0", 3),
        (_replace(BASE, 5, "0720"), 5),  # leading zero
        (_replace(BASE, 5, "+720"), 5),
        (_replace(BASE, 5, "700"), 5),  # not a defined height
        (_replace(BASE, 5, "_"), 5),  # applies, so not _
        (_replace(BASE, 4, "vp9"), 4),
        (_replace(BASE, 4, "h264h"), 4),
        (_replace(BASE, 6, "121"), 6),
        (_replace(BASE, 6, "29.97"), 6),
        (_replace(BASE, 7, "49"), 7),
        (_replace(_replace(BASE, 6, "29_97"), 8, "2000"), 8),  # not whole frames
        (_replace(BASE, 9, "2"), 9),
        (_replace(BASE, 11, "3"), 11),
        (_replace(BASE, 13, "101"), 13),
        (_replace(BASE, 14, "2"), 14),
        (_replace(BASE, 15, "film"), 15),
        (_replace(BASE, 16, "86401"), 16),
        (PREFIX + "none/cmaf/_/h264/720/30/2500/2000/0/aac/2/128/1/0/clock/0", 2),
        (PREFIX + "none/loc/_/none/720/_/_/_/_/aac/2/128/1/0/clock/0", 5),  # n/a must be _
        (PREFIX + "msf/locmaf/catalog/h264/720/30/2500/2000/0/aac/2/128/1/3/clock/0", 14),
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
        _ns(BASE) + (b"x",),  # too many
        _ns(BASE.replace(VERSION, "moq_test_tracks_01")),  # unknown version
        tuple(f"p{i}".encode() for i in range(20)) + _ns(BASE)[2:],  # over 32 fields
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
    video = dict.fromkeys(
        ("video_height", "frame_rate", "video_kbps", "group_ms", "video_forwarding")
    )
    if video_codec != "none":
        rate = draw(st.sampled_from([*FRACTIONAL_RATES, *(str(n) for n in range(1, 121))]))
        step = group_ms_step(rate)
        k = draw(st.integers(math.ceil(100 / step), 10000 // step))
        video = dict(
            video_height=draw(st.sampled_from(HEIGHTS)),
            frame_rate=rate,
            video_kbps=draw(st.integers(50, 50000)),
            group_ms=k * step,
            video_forwarding=draw(st.sampled_from((0, 1))),
        )
    audio = dict.fromkeys(
        ("audio_channels", "audio_kbps", "audio_frames_per_group", "audio_forwarding")
    )
    if audio_codec != "none":
        forwarding = (0, 1, 3) if packaging in DATAGRAM_PACKAGING else (0, 1)
        audio = dict(
            audio_channels=draw(st.sampled_from((1, 2))),
            audio_kbps=draw(st.integers(8, 512)),
            audio_frames_per_group=draw(st.integers(1, 100)),
            audio_forwarding=draw(st.sampled_from(forwarding)),
        )
    return TrackSpec(
        catalog=catalog,
        packaging=packaging,
        init_data=init_data,
        video_codec=video_codec,
        audio_codec=audio_codec,
        content=draw(st.sampled_from(CONTENTS)),
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
    "--cat", "msf", "--pkg", "loc", "--init", "catalog",
    "--vc", "h264", "--h", "720", "--fps", "30", "--vbr", "2500", "--gop", "2000", "--vfwd", "0",
    "--ac", "aac", "--ch", "2", "--abr", "128", "--afpg", "1", "--afwd", "0",
    "--content", "clock", "--dur", "0",
]  # fmt: skip


def test_cli_encode(capsys):
    assert main(ENCODE_ARGS) == 0
    assert capsys.readouterr().out.strip() == BASE
    assert main([*ENCODE_ARGS, "--render", "--track", "video"]) == 0
    assert capsys.readouterr().out.strip() == DRAFT_RENDERED


def test_cli_encode_requires_applicable_fields(capsys):
    args = [a for a in ENCODE_ARGS if a not in ("--vbr", "2500")]
    assert main(args) == 2
    assert "--vbr is required" in capsys.readouterr().err


def test_cli_decode(capsys):
    assert main(["ns", "decode", DRAFT_RENDERED.removesuffix("--video")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["protocol"] == VERSION
    assert out["prefix"] == ["openmoq", "mtt"]
    assert out["tracks"] == ["catalog", "video", "audio"]
    assert out["video"]["timing"] == {
        "timescale": 30000,
        "object_multiplier": 1000,
        "group_multiplier": 60000,
    }


def test_cli_decode_reports_field(capsys):
    assert main(["ns", "decode", _replace(BASE, 5, "700")]) == 2
    assert "tuple field 5 (video_height)" in capsys.readouterr().err


def test_cli_render(capsys):
    assert main(["ns", "render", BASE, "--track", "video"]) == 0
    assert capsys.readouterr().out.strip() == DRAFT_RENDERED
