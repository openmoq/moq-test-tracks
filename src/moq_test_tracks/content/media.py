# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# Clip mezzanines: `mtt media fetch` records a clip from its allowlisted URL,
# and `mtt media import` takes its local files, into the runner's cache. Jobs
# encode from the newest one.

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from ..config import Clip

CLIP_DIR = "clips"
MAX_BYTES = 2 * 1024**3
# No file or plain-http input: a hostile playlist could otherwise point ffmpeg
# at local files or internal addresses.
PROTOCOLS = "https,tls,tcp,crypto"


class MediaError(RuntimeError):
    pass


@dataclass(frozen=True)
class Mezzanine:
    clip: str
    path: Path
    sha256: str
    duration_s: float
    captured_at: str
    video: dict
    audio: dict

    def summary(self) -> dict:
        return {
            "sha256": self.sha256,
            "duration_s": self.duration_s,
            "captured_at": self.captured_at,
            "video": self.video,
            "audio": self.audio,
        }


def _dir(cache: Path) -> Path:
    return cache / CLIP_DIR


def mezzanine(cache: Path, clip_id: str) -> Mezzanine | None:
    """The clip's current recording, or None if it has not been fetched."""
    try:
        meta = json.loads((_dir(cache) / f"{clip_id}.json").read_text())
    except (OSError, ValueError):
        return None
    path = _dir(cache) / meta.get("file", "")
    if meta.get("clip") != clip_id or not path.is_file():
        return None
    return Mezzanine(
        clip_id, path, meta["sha256"], meta["duration_s"], meta["captured_at"],
        meta["video"], meta["audio"],
    )  # fmt: skip


def capture_command(clip: Clip, out: Path) -> list[str]:
    """ffmpeg argv recording capture_s seconds of the clip's best rendition, as is."""
    return [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "warning", "-y",
        "-protocol_whitelist", PROTOCOLS, "-rw_timeout", "15000000",
        *(["-ss", str(clip.start_s)] if clip.start_s else []),
        "-i", clip.url, "-t", str(clip.capture_s), "-sn", "-dn", "-c", "copy",
        "-fs", str(MAX_BYTES), "-f", "matroska", str(out),
    ]  # fmt: skip


def _probe(path: Path) -> dict:
    r = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=60,
    )
    if r.returncode != 0:
        raise MediaError(f"ffprobe failed: {r.stderr.strip()[-300:]}")
    return json.loads(r.stdout)


def _streams(info: dict) -> tuple[dict, dict]:
    def first(kind: str) -> dict:
        return next((s for s in info.get("streams", []) if s.get("codec_type") == kind), {})

    v, a = first("video"), first("audio")
    video = {
        "codec": v.get("codec_name"),
        "profile": v.get("profile"),
        "width": v.get("width"),
        "height": v.get("height"),
        "fps": str(Fraction(v["avg_frame_rate"]))
        if v.get("avg_frame_rate", "0/0") != "0/0"
        else None,
    }
    audio = {
        "codec": a.get("codec_name"),
        "profile": a.get("profile"),
        "sample_rate": int(a["sample_rate"]) if a.get("sample_rate") else None,
        "channels": a.get("channels"),
    }
    return (video if v else {}), (audio if a else {})


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def store(clip: Clip, cache: Path, recording: Path) -> Mezzanine:
    """Check a recording, then make it the clip's mezzanine and drop older ones."""
    info = _probe(recording)
    video, audio = _streams(info)
    if not video or not audio:
        raise MediaError(f"clip {clip.id} needs a video and an audio stream")
    duration = float(info.get("format", {}).get("duration") or 0)
    if duration < 0.9 * clip.capture_s:
        raise MediaError(f"clip {clip.id}: recorded {duration:.1f} s of {clip.capture_s} s")
    sha = _sha256(recording)
    d = _dir(cache)
    name = f"{clip.id}-{sha[:12]}.mkv"
    os.replace(recording, d / name)
    meta = {
        "clip": clip.id,
        "file": name,
        "sha256": sha,
        "duration_s": round(duration, 3),
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "url": clip.url,
        "files": [f for f in (clip.video, clip.audio) if f],
        "video": video,
        "audio": audio,
    }
    tmp = d / f"{clip.id}.json.part"
    tmp.write_text(json.dumps(meta, indent=2))
    os.replace(tmp, d / f"{clip.id}.json")
    for old in d.glob(f"{clip.id}-*.mkv"):
        if old.name != name:
            old.unlink(missing_ok=True)
    found = mezzanine(cache, clip.id)
    if found is None:
        raise MediaError(f"clip {clip.id}: the stored mezzanine cannot be read back")
    return found


def import_command(video: Path, audio: Path | None, out: Path) -> list[str]:
    """ffmpeg argv copying the video (and a separate audio file) into one mezzanine."""
    argv = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "warning", "-y", "-i", str(video)]
    maps = ["-map", "0:v:0", "-map", "0:a:0?"]
    if audio is not None:
        argv += ["-i", str(audio)]
        maps = ["-map", "0:v:0", "-map", "1:a:0"]
    return [*argv, *maps, "-sn", "-dn", "-c", "copy", "-f", "matroska", str(out)]


def _media_file(media_dir: Path, rel: str) -> Path:
    path = (media_dir / rel).resolve()
    if not path.is_relative_to(media_dir.resolve()) or not path.is_file():
        raise MediaError(f"{rel} is not a file under {media_dir}")
    return path


def import_files(clip: Clip, cache: Path, media_dir: Path) -> Mezzanine:
    """Copy the clip's local video (and audio) file into its mezzanine."""
    if clip.video is None:
        raise MediaError(f"clip {clip.id} has a URL; use mtt media fetch")
    video = _media_file(media_dir, clip.video)
    audio = _media_file(media_dir, clip.audio) if clip.audio else None
    d = _dir(cache)
    d.mkdir(parents=True, exist_ok=True)
    part = d / f"{clip.id}.part.mkv"
    try:
        r = subprocess.run(  # noqa: S603 - argv from import_command, no shell
            import_command(video, audio, part),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if r.returncode != 0:
            raise MediaError(f"importing {clip.id} failed: {r.stderr.strip()[-400:]}")
        return store(clip, cache, part)
    finally:
        part.unlink(missing_ok=True)


def fetch(clip: Clip, cache: Path) -> Mezzanine:
    """Record the clip from its URL and store it as the clip's mezzanine."""
    if clip.url is None:
        raise MediaError(f"clip {clip.id} has local files; use mtt media import")
    d = _dir(cache)
    d.mkdir(parents=True, exist_ok=True)
    part = d / f"{clip.id}.part.mkv"
    try:
        r = subprocess.run(  # noqa: S603 - argv from capture_command, no shell
            capture_command(clip, part),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=clip.capture_s + 120,
        )
        if r.returncode != 0:
            raise MediaError(f"recording {clip.id} failed: {r.stderr.strip()[-400:]}")
        return store(clip, cache, part)
    finally:
        part.unlink(missing_ok=True)
