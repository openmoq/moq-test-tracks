# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# Public API and dashboard. Authenticates callers, applies roles and the
# namespace prefix rule, and relays job calls to the runner agent.

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..config import Config
from ..spec.fields import NamespaceError
from ..spec.namespace import decode, encode, from_text, render, to_slash
from . import auth, presets
from .auth import LoginLimiter, Principal, Sessions
from .db import Db

STATIC = Path(__file__).parent / "static"
COOKIE = "mtt_session"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


@dataclass(frozen=True)
class Settings:
    runner_url: str
    runner_token: str
    session_secret: str
    db_path: Path
    public_origins: tuple[str, ...]
    trust_proxy: bool = True
    secure_cookies: bool = True


def _errors(e: Exception) -> list[str]:
    if isinstance(e, NamespaceError):
        return [str(err) for err in e.errors]
    return [str(e)]


def create_app(
    cfg: Config, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    db = Db(settings.db_path)
    sessions = Sessions(settings.session_secret, db)
    limiter = LoginLimiter()
    runner = httpx.AsyncClient(
        base_url=settings.runner_url,
        headers={"Authorization": f"Bearer {settings.runner_token}"},
        transport=transport,
        timeout=httpx.Timeout(30, read=None),
    )
    app = FastAPI(title="moq-test-tracks", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db = db

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response

    def client_ip(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if settings.trust_proxy and forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "?"

    def authenticate(request: Request) -> tuple[Principal | None, str | None]:
        header = request.headers.get("authorization", "")
        if header.startswith("Bearer "):
            return auth.verify(db, header[len("Bearer ") :].strip()), "key"
        cookie = request.cookies.get(COOKIE)
        if cookie:
            return sessions.verify(cookie), "cookie"
        return None, None

    def require(role: str):
        def dependency(request: Request) -> Principal:
            ip = client_ip(request)
            if limiter.blocked(ip):
                raise HTTPException(429, {"errors": ["too many failed authentications"]})
            principal, via = authenticate(request)
            if principal is None:
                if via is not None:
                    limiter.fail(ip)
                raise HTTPException(401, {"errors": ["authentication required"]})
            if via == "cookie" and request.method not in SAFE_METHODS:
                if request.headers.get("origin") not in settings.public_origins:
                    raise HTTPException(403, {"errors": ["cross-origin request refused"]})
            if not principal.can(role):
                raise HTTPException(403, {"errors": [f"needs the {role} role"]})
            return principal

        return dependency

    as_view = Depends(require("view"))
    as_run = Depends(require("run"))
    as_admin = Depends(require("admin"))

    async def call_runner(method: str, path: str, **kw) -> dict | list:
        try:
            r = await runner.request(method, path, **kw)
        except httpx.HTTPError:
            raise HTTPException(503, {"errors": ["the runner is unreachable"]}) from None
        body = r.json() if r.content else {}
        if r.status_code >= 400:
            detail = body.get("detail", body) if isinstance(body, dict) else body
            raise HTTPException(r.status_code, detail)
        return body

    def owners() -> dict[str, str]:
        return {k["id"]: k["name"] for k in auth.list_keys(db)}

    def named(job: dict, names: dict[str, str]) -> dict:
        return {**job, "owner_name": names.get(job.get("owner") or "", job.get("owner"))}

    def resolve_namespace(params: dict, preset: str | None, tool: str, clip: str | None):
        """Namespace from a preset and parameters; clip=<id> selects that clip's content."""
        if clip is not None:
            if clip not in cfg.clips:
                raise ValueError(f"unknown clip {clip}")
            if params.get("content", "clip") != "clip":
                raise ValueError("clip=<id> needs content clip")
            params = {**params, "content": "clip"}
        spec = presets.resolve(params, preset)
        if spec.content == "clip" and clip is None:
            raise ValueError("content clip needs clip=<id>")
        return encode(spec, cfg.prefix(tool, clip)), spec

    def check_prefix(decoded, tool: str) -> None:
        """The policy prefix for the tool, plus a configured clip id for clip content."""
        prefix = tuple(f.decode(errors="replace") for f in decoded.prefix)
        base = cfg.prefix(tool)
        if getattr(decoded.spec, "content", None) == "clip":
            clip = prefix[-1] if len(prefix) == len(base) + 1 else None
            if prefix[: len(base)] != base or clip is None:
                raise ValueError(f"the namespace prefix must be {'/'.join(base)}/<clip id>")
            if clip not in cfg.clips:
                raise ValueError(f"unknown clip {clip}")
        elif prefix != base:
            raise ValueError(f"the namespace prefix must be {'/'.join(base)}")

    async def job_body(request: Request) -> dict:
        """Runner request from a JSON body or query parameters: a namespace, or
        a preset and parameters resolved under the policy prefix for the tool."""
        data: dict = {}
        if request.headers.get("content-type", "").startswith("application/json"):
            data = await request.json()
            if not isinstance(data, dict):
                raise HTTPException(422, {"errors": ["expected a JSON object"]})
        query = dict(request.query_params)
        params = {
            **{k: v for k, v in query.items() if k in presets.PARAMS},
            **data.get("params", {}),
        }
        tool = data.get("tool") or query.get("tool") or "aiomoqt"
        ns_text = data.get("namespace") or query.get("ns")
        try:
            if ns_text:
                namespace = from_text(ns_text)
                check_prefix(decode(namespace), tool)
            else:
                preset = data.get("preset") or query.get("preset")
                clip = data.get("clip") or query.get("clip") or None
                namespace, _ = resolve_namespace(params, preset, tool, clip)
        except (ValueError, NamespaceError) as e:
            raise HTTPException(422, {"errors": _errors(e)}) from None

        def pick(name: str, cast=str):
            value = data.get(name, query.get(name))
            return cast(value) if value not in (None, "") else None

        try:
            body = {
                "namespace": to_slash(namespace),
                "tool": tool,
                "relay": pick("relay") or cfg.default_relay,
                "transport": pick("transport") or "moqt",
                "mode": pick("mode") or "publish",
                "draft": pick("draft", int),
                "ttl_s": pick("ttl_s", int) or pick("ttl", int),
            }
        except ValueError as e:
            raise HTTPException(422, {"errors": [str(e)]}) from None
        return body

    @app.get("/healthz")
    async def healthz() -> dict:
        try:
            ok = (await runner.get("/healthz")).status_code == 200
        except httpx.HTTPError:
            ok = False
        return {"ok": True, "runner": ok}

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.post("/login")
    async def login(request: Request) -> Response:
        ip = client_ip(request)
        if limiter.blocked(ip):
            raise HTTPException(429, {"errors": ["too many failed authentications"]})
        if request.headers.get("origin") not in settings.public_origins:
            raise HTTPException(403, {"errors": ["cross-origin request refused"]})
        try:
            key = (await request.json()).get("key", "")
        except (json.JSONDecodeError, AttributeError):
            key = ""
        principal = auth.verify(db, key) if isinstance(key, str) else None
        if principal is None:
            limiter.fail(ip)
            db.audit("login_failed", None, ip)
            raise HTTPException(401, {"errors": ["invalid key"]})
        db.audit("login", principal.key_id, ip)
        response = JSONResponse(
            {"key_id": principal.key_id, "name": principal.name, "role": principal.role}
        )
        response.set_cookie(
            COOKIE,
            sessions.issue(principal),
            max_age=auth.SESSION_TTL_S,
            httponly=True,
            secure=settings.secure_cookies,
            samesite="strict",
            path="/",
        )
        return response

    @app.post("/logout")
    async def logout() -> Response:
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/")
        return response

    @app.get("/api/v1/me")
    async def me(p: Principal = as_view) -> dict:
        return {"key_id": p.key_id, "name": p.name, "role": p.role}

    @app.get("/api/v1/presets")
    async def list_presets() -> dict:
        return {"presets": presets.PRESETS, "defaults": presets.DEFAULTS, "params": presets.PARAMS}

    @app.get("/api/v1/namespace")
    async def namespace(request: Request) -> dict:
        query = dict(request.query_params)
        params = {k: v for k, v in query.items() if k in presets.PARAMS}
        tool = query.get("tool", "aiomoqt")
        try:
            ns, spec = resolve_namespace(
                params, query.get("preset"), tool, query.get("clip") or None
            )
        except (ValueError, NamespaceError) as e:
            raise HTTPException(422, {"errors": _errors(e)}) from None
        return {
            "namespace": to_slash(ns),
            "namespace_safe": render(ns),
            "tracks": list(spec.tracks),
        }

    @app.get("/api/v1/namespace/decode")
    async def namespace_decode(ns: str) -> dict:
        try:
            decoded = decode(from_text(ns))
        except (ValueError, NamespaceError) as e:
            raise HTTPException(422, {"errors": _errors(e)}) from None
        fields = {k: getattr(decoded.spec, k) for k in decoded.spec.__dataclass_fields__}
        return {"protocol": decoded.protocol, "fields": fields}

    @app.get("/api/v1/tools")
    async def tools(p: Principal = as_view) -> list[dict]:
        return [
            {"id": t.id, "name": t.name, "version": t.version, "capabilities": t.capabilities}
            for t in cfg.tools.values()
        ]

    @app.get("/api/v1/clips")
    async def clips(p: Principal = as_view) -> list[dict]:
        return await call_runner("GET", "/clips")

    @app.get("/api/v1/relays")
    async def relays(p: Principal = as_view) -> dict:
        return {
            "default": cfg.default_relay,
            "relays": [
                {"id": r.id, "name": r.name, "endpoints": r.endpoints, "drafts": list(r.drafts)}
                for r in cfg.relays.values()
            ],
        }

    @app.post("/api/v1/jobs/check")
    async def check(request: Request, p: Principal = as_run) -> dict:
        body = {**await job_body(request), "unrestricted": p.exempt}
        return await call_runner("POST", "/check", json=body)

    @app.post("/api/v1/jobs", status_code=201)
    async def start(request: Request, p: Principal = as_run) -> dict:
        body = {**await job_body(request), "owner": p.key_id, "unrestricted": p.exempt}
        job = await call_runner("POST", "/jobs", json=body)
        action = "job_joined" if job.get("joined") else "job_started"
        db.audit(action, p.key_id, client_ip(request), f"{job['id']} {job['namespace']}")
        return named(job, owners())

    @app.get("/api/v1/jobs")
    async def jobs(active: bool = False, p: Principal = as_view) -> list[dict]:
        names = owners()
        rows = await call_runner("GET", "/jobs", params={"active": str(active).lower()})
        return [named(j, names) for j in rows]

    @app.get("/api/v1/jobs/{job_id}")
    async def job(job_id: str, p: Principal = as_view) -> dict:
        return named(await call_runner("GET", f"/jobs/{job_id}"), owners())

    @app.delete("/api/v1/jobs/{job_id}")
    async def stop(
        job_id: str, request: Request, force: bool = False, p: Principal = as_run
    ) -> dict:
        current = await call_runner("GET", f"/jobs/{job_id}")
        if current.get("owner") != p.key_id and not p.can("admin"):
            raise HTTPException(403, {"errors": ["only the job's owner or an admin can stop it"]})
        result = await call_runner(
            "DELETE", f"/jobs/{job_id}", params={"force": str(force).lower()}
        )
        db.audit("job_killed" if force else "job_stopped", p.key_id, client_ip(request), job_id)
        return named(result, owners())

    @app.post("/api/v1/admin/stop-all")
    async def stop_all(request: Request, p: Principal = as_admin) -> dict:
        result = await call_runner("POST", "/stop-all")
        db.audit("stop_all", p.key_id, client_ip(request), str(result.get("stopped")))
        return result

    @app.get("/api/v1/jobs/{job_id}/events")
    async def events(job_id: str, since: int = 0, p: Principal = as_view) -> StreamingResponse:
        req = runner.build_request("GET", f"/jobs/{job_id}/events", params={"since": since})
        try:
            upstream = await runner.send(req, stream=True)
        except httpx.HTTPError:
            raise HTTPException(503, {"errors": ["the runner is unreachable"]}) from None
        if upstream.status_code != 200:
            await upstream.aclose()
            raise HTTPException(upstream.status_code, {"errors": [f"no job {job_id}"]})

        async def relay_stream():
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            finally:
                await upstream.aclose()

        return StreamingResponse(
            relay_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app
