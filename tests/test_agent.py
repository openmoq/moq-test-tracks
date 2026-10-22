# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# Runner agent API against the fake adapter.

import subprocess
import sys
import textwrap
import time

import pytest
from fastapi.testclient import TestClient
from test_supervisor import FAKE_ADAPTER

from moq_test_tracks import config
from moq_test_tracks.runner.agent import create_app, sweep_orphans

TOKEN = "t0ken"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
NS = "openmoq/mtt/fake/moq_test_tracks_00/msf/loc/h264/180/30/200/1000/0/aac/2/64/1/0/ts/0"


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


def _job(content="ts", **extra):
    return {
        "namespace": NS.replace("/ts/", f"/{content}/"),
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
    assert client.post("/jobs", json=_job("ts"), headers=AUTH).status_code == 201
    assert client.post("/jobs", json=_job("noise"), headers=AUTH).status_code == 201
    third = client.post("/jobs", json=_job("ts", ttl_s=99), headers=AUTH)
    assert third.json().get("joined") is True  # same namespace joins even at the cap
    other = _job("ts")
    other["namespace"] = NS.replace("/180/", "/360/").replace("/200/", "/600/")
    r = client.post("/jobs", json=other, headers=AUTH)
    assert r.status_code == 429 and "limit of 2 jobs" in r.json()["detail"]["errors"][0]
    assert client.post("/stop-all", headers=AUTH).json()["stopped"] == 2


@pytest.mark.parametrize(
    "body, text",
    [
        (_job("bars"), "content: no"),  # the adapter refuses
        ({**_job(), "namespace": NS.replace("/180/", "/700/")}, "tuple field 4"),
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


def test_force_kill(client):
    job = client.post("/jobs", json=_job("noise"), headers=AUTH).json()  # ignores SIGINT
    _wait(client, job["id"], ("running",))
    client.delete(f"/jobs/{job['id']}?force=true", headers=AUTH)
    killed = _wait(client, job["id"], ("killed",))
    assert killed["exit_code"] == 137


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
