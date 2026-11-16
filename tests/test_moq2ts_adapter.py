# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# moq2ts adapter: the accept/refuse table, moq2ts-cli argv, the source feed and output
# translation.

import contextlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import threading
import time
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

from moq_test_tracks import config
from moq_test_tracks.config import Relay
from moq_test_tracks.runner.request import build
from moq_test_tracks.spec import decode, from_slash

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "adapters" / "moq2ts" / "run"
NS = "openmoq/moq2ts/mtt_00/clock/msfts/mpeg2/720/25/mp2/2"
RELAY = Relay("r", "r", {"moqt": "moqt://relay:4443"}, (18,), 18)

_loader = SourceFileLoader("moq2ts_adapter", str(ADAPTER))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
adapter = importlib.util.module_from_spec(_spec)
_loader.exec_module(adapter)


def _request(ns=NS, **extra):
    namespace = from_slash(ns)
    spec = decode(namespace).spec
    req = build("j", namespace, spec, RELAY, transport="moqt", draft=18, mode="namespace", ttl_s=30)
    req.update(extra)
    return req


def test_plan():
    p = adapter.plan(_request())
    assert p["accept"] and p["source"] == "mpegts"
    assert p["argv"] == [
        "moq2ts-cli", "--endpoint", "moqt://relay:4443", "--namespace", NS, "--draft", "18",
        "--transparent", "--segment-bytes", "1316", "--video", "/dev/stdin",
    ]  # fmt: skip


def test_segment_bytes_and_insecure():
    req = _request(NS + "/msf/catalog/1000/_/0/_/8000/348")
    req["tls"]["verify"] = False
    argv = adapter.plan(req)["argv"]
    assert argv[argv.index("--segment-bytes") + 1] == "65424" and argv[-1] == "--insecure"


@pytest.mark.parametrize(
    "change, field",
    [
        ({"fields": {"packaging": "loc"}}, "packaging"),
        ({"endpoints": [{"url": "a", "draft": 18}] * 2}, "endpoints"),
        ({"tls": {"verify": True, "ca_file": "/ca.pem"}}, "tls"),
    ],
)
def test_refuses(change, field):
    req = _request()
    if "fields" in change:
        req["fields"] = {**req["fields"], **change.pop("fields")}
    req.update(change)
    p = adapter.plan(req)
    assert not p["accept"] and [r["field"] for r in p["reasons"]] == [field]


def test_manifest_matches_the_repo_config():
    cfg = config.load(ROOT)
    tool = cfg.tools["moq2ts"]
    assert tool.sources == ("mpegts",) and tool.capabilities["packaging"] == ["msfts"]
    spec = decode(from_slash(NS)).spec
    errors = config.job_errors(
        tool, cfg.relays["aiomoqt-relay"], spec, mode="namespace", transport="moqt", draft=18,
        ttl_s=600, policy=cfg.policy, unrestricted=True,
    )  # fmt: skip
    assert errors == []
    aiomoqt = config.job_errors(
        cfg.tools["aiomoqt"], cfg.relays["aiomoqt-relay"], spec, mode="publish",
        transport="moqt", draft=18, ttl_s=600, policy=cfg.policy, unrestricted=True,
    )  # fmt: skip
    assert "aiomoqt does not support packaging msfts" in aiomoqt


FAKE_TOOL = """\
import os, sys, time
got = 0
while chunk := sys.stdin.buffer.read(1316):
    got += len(chunk)
    print(f"[stats] objects={got // 1316} bytes={got} groups=1", flush=True)
print(f"[final] objects={got // 1316} bytes={got} groups=1 sent_objects=1 sent_bytes=1", flush=True)
sys.exit(int(os.environ.get("FAKE_RC", "0")))
"""


@pytest.fixture
def fake_run(tmp_path, monkeypatch):
    """run() against a stand-in for moq2ts-cli fed from a FIFO; restores signal handlers."""
    saved = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    monkeypatch.setattr(adapter, "STATS_S", 0.05)

    def use(rc=0, size=1316 * 400, tool=FAKE_TOOL, pace_s=0.0):
        fifo = tmp_path / "source.ts"
        os.mkfifo(fifo)
        argv = [sys.executable, "-c", tool]
        monkeypatch.setenv("FAKE_RC", str(rc))
        monkeypatch.setattr(adapter, "plan", lambda req: {"accept": True, "argv": argv})

        def write():
            with contextlib.suppress(BrokenPipeError), fifo.open("wb") as fh:
                for _ in range(size // 1316):
                    fh.write(b"\x47" * 1316)
                    time.sleep(pace_s)

        writer = threading.Thread(target=write)
        writer.start()
        rc = adapter.run(_request(source={"path": str(fifo)}))
        writer.join()
        return rc

    yield use
    for s, handler in saved.items():
        signal.signal(s, handler)


def _events(out):
    return [json.loads(line.removeprefix("@mtt ")) for line in out.splitlines()]


def test_run_feeds_the_tool_and_reports(fake_run, capfd):
    assert fake_run() == 0
    out, err = capfd.readouterr()
    events = _events(out)
    assert events[0] == {
        "event": "ready",
        "endpoints": [{"url": "moqt://relay:4443", "draft": 18, "state": "up"}],
    }
    assert events[-1] == {"event": "done", "reason": "end of source"}
    assert "[final] objects=400 bytes=526400" in err and "[stats]" not in err


@pytest.mark.parametrize("code, expected", [(1, 69), (2, 70)])
def test_run_exit_codes(fake_run, capfd, code, expected):
    assert fake_run(rc=code) == expected
    kinds = [e["event"] for e in _events(capfd.readouterr()[0])]
    assert kinds[-1] == "done" and (("endpoint_lost" in kinds) is (code == 1))


def test_a_tool_that_reads_nothing_leaves_the_source_live(fake_run, capfd):
    """Before a first subscriber moq2ts-cli reads nothing: whole chunks are dropped."""
    idle = "import sys, time; time.sleep(1); sys.stdin.buffer.read()"
    assert fake_run(size=1316 * 4000, tool=idle) == 0
    stats = [e for e in _events(capfd.readouterr()[0]) if e["event"] == "stats"]
    assert stats[-1]["input_dropped"] > 0


def test_an_early_clean_exit_is_a_lost_session(fake_run, capfd):
    early = "import sys; sys.stdin.buffer.read(1316)"
    assert fake_run(size=1316 * 1000, tool=early, pace_s=0.001) == 69
    kinds = [e["event"] for e in _events(capfd.readouterr()[0])]
    assert kinds[-2:] == ["endpoint_lost", "done"]


def test_stats_translate_the_tool_counters(monkeypatch):
    monkeypatch.setattr(adapter, "STATS_S", 0.01)
    emitted = []
    feed = adapter.Feed(_request(), None, emitted.append)
    feed.tool = adapter.stats_counters("[stats] objects=10 bytes=13160 groups=2\n")
    feed.input_bytes = 26320
    threading.Timer(0.035, feed.done.set).start()
    feed.report()
    track = emitted[0]["tracks"]["program-1"]
    assert (track["objects"], track["groups"]) == (10, 2) and track["kbps"] > 0
    assert emitted[0]["input_kbps"] > 0 and all(e["event"] == "stats" for e in emitted)


def test_check_mode_process(tmp_path):
    path = tmp_path / "request.json"
    path.write_text(json.dumps(_request()))
    env = {"MTT_MODE": "check", "MTT_REQUEST_FILE": str(path), "PATH": os.environ["PATH"]}
    r = subprocess.run([str(ADAPTER)], env=env, capture_output=True, text=True)
    assert r.returncode == 0 and json.loads(r.stdout)["source"] == "mpegts"
