# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# Clip config, the capture command, and storing a recording as a mezzanine.

import dataclasses
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from moq_test_tracks import config
from moq_test_tracks.cli import main
from moq_test_tracks.config import Clip
from moq_test_tracks.content import media

ROOT = Path(__file__).resolve().parents[1]
CLIP = Clip("demo", "Demo", "https://example.com/live/master.m3u8", 12, "test only", "Demo credit")
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def test_repo_clips_load():
    clips = config.load(ROOT).clips
    assert {"akamai_test", "nasa_tv", "artemis_tv"} <= set(clips)
    assert all(c.url.startswith("https://") for c in clips.values() if c.url)
    tv = clips["artemis_tv"]
    assert tv.video == "artemis/synced/tv.mp4" and tv.audio == "artemis/audio/tv.m4a"
    assert tv.loop_s == 150 and tv.url is None


@pytest.mark.parametrize(
    "entry, problem",
    [
        ({"url": "http://x/a.m3u8"}, "url must be https"),
        ({"attribution": "Credit: me"}, "attribution allows"),
        ({"capture_s": 5}, "capture_s"),
        ({"start_s": -1}, "start_s"),
        ({"video": "a.mp4"}, "either url or video"),
        ({"url": None, "video": "../etc/x.mp4"}, "relative to"),
        ({"url": None, "video": "/abs/x.mp4"}, "relative to"),
        ({"url": None, "video": "a.mp4", "loop_s": 5}, "loop_s"),
    ],
)
def test_clip_validation(tmp_path, entry, problem):
    clip = {"url": "https://x/a.m3u8", "license": "l", "attribution": "ok", **entry}
    lines = [f"    {k}: {json.dumps(v)}" for k, v in clip.items() if v is not None]
    (tmp_path / "sources.yaml").write_text("clips:\n  c1:\n" + "\n".join(lines) + "\n")
    with pytest.raises(ValueError, match=problem):
        config._clips(tmp_path / "sources.yaml")


def test_attribution_allows_license_credits(tmp_path):
    credit = "Big Buck Bunny © 2008 Blender Foundation | www.bigbuckbunny.org"
    (tmp_path / "sources.yaml").write_text(
        f"clips:\n  c1:\n    url: https://x\n    license: l\n    attribution: {credit}\n"
    )
    assert config._clips(tmp_path / "sources.yaml")["c1"].attribution == credit


def test_bad_clip_id(tmp_path):
    (tmp_path / "sources.yaml").write_text(
        "clips:\n  Bad-Id:\n    url: https://x\n    license: l\n    attribution: ok\n"
    )
    with pytest.raises(ValueError, match=r"\[a-z0-9_\]"):
        config._clips(tmp_path / "sources.yaml")


def test_capture_command_is_https_only(tmp_path):
    argv = media.capture_command(CLIP, tmp_path / "out.mkv")
    allowed = argv[argv.index("-protocol_whitelist") + 1].split(",")
    assert "file" not in allowed and "http" not in allowed and "https" in allowed
    assert argv[argv.index("-i") + 1] == CLIP.url
    assert argv[argv.index("-t") + 1] == "12" and "-fs" in argv
    assert argv[argv.index("-c") + 1] == "copy" and "-ss" not in argv
    later = media.capture_command(dataclasses.replace(CLIP, start_s=90), tmp_path / "out.mkv")
    assert later[later.index("-ss") + 1] == "90" and later.index("-ss") < later.index("-i")


def _record(path: Path, seconds: float, audio: bool = True) -> None:
    argv = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    argv += ["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30"]
    if audio:
        argv += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
    argv += ["-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast"]
    argv += ["-c:a", "aac"] if audio else []
    argv += ["-f", "matroska", str(path)]
    subprocess.run(argv, check=True)


@needs_ffmpeg
def test_store_and_lookup(tmp_path):
    cache = tmp_path / "cache"
    (cache / media.CLIP_DIR).mkdir(parents=True)
    stale = cache / media.CLIP_DIR / "demo-000000000000.mkv"
    stale.write_bytes(b"old")
    rec = cache / media.CLIP_DIR / "demo.part.mkv"
    _record(rec, 12)
    mezz = media.store(CLIP, cache, rec)
    assert mezz.path.name == f"demo-{mezz.sha256[:12]}.mkv" and mezz.path.is_file()
    assert mezz.video["width"] == 320 and mezz.video["fps"] == "30"
    assert mezz.audio["sample_rate"] == 48000 and mezz.duration_s >= 11.9
    assert not rec.exists() and not stale.exists()
    assert media.mezzanine(cache, "demo") == mezz
    assert media.mezzanine(cache, "other") is None


@needs_ffmpeg
@pytest.mark.parametrize("seconds, audio, problem", [(5, True, "recorded"), (12, False, "audio")])
def test_store_rejects(tmp_path, seconds, audio, problem):
    cache = tmp_path / "cache"
    (cache / media.CLIP_DIR).mkdir(parents=True)
    rec = cache / media.CLIP_DIR / "demo.part.mkv"
    _record(rec, seconds, audio)
    with pytest.raises(media.MediaError, match=problem):
        media.store(CLIP, cache, rec)
    assert media.mezzanine(cache, "demo") is None


@needs_ffmpeg
def test_import_video_with_separate_audio(tmp_path):
    media_dir, cache = tmp_path / "media", tmp_path / "cache"
    (media_dir / "set").mkdir(parents=True)
    video, audio = media_dir / "set" / "view.mp4", media_dir / "set" / "audio.m4a"
    ff = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i"]
    subprocess.run(
        [*ff, "testsrc2=size=320x180:rate=30", "-t", "12", "-c:v", "libx264", "-preset",
         "ultrafast", str(video)],
        check=True,
    )  # fmt: skip
    subprocess.run(
        [*ff, "sine=frequency=440:sample_rate=48000", "-t", "12", "-c:a", "aac", str(audio)],
        check=True,
    )  # fmt: skip
    clip = dataclasses.replace(
        CLIP, url=None, video="set/view.mp4", audio="set/audio.m4a", loop_s=12
    )
    mezz = media.import_files(clip, cache, media_dir)
    assert mezz.video["width"] == 320 and mezz.audio["sample_rate"] == 48000
    assert mezz.duration_s >= 11.9 and media.mezzanine(cache, "demo") == mezz
    outside = dataclasses.replace(clip, video="../escape.mp4")
    with pytest.raises(media.MediaError, match="not a file under"):
        media.import_files(outside, cache, media_dir)
    with pytest.raises(media.MediaError, match="use mtt media import"):
        media.fetch(clip, cache)


def test_cli_media_list(tmp_path, capsys):
    assert main(["media", "--root", str(ROOT), "--var", str(tmp_path), "list"]) == 0
    out = capsys.readouterr().out
    assert "akamai_test" in out and "not fetched" in out
    assert main(["media", "--root", str(ROOT), "--var", str(tmp_path), "fetch", "nope"]) == 2
