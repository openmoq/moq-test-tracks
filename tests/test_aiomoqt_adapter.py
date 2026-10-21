# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# aiomoqt adapter: the accept/refuse table, pub_media argv, and output translation.

import dataclasses
import importlib.util
import json
import os
import subprocess
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

from moq_test_tracks.config import Relay
from moq_test_tracks.runner.request import build
from moq_test_tracks.spec import decode, from_slash

ADAPTER = Path(__file__).resolve().parents[1] / "adapters" / "aiomoqt" / "run"
NS = "openmoq/mtt/aiomoqt/moq_test_tracks_00/msf/loc/h264/360/30/600/2000/0/aac/2/128/1/0/ts/0"
RELAY = Relay(
    "r", "r", {"moqt": "moqt://relay:4433", "webtransport": "https://relay:4433/moq"}, (18,), 18
)

_loader = SourceFileLoader("aiomoqt_adapter", str(ADAPTER))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
adapter = importlib.util.module_from_spec(_spec)
_loader.exec_module(adapter)

NO_AUDIO = dict(
    audio_codec="none",
    audio_channels=None,
    audio_kbps=None,
    audio_frames_per_group=None,
    audio_forwarding=None,
)


def _request(mode="publish", transport="moqt", ttl=30, **changes):
    namespace = from_slash(NS)
    spec = dataclasses.replace(decode(namespace).spec, **changes)
    return build("j", namespace, spec, RELAY, transport=transport, draft=18, mode=mode, ttl_s=ttl)


def test_loc_h264_is_live_ts():
    p = adapter.plan(_request(), python="py")
    assert p["accept"] and p["source"] == "mpegts"
    argv = p["argv"]
    assert argv[:4] == ["py", "-u", "-m", "aiomoqt.tools.pub_media"]
    assert argv[4] == "moqt://relay:4433"
    assert argv[argv.index("-N") + 1] == NS
    assert argv[argv.index("--draft") + 1] == "18"
    assert argv[argv.index("-t") + 1] == "30"
    assert argv[argv.index("--forward") + 1] == "1"
    assert "--pub-both" in argv and argv[argv.index("--ts") + 1] == "<source>"
    assert "-D" not in argv and "--no-audio" not in argv


@pytest.mark.parametrize(
    "changes, source, flags",
    [
        ({"catalog": "cmsf", "packaging": "cmaf"}, "mp4", ["--packaging", "cmaf"]),
        ({"video_codec": "av1"}, "mp4", ["--packaging", "loc"]),
        (NO_AUDIO, "mpegts", ["--no-audio"]),
        ({"audio_forwarding": 3}, "mpegts", ["-D"]),
        ({"duration_s": 600}, "mpegts", ["-t", "600"]),
    ],
)
def test_accepts(changes, source, flags):
    p = adapter.plan(_request(**changes))
    assert p["accept"] and p["source"] == source
    i = p["argv"].index(flags[0])
    assert p["argv"][i : i + len(flags)] == flags


def test_namespace_mode():
    assert "--pub-ns" in adapter.plan(_request(mode="namespace"))["argv"]


@pytest.mark.parametrize(
    "kwargs, field",
    [
        ({"catalog": "none"}, "catalog"),
        ({"video_codec": "h265"}, "video_codec"),
        ({"video_forwarding": 1}, "video_forwarding"),
        ({"content": "clip"}, "content"),
        ({"audio_codec": "opus", "audio_kbps": 64}, "audio_codec"),
        ({"audio_frames_per_group": 10}, "audio_frames_per_group"),
        ({"catalog": "cmsf", "packaging": "locmaf"}, "audio_frames_per_group"),
        ({"audio_forwarding": 1}, "audio_forwarding"),
        ({"audio_forwarding": 3, "transport": "webtransport"}, "transport"),
    ],
)
def test_refuses(kwargs, field):
    transport = kwargs.pop("transport", "moqt")
    p = adapter.plan(_request(transport=transport, **kwargs))
    assert not p["accept"]
    assert field in [r["field"] for r in p["reasons"]]


def test_refuses_ca_file():
    req = _request()
    req["tls"]["ca_file"] = "/ca.pem"
    assert not adapter.plan(req)["accept"]


def test_translate_stats():
    line = (
        "  [pub 1:02.500] catalog: 3 obj · 0 obj/s · 0 kbps · video: 1875 obj · 30 obj/s"
        " · 598 kbps · lag 12/40 ms · dropped 7 · audio: 2930 obj · 47 obj/s · 131 kbps\n"
    )
    event = adapter.translate(line, [])
    assert event["event"] == "stats" and event["t"] == 62.5
    assert event["tracks"]["video"] == {
        "objects": 1875,
        "objects_per_s": 30.0,
        "kbps": 598.0,
        "dropped": 7,
    }
    assert event["tracks"]["audio"]["kbps"] == 131.0


def test_translate_lifecycle():
    endpoints = [{"url": "moqt://relay:4433", "transport": "moqt", "draft": 18}]
    assert adapter.translate("  publishing...\n", endpoints)["event"] == "ready"
    assert adapter.translate("  done\n", endpoints) == {"event": "done", "reason": "ended"}
    lost = adapter.translate("  error: moqt://relay:4433 closed: code=0 reason=bye\n", endpoints)
    assert lost == {
        "event": "endpoint_lost",
        "url": "moqt://relay:4433",
        "reason": "code=0 reason=bye",
    }
    assert adapter.translate("  tracks: catalog, video, audio\n", endpoints) is None


def test_check_mode_process(tmp_path):
    for changes, code in (({}, 0), ({"catalog": "none"}, 64)):
        path = tmp_path / "request.json"
        path.write_text(json.dumps(_request(**changes)))
        env = {"MTT_MODE": "check", "MTT_REQUEST_FILE": str(path), "PATH": os.environ["PATH"]}
        r = subprocess.run([str(ADAPTER)], env=env, capture_output=True, text=True)
        assert r.returncode == code
        assert json.loads(r.stdout)["accept"] is (code == 0)
