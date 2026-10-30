# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# API convenience: presets and per-parameter fallbacks that fill in whatever a
# caller leaves out. The API returns the namespace in its shortest form.

from __future__ import annotations

from ..spec.fields import FIELDS, NA, VERSION, TrackSpec, applies, omitted_value
from ..spec.namespace import decode

# Query parameter -> field name (docs/design.md).
PARAMS = {
    "content": "content",
    "cat": "catalog",
    "pkg": "packaging",
    "vc": "video_codec",
    "h": "video_height",
    "fps": "frame_rate",
    "ac": "audio_codec",
    "ch": "audio_channels",
    "init": "init_data",
    "gop": "group_ms",
    "adel": "audio_delivery",
    "dur": "duration_s",
    "vt": "video_tracks",
}

# Parameters the draft no longer has, with what replaced them.
REMOVED = {
    "vbr": "video bitrate follows from height and frame rate",
    "abr": "audio bitrate follows from codec and channels",
    "vfwd": "video always goes one stream per group",
    "afwd": "use adel=dgram for datagram audio",
    "afpg": "use adel=N for N audio frames per group",
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
    "content": "clock",
    "cat": "msf",
    "pkg": "loc",
    "vc": "h264",
    "h": "720",
    "fps": "30",
    "ac": "aac",
    "ch": "2",
}


class PresetError(ValueError):
    pass


def resolve(params: dict[str, str], preset: str | None = None) -> TrackSpec:
    """Preset, then explicit params, then fallbacks, to a validated TrackSpec. Optional
    fields not given take the value they have when omitted."""
    removed = [f"{p}: {REMOVED[p]}" for p in sorted(set(params) & set(REMOVED))]
    if removed:
        raise PresetError(f"removed parameters ({'; '.join(removed)})")
    unknown = sorted(set(params) - set(PARAMS))
    if unknown:
        raise PresetError(f"unknown parameters: {', '.join(unknown)}")
    if preset is not None and preset not in PRESETS:
        raise PresetError(f"unknown preset {preset}; one of {', '.join(PRESETS)}")
    p = {**DEFAULTS, **(PRESETS.get(preset, {}) if preset else {}), **params}
    has = {"catalog": p["cat"] != "none", "video": p["vc"] != "none", "audio": p["ac"] != "none"}
    opt = {name: key for key, name in PARAMS.items()}
    tokens: list[str] = []
    values: dict[str, object] = {}
    for field in FIELDS:
        given = p.get(opt[field.name])
        if not applies(field, **has):
            token = NA
        elif given is not None:
            token = given
        elif field.omitted is not None:
            value = omitted_value(field, values)
            token = NA if value is None else str(value)
        else:
            token = NA
        tokens.append(token)
        values[field.name] = token if token != NA else None
    return decode((b"p", VERSION.encode(), *(t.encode() for t in tokens))).spec
