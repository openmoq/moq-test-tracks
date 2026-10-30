# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# request.json for a job (docs/tool-contract.md#requestjson).

from __future__ import annotations

from ..config import CONTRACT_VERSION, Relay
from ..spec.fields import VERSION, TrackSpec
from ..spec.namespace import Namespace, render


def fields_dict(spec: TrackSpec) -> dict:
    """Tuple fields, with omitted ones filled in, plus the values the draft derives."""
    return {
        "content": spec.content,
        "catalog": spec.catalog,
        "packaging": spec.packaging,
        "video_codec": spec.video_codec,
        "video_height": spec.video_height,
        "video_width": spec.video_width,
        "frame_rate": spec.frame_rate,
        "video_kbps": spec.video_kbps,
        "audio_codec": spec.audio_codec,
        "audio_channels": spec.audio_channels,
        "audio_kbps": spec.audio_kbps,
        "init_data": spec.init_data,
        "group_ms": spec.group_ms,
        "audio_delivery": spec.audio_delivery,
        "audio_frames_per_group": spec.audio_frames_per_group,
        "duration_s": spec.duration_s,
        "video_tracks": spec.video_tracks,
    }


def build(
    job_id: str,
    namespace: Namespace,
    spec: TrackSpec,
    relay: Relay,
    *,
    transport: str,
    draft: int,
    mode: str,
    ttl_s: int,
    tls_verify: bool = True,
    clip: dict | None = None,
) -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "job_id": job_id,
        "protocol": VERSION,
        "namespace": [f.decode() for f in namespace],
        "namespace_safe": render(namespace),
        "tracks": list(spec.tracks),
        "fields": fields_dict(spec),
        "endpoints": [{"url": relay.endpoints[transport], "transport": transport, "draft": draft}],
        "mode": mode,
        "ttl_s": ttl_s,
        "source": None,
        "clip": clip,
        "tls": {"verify": tls_verify, "ca_file": None},
    }
