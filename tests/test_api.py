# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# Public API: presets, tokens, sessions, roles, relay grants, origin checks,
# and runner relay, against a stub runner.

import base64
import io
import json
import os
import sqlite3
import time
from pathlib import Path

import cbor2
import httpx
import pytest
from fastapi.testclient import TestClient

from moq_test_tracks import config
from moq_test_tracks.api import auth, presets
from moq_test_tracks.api.app import Settings, create_app
from moq_test_tracks.api.db import Db
from moq_test_tracks.cli import main

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://mtt.test"
SECRET = "s" * 40
TOKEN_SECRET = "k" * 40
SIGNER = auth.Signer(TOKEN_SECRET)
RELAYS = tuple(config.load(ROOT).relays)


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
        token_secret=TOKEN_SECRET,
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


def mint(name, role="run", relays=RELAYS, **kw):
    return auth.mint(SIGNER, name, role, relays=relays, **kw)[0]


def token_id(token):
    return auth.principal(SIGNER.open(token)).token_id


@pytest.fixture
def keys(app):
    return {role: mint(f"{role}-user", role) for role in auth.ROLES}


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
    assert presets.resolve({"adel": "dgram"}).audio_datagram
    assert presets.resolve({"vt": "alt3", "h": "1080"}).ladder() == (1080, 720, 540)
    with pytest.raises(presets.PresetError, match="vbr: video bitrate follows"):
        presets.resolve({"h": "360", "vbr": "900"})
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


# tokens and sessions


def test_tokens(tmp_path):
    db = Db(tmp_path / "t.sqlite")
    token, p = auth.mint(SIGNER, "alice", "run", relays=["moqx-main", "moqx-main", "us-ord-3"])
    assert token.startswith("mtt1_") and len(token) < 200
    assert auth.verify(SIGNER, db, token) == p
    assert p.relays == ("moqx-main", "us-ord-3") and p.tier == "standard"
    assert p.can("view") and p.can("run") and not p.can("admin") and not p.exempt
    assert 29.9 * 86400 < p.expires - time.time() <= 30 * 86400
    for i in (6, len(token) // 2, len(token) - 1):
        flipped = token[:i] + ("A" if token[i] != "A" else "B") + token[i + 1 :]
        assert auth.verify(SIGNER, db, flipped) is None
    assert auth.verify(auth.Signer("o" * 40), db, token) is None
    for junk in ("nonsense", "mtt_x_y", "mtt1_", "mtt1_!!!!", token + "AAAA", "mtt1_" + "A" * 5000):
        assert auth.verify(SIGNER, db, junk) is None
    expired, _ = auth.mint(SIGNER, "bob", "view", expires_days=1, now=time.time() - 2 * 86400)
    assert auth.verify(SIGNER, db, expired) is None
    future, _ = auth.mint(SIGNER, "bob", "view", now=time.time() + 3600)
    assert auth.verify(SIGNER, db, future) is None
    assert auth.revoke(db, p.token_id) and auth.verify(SIGNER, db, token) is None
    assert not auth.revoke(db, p.token_id)
    _, wide = auth.mint(SIGNER, "carol", "run", tier="unrestricted", expires_days=30)
    assert wide.exempt and not wide.can("admin")
    assert auth.mint(SIGNER, "root", "admin")[1].exempt
    for kw, text in (
        ({"tier": "unrestricted", "expires_days": 31}, "within 30 days"),
        ({"expires_days": 366}, "within 365 days"),
        ({"expires_days": 0}, "within 365 days"),
        ({"tier": "gold"}, "tier must be"),
        ({"role": "root"}, "role must be"),
        ({"name": 'a "b"'}, "name:"),
        ({"relays": [""]}, "must not be empty"),
    ):
        args = {"name": "dave", "role": "run", **kw}
        with pytest.raises(ValueError, match=text):
            auth.mint(SIGNER, args.pop("name"), args.pop("role"), **args)


def unwrap(token):
    """The CWT tag around a token's COSE_Mac0."""
    body = token[len(auth.PREFIX) :]
    return cbor2.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def test_token_structure():
    token, p = auth.mint(SIGNER, "alice", "admin", relays=["moqx-main"], tier="unrestricted")
    cwt = unwrap(token)
    assert cwt.tag == 61 and cwt.value.tag == 17
    protected, unprotected, payload, tag = cwt.value.value
    header = cbor2.loads(protected)
    assert header[1] == 5 and len(header[4]) == 4 and unprotected == {} and len(tag) == 32
    claims = cbor2.loads(payload)
    assert (claims[1], claims[2], claims[7].hex()) == ("mtt", "alice", p.token_id)
    assert claims["mtt"] == {1: "admin", 2: ["moqx-main"], 3: "unrestricted"}
    standard = cbor2.loads(unwrap(mint("bob")).value.value[2])
    assert 3 not in standard["mtt"]  # the default tier is left out
    assert SIGNER.open(SIGNER.sign([1, 2])) is None  # a MAC'd payload that is not a map
    assert auth.principal({1: "mtt", 2: "x", 4: 1, 6: 1, 7: b"x" * 8}) is None
    assert auth.principal({**claims, "mtt": {1: "run", 2: "moqx-main"}}) is None


def test_db_drops_old_keys_table(tmp_path):
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE keys (id TEXT PRIMARY KEY, secret_hash TEXT)")
    db = Db(path)
    tables = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "keys" not in tables and {"revoked", "audit"} <= tables


def test_sessions(tmp_path):
    db = Db(tmp_path / "s.sqlite")
    token, p = auth.mint(SIGNER, "alice", "run", relays=["moqx-main"])
    sessions = auth.Sessions(SECRET, db)
    cookie, max_age = sessions.issue(p)
    s = sessions.verify(cookie)
    assert (s.token_id, s.name, s.role, s.relays) == (p.token_id, p.name, p.role, p.relays)
    assert max_age == pytest.approx(auth.SESSION_TTL_S, abs=2)
    assert s.expires <= time.time() + auth.SESSION_TTL_S
    assert auth.verify(SIGNER, db, cookie) is None  # a session is not a token
    assert sessions.verify(token) is None  # nor a token a session
    flipped = cookie[:-1] + ("A" if cookie[-1] != "A" else "B")
    assert sessions.verify(flipped) is None and sessions.verify("garbage") is None
    assert auth.Sessions("t" * 40, db).verify(cookie) is None
    _, short = auth.mint(SIGNER, "bob", "run", relays=["moqx-main"], expires_days=0.1)
    assert sessions.issue(short)[1] <= 0.1 * 86400
    auth.revoke(db, p.token_id)
    assert sessions.verify(cookie) is None
    with pytest.raises(ValueError):
        auth.Sessions("short", db)


def test_cli_tokens(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "t.sqlite")
    root = ["--root", str(ROOT)]

    def tokens(*args):
        return main(["tokens", "--db", db, *args])

    monkeypatch.delenv("MTT_TOKEN_SECRET", raising=False)
    assert tokens("create", "--name", "alice", "--relay", "moqx-main", *root) == 2
    assert "MTT_TOKEN_SECRET" in capsys.readouterr().err
    monkeypatch.setenv("MTT_TOKEN_SECRET", TOKEN_SECRET)
    assert tokens("create", "--name", "alice", "--relay", "moqx-main", *root) == 0
    out = capsys.readouterr()
    alice = out.out.strip()
    assert alice.startswith("mtt1_") and "cannot be shown again" in out.err
    assert auth.principal(SIGNER.open(alice)).relays == ("moqx-main",)
    path = tmp_path / "bob.token"
    create_bob = ("create", "--name", "bob", "--role", "admin", "--relay", "us-ord-3")
    assert tokens(*create_bob, "--out", str(path), *root) == 0
    assert capsys.readouterr().out == ""
    assert os.stat(path).st_mode & 0o777 == 0o600
    bob = path.read_text().strip()
    assert tokens(*create_bob, "--out", str(path), *root) == 2  # never overwrites
    for bad in (
        ("--name", "carol"),  # a run token needs a relay
        ("--name", "carol", "--relay", "nowhere"),
        (
            "--name",
            "carol",
            "--relay",
            "moqx-main",
            "--tier",
            "unrestricted",
            "--expires-days",
            "31",
        ),
    ):
        assert tokens("create", *bad, *root) == 2
    assert tokens("create", "--name", "vic", "--role", "view", *root) == 0
    capsys.readouterr()
    assert tokens("revoke", token_id(alice), "--reason", "left") == 0
    assert tokens("revoke", token_id(alice)) == 2
    assert tokens("revoke", "not-an-id") == 2
    assert tokens("revoke", "0123456789abcdef") == 0
    assert "not in this issue log" in capsys.readouterr().out
    assert tokens("list") == 0
    rows = {line.split()[-1]: line.split() for line in capsys.readouterr().out.splitlines()}
    assert rows["alice"][3] == "revoked" and rows["bob"][3] == "active"
    assert rows["bob"][1:3] == ["admin", "standard"] and rows["bob"][-2] == "us-ord-3"
    assert rows["vic"][-2] == "-"
    monkeypatch.setattr("sys.stdin", io.StringIO(alice + "\n"))
    assert tokens("show") == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["name"] == "alice" and shown["status"] == "revoked" and shown["issued"] > 0
    assert tokens("show", "--file", str(path)) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "active"
    monkeypatch.setenv("MTT_TOKEN_SECRET", "o" * 40)
    assert tokens("show", "--file", str(path)) == 2
    assert tokens("list") == 0  # listing needs no secret
    actions = [r["action"] for r in Db(Path(db)).query("SELECT action FROM audit")]
    assert actions.count("token_created") == 3 and actions.count("token_revoked") == 2
    assert bob not in Path(db).read_bytes().decode(errors="replace")


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
        "openmoq/mtt/aiomoqt/moq_test_tracks_00/clock/msf/loc/h264/360/"
    )
    assert client.get("/api/v1/namespace", params={"vbr": "900"}).status_code == 422
    assert client.get("/api/v1/namespace", params={"h": "700"}).status_code == 422
    decoded = client.get("/api/v1/namespace/decode", params={"ns": r.json()["namespace"]})
    assert decoded.json()["fields"]["video_height"] == 360
    assert decoded.json()["video_kbps"] == 600 and decoded.json()["audio_kbps"] == 128
    full = r.json()["namespace"] + "/catalog/2000/1/0/1"
    shortened = client.get("/api/v1/namespace/decode", params={"ns": full}).json()["namespace"]
    assert shortened == r.json()["namespace"]
    assert "1080p60" in client.get("/api/v1/presets").json()["presets"]
    page = client.get("/")
    assert page.status_code == 200 and "moq-test-tracks" in page.text
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert client.get("/static/app.js").status_code == 200


def test_authentication_required(client, keys):
    assert client.get("/api/v1/jobs").status_code == 401
    assert client.get("/api/v1/jobs", params={"key": keys["admin"]}).status_code == 401
    assert client.get("/api/v1/jobs", headers=bearer("mtt_x_y")).status_code == 401
    other = auth.mint(auth.Signer("o" * 40), "x", "admin", relays=RELAYS)[0]
    assert client.get("/api/v1/jobs", headers=bearer(other)).status_code == 401
    info = client.get("/api/v1/token", headers=bearer(keys["view"])).json()
    assert info["role"] == "view" and info["name"] == "view-user" and info["tier"] == "standard"
    assert info["token_id"] == token_id(keys["view"]) and info["relays"] == list(RELAYS)
    assert client.get("/api/v1/me", headers=bearer(keys["view"])).json() == info
    auth.revoke(client.app.state.db, info["token_id"])
    assert client.get("/api/v1/token", headers=bearer(keys["view"])).status_code == 401


def test_relay_grants(client, stub):
    one = bearer(mint("one", relays=["aiomoqt-relay"]))
    listed = client.get("/api/v1/relays", headers=one).json()
    assert listed["default"] == "aiomoqt-relay"
    assert [r["id"] for r in listed["relays"]] == ["aiomoqt-relay"]
    assert client.post("/api/v1/jobs", json={"preset": "360p"}, headers=one).status_code == 201
    assert stub.posted[-1]["relay"] == "aiomoqt-relay"
    for path in ("/api/v1/jobs", "/api/v1/jobs/check"):
        r = client.post(path, json={"preset": "360p", "relay": "moqx-main"}, headers=one)
        assert r.status_code == 403 and "may not publish to moqx-main" in r.text
    r = client.post("/api/v1/jobs?relay=nowhere", json={}, headers=one)
    assert r.status_code == 403
    both = bearer(mint("both", relays=["us-ord-3", "moqx-main"]))
    assert client.get("/api/v1/relays", headers=both).json()["default"] == "moqx-main"
    none = bearer(mint("none", relays=()))
    assert client.get("/api/v1/relays", headers=none).json() == {"default": None, "relays": []}
    r = client.post("/api/v1/jobs", json={"preset": "360p"}, headers=none)
    assert r.status_code == 403 and "grants no relays" in r.text
    assert len(stub.posted) == 1


def test_roles(client, keys, stub):
    assert client.get("/api/v1/tools", headers=bearer(keys["view"])).status_code == 200
    assert client.post("/api/v1/jobs", json={}, headers=bearer(keys["view"])).status_code == 403
    r = client.post("/api/v1/jobs", json={"preset": "360p"}, headers=bearer(keys["run"]))
    assert r.status_code == 201
    job = r.json()
    assert stub.posted[-1]["owner"] == token_id(keys["run"]) and job["owner_name"] == "run-user"
    assert stub.posted[-1]["relay"] == "moqx-main" and stub.posted[-1]["mode"] == "publish"
    assert stub.posted[-1]["unrestricted"] is False
    forged = {"preset": "360p", "unrestricted": True}
    client.post("/api/v1/jobs", json=forged, headers=bearer(keys["run"]))
    assert stub.posted[-1]["unrestricted"] is False  # only the caller's key sets it
    client.post("/api/v1/jobs", json={"preset": "360p"}, headers=bearer(keys["admin"]))
    assert stub.posted[-1]["unrestricted"] is True
    wide = mint("wide", tier="unrestricted", expires_days=1)
    client.post("/api/v1/jobs", json={"preset": "360p"}, headers=bearer(wide))
    assert stub.posted[-1]["unrestricted"] is True
    other = mint("other")
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
        "/api/v1/jobs/check?h=1080&fps=60&ac=opus&adel=dgram&relay=moqx-main",
        headers=bearer(keys["run"]),
    )
    assert r.status_code == 200
    ns = r.json()["plan"][1]
    assert ns.endswith("_00/clock/msf/loc/h264/1080/60/opus/2/catalog/2000/dgram")


def test_namespace_prefix_is_enforced(client, keys):
    ns = "elsewhere/x/moq_test_tracks_00/clock/msf/loc/h264/360/30/aac/2"
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
    assert ns.startswith("openmoq/mtt/aiomoqt/nasa_tv/moq_test_tracks_00/clip/")
    assert client.post("/api/v1/jobs", json={"namespace": ns}, headers=run).status_code == 201
    assert client.post("/api/v1/jobs", json={"clip": "nasa_tv"}, headers=run).status_code == 201
    for bad, text in (
        ({"clip": "bogus"}, "unknown clip bogus"),
        ({"params": {"content": "clip"}}, "needs clip=<id>"),
        ({"clip": "nasa_tv", "params": {"content": "bars"}}, "needs content clip"),
        ({"namespace": ns.replace("/nasa_tv/", "/bogus/")}, "unknown clip bogus"),
        ({"namespace": ns.replace("_00/clip/", "_00/bars/")}, "prefix must be openmoq/mtt/aiomoqt"),
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
    assert viewer.headers["x-webauth-user"] == f"mtt-{token_id(keys['run'])}"
    assert viewer.headers["x-webauth-role"] == "Viewer"
    assert viewer.headers["x-webauth-name"] == "run-user"
    admin = client.get("/auth/grafana", headers=bearer(keys["admin"]))
    assert admin.headers["x-webauth-role"] == "Admin"
    odd = mint("a@b c", "view")
    assert client.get("/auth/grafana", headers=bearer(odd)).headers["x-webauth-name"] == "a_b c"
    post = {"X-Forwarded-Method": "POST"}
    assert client.get("/auth/grafana", headers={**bearer(keys["run"]), **post}).status_code == 200


def test_grafana_cookie_needs_origin_for_unsafe_methods(client, keys):
    client.post("/login", json={"token": keys["run"]}, headers={"Origin": ORIGIN})
    assert client.get("/auth/grafana").status_code == 200
    post = {"X-Forwarded-Method": "POST"}
    assert client.get("/auth/grafana", headers=post).status_code == 403
    assert (
        client.get("/auth/grafana", headers={**post, "Origin": "https://evil"}).status_code == 403
    )
    assert client.get("/auth/grafana", headers={**post, "Origin": ORIGIN}).status_code == 200
    client.cookies.set("mtt_session", "forged.0.x")
    for _ in range(12):  # stale or forged sessions don't count toward the lockout
        assert client.get("/auth/grafana").status_code == 401
    client.cookies.clear()
    assert client.get("/auth/grafana", headers=bearer(keys["run"])).status_code == 200


def test_login_session_and_origin(client, keys):
    assert client.post("/login", json={"token": keys["run"]}).status_code == 403  # no Origin
    r = client.post("/login", json={"token": keys["run"]}, headers={"Origin": ORIGIN})
    assert r.status_code == 200 and "mtt_session" in r.cookies
    assert r.json()["token_id"] == token_id(keys["run"])
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
        r = client.post("/login", json={"token": "mtt1_nope"}, headers={"Origin": ORIGIN})
        assert r.status_code == 401
    r = client.post("/login", json={"token": keys["run"]}, headers={"Origin": ORIGIN})
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
        runner_url="http://runner",
        runner_token="t",
        token_secret=TOKEN_SECRET,
        session_secret=SECRET,
        db_path=tmp_path / "a.sqlite",
        public_origins=(ORIGIN,),
        trust_proxy=False,
        secure_cookies=False,
    )
    app = create_app(config.load(ROOT), settings, transport=httpx.MockTransport(down))
    key = mint("k", "view")
    with TestClient(app) as c:
        assert c.get("/healthz").json()["runner"] is False
        assert c.get("/api/v1/jobs", headers=bearer(key)).status_code == 503


def test_session_ttl_is_bounded():
    assert auth.SESSION_TTL_S <= 24 * 3600
