# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# Public API: presets, keys, sessions, roles, origin checks, and runner relay,
# against a stub runner.

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from moq_test_tracks import config
from moq_test_tracks.api import auth, presets
from moq_test_tracks.api.app import Settings, create_app
from moq_test_tracks.api.db import Db

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://mtt.test"
SECRET = "s" * 40


class StubRunner:
    def __init__(self):
        self.jobs = {}
        self.posted = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        assert request.headers["authorization"] == "Bearer runner-token"
        if path == "/healthz":
            return httpx.Response(200, json={"ok": True})
        if path == "/check":
            body = json.loads(request.content)
            return httpx.Response(200, json={"accept": True, "plan": ["pub", body["namespace"]]})
        if path == "/clips":
            return httpx.Response(200, json=[{"id": "nasa_tv", "fetched": None}])
        if path == "/stop-all":
            return httpx.Response(200, json={"stopped": len(self.jobs)})
        if path == "/jobs" and method == "POST":
            body = json.loads(request.content)
            self.posted.append(body)
            job_id = f"j_{len(self.jobs)}"
            self.jobs[job_id] = {"id": job_id, "state": "running", "joined": False, **body}
            return httpx.Response(201, json=self.jobs[job_id])
        if path == "/jobs":
            return httpx.Response(200, json=list(self.jobs.values()))
        job_id = path.split("/")[2]
        if job_id not in self.jobs:
            return httpx.Response(404, json={"detail": {"errors": [f"no job {job_id}"]}})
        if path.endswith("/events"):
            return httpx.Response(200, text='event: state\ndata: {"state": "running"}\n\n')
        if method == "DELETE":
            self.jobs[job_id]["state"] = "stopping"
        return httpx.Response(200, json=self.jobs[job_id])


@pytest.fixture
def stub():
    return StubRunner()


@pytest.fixture
def app(tmp_path, stub):
    settings = Settings(
        runner_url="http://runner",
        runner_token="runner-token",
        session_secret=SECRET,
        db_path=tmp_path / "api.sqlite",
        public_origins=(ORIGIN,),
        trust_proxy=False,
        secure_cookies=False,
    )
    return create_app(config.load(ROOT), settings, transport=httpx.MockTransport(stub))


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def keys(app):
    db = app.state.db
    return {role: auth.new_key(db, f"{role}-user", role) for role in auth.ROLES}


def bearer(key):
    return {"Authorization": f"Bearer {key}"}


# presets


def test_preset_defaults():
    spec = presets.resolve({})
    assert (spec.catalog, spec.packaging, spec.video_codec, spec.video_height) == (
        "msf",
        "loc",
        "h264",
        720,
    )
    assert (spec.video_kbps, spec.group_ms, spec.audio_kbps) == (2500, 2000, 128)


def test_preset_fallbacks():
    assert presets.resolve({}, "1080p60").video_kbps == 6750
    assert presets.resolve({"fps": "29_97"}).group_ms == 2002
    assert presets.resolve({"ac": "opus"}).audio_kbps == 64
    audio = presets.resolve({}, "audio")
    assert not audio.has_video and audio.video_height is None
    assert presets.resolve({"h": "360", "vbr": "900"}).video_kbps == 900
    cmaf = presets.resolve({}, "cmaf")
    assert (cmaf.catalog, cmaf.packaging, cmaf.init_data) == ("msf", "cmaf", "catalog")
    assert presets.resolve({"init": "object"}).init_data == "object"
    assert presets.resolve({"cat": "none"}).init_data is None


def test_preset_errors():
    with pytest.raises(presets.PresetError):
        presets.resolve({"bogus": "1"})
    with pytest.raises(presets.PresetError):
        presets.resolve({}, "8k")
    with pytest.raises(ValueError):
        presets.resolve({"h": "700"})
    with pytest.raises(ValueError):
        presets.resolve({"init": "track"}, "cmaf")


# keys and sessions


def test_keys(tmp_path):
    db = Db(tmp_path / "k.sqlite")
    key = auth.new_key(db, "alice", "run")
    assert key.startswith("mtt_") and auth.parse(key)
    p = auth.verify(db, key)
    assert p.name == "alice" and p.can("view") and p.can("run") and not p.can("admin")
    assert auth.verify(db, key[:-1] + ("x" if key[-1] != "x" else "y")) is None
    assert auth.verify(db, "nonsense") is None
    stored = db.query("SELECT secret_hash FROM keys")[0]["secret_hash"]
    assert auth.parse(key)[1] not in stored
    assert auth.revoke(db, p.key_id) and auth.verify(db, key) is None
    expired = auth.new_key(db, "bob", "view", expires_days=-1)
    assert auth.verify(db, expired) is None
    wide = auth.verify(db, auth.new_key(db, "carol", "run", expires_days=7, unrestricted=True))
    assert wide.unrestricted and wide.exempt and not wide.can("admin")
    assert not p.exempt
    for days in (None, 31):
        with pytest.raises(ValueError, match="within 30 days"):
            auth.new_key(db, "dave", "run", expires_days=days, unrestricted=True)


def test_keys_table_gains_unrestricted(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as c:
        c.execute(
            "CREATE TABLE keys (id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL,"
            " secret_hash TEXT NOT NULL, created REAL NOT NULL, expires REAL, revoked REAL,"
            " last_used REAL)"
        )
    db = Db(path)
    assert auth.verify(db, auth.new_key(db, "erin", "run")).unrestricted is False
    with pytest.raises(ValueError):
        auth.new_key(db, "eve", "root")


def test_sessions(tmp_path):
    db = Db(tmp_path / "s.sqlite")
    key = auth.new_key(db, "alice", "run")
    p = auth.verify(db, key)
    sessions = auth.Sessions(SECRET, db)
    cookie = sessions.issue(p)
    assert sessions.verify(cookie) == p
    key_id, expiry, sig = cookie.split(".")
    assert sessions.verify(f"{key_id}.{int(expiry) + 999}.{sig}") is None
    flipped = cookie[:-1] + ("0" if cookie[-1] != "0" else "1")
    assert sessions.verify(flipped) is None
    assert sessions.verify("garbage") is None
    other = auth.Sessions("t" * 40, db)
    assert other.verify(cookie) is None
    auth.revoke(db, p.key_id)
    assert sessions.verify(cookie) is None
    with pytest.raises(ValueError):
        auth.Sessions("short", db)


def test_limiter():
    limiter = auth.LoginLimiter(limit=3, window_s=60)
    for _ in range(3):
        assert not limiter.blocked("1.2.3.4")
        limiter.fail("1.2.3.4")
    assert limiter.blocked("1.2.3.4") and not limiter.blocked("5.6.7.8")


# api


def test_public_endpoints(client):
    assert client.get("/healthz").json() == {"ok": True, "runner": True}
    r = client.get("/api/v1/namespace", params={"preset": "360p"})
    assert r.status_code == 200
    assert r.json()["namespace"].startswith(
        "openmoq/mtt/aiomoqt/moq_test_tracks_00/msf/loc/catalog/h264/360/"
    )
    assert client.get("/api/v1/namespace", params={"h": "700"}).status_code == 422
    decoded = client.get("/api/v1/namespace/decode", params={"ns": r.json()["namespace"]})
    assert decoded.json()["fields"]["video_height"] == 360
    assert "1080p60" in client.get("/api/v1/presets").json()["presets"]
    page = client.get("/")
    assert page.status_code == 200 and "moq-test-tracks" in page.text
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert client.get("/static/app.js").status_code == 200


def test_authentication_required(client, keys):
    assert client.get("/api/v1/jobs").status_code == 401
    assert client.get("/api/v1/jobs", params={"key": keys["admin"]}).status_code == 401
    assert client.get("/api/v1/jobs", headers=bearer("mtt_x_y")).status_code == 401
    assert client.get("/api/v1/me", headers=bearer(keys["view"])).json()["role"] == "view"


def test_roles(client, keys, stub):
    assert client.get("/api/v1/tools", headers=bearer(keys["view"])).status_code == 200
    assert client.post("/api/v1/jobs", json={}, headers=bearer(keys["view"])).status_code == 403
    r = client.post("/api/v1/jobs", json={"preset": "360p"}, headers=bearer(keys["run"]))
    assert r.status_code == 201
    job = r.json()
    run_id = auth.parse(keys["run"])[0]
    assert stub.posted[-1]["owner"] == run_id and job["owner_name"] == "run-user"
    assert stub.posted[-1]["relay"] == "moqx-main" and stub.posted[-1]["mode"] == "publish"
    assert stub.posted[-1]["unrestricted"] is False
    forged = {"preset": "360p", "unrestricted": True}
    client.post("/api/v1/jobs", json=forged, headers=bearer(keys["run"]))
    assert stub.posted[-1]["unrestricted"] is False  # only the caller's key sets it
    client.post("/api/v1/jobs", json={"preset": "360p"}, headers=bearer(keys["admin"]))
    assert stub.posted[-1]["unrestricted"] is True
    wide = auth.new_key(client.app.state.db, "wide", "run", expires_days=1, unrestricted=True)
    client.post("/api/v1/jobs", json={"preset": "360p"}, headers=bearer(wide))
    assert stub.posted[-1]["unrestricted"] is True
    other = auth.new_key(client.app.state.db, "other", "run")
    assert client.delete(f"/api/v1/jobs/{job['id']}", headers=bearer(other)).status_code == 403
    assert (
        client.delete(f"/api/v1/jobs/{job['id']}", headers=bearer(keys["run"])).status_code == 200
    )
    assert (
        client.delete(f"/api/v1/jobs/{job['id']}", headers=bearer(keys["admin"])).status_code == 200
    )
    assert client.post("/api/v1/admin/stop-all", headers=bearer(keys["run"])).status_code == 403
    assert client.post("/api/v1/admin/stop-all", headers=bearer(keys["admin"])).status_code == 200
    actions = [r["action"] for r in client.app.state.db.query("SELECT action FROM audit")]
    assert actions.count("job_started") == 4 and "stop_all" in actions


def test_query_parameters_and_check(client, keys, stub):
    r = client.post(
        "/api/v1/jobs/check?h=1080&fps=60&ac=opus&afwd=3&relay=moqx-main",
        headers=bearer(keys["run"]),
    )
    assert r.status_code == 200
    ns = r.json()["plan"][1]
    assert "/h264/1080/60/6750/2000/0/opus/2/64/1/3/" in ns


def test_namespace_prefix_is_enforced(client, keys):
    ns = "elsewhere/x/moq_test_tracks_00/msf/loc/catalog/h264/360/30/600/2000/0/aac/2/128/1/0/clock/0"
    r = client.post("/api/v1/jobs", json={"namespace": ns}, headers=bearer(keys["run"]))
    assert r.status_code == 422 and "prefix must be openmoq/mtt/aiomoqt" in r.text
    ok = ns.replace("elsewhere/x", "openmoq/mtt/aiomoqt")
    assert (
        client.post("/api/v1/jobs", json={"namespace": ok}, headers=bearer(keys["run"])).status_code
        == 201
    )


def test_clip_namespaces(client, keys):
    run = bearer(keys["run"])
    r = client.get("/api/v1/namespace", params={"clip": "nasa_tv", "h": "720"}, headers=run)
    assert r.status_code == 200
    ns = r.json()["namespace"]
    assert ns.startswith("openmoq/mtt/aiomoqt/nasa_tv/moq_test_tracks_00/") and ns.endswith(
        "/clip/0"
    )
    assert client.post("/api/v1/jobs", json={"namespace": ns}, headers=run).status_code == 201
    assert client.post("/api/v1/jobs", json={"clip": "nasa_tv"}, headers=run).status_code == 201
    for bad, text in (
        ({"clip": "bogus"}, "unknown clip bogus"),
        ({"params": {"content": "clip"}}, "needs clip=<id>"),
        ({"clip": "nasa_tv", "params": {"content": "bars"}}, "needs content clip"),
        ({"namespace": ns.replace("/nasa_tv/", "/bogus/")}, "unknown clip bogus"),
        ({"namespace": ns.replace("/clip/0", "/bars/0")}, "prefix must be openmoq/mtt/aiomoqt"),
        ({"namespace": ns.replace("nasa_tv/", "")}, "/<clip id>"),
    ):
        r = client.post("/api/v1/jobs", json=bad, headers=run)
        assert r.status_code == 422 and text in r.text, (bad, r.text)
    assert client.get("/api/v1/clips", headers=bearer(keys["view"])).json()[0]["id"] == "nasa_tv"


def test_grafana_forward_auth(client, keys):
    forged = {"X-WEBAUTH-USER": "admin", "X-WEBAUTH-ROLE": "Admin"}
    r = client.get("/auth/grafana", headers=forged)
    assert r.status_code == 401 and "x-webauth-user" not in r.headers
    browser = client.get("/auth/grafana", headers={"Accept": "text/html"}, follow_redirects=False)
    assert browser.status_code == 302 and browser.headers["location"] == "/"
    viewer = client.get("/auth/grafana", headers={**bearer(keys["run"]), **forged})
    assert viewer.status_code == 200
    assert viewer.headers["x-webauth-user"] == "mtt-run-user"
    assert viewer.headers["x-webauth-role"] == "Viewer"
    admin = client.get("/auth/grafana", headers=bearer(keys["admin"]))
    assert admin.headers["x-webauth-role"] == "Admin"
    odd = auth.new_key(client.app.state.db, 'a "b"\nc', "view")
    assert (
        client.get("/auth/grafana", headers=bearer(odd)).headers["x-webauth-user"] == "mtt-a__b__c"
    )


def test_login_session_and_origin(client, keys):
    assert client.post("/login", json={"key": keys["run"]}).status_code == 403  # no Origin
    r = client.post("/login", json={"key": keys["run"]}, headers={"Origin": ORIGIN})
    assert r.status_code == 200 and "mtt_session" in r.cookies
    assert client.get("/api/v1/me").json()["name"] == "run-user"
    assert client.post("/api/v1/jobs", json={"preset": "360p"}).status_code == 403
    bad = client.post("/api/v1/jobs", json={"preset": "360p"}, headers={"Origin": "https://evil"})
    assert bad.status_code == 403
    good = client.post("/api/v1/jobs", json={"preset": "360p"}, headers={"Origin": ORIGIN})
    assert good.status_code == 201
    client.post("/logout")
    client.cookies.clear()
    assert client.get("/api/v1/me").status_code == 401


def test_login_rate_limit(client, keys):
    for _ in range(10):
        r = client.post("/login", json={"key": "mtt_nope_nope"}, headers={"Origin": ORIGIN})
        assert r.status_code == 401
    r = client.post("/login", json={"key": keys["run"]}, headers={"Origin": ORIGIN})
    assert r.status_code == 429
    assert client.get("/api/v1/me", headers=bearer(keys["run"])).status_code == 429


def test_events_relay(client, keys, stub):
    job = client.post("/api/v1/jobs", json={}, headers=bearer(keys["run"])).json()
    with client.stream(
        "GET", f"/api/v1/jobs/{job['id']}/events", headers=bearer(keys["view"])
    ) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        assert "event: state" in "".join(r.iter_text())
    assert client.get("/api/v1/jobs/j_none/events", headers=bearer(keys["view"])).status_code == 404


def test_runner_down(tmp_path):
    def down(request):
        raise httpx.ConnectError("refused")

    settings = Settings(
        "http://runner", "t", SECRET, tmp_path / "a.sqlite", (ORIGIN,), False, False
    )
    app = create_app(config.load(ROOT), settings, transport=httpx.MockTransport(down))
    key = auth.new_key(app.state.db, "k", "view")
    with TestClient(app) as c:
        assert c.get("/healthz").json()["runner"] is False
        assert c.get("/api/v1/jobs", headers=bearer(key)).status_code == 503


def test_session_ttl_is_bounded():
    assert auth.SESSION_TTL_S <= 24 * 3600
