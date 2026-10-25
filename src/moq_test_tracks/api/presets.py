# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# API convenience: presets and per-parameter fallbacks that fill in whatever a
# caller leaves out. The namespace itself stays fully explicit.

from __future__ import annotations

import math
from fractions import Fraction

from ..spec.fields import FIELDS, NA, VERSION, TrackSpec, applies, group_ms_step
from ..spec.namespace import decode

# Query parameter -> field name (docs/design.md).
PARAMS = {
    "cat": "catalog",
    "pkg": "packaging",
    "init": "init_data",
    "vc": "video_codec",
    "h": "video_height",
    "fps": "frame_rate",
    "vbr": "video_kbps",
    "gop": "group_ms",
    "vfwd": "video_forwarding",
    "ac": "audio_codec",
    "ch": "audio_channels",
    "abr": "audio_kbps",
    "afpg": "audio_frames_per_group",
    "afwd": "audio_forwarding",
    "content": "content",
    "dur": "duration_s",
}

PRESETS = {
    "180p": {"h": "180", "fps": "15"},
    "360p": {"h": "360", "fps": "30"},
    "720p30": {"h": "720", "fps": "30"},
    "720p60": {"h": "720", "fps": "60"},
    "1080p30": {"h": "1080", "fps": "30"},
    "1080p60": {"h": "1080", "fps": "60"},
    "audio": {"vc": "none"},
    "cmaf": {"pkg": "cmaf"},
}

DEFAULTS = {
    "cat": "msf",
    "pkg": "loc",
    "init": "catalog",
    "vc": "h264",
    "h": "720",
    "fps": "30",
    "gop": "2000",
    "vfwd": "0",
    "ac": "aac",
    "ch": "2",
    "afpg": "1",
    "afwd": "0",
    "content": "clock",
    "dur": "0",
}
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
AUDIO_KBPS = {"aac": 128, "opus": 64}


class PresetError(ValueError):
    pass


def resolve(params: dict[str, str], preset: str | None = None) -> TrackSpec:
    """Preset, then explicit params, then fallbacks, to a validated TrackSpec."""
    unknown = sorted(set(params) - set(PARAMS))
    if unknown:
        raise PresetError(f"unknown parameters: {', '.join(unknown)}")
    if preset is not None and preset not in PRESETS:
        raise PresetError(f"unknown preset {preset}; one of {', '.join(PRESETS)}")
    p = {**DEFAULTS, **(PRESETS.get(preset, {}) if preset else {}), **params}
    has = {"catalog": p["cat"] != "none", "video": p["vc"] != "none", "audio": p["ac"] != "none"}
    has_video, has_audio = has["video"], has["audio"]
    if has_video:
        if "gop" not in params:
            step = group_ms_step(p["fps"]) if p["fps"].replace("_", "").isdigit() else 1
            p["gop"] = str(step * math.ceil(int(DEFAULTS["gop"]) / step))
        if "vbr" not in params and p["h"].isdigit() and int(p["h"]) in VIDEO_KBPS:
            kbps = VIDEO_KBPS[int(p["h"])]
            fps = Fraction(p["fps"].replace("_", ".")) if p["fps"] else 0
            p["vbr"] = str(round(kbps * 1.5) if fps > 30 else kbps)
    if has_audio and "abr" not in params:
        p["abr"] = str(AUDIO_KBPS.get(p["ac"], 128))
    tokens = []
    for opt, name in PARAMS.items():
        field = next(f for f in FIELDS if f.name == name)
        tokens.append(p.get(opt, NA) if applies(field, **has) else NA)
    return decode((b"p", VERSION.encode(), *(t.encode() for t in tokens))).spec
