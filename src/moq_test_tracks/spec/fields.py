# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# Characteristic fields of moq_test_tracks_00 (draft-marzot-moq-test-tracks).

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction

VERSION = "moq_test_tracks_00"
NA = "_"

CATALOGS = ("msf", "none")
PACKAGINGS = ("loc", "cmaf", "locmaf")
INIT_DATA = ("catalog", "track", "object")
VIDEO_CODECS = ("h264", "h264cb", "h265", "av1", "none")
HEIGHTS = (180, 360, 480, 540, 720, 1080, 1440, 2160)
FRACTIONAL_RATES = {
    "23_976": Fraction(24000, 1001),
    "29_97": Fraction(30000, 1001),
    "59_94": Fraction(60000, 1001),
}
AUDIO_CODECS = ("aac", "opus", "none")
CONTENTS = ("clock", "bars", "noise", "clip")

CATALOG_PACKAGING = {"msf": ("loc", "cmaf", "locmaf"), "none": ("loc",)}
CATALOG_INIT_PACKAGING = ("cmaf", "locmaf")  # CMSF: CMAF headers go inline in the catalog
DATAGRAM_PACKAGING = ("loc", "cmaf")
DATAGRAM = 3

AUDIO_SAMPLE_RATE = 48000
AUDIO_FRAME_SAMPLES = {"aac": 1024, "opus": 960}

_DECIMAL = re.compile(r"0|[1-9][0-9]*")


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


def _frame_rate(token: str) -> str:
    if token in FRACTIONAL_RATES:
        return token
    if not 1 <= _decimal(token) <= 120:
        raise ValueError(f"{token} is outside 1-120 and not 23_976, 29_97 or 59_94")
    return token


@dataclass(frozen=True)
class Field:
    index: int
    name: str
    needs: str | None  # "catalog", "video" or "audio": applies only when that track exists
    parse: Callable[[str], object]


def applies(field: Field, *, catalog: bool, video: bool, audio: bool) -> bool:
    return field.needs is None or {"catalog": catalog, "video": video, "audio": audio}[field.needs]


FIELDS = (
    Field(1, "catalog", None, _tokens(CATALOGS)),
    Field(2, "packaging", None, _tokens(PACKAGINGS)),
    Field(3, "init_data", "catalog", _tokens(INIT_DATA)),
    Field(4, "video_codec", None, _tokens(VIDEO_CODECS)),
    Field(5, "video_height", "video", _int_choices(HEIGHTS)),
    Field(6, "frame_rate", "video", _frame_rate),
    Field(7, "video_kbps", "video", _int_range(50, 50000)),
    Field(8, "group_ms", "video", _int_range(100, 10000)),
    Field(9, "video_forwarding", "video", _int_choices((0, 1))),
    Field(10, "audio_codec", None, _tokens(AUDIO_CODECS)),
    Field(11, "audio_channels", "audio", _int_choices((1, 2))),
    Field(12, "audio_kbps", "audio", _int_range(8, 512)),
    Field(13, "audio_frames_per_group", "audio", _int_range(1, 100)),
    Field(14, "audio_forwarding", "audio", _int_choices((0, 1, DATAGRAM))),
    Field(15, "content", None, _tokens(CONTENTS)),
    Field(16, "duration_s", None, _int_range(0, 86400)),
)


@dataclass(frozen=True)
class Timing:
    """TIMESCALE and TIMESTAMP_MAPPING multipliers for one track (draft §Timing)."""

    timescale: int
    object_multiplier: int
    group_multiplier: int


@dataclass(frozen=True)
class TrackSpec:
    catalog: str
    packaging: str
    init_data: str | None
    video_codec: str
    video_height: int | None
    frame_rate: str | None
    video_kbps: int | None
    group_ms: int | None
    video_forwarding: int | None
    audio_codec: str
    audio_channels: int | None
    audio_kbps: int | None
    audio_frames_per_group: int | None
    audio_forwarding: int | None
    content: str
    duration_s: int

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
    def tracks(self) -> tuple[str, ...]:
        names = ["catalog"] if self.has_catalog else []
        if self.has_video:
            names.append("video")
        if self.has_audio:
            names.append("audio")
        return tuple(names)

    @property
    def video_width(self) -> int | None:
        if self.video_height is None:
            return None
        return 2 * round(self.video_height * 16 / 9 / 2)

    @property
    def fps(self) -> Fraction | None:
        if self.frame_rate is None:
            return None
        return FRACTIONAL_RATES.get(self.frame_rate, Fraction(self.frame_rate))

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


def check(spec: TrackSpec) -> list[FieldError]:
    """All rule violations in a spec built in code, empty when valid."""
    errors: list[FieldError] = []
    for field in FIELDS:
        value = getattr(spec, field.name)
        if not applies(field, catalog=spec.has_catalog, video=spec.has_video, audio=spec.has_audio):
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
    return _cross_checks(spec)


def _cross_checks(spec: TrackSpec) -> list[FieldError]:
    errors: list[FieldError] = []
    if spec.packaging not in CATALOG_PACKAGING[spec.catalog]:
        allowed = " or ".join(CATALOG_PACKAGING[spec.catalog])
        errors.append(
            FieldError(2, "packaging", f"catalog {spec.catalog} requires packaging {allowed}")
        )
    if spec.packaging in CATALOG_INIT_PACKAGING and spec.init_data not in (None, "catalog"):
        errors.append(
            FieldError(3, "init_data", f"{spec.packaging} carries init data in the catalog")
        )
    if spec.frames_per_group is not None and spec.frames_per_group.denominator != 1:
        errors.append(
            FieldError(
                8,
                "group_ms",
                f"{spec.group_ms} ms is not a whole number of frames at {spec.frame_rate}",
            )
        )
    if spec.audio_forwarding == DATAGRAM and spec.packaging not in DATAGRAM_PACKAGING:
        errors.append(
            FieldError(
                14, "audio_forwarding", f"datagram requires loc or cmaf, not {spec.packaging}"
            )
        )
    return errors


def group_ms_step(frame_rate: str) -> int:
    """Smallest whole-ms group duration that is a whole number of frames."""
    fps = FRACTIONAL_RATES.get(frame_rate, Fraction(frame_rate))
    return (Fraction(1000) / fps).numerator
