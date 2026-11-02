# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# aiomoqt adapter: the accept/refuse table, pub_media argv, and output translation.

import dataclasses
import importlib.util
import json
import os
import signal
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

from moq_test_tracks.config import Relay
from moq_test_tracks.runner.request import build
from moq_test_tracks.spec import decode, from_slash

ADAPTER = Path(__file__).resolve().parents[1] / "adapters" / "aiomoqt" / "run"
NS = "openmoq/aiomoqt/mtt_00/clock/loc/h264/360/30/aac/2"
RELAY = Relay(
    "r", "r", {"moqt": "moqt://relay:4433", "webtransport": "https://relay:4433/moq"}, (18,), 18
)

_loader = SourceFileLoader("aiomoqt_adapter", str(ADAPTER))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
adapter = importlib.util.module_from_spec(_spec)
_loader.exec_module(adapter)

NO_AUDIO = dict(audio_codec="none", audio_channels=None, audio_delivery=None)


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
    assert argv[argv.index("-t") + 1] == "0"  # until stopped: the runner holds the TTL
    assert argv[argv.index("--forward") + 1] == "1"
    assert "--pub-both" in argv and argv[argv.index("--ts") + 1] == "<source>"
    assert "-D" not in argv and "--no-audio" not in argv
    assert "--timestamp-mapping" in argv and "--stats-json" in argv and "--reconnect" in argv
    assert argv[argv.index("--ts-cadence") + 1] == "fps=30,gop=60"
    assert argv[argv.index("--catalog-bitrate") + 1] == "600000"
    assert argv[argv.index("--catalog-audio-bitrate") + 1] == "128000"
    assert "--start-at" not in argv  # check mode has no source yet
    assert argv[argv.index("--init-data") + 1] == "catalog" and "--no-catalog" not in argv
    assert argv[argv.index("--target-latency") + 1] == "250"
    assert argv[argv.index("--fetch-history") + 1] == "300000"  # 2 groups of 2 s at 600 kbps
    assert argv[argv.index("--audio-frames-per-group") + 1] == "1"
    assert "--video-subgroups" not in argv and "--audio-subgroups" not in argv
    assert p["notes"] == []


@pytest.mark.parametrize(
    "changes, size",
    [
        ({}, 300_000),
        ({"video_kbps": 6000}, 3_000_000),
        ({"video_kbps": 12000, "group_ms": 1000}, 3_000_000),
        ({"video_codec": "none"}, 65_536),
        ({"video_codec": "none", "audio_frames_per_group": 200}, 136_533),
        ({**NO_AUDIO, "video_kbps": 6000}, 3_000_000),
    ],
)
def test_fetch_history(changes, size):
    f = {**_request()["fields"], **changes}
    assert adapter._fetch_history(f) == size


def test_fractional_rate_cadence():
    argv = adapter.plan(_request(frame_rate="29_97", group_ms=2002))["argv"]
    assert argv[argv.index("--ts-cadence") + 1] == "fps=30000/1001,gop=60"


def test_live_source_start():
    req = _request()
    req["source"] = {"format": "mpegts", "path": "/w/source.ts", "fifo": True, "loop": False,
                     "start_us": 1793623206000000}  # fmt: skip
    argv = adapter.plan(req)["argv"]
    assert argv[argv.index("--ts") + 1] == "/w/source.ts"
    assert argv[argv.index("--start-at") + 1] == "1793623206000000"


@pytest.mark.parametrize(
    "changes, video, audio",
    [
        ({"packaging": "cmaf"}, "600000", "128000"),
        ({"video_codec": "none"}, None, "128000"),
        (NO_AUDIO, "600000", None),
    ],
)
def test_catalog_bitrates(changes, video, audio):
    argv = adapter.plan(_request(**changes))["argv"]

    def flag(name):
        return argv[argv.index(name) + 1] if name in argv else None

    assert flag("--catalog-bitrate") == video and flag("--catalog-audio-bitrate") == audio


def test_no_catalog():
    p = adapter.plan(_request(catalog="none", init_data=None))
    assert p["accept"] and "--no-catalog" in p["argv"] and "--init-data" not in p["argv"]
    assert "--target-latency" not in p["argv"] and "--catalog-bitrate" not in p["argv"]
    assert "parameter sets in the payload" in p["notes"][0]


def test_cmaf_leaves_init_data_to_the_catalog():
    p = adapter.plan(_request(packaging="cmaf"))
    assert p["accept"] and "--init-data" not in p["argv"] and "--ts-cadence" not in p["argv"]
    assert p["argv"][p["argv"].index("--target-latency") + 1] == "250"


def test_datagram_audio_over_webtransport():
    p = adapter.plan(_request(transport="webtransport", audio_delivery="dgram"))
    assert p["accept"] and "-D" in p["argv"] and p["argv"][4] == "https://relay:4433/moq"


@pytest.mark.parametrize(
    "changes, source, flags",
    [
        ({"catalog": "msf", "packaging": "cmaf"}, "mp4", ["--packaging", "cmaf"]),
        ({"packaging": "locmaf"}, "mp4", ["--packaging", "locmaf"]),
        ({"video_codec": "av1"}, "mp4", ["--packaging", "loc"]),
        ({"video_codec": "h265"}, "mp4", ["--packaging", "loc"]),
        ({"video_codec": "h264cb"}, "mpegts", ["--ts", "<source>"]),
        ({"audio_codec": "opus"}, "mpegts", ["--ts", "<source>"]),
        ({"audio_delivery": 10}, "mpegts", ["--audio-frames-per-group", "10"]),
        (NO_AUDIO, "mpegts", ["--no-audio"]),
        ({"audio_delivery": "dgram"}, "mpegts", ["--audio-frames-per-group", "1", "-D"]),
        ({"init_data": "track"}, "mpegts", ["--init-data", "track"]),
        ({"init_data": "object"}, "mpegts", ["--init-data", "object"]),
        ({"duration_s": 600}, "mpegts", ["-t", "600"]),
    ],
)
def test_accepts(changes, source, flags):
    p = adapter.plan(_request(**changes))
    assert p["accept"] and p["source"] == source
    i = p["argv"].index(flags[0])
    assert p["argv"][i : i + len(flags)] == flags


def test_clip_uses_the_mp4_loop():
    req = _request(content="clip")
    req["clip"] = {"id": "demo", "path": "/cache/clips/demo.mkv", "attribution": "Demo"}
    p = adapter.plan(req)
    assert p["accept"] and p["source"] == "mp4"
    assert p["argv"][p["argv"].index("--packaging") + 1] == "loc"


def test_namespace_mode():
    assert "--pub-ns" in adapter.plan(_request(mode="namespace"))["argv"]


@pytest.mark.parametrize(
    "kwargs, field",
    [
        ({"content": "clip"}, "content"),
        ({"packaging": "cmaf", "audio_delivery": "dgram"}, "audio_delivery"),
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


@pytest.fixture
def fake_pub_media(monkeypatch):
    """run() against a stand-in for pub_media; restores the signal handlers run() sets."""
    saved = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}

    def use(script):
        argv = [sys.executable, "-c", script]
        monkeypatch.setattr(adapter, "plan", lambda req: {"accept": True, "argv": argv})
        return adapter.run(_request())

    yield use
    for s, handler in saved.items():
        signal.signal(s, handler)


def test_run_passes_events_through(fake_pub_media, capfd):
    script = (
        'print(\'@mtt {"event":"ready","endpoints":[]}\'); print(\'a log line\'); '
        'print(\'@mtt {"event":"done","reason":"duration"}\')'
    )
    assert fake_pub_media(script) == 0
    out, err = capfd.readouterr()
    events = [json.loads(line.removeprefix("@mtt ")) for line in out.splitlines()]
    assert [e["event"] for e in events] == ["ready", "done"]
    assert "a log line" in err


@pytest.mark.parametrize("code, expected", [(64, 64), (69, 69), (70, 70), (1, 70), (2, 70)])
def test_run_exit_codes(fake_pub_media, capfd, code, expected):
    assert fake_pub_media(f"raise SystemExit({code})") == expected
    out, _ = capfd.readouterr()
    assert json.loads(out.removeprefix("@mtt ")) == {"event": "done", "reason": f"exit {code}"}


def test_check_mode_process(tmp_path):
    for changes, code in (({}, 0), ({"content": "clip"}, 64)):
        path = tmp_path / "request.json"
        path.write_text(json.dumps(_request(**changes)))
        env = {"MTT_MODE": "check", "MTT_REQUEST_FILE": str(path), "PATH": os.environ["PATH"]}
        r = subprocess.run([str(ADAPTER)], env=env, capture_output=True, text=True)
        assert r.returncode == code
        assert json.loads(r.stdout)["accept"] is (code == 0)


def _flags(argv, flag):
    return [argv[i + 1] for i, a in enumerate(argv) if a == flag]


def test_live_ladder():
    req = _request(video_height=720, video_tracks="alt3")
    req["source"] = {"format": "mpegts", "path": "/j/s.ts", "start_us": 1793623206000000}
    p = adapter.plan(req, python="py")
    argv = p["argv"]
    assert p["accept"] and p["source"] == "mpegts" and _flags(argv, "--ts") == ["/j/s.ts"]
    assert _flags(argv, "--video-alt") == ["one"]
    assert _flags(argv, "--video-label") == ["720p", "540p", "360p"]
    assert _flags(argv, "--catalog-bitrate") == ["2500000", "1500000", "600000"]


def test_views_from_mp4_loops():
    req = _request(packaging="cmaf", video_tracks="view2")
    req["source"] = {"format": "mp4", "path": "/c/a.mp4", "paths": ["/c/a.mp4", "/c/b.mp4"]}
    argv = adapter.plan(req, python="py")["argv"]
    assert _flags(argv, "--mp4") == ["/c/a.mp4", "/c/b.mp4"] and "--loop" in argv
    assert _flags(argv, "--video-alt") == ["each"]
    assert _flags(argv, "--video-label") == ["view 1", "view 2"]
    check = adapter.plan(_request(packaging="cmaf", video_tracks="view3"))["argv"]
    assert _flags(check, "--mp4") == ["<source>"] * 3


def test_one_video_track_has_no_set_flags():
    argv = adapter.plan(_request(), python="py")["argv"]
    assert "--video-alt" not in argv and "--video-label" not in argv
    assert _flags(argv, "--catalog-bitrate") == ["600000"]
