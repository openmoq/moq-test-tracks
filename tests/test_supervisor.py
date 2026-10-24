# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# Job supervision against a fake adapter, the source stage, and config loading.

import asyncio
import dataclasses
import json
import shutil
import signal
import sys
import textwrap
from pathlib import Path

import pytest

from moq_test_tracks import config
from moq_test_tracks.config import Relay, Tool, job_errors
from moq_test_tracks.content import recipes
from moq_test_tracks.runner.request import build
from moq_test_tracks.runner.supervisor import EXIT_OK, Job
from moq_test_tracks.spec import decode, from_slash

ROOT = Path(__file__).resolve().parents[1]
NS = "openmoq/mtt/fake/moq_test_tracks_00/msf/loc/catalog/h264/180/30/200/1000/0/aac/2/64/1/0/ts/0"
RELAY = Relay("local", "local", {"moqt": "moqt://127.0.0.1:4433"}, (18,), 18)
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")

# Behaviour is chosen by the content field: bars refuses, noise ignores SIGINT,
# and a source path is read when one is given.
FAKE_ADAPTER = textwrap.dedent(
    f"""\
    #!{sys.executable}
    import json, os, signal, sys, time
    req = json.load(open(os.environ["MTT_REQUEST_FILE"]))
    content = req["fields"]["content"]
    if os.environ["MTT_MODE"] == "check":
        if content == "bars":
            print(json.dumps({{"accept": False, "reasons": [{{"field": "content", "message": "no"}}]}}))
            sys.exit(64)
        print(json.dumps({{"accept": True, "source": req["clip"], "argv": ["fake"]}}))
        sys.exit(0)
    open(os.path.join(os.environ["MTT_JOB_DIR"], "env.json"), "w").write(json.dumps(sorted(os.environ)))
    src = req["source"]
    if src and src["fifo"]:
        with open(src["path"], "rb") as fh:
            got = len(fh.read(188 * 100))
        print("read", got, file=sys.stderr)
    stop = []
    signal.signal(signal.SIGINT, signal.SIG_IGN if content == "noise" else (lambda *_: stop.append(1)))
    print("@mtt " + json.dumps({{"event": "ready"}}), flush=True)
    print("plain log line", flush=True)
    t = 0.0
    while not stop:
        print("@mtt " + json.dumps({{"event": "stats", "t": t, "tracks": {{}}}}), flush=True)
        time.sleep(0.05)
        t += 0.05
    print("@mtt " + json.dumps({{"event": "done", "reason": "signal"}}), flush=True)
    """
)


@pytest.fixture
def fake_tool(tmp_path):
    adapter = tmp_path / "fake" / "run"
    adapter.parent.mkdir()
    adapter.write_text(FAKE_ADAPTER)
    adapter.chmod(0o755)
    return Tool(
        "fake", "fake", "0", ("moq_test_tracks_00",), ("mpegts", "mp4"),
        signal.SIGINT, 0.5, {}, adapter, 1,
    )  # fmt: skip


def _job(tool, tmp_path, ttl=30, source=None, **changes):
    namespace = from_slash(NS)
    spec = dataclasses.replace(decode(namespace).spec, **changes)
    req = build(
        "j_test", namespace, spec, RELAY, transport="moqt", draft=18, mode="publish", ttl_s=ttl
    )
    req["clip"] = source  # the fake adapter echoes this as its requested source
    return Job(tool, req, spec, tmp_path / "jobs", tmp_path / "cache")


def _collect(job, stop_after=None):
    events, logs = [], []

    async def go():
        check = await job.check()
        assert check.accept

        def on_event(e):
            events.append(e)
            if stop_after and e["event"] == "stats" and e["t"] >= stop_after:
                job.stop()

        rc = await job.run(check, on_event, logs.append)
        return rc

    return asyncio.run(go()), events, logs


def test_check_refuses(fake_tool, tmp_path):
    job = _job(fake_tool, tmp_path, content="bars")
    result = asyncio.run(job.check())
    assert not result.accept
    assert result.reasons == [{"field": "content", "message": "no"}]


def test_run_events_and_stop(fake_tool, tmp_path):
    job = _job(fake_tool, tmp_path)
    rc, events, logs = _collect(job, stop_after=0.2)
    assert rc == EXIT_OK and job.state == "ended"
    kinds = [e["event"] for e in events]
    assert kinds[0] == "ready" and "stats" in kinds and kinds[-1] == "done"
    assert "plain log line" in logs
    assert "plain log line" in (job.dir / "log").read_text()
    request = json.loads(job.request_file.read_text())
    assert request["contract_version"] == 1 and request["mode"] == "publish"


def test_ttl_expires(fake_tool, tmp_path):
    job = _job(fake_tool, tmp_path, ttl=1)
    rc, events, _ = _collect(job)
    assert rc == EXIT_OK and job.state == "expired"
    assert events[-1]["event"] == "done"


def test_kill_after_grace(fake_tool, tmp_path):
    job = _job(fake_tool, tmp_path, content="noise")
    rc, _, _ = _collect(job, stop_after=0.1)
    assert rc == 128 + signal.SIGKILL and job.state == "killed"


def test_environment_is_reset(fake_tool, tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "x")
    job = _job(fake_tool, tmp_path)
    _collect(job, stop_after=0.05)
    keys = set(json.loads((job.dir / "env.json").read_text()))
    keys -= {"LC_CTYPE", "PWD", "SHLVL", "_"}  # set by the interpreter or shell
    assert keys == {
        "MTT_CONTRACT_VERSION",
        "MTT_MODE",
        "MTT_REQUEST_FILE",
        "MTT_JOB_DIR",
        "PATH",
        "HOME",
        "LANG",
    }


@needs_ffmpeg
def test_live_source_fifo(fake_tool, tmp_path):
    job = _job(fake_tool, tmp_path, source="mpegts")
    _, _, logs = _collect(job, stop_after=0.2)
    assert job.request["source"]["fifo"] is True
    assert any(line.startswith("read ") and line != "read 0" for line in logs)


@needs_ffmpeg
def test_mp4_loop_is_cached(fake_tool, tmp_path, monkeypatch):
    monkeypatch.setattr(recipes, "LOOP_TARGET_S", 2)
    job = _job(fake_tool, tmp_path, source="mp4")
    _collect(job, stop_after=0.05)
    path = Path(job.request["source"]["path"])
    assert path.exists() and path.parent == tmp_path / "cache"
    mtime = path.stat().st_mtime
    again = _job(fake_tool, tmp_path, source="mp4")
    _collect(again, stop_after=0.05)
    assert again.request["source"]["path"] == str(path) and path.stat().st_mtime == mtime


def test_config_loads_repo():
    cfg = config.load(ROOT)
    assert cfg.default_relay in cfg.relays
    tool = cfg.tools["aiomoqt"]
    assert tool.adapter == ROOT / "adapters" / "aiomoqt" / "run"
    assert tool.stop_signal == signal.SIGINT


def test_job_errors(fake_tool):
    spec = decode(from_slash(NS)).spec
    policy = config.Policy(("openmoq", "mtt"), 1800, 3600, 720, 30, 4000)
    caps = dataclasses.replace(
        fake_tool, capabilities={"video_codec": ["av1"], "mode": ["namespace"], "draft": [16]}
    )
    errors = job_errors(
        caps, RELAY, dataclasses.replace(spec, video_height=1080, video_kbps=5000),
        mode="publish", transport="webtransport", draft=16, ttl_s=7200, policy=policy,
    )  # fmt: skip
    text = " | ".join(errors)
    for expected in (
        "no webtransport endpoint",
        "does not speak draft 16",
        "does not support video_codec h264",
        "does not support mode publish",
        "height 1080 exceeds",
        "video bitrate 5000 exceeds",
        "ttl 7200",
    ):
        assert expected in text
    admin = " | ".join(
        job_errors(
            caps,
            RELAY,
            dataclasses.replace(spec, video_height=1080, video_kbps=5000),
            mode="namespace",
            transport="moqt",
            draft=18,
            ttl_s=7200,
            policy=policy,
            admin=True,
        )  # fmt: skip
    )
    assert "exceeds" not in admin and "ttl" not in admin
