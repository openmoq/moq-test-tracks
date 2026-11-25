# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# Characteristic fields of mtt_00 (draft-marzot-moq-test-tracks).

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction

VERSION = "mtt_00"
NA = "_"

GENERATED = ("clock", "bars", "noise")
CONTENTS = (*GENERATED, "clip")  # for clip, tuple field 1 is the clip id
# Field 1 values the draft reserves for later revisions; never clip ids
RESERVED = (
    *("black", "motion", "zoneplate", "ramp", "sweep", "silence", "sync", "scroll"),
    *("clip", "none", "any", "all", "default", "test", "live", "random"),
)
CATALOGS = ("msf", "none")
TS = "msfts"  # MSFTS transparent carriage: one MPEG-TS track (draft-gregoire-moq-msfts)
PACKAGINGS = ("loc", "cmaf", "locmaf", TS)
VIDEO_CODECS = ("h264", "h264cb", "h265", "av1", "mpeg2", "none")
HEIGHTS = (180, 360, 480, 540, 720, 1080, 1440, 2160)
FRACTIONAL_RATES = {
    "23_976": Fraction(24000, 1001),
    "29_97": Fraction(30000, 1001),
    "59_94": Fraction(60000, 1001),
}
# msfts audio set: the program carries one track of each (AUDIO_SET_TRACKS)
AUDIO_SET = "s302m_aac_ac3"
AUDIO_CODECS = ("aac", "opus", "mp2", AUDIO_SET, "none")
INIT_DATA = ("catalog", "track", "object")
DGRAM = "dgram"
ONE_TRACK = "1"

CATALOG_PACKAGING = {"msf": ("loc", "cmaf", "locmaf", TS), "none": ("loc",)}
# CMSF: CMAF headers go inline in the catalog; MSFTS: the PAT and PMT
CATALOG_INIT_PACKAGING = ("cmaf", "locmaf", TS)
DATAGRAM_PACKAGING = ("loc", "cmaf")

# Draft §Bitrates: video by height, 1.5 times above 30 fps; audio by codec, mono and stereo.
VIDEO_KBPS = {
    180: 200,
    360: 600,
    480: 1000,
    540: 1500,
    720: 2500,
    1080: 4500,
    1440: 9000,
    2160: 16000,
}
AUDIO_KBPS = {"aac": (64, 128), "opus": (32, 64), "mp2": (96, 192)}
# (codec, channels, kbps) per track of AUDIO_SET; s302m is 24-bit PCM at 48 kHz.
AUDIO_SET_TRACKS = (("s302m", 2, 2688), ("aac", 2, 256), ("ac3", 6, 448))
MAX_LADDER = 7
MAX_VIEWS = 16

AUDIO_SAMPLE_RATE = 48000
# AUDIO_SET: the span after which its AAC and AC-3 frames both start again
AUDIO_FRAME_SAMPLES = {"aac": 1024, "opus": 960, "mp2": 1152, AUDIO_SET: 3072}

# MSFTS: total TS rate (null-padded CBR) -> video rate by codec; default total by height.
TS_MUX_KBPS = {8000: 6500, 20000: 17000, 40000: 36000}
TS_VIDEO_KBPS = {"mpeg2": TS_MUX_KBPS, "h265": {8000: 4000, 20000: 15700, 40000: 32000}}
TS_DEFAULT_MUX_KBPS = {720: 8000, 1080: 20000}
TS_GROUP_MS = 1000
TS_PACKET_BYTES = 188
TS_PACKETS = 7  # per Object: 1316 B, an SRT payload
TS_MAX_PACKETS = 348  # 65424 B, under moq2ts's default 64 KiB Object
TS_TRACK = "program-1"
TS_TIMELINE_TRACK = "program-1.timeline"
MPEG2_RATES = ("23_976", "24", "25", "29_97", "30", "50", "59_94", "60")
TS_VIDEO_CODECS = ("mpeg2", "h265")
TS_AUDIO_CODECS = ("mp2", AUDIO_SET, "none")
TS_ONLY_CODECS = ("mpeg2", "mp2", AUDIO_SET)

REQUIRED = 7  # tuple fields 1-7; fields 8-15 may be omitted from the end

_DECIMAL = re.compile(r"0|[1-9][0-9]*")
CLIP_ID = re.compile(r"[a-z0-9_]+")
_VIDEO_TRACKS = re.compile(r"(alt|view)([1-9][0-9]*)")


@dataclass(frozen=True)
class FieldError:
    index: int | None  # tuple field index; 0 is the version, None is the whole namespace
    name: str
    message: str

    def __str__(self) -> str:
        if self.index is None:
            return f"{self.name}: {self.message}"
        return f"tuple field {self.index} ({self.name}): {self.message}"


class NamespaceError(ValueError):
    def __init__(self, errors: list[FieldError] | tuple[FieldError, ...]):
        self.errors = tuple(errors)
        super().__init__("; ".join(str(e) for e in self.errors))


def _decimal(token: str) -> int:
    if not _DECIMAL.fullmatch(token):
        raise ValueError(f"{token!r} is not a decimal integer without leading zeros")
    return int(token)


def _tokens(choices: tuple[str, ...]) -> Callable[[str], str]:
    def parse(token: str) -> str:
        if token not in choices:
            raise ValueError(f"{token!r} is not one of {', '.join(choices)}")
        return token

    return parse


def _int_choices(choices: tuple[int, ...]) -> Callable[[str], int]:
    def parse(token: str) -> int:
        value = _decimal(token)
        if value not in choices:
            raise ValueError(f"{value} is not one of {', '.join(map(str, choices))}")
        return value

    return parse


def _int_range(lo: int, hi: int) -> Callable[[str], int]:
    def parse(token: str) -> int:
        value = _decimal(token)
        if not lo <= value <= hi:
            raise ValueError(f"{value} is outside {lo}-{hi}")
        return value

    return parse


def _content(token: str) -> str:
    if token in GENERATED or CLIP_ID.fullmatch(token):
        return token
    raise ValueError(f"{token!r} is not clock, bars, noise or a clip id of a-z, 0-9 and _")


def split_content(token: str) -> tuple[str, str | None]:
    """Field 1 to (content, clip id)."""
    return (token, None) if token in GENERATED else ("clip", token)


def _frame_rate(token: str) -> str:
    if token in FRACTIONAL_RATES:
        return token
    if not 1 <= _decimal(token) <= 120:
        raise ValueError(f"{token} is outside 1-120 and not 23_976, 29_97 or 59_94")
    return token


def _audio_delivery(token: str) -> int | str:
    if token == DGRAM:
        return token
    try:
        return _int_range(1, 100)(token)
    except ValueError:
        raise ValueError(f"{token!r} is not 1-100 frames per group or {DGRAM}") from None


def _video_tracks(token: str) -> str:
    if token == ONE_TRACK:
        return token
    m = _VIDEO_TRACKS.fullmatch(token)
    top = {"alt": MAX_LADDER, "view": MAX_VIEWS}[m.group(1)] if m else 0
    if not m or not 2 <= int(m.group(2)) <= top:
        raise ValueError(f"{token!r} is not 1, alt2-alt{MAX_LADDER} or view2-view{MAX_VIEWS}")
    return token


def width_of(height: int) -> int:
    """16:9 width, even."""
    return 2 * round(height * 16 / 9 / 2)


def default_group_ms(frame_rate: str, packaging: str | None = None) -> int:
    """Group duration when tuple field 10 is omitted: whole frames nearest 2 s, or 1 s with
    msfts."""
    if packaging == TS:
        return 1001 if frame_rate in FRACTIONAL_RATES else TS_GROUP_MS
    return 2002 if frame_rate in FRACTIONAL_RATES else 2000


def _default_mux_kbps(values: dict) -> int:
    return TS_DEFAULT_MUX_KBPS[1080 if int(values["video_height"] or 0) >= 1080 else 720]


@dataclass(frozen=True)
class Field:
    index: int
    name: str
    # "catalog", "video" or "audio": applies only when that track exists; "channels": audio
    # other than AUDIO_SET
    needs: str | None
    parse: Callable[[str], object]
    omitted: Callable[[dict], object] | None = None  # value when left off the end; None: required
    ts: bool | None = None  # True: msfts packaging only; False: never with msfts


def applies(
    field: Field,
    *,
    catalog: bool,
    video: bool,
    audio: bool,
    ts: bool = False,
    channels: bool | None = None,
) -> bool:
    if field.ts is not None and field.ts != ts:
        return False
    has = {"catalog": catalog, "video": video, "audio": audio}
    has["channels"] = audio if channels is None else channels
    return field.needs is None or has[field.needs]


def has_channels(audio_codec: object) -> bool:
    return audio_codec not in ("none", AUDIO_SET, None)


# The field whose value none means the track is absent
_NEEDS = {
    "catalog": "catalog",
    "video": "video_codec",
    "audio": "audio_codec",
    "channels": "audio_codec",
}

FIELDS = (
    Field(1, "content", None, _content),
    Field(2, "packaging", None, _tokens(PACKAGINGS)),
    Field(3, "video_codec", None, _tokens(VIDEO_CODECS)),
    Field(4, "video_height", "video", _int_choices(HEIGHTS)),
    Field(5, "frame_rate", "video", _frame_rate),
    Field(6, "audio_codec", None, _tokens(AUDIO_CODECS)),
    Field(7, "audio_channels", "channels", _int_choices((1, 2))),
    Field(8, "catalog", None, _tokens(CATALOGS), lambda v: "msf"),
    Field(9, "init_data", "catalog", _tokens(INIT_DATA), lambda v: "catalog"),
    Field(
        10,
        "group_ms",
        "video",
        _int_range(100, 10000),
        lambda v: default_group_ms(v["frame_rate"], v["packaging"]),
    ),
    Field(11, "audio_delivery", "audio", _audio_delivery, lambda v: 1, ts=False),
    Field(12, "duration_s", None, _int_range(0, 86400), lambda v: 0),
    Field(13, "video_tracks", "video", _video_tracks, lambda v: ONE_TRACK, ts=False),
    Field(14, "mux_kbps", "video", _int_choices(tuple(TS_MUX_KBPS)), _default_mux_kbps, ts=True),
    Field(15, "ts_packets", None, _int_range(1, TS_MAX_PACKETS), lambda v: TS_PACKETS, ts=True),
)


def omitted_value(field: Field, values: dict) -> object:
    """The value of an optional field left off the end, given the fields before it."""
    if field.omitted is None or (field.needs and values[_NEEDS[field.needs]] == "none"):
        return None
    if field.ts is not None and field.ts != (values["packaging"] == TS):
        return None
    return field.omitted(values)


@dataclass(frozen=True)
class Timing:
    """TIMESCALE and TIMESTAMP_MAPPING multipliers for one track (draft §Timing)."""

    timescale: int
    object_multiplier: int
    group_multiplier: int


@dataclass(frozen=True)
class VideoTrack:
    """One video track of a set (draft §Several Video Tracks)."""

    track: str
    width: int
    height: int
    kbps: int
    label: str | None  # catalog label; None for a single video track


@dataclass(frozen=True)
class TrackSpec:
    content: str
    catalog: str
    packaging: str
    video_codec: str
    video_height: int | None
    frame_rate: str | None
    audio_codec: str
    audio_channels: int | None
    init_data: str | None
    group_ms: int | None
    audio_delivery: int | str | None
    duration_s: int
    video_tracks: str | None
    clip: str | None = None  # the clip id when content is clip
    mux_kbps: int | None = None
    ts_packets: int | None = None

    @property
    def has_catalog(self) -> bool:
        return self.catalog != "none"

    @property
    def has_video(self) -> bool:
        return self.video_codec != "none"

    @property
    def has_audio(self) -> bool:
        return self.audio_codec != "none"

    @property
    def is_ts(self) -> bool:
        return self.packaging == TS

    @property
    def segment_bytes(self) -> int | None:
        """Object size of an msfts track: whole TS packets."""
        return self.ts_packets * TS_PACKET_BYTES if self.ts_packets else None

    @property
    def video_track_names(self) -> tuple[str, ...]:
        if not self.has_video:
            return ()
        n = (
            int(self.video_tracks[4 if self.is_views else 3 :])
            if self.video_tracks not in (None, ONE_TRACK)
            else 1
        )
        return ("video", *(f"video_{i}" for i in range(1, n)))

    @property
    def is_ladder(self) -> bool:
        return bool(self.video_tracks and self.video_tracks.startswith("alt"))

    @property
    def is_views(self) -> bool:
        return bool(self.video_tracks and self.video_tracks.startswith("view"))

    @property
    def tracks(self) -> tuple[str, ...]:
        if self.is_ts:
            return ("catalog", TS_TRACK, TS_TIMELINE_TRACK)
        names = ["catalog"] if self.has_catalog else []
        names += self.video_track_names
        if self.has_audio:
            names.append("audio")
        return tuple(names)

    def ladder(self) -> tuple[int, ...] | None:
        """Rendition heights of an altN ladder, or None when it does not fit (draft §Field 13)."""
        if not self.is_ladder or self.video_height is None:
            return None
        heights = [self.video_height]
        while len(heights) < len(self.video_track_names):
            lower = [h for h in HEIGHTS if 4 * h <= 3 * heights[-1]]
            if not lower:
                return None
            heights.append(max(lower))
        return tuple(heights)

    def video_set(self) -> tuple[VideoTrack, ...]:
        """The video tracks in Track Name order; empty without video or when a ladder does not
        fit."""
        names = self.video_track_names
        heights = self.ladder() if self.is_ladder else (self.video_height,) * len(names)
        if not names or heights is None or self.video_height is None:
            return ()
        if len(names) == 1:
            labels = [None]
        elif self.is_ladder:
            labels = [f"{h}p" for h in heights]
        else:
            labels = [f"view {i}" for i in range(1, len(names) + 1)]
        return tuple(
            VideoTrack(n, width_of(h), h, self.kbps_at(h), label)
            for n, h, label in zip(names, heights, labels, strict=True)
        )

    @property
    def total_video_kbps(self) -> int | None:
        """Target bitrate of all video tracks together."""
        if self.video_height is None:
            return None
        return sum(t.kbps for t in self.video_set()) or self.video_kbps

    @property
    def video_width(self) -> int | None:
        if self.video_height is None:
            return None
        return width_of(self.video_height)

    @property
    def fps(self) -> Fraction | None:
        if self.frame_rate is None:
            return None
        return FRACTIONAL_RATES.get(self.frame_rate, Fraction(self.frame_rate))

    def kbps_at(self, height: int) -> int:
        if self.mux_kbps is not None:
            return TS_VIDEO_KBPS[self.video_codec][self.mux_kbps]
        kbps = VIDEO_KBPS[height]
        return kbps * 3 // 2 if self.fps is not None and self.fps > 30 else kbps

    @property
    def video_kbps(self) -> int | None:
        """Target bitrate of the "video" track (draft §Bitrates)."""
        if self.video_height is None:
            return None
        return self.kbps_at(self.video_height)

    @property
    def audio_kbps(self) -> int | None:
        """All audio tracks together."""
        if self.audio_codec == AUDIO_SET:
            return sum(kbps for _, _, kbps in AUDIO_SET_TRACKS)
        if not self.has_audio or self.audio_channels is None:
            return None
        return AUDIO_KBPS[self.audio_codec][self.audio_channels - 1]

    @property
    def audio_datagram(self) -> bool:
        return self.audio_delivery == DGRAM

    @property
    def audio_delivery_mode(self) -> str | None:
        """stream or dgram, for tool capabilities."""
        if self.audio_delivery is None:
            return None
        return DGRAM if self.audio_datagram else "stream"

    @property
    def audio_frames_per_group(self) -> int | None:
        if self.audio_delivery is None:
            return None
        return 1 if self.audio_datagram else int(self.audio_delivery)

    @property
    def frames_per_group(self) -> Fraction | None:
        if self.group_ms is None or self.fps is None:
            return None
        return Fraction(self.group_ms, 1000) * self.fps

    def video_timing(self) -> Timing | None:
        if self.fps is None or self.frames_per_group is None:
            return None
        fractional = self.frame_rate in FRACTIONAL_RATES
        timescale = round(self.fps) * 1000
        object_multiplier = 1001 if fractional else 1000
        return Timing(timescale, object_multiplier, int(self.frames_per_group) * object_multiplier)

    def audio_timing(self) -> Timing | None:
        if not self.has_audio or self.audio_frames_per_group is None:
            return None
        samples = AUDIO_FRAME_SAMPLES[self.audio_codec]
        return Timing(AUDIO_SAMPLE_RATE, samples, self.audio_frames_per_group * samples)


def field_values(spec: TrackSpec) -> dict[str, object]:
    """Tuple field values by name; field 1 carries the clip id for clip content."""
    out = {f.name: getattr(spec, f.name) for f in FIELDS}
    if spec.content == "clip":
        out["content"] = spec.clip
    return out


def check(spec: TrackSpec) -> list[FieldError]:
    """All rule violations in a spec built in code, empty when valid."""
    errors: list[FieldError] = []
    tokens = field_values(spec)
    for field in FIELDS:
        value = tokens[field.name]
        has = dict(catalog=spec.has_catalog, video=spec.has_video, audio=spec.has_audio)
        if not applies(field, **has, ts=spec.is_ts, channels=has_channels(spec.audio_codec)):
            if value is not None:
                errors.append(FieldError(field.index, field.name, "must be _ (does not apply)"))
            continue
        if value is None:
            errors.append(FieldError(field.index, field.name, "must not be _ (applies)"))
            continue
        try:
            field.parse(str(value))
        except ValueError as e:
            errors.append(FieldError(field.index, field.name, str(e)))
    if errors:
        return errors
    if split_content(str(tokens["content"])) != (spec.content, spec.clip):
        if spec.content not in CONTENTS:
            problem = f"{spec.content!r} is not one of {', '.join(CONTENTS)}"
        elif spec.content == "clip":
            problem = f"clip id {spec.clip} is reserved for generated content"
        else:
            problem = f"{spec.content} content takes no clip id"
        return [FieldError(1, "content", problem)]
    return _cross_checks(spec)


def _cross_checks(spec: TrackSpec) -> list[FieldError]:
    errors = _ts_checks(spec)
    if spec.packaging not in CATALOG_PACKAGING[spec.catalog]:
        allowed = " or ".join(CATALOG_PACKAGING[spec.catalog])
        errors.append(
            FieldError(2, "packaging", f"catalog {spec.catalog} requires packaging {allowed}")
        )
    if spec.packaging in CATALOG_INIT_PACKAGING and spec.init_data not in (None, "catalog"):
        errors.append(
            FieldError(9, "init_data", f"{spec.packaging} carries init data in the catalog")
        )
    if spec.frames_per_group is not None and spec.frames_per_group.denominator != 1:
        errors.append(
            FieldError(
                10,
                "group_ms",
                f"{spec.group_ms} ms is not a whole number of frames at {spec.frame_rate}",
            )
        )
    if spec.audio_datagram and spec.packaging not in DATAGRAM_PACKAGING:
        errors.append(
            FieldError(11, "audio_delivery", f"dgram requires loc or cmaf, not {spec.packaging}")
        )
    if spec.video_tracks not in (None, ONE_TRACK):
        if not spec.has_catalog:
            errors.append(
                FieldError(13, "video_tracks", f"{spec.video_tracks} requires catalog msf")
            )
        elif spec.is_ladder and spec.ladder() is None:
            errors.append(
                FieldError(
                    13,
                    "video_tracks",
                    f"{spec.video_tracks} needs more renditions than height {spec.video_height}"
                    " yields",
                )
            )
    return errors


def _ts_checks(spec: TrackSpec) -> list[FieldError]:
    """msfts carries MPEG-2 or H.265 video and MP2 or AUDIO_SET audio; only msfts carries
    MPEG-2, MP2 and AUDIO_SET."""
    errors: list[FieldError] = []
    if not spec.is_ts:
        if spec.video_codec in TS_ONLY_CODECS:
            errors.append(FieldError(3, "video_codec", f"{spec.video_codec} requires msfts"))
        if spec.audio_codec in TS_ONLY_CODECS:
            errors.append(FieldError(6, "audio_codec", f"{spec.audio_codec} requires msfts"))
        return errors
    if spec.video_codec not in TS_VIDEO_CODECS:
        errors.append(
            FieldError(3, "video_codec", f"msfts requires {' or '.join(TS_VIDEO_CODECS)}")
        )
        return errors
    if spec.audio_codec not in TS_AUDIO_CODECS:
        errors.append(
            FieldError(6, "audio_codec", f"msfts requires {' or '.join(TS_AUDIO_CODECS)}")
        )
    elif spec.audio_codec == AUDIO_SET and spec.video_codec != "h265":
        # With MPEG-2 video the set does not fit the TS rate.
        errors.append(FieldError(6, "audio_codec", f"{AUDIO_SET} requires h265"))
    if spec.video_height not in TS_DEFAULT_MUX_KBPS:
        heights = " or ".join(map(str, TS_DEFAULT_MUX_KBPS))
        errors.append(FieldError(4, "video_height", f"msfts requires {heights}"))
    if spec.video_codec == "mpeg2" and spec.frame_rate not in MPEG2_RATES:
        errors.append(
            FieldError(
                5, "frame_rate", f"MPEG-2 takes {', '.join(MPEG2_RATES)}, not {spec.frame_rate}"
            )
        )
    return errors


def group_ms_step(frame_rate: str) -> int:
    """Smallest whole-ms group duration that is a whole number of frames."""
    fps = FRACTIONAL_RATES.get(frame_rate, Fraction(frame_rate))
    return (Fraction(1000) / fps).numerator
