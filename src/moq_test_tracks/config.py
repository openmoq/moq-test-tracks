# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# Loads config/*.yaml and adapters/<id>/manifest.yaml, and checks a job
# against the relay allowlist, tool capabilities and policy caps.

from __future__ import annotations

import os
import signal
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import yaml

from .spec.fields import DATAGRAM, TrackSpec

CONTRACT_VERSION = 1
MODES = ("namespace", "publish")


@dataclass(frozen=True)
class Relay:
    id: str
    name: str
    endpoints: dict[str, str]  # transport -> URL
    drafts: tuple[int, ...]
    default_draft: int


@dataclass(frozen=True)
class Tool:
    id: str
    name: str
    version: str
    protocols: tuple[str, ...]
    sources: tuple[str, ...]
    stop_signal: signal.Signals
    grace_s: float
    capabilities: dict
    adapter: Path
    max_jobs: int


@dataclass(frozen=True)
class Policy:
    namespace_prefix: tuple[str, ...]
    default_ttl_s: int
    max_ttl_s: int
    max_height: int
    max_fps: int
    max_video_kbps: int
    max_jobs: int = 6
    max_live_encodes: int = 2
    global_max_ttl_s: int = 86400


@dataclass(frozen=True)
class Config:
    root: Path
    relays: dict[str, Relay]
    default_relay: str
    tools: dict[str, Tool]
    policy: Policy


def find_root(start: Path | None = None) -> Path:
    """MTT_ROOT, else the nearest directory upward holding config/tools.yaml."""
    if env := os.environ.get("MTT_ROOT"):
        return Path(env)
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        if (d / "config" / "tools.yaml").is_file():
            return d
    raise FileNotFoundError("no config/tools.yaml here or above; set MTT_ROOT")


def _yaml(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f) or {}


def load(root: Path | None = None) -> Config:
    root = root or find_root()
    relays_doc = _yaml(root / "config" / "relays.yaml")
    relays = {
        rid: Relay(
            id=rid,
            name=r.get("name", rid),
            endpoints=dict(r["endpoints"]),
            drafts=tuple(r["drafts"]),
            default_draft=r.get("default_draft", max(r["drafts"])),
        )
        for rid, r in relays_doc["relays"].items()
    }
    tools = {}
    for tid, t in _yaml(root / "config" / "tools.yaml")["tools"].items():
        adapter_dir = root / "adapters" / tid
        m = _yaml(adapter_dir / "manifest.yaml")
        if m.get("contract_version") != CONTRACT_VERSION:
            continue
        tools[tid] = Tool(
            id=tid,
            name=m.get("name", tid),
            version=str(m.get("version", "")),
            protocols=tuple(m.get("protocols", ())),
            sources=tuple(m.get("sources", ())),
            stop_signal=signal.Signals[m.get("stop", {}).get("signal", "SIGTERM")],
            grace_s=float(m.get("stop", {}).get("grace_s", 3)),
            capabilities=dict(m.get("capabilities", {})),
            adapter=adapter_dir / "run",
            max_jobs=int(t.get("max_jobs", 1)),
        )
    p = _yaml(root / "config" / "policy.yaml")
    per_key = p.get("per_key_defaults", {})
    caps = p.get("global", {})
    policy = Policy(
        namespace_prefix=tuple(p.get("namespace_prefix", ("openmoq", "mtt"))),
        default_ttl_s=int(per_key.get("default_ttl_s", 1800)),
        max_ttl_s=int(per_key.get("max_ttl_s", 3600)),
        max_height=int(per_key.get("max_height", 1080)),
        max_fps=int(per_key.get("max_fps", 60)),
        max_video_kbps=int(per_key.get("max_video_kbps", 8000)),
        max_jobs=int(caps.get("max_jobs", 6)),
        max_live_encodes=int(caps.get("max_live_encodes", 2)),
        global_max_ttl_s=int(caps.get("max_ttl_s", 86400)),
    )
    return Config(root, relays, relays_doc.get("default", next(iter(relays))), tools, policy)


def job_errors(
    tool: Tool,
    relay: Relay,
    spec: TrackSpec,
    *,
    mode: str,
    transport: str,
    draft: int,
    ttl_s: int,
    policy: Policy,
    admin: bool = False,
) -> list[str]:
    """Reasons a job is out of bounds before the adapter is asked. Admin jobs
    may exceed the per-key limits, never the global caps."""
    errors = []
    if mode not in MODES:
        errors.append(f"mode {mode} is not one of {', '.join(MODES)}")
    if transport not in relay.endpoints:
        errors.append(f"relay {relay.id} has no {transport} endpoint")
    if draft not in relay.drafts:
        errors.append(f"relay {relay.id} does not speak draft {draft}")
    caps = tool.capabilities
    for key, allowed in caps.items():
        if not isinstance(allowed, list) or not hasattr(spec, key):
            continue
        value = getattr(spec, key)
        if value is not None and value not in allowed:
            errors.append(f"{tool.id} does not support {key} {value}")
    for key, value in (("mode", mode), ("transport", transport), ("draft", draft)):
        if key in caps and value not in caps[key]:
            errors.append(f"{tool.id} does not support {key} {value}")
    if spec.audio_forwarding == DATAGRAM and transport not in caps.get(
        "datagram_transport", caps.get("transport", [transport])
    ):
        errors.append(f"{tool.id} does not send datagrams over {transport}")
    if not admin:
        if spec.video_height is not None and spec.video_height > policy.max_height:
            errors.append(f"height {spec.video_height} exceeds the cap {policy.max_height}")
        if spec.fps is not None and spec.fps > Fraction(policy.max_fps):
            errors.append(f"frame rate {spec.frame_rate} exceeds the cap {policy.max_fps}")
        if spec.video_kbps is not None and spec.video_kbps > policy.max_video_kbps:
            errors.append(
                f"video bitrate {spec.video_kbps} exceeds the cap {policy.max_video_kbps}"
            )
    max_ttl = policy.global_max_ttl_s if admin else policy.max_ttl_s
    if not 1 <= ttl_s <= max_ttl:
        errors.append(f"ttl {ttl_s} s is outside 1-{max_ttl}")
    return errors
