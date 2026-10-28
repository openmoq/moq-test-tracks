# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# Runner agent API against the fake adapter.

import json
import subprocess
import sys
import textwrap
import time

import pytest
from fastapi.testclient import TestClient
from test_supervisor import FAKE_ADAPTER

from moq_test_tracks import config
from moq_test_tracks.runner import agent
from moq_test_tracks.runner.agent import create_app, sweep_orphans

TOKEN = "t0ken"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
NS = "openmoq/mtt/fake/moq_test_tracks_00/msf/loc/catalog/h264/180/30/200/1000/0/aac/2/64/1/0/clock/0"


@pytest.fixture
def root(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "tools.yaml").write_text("tools:\n  fake:\n    max_jobs: 3\n")
    (tmp_path / "config" / "relays.yaml").write_text(
        textwrap.dedent(
            """\
            default: local
            relays:
              local:
                endpoints: {moqt: "moqt://127.0.0.1:4433"}
                drafts: [18]
            """
        )
    )
    (tmp_path / "config" / "policy.yaml").write_text(
        "namespace_prefix: [openmoq, mtt]\nglobal: {max_jobs: 2, max_live_encodes: 2}\n"
        "per_key_defaults: {max_jobs: 1}\n"
    )
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "sources.yaml").write_text(
        "clips:\n  demo:\n    url: https://example.com/a.m3u8\n    license: test\n"
        "    attribution: Demo credit\n"
    )
    adapter = tmp_path / "adapters" / "fake"
    adapter.mkdir(parents=True)
    (adapter / "manifest.yaml").write_text(
        "contract_version: 1\nid: fake\nsources: [mpegts, mp4]\nstop: {signal: SIGINT, grace_s: 1}\n"
    )
    (adapter / "run").write_text(FAKE_ADAPTER)
    (adapter / "run").chmod(0o755)
    return tmp_path


@pytest.fixture
def client(root):
    cfg = config.load(root)
    app = create_app(cfg, token=TOKEN, work=root / "var" / "jobs", cache=root / "var" / "cache")
    with TestClient(app) as c:
        yield c


def _job(content="clock", **extra):
    return {
        "namespace": NS.replace("/clock/", f"/{content}/"),
        "tool": "fake",
        "relay": "local",
        **extra,
    }


def _wait(client, job_id, states, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/jobs/{job_id}", headers=AUTH).json()
        if job["state"] in states:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} never reached {states}")


def _until(client, job_id, check, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/jobs/{job_id}", headers=AUTH).json()
        if check(job):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id}: condition not met: {job}")


@pytest.fixture
def relay_loss(root, monkeypatch):
    """The fake adapter exits 69 (every relay lost) this many more times."""
    monkeypatch.setattr(agent, "RESTART_BACKOFF_S", 0.05)
    monkeypatch.setattr(agent, "RESTART_BACKOFF_MAX_S", 0.2)
    jobs = root / "var" / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    return lambda n: (jobs / "fail-next").write_text(str(n))


def test_restart_after_relay_loss(client, relay_loss):
    relay_loss(2)
    job = client.post("/jobs", json=_job(), headers=AUTH).json()
    back = _until(client, job["id"], lambda j: j["restarts"] == 2 and j["state"] == "running")
    assert back["namespace"] == job["namespace"]
    client.post("/stop-all", headers=AUTH)


def test_stop_while_reconnecting(client, relay_loss, monkeypatch):
    monkeypatch.setattr(agent, "RESTART_BACKOFF_S", 30.0)
    relay_loss(1)
    job = client.post("/jobs", json=_job(), headers=AUTH).json()
    _wait(client, job["id"], ("reconnecting",))
    assert client.get("/jobs?active=true", headers=AUTH).json()[0]["id"] == job["id"]
    client.delete(f"/jobs/{job['id']}", headers=AUTH)
    assert _wait(client, job["id"], ("ended",))["restarts"] == 1


def test_restart_gives_up(client, relay_loss, monkeypatch):
    monkeypatch.setattr(agent, "RESTART_MAX", 2)
    relay_loss(10)
    job = client.post("/jobs", json=_job(), headers=AUTH).json()
    failed = _wait(client, job["id"], ("failed",))
    assert failed["restarts"] == 2 and failed["exit_code"] == 69


def test_ttl_ends_reconnecting(client, relay_loss, monkeypatch):
    monkeypatch.setattr(agent, "RESTART_BACKOFF_S", 30.0)
    relay_loss(1)
    job = client.post("/jobs", json=_job(ttl_s=1), headers=AUTH).json()
    assert _wait(client, job["id"], ("expired",), timeout=5)["restarts"] == 1


def test_token_required(client):
    assert client.get("/healthz").status_code == 200
    assert client.get("/jobs").status_code == 401
    assert client.get("/jobs", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.post("/jobs", json=_job()).status_code == 401


def test_no_token_no_app(root):
    with pytest.raises(ValueError):
        create_app(config.load(root), token="", work=root, cache=root)


def test_job_lifecycle_and_events(client):
    r = client.post("/jobs", json=_job(owner="k1"), headers=AUTH)
    assert r.status_code == 201
    job = r.json()
    assert job["joined"] is False and job["owner"] == "k1" and job["plan"] == ["fake"]
    assert job["namespace"] == NS and job["relay"] == "local" and job["draft"] == 18
    running = _wait(client, job["id"], ("running",))
    assert running["expires"] > running["created"]
    _wait_stats(client, job["id"])
    assert client.delete(f"/jobs/{job['id']}", headers=AUTH).status_code == 200
    ended = _wait(client, job["id"], ("ended",))
    assert ended["exit_code"] == 0
    with client.stream("GET", f"/jobs/{job['id']}/events", headers=AUTH) as r:
        body = "".join(r.iter_text())
    assert "event: state" in body and '"ready"' in body and "event: log" in body
    assert '"state": "ended"' in body
    with client.stream("GET", f"/jobs/{job['id']}/events?since=1000", headers=AUTH) as r:
        assert "".join(r.iter_text()) == ""


def _wait_stats(client, job_id):
    deadline = time.time() + 5
    while time.time() < deadline:
        if client.get(f"/jobs/{job_id}", headers=AUTH).json()["last_stats"]:
            return
        time.sleep(0.05)
    raise AssertionError("no stats")


def test_same_namespace_joins(client):
    first = client.post("/jobs", json=_job(), headers=AUTH).json()
    second = client.post("/jobs", json=_job(owner="other"), headers=AUTH)
    assert second.status_code == 201 and second.json()["joined"] is True
    assert second.json()["id"] == first["id"]
    assert len(client.get("/jobs?active=true", headers=AUTH).json()) == 1
    client.post("/stop-all", headers=AUTH)


def test_job_cap(client):
    assert client.post("/jobs", json=_job("clock"), headers=AUTH).status_code == 201
    assert client.post("/jobs", json=_job("noise"), headers=AUTH).status_code == 201
    third = client.post("/jobs", json=_job("clock", ttl_s=99), headers=AUTH)
    assert third.json().get("joined") is True  # same namespace joins even at the cap
    other = _job("clock")
    other["namespace"] = NS.replace("/180/", "/360/").replace("/200/", "/600/")
    r = client.post("/jobs", json=other, headers=AUTH)
    assert r.status_code == 429 and "limit of 2 jobs" in r.json()["detail"]["errors"][0]
    assert client.post("/stop-all", headers=AUTH).json()["stopped"] == 2


@pytest.mark.parametrize(
    "body, text",
    [
        (_job("bars"), "content: no"),  # the adapter refuses
        ({**_job(), "namespace": NS.replace("/180/", "/700/")}, "tuple field 5"),
        ({**_job(), "relay": "elsewhere"}, "not in the allowlist"),
        ({**_job(), "tool": "nope"}, "unknown or disabled tool"),
        ({**_job(), "ttl_s": 999999}, "ttl"),
        ({**_job(), "mode": "both"}, "mode both"),
    ],
)
def test_rejections(client, body, text):
    r = client.post("/jobs", json=body, headers=AUTH)
    assert r.status_code == 422
    assert any(text in e for e in r.json()["detail"]["errors"])


CLIP_NS = NS.replace("/fake/", "/fake/demo/").replace("/clock/0", "/clip/0")


def test_clip_jobs(client, root):
    clip_job = {**_job(), "namespace": CLIP_NS}
    r = client.post("/check", json=clip_job, headers=AUTH)
    assert r.status_code == 422 and "clip demo has not been fetched" in r.text
    other = {**_job(), "namespace": CLIP_NS.replace("/demo/", "/other/")}
    assert "configured clip id" in client.post("/check", json=other, headers=AUTH).text
    clips = root / "var" / "cache" / "clips"
    clips.mkdir(parents=True)
    (clips / "demo-abc.mkv").write_bytes(b"x")
    meta = {"clip": "demo", "file": "demo-abc.mkv", "sha256": "abc", "duration_s": 120.0}
    meta |= {"captured_at": "2026-10-25T00:00:00Z", "video": {}, "audio": {}}
    (clips / "demo.json").write_text(json.dumps(meta))
    assert client.post("/check", json=clip_job, headers=AUTH).status_code == 200
    job = client.post("/jobs", json=clip_job, headers=AUTH).json()
    req = json.loads((root / "var" / "jobs" / job["id"] / "request.json").read_text())
    path = str(clips / "demo-abc.mkv")
    assert req["clip"] == {"id": "demo", "path": path, "attribution": "Demo credit"}
    listed = client.get("/clips", headers=AUTH).json()
    assert listed[0]["id"] == "demo" and listed[0]["fetched"]["sha256"] == "abc"
    client.post("/stop-all", headers=AUTH)


def test_key_job_limit(client):
    assert client.post("/jobs", json=_job(owner="k1"), headers=AUTH).status_code == 201
    other = {
        **_job(owner="k1"),
        "namespace": NS.replace("/180/", "/360/").replace("/200/", "/600/"),
    }
    r = client.post("/jobs", json=other, headers=AUTH)
    assert (
        r.status_code == 429
        and "this key is at its limit of 1 jobs" in r.json()["detail"]["errors"][0]
    )
    assert (
        client.post("/jobs", json={**other, "unrestricted": True}, headers=AUTH).status_code == 201
    )
    client.post("/stop-all", headers=AUTH)


def test_admin_ttl(client):
    long = {**_job(), "ttl_s": 7200}
    assert client.post("/check", json=long, headers=AUTH).status_code == 422
    assert (
        client.post("/check", json={**long, "unrestricted": True}, headers=AUTH).status_code == 200
    )
    beyond = {**_job(), "ttl_s": 86401, "unrestricted": True}
    assert client.post("/check", json=beyond, headers=AUTH).status_code == 422


def test_force_kill(client):
    job = client.post("/jobs", json=_job("noise"), headers=AUTH).json()  # ignores SIGINT
    _wait(client, job["id"], ("running",))
    client.delete(f"/jobs/{job['id']}?force=true", headers=AUTH)
    killed = _wait(client, job["id"], ("killed",))
    assert killed["exit_code"] == 137


def test_check_only(client):
    r = client.post("/check", json=_job(), headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["accept"] and body["plan"] == ["fake"] and body["joins"] is None
    assert body["namespace"] == NS and body["tracks"] == ["catalog", "video", "audio"]
    assert client.get("/jobs", headers=AUTH).json() == []
    started = client.post("/jobs", json=_job(), headers=AUTH).json()
    assert client.post("/check", json=_job(), headers=AUTH).json()["joins"] == started["id"]
    refused = client.post("/check", json=_job("bars"), headers=AUTH)
    assert refused.status_code == 422
    client.post("/stop-all", headers=AUTH)


def test_unknown_job(client):
    assert client.get("/jobs/j_nope", headers=AUTH).status_code == 404
    assert client.delete("/jobs/j_nope", headers=AUTH).status_code == 404


def test_sweep_kills_only_owned_groups(tmp_path):
    job_dir = tmp_path / "jobs" / "j_old"
    job_dir.mkdir(parents=True)
    owned = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)", str(job_dir)],
        start_new_session=True,
    )
    stranger = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    (job_dir / "pgids").write_text(f"{owned.pid}\n{stranger.pid}\n999999\n")
    try:
        assert sweep_orphans(tmp_path / "jobs") == 1
        assert owned.wait(timeout=5) < 0
        assert stranger.poll() is None
        assert not (job_dir / "pgids").exists()
    finally:
        stranger.kill()
        owned.kill()
