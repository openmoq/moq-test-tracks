# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# Runner agent: the internal HTTP API the api service drives. It validates
# every job again, joins duplicate requests, enforces the policy caps, runs
# jobs with the supervisor, and streams their events.

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import secrets
import shutil
import signal
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..config import Config, job_errors
from ..spec.fields import NamespaceError, TrackSpec
from ..spec.namespace import Namespace, decode, from_text, render, to_slash
from .request import build as build_request
from .supervisor import CheckResult, Job, JobError

ACTIVE = ("requested", "checked", "starting", "running", "stopping")
EVENT_BACKLOG = 300
FINISHED_KEEP = 100
HEARTBEAT_S = 15
LISTENER_QUEUE = 1000


class JobIn(BaseModel):
    namespace: str
    tool: str
    relay: str
    transport: str = "moqt"
    draft: int | None = None
    mode: str = "publish"
    ttl_s: int | None = None
    owner: str | None = None
    admin: bool = False  # set by the api service from the caller's role


class Rejected(Exception):
    def __init__(self, status: int, errors: list[str]):
        self.status = status
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass
class Entry:
    job: Job
    namespace: Namespace
    relay: str
    owner: str | None
    created: float
    check: CheckResult
    exit_code: int | None = None
    error: str | None = None
    last_stats: dict | None = None
    backlog: deque = field(default_factory=lambda: deque(maxlen=EVENT_BACKLOG))
    listeners: set = field(default_factory=set)
    task: asyncio.Task | None = None
    seq: int = 0
    last_state: str = ""

    @property
    def id(self) -> str:
        return self.job.request["job_id"]

    @property
    def active(self) -> bool:
        return self.job.state in ACTIVE

    def publish(self, kind: str, data: object) -> None:
        self.seq += 1
        item = {"seq": self.seq, "kind": kind, "data": data, "time": time.time()}
        self.backlog.append(item)
        for q in list(self.listeners):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(item)

    def sync_state(self) -> None:
        if self.job.state != self.last_state:
            self.last_state = self.job.state
            self.publish("state", {"state": self.job.state, "exit_code": self.exit_code})

    def summary(self) -> dict:
        req = self.job.request
        endpoint = req["endpoints"][0]
        return {
            "id": self.id,
            "state": self.job.state,
            "namespace": to_slash(self.namespace),
            "namespace_safe": render(self.namespace),
            "tracks": req["tracks"],
            "tool": self.job.tool.id,
            "relay": self.relay,
            "url": endpoint["url"],
            "transport": endpoint["transport"],
            "draft": endpoint["draft"],
            "mode": req["mode"],
            "ttl_s": req["ttl_s"],
            "created": self.created,
            "expires": self.created + req["ttl_s"],
            "owner": self.owner,
            "plan": self.check.argv,
            "source": self.check.source,
            "exit_code": self.exit_code,
            "error": self.error,
            "last_stats": self.last_stats,
        }


class Registry:
    def __init__(self, cfg: Config, work: Path, cache: Path, nice: int = 0):
        self.cfg = cfg
        self.work = work
        self.cache = cache
        self.nice = nice
        self.jobs: dict[str, Entry] = {}

    def active(self) -> list[Entry]:
        return [e for e in self.jobs.values() if e.active]

    def _find(self, relay: str, namespace: Namespace) -> Entry | None:
        for e in self.active():
            if e.relay == relay and e.namespace == namespace:
                return e
        return None

    def _validate(self, body: JobIn):
        cfg = self.cfg
        tool = cfg.tools.get(body.tool)
        if tool is None:
            raise Rejected(422, [f"unknown or disabled tool {body.tool}"])
        relay = cfg.relays.get(body.relay)
        if relay is None:
            raise Rejected(422, [f"relay {body.relay} is not in the allowlist"])
        try:
            namespace = from_text(body.namespace)
            decoded = decode(namespace)
        except NamespaceError as e:
            raise Rejected(422, [str(err) for err in e.errors]) from None
        except ValueError as e:
            raise Rejected(422, [str(e)]) from None
        if not isinstance(decoded.spec, TrackSpec):
            raise Rejected(422, [f"{decoded.protocol} jobs are not supported yet"])
        draft = body.draft or relay.default_draft
        ttl = body.ttl_s or cfg.policy.default_ttl_s
        errors = job_errors(
            tool, relay, decoded.spec, mode=body.mode, transport=body.transport, draft=draft,
            ttl_s=ttl, policy=cfg.policy, admin=body.admin,
        )  # fmt: skip
        if errors:
            raise Rejected(422, errors)
        return tool, relay, namespace, decoded.spec, draft, ttl

    def _job(self, body, tool, relay, namespace, spec, draft, ttl, work: Path) -> Job:
        req = build_request(
            f"j_{secrets.token_hex(6)}", namespace, spec, relay,
            transport=body.transport, draft=draft, mode=body.mode, ttl_s=ttl,
        )  # fmt: skip
        return Job(tool, req, spec, work, self.cache, nice=self.nice)

    @staticmethod
    async def _adapter_check(job: Job) -> CheckResult:
        try:
            check = await job.check()
        except JobError as e:
            raise Rejected(500, [str(e)]) from None
        if not check.accept:
            reasons = [f"{r.get('field')}: {r.get('message')}" for r in check.reasons]
            raise Rejected(422, reasons or ["refused by the adapter"])
        return check

    async def check(self, body: JobIn) -> dict:
        """Validate and ask the adapter, without starting anything."""
        tool, relay, namespace, spec, draft, ttl = self._validate(body)
        existing = self._find(relay.id, namespace)
        job = self._job(body, tool, relay, namespace, spec, draft, ttl, self.work / ".check")
        try:
            check = await self._adapter_check(job)
        finally:
            shutil.rmtree(job.dir, ignore_errors=True)
        return {
            "accept": True,
            "source": check.source,
            "plan": check.argv,
            "notes": check.notes,
            "namespace": to_slash(namespace),
            "namespace_safe": render(namespace),
            "tracks": job.request["tracks"],
            "joins": existing.id if existing else None,
        }

    async def submit(self, body: JobIn) -> tuple[Entry, bool]:
        """Start a job, or return the active one with the same relay and namespace."""
        cfg = self.cfg
        tool, relay, namespace, spec, draft, ttl = self._validate(body)
        existing = self._find(relay.id, namespace)
        if existing is not None:
            return existing, False
        active = self.active()
        if len(active) >= cfg.policy.max_jobs:
            raise Rejected(429, [f"the runner is at its limit of {cfg.policy.max_jobs} jobs"])
        if sum(1 for e in active if e.job.tool.id == tool.id) >= tool.max_jobs:
            raise Rejected(429, [f"{tool.id} is at its limit of {tool.max_jobs} jobs"])
        if body.owner is not None and not body.admin:
            limit = cfg.policy.key_max_jobs
            if sum(1 for e in active if e.owner == body.owner) >= limit:
                raise Rejected(429, [f"this key is at its limit of {limit} jobs"])
        job = self._job(body, tool, relay, namespace, spec, draft, ttl, self.work)
        check = await self._adapter_check(job)
        live = sum(1 for e in active if e.check.source == "mpegts")
        if check.source == "mpegts" and live >= cfg.policy.max_live_encodes:
            limit = cfg.policy.max_live_encodes
            raise Rejected(429, [f"the runner is at its limit of {limit} live encodes"])
        entry = Entry(job, namespace, relay.id, body.owner, time.time(), check)
        self.jobs[entry.id] = entry
        entry.sync_state()
        entry.task = asyncio.create_task(self._run(entry))
        self._prune()
        return entry, True

    async def _run(self, entry: Entry) -> None:
        def on_event(event: dict) -> None:
            if event.get("event") == "stats":
                entry.last_stats = event
            entry.publish("event", event)
            entry.sync_state()

        def on_log(line: str) -> None:
            entry.publish("log", line)

        try:
            entry.exit_code = await entry.job.run(entry.check, on_event, on_log)
        except Exception as e:  # any failure ends the job, never the agent
            entry.error = str(e)
            entry.job.state = "failed"
        entry.sync_state()

    def stop(self, job_id: str, force: bool = False) -> Entry:
        entry = self.jobs.get(job_id)
        if entry is None:
            raise Rejected(404, [f"no job {job_id}"])
        if entry.active:
            entry.job.kill() if force else entry.job.stop()
            entry.sync_state()
        return entry

    def stop_all(self) -> int:
        active = self.active()
        for e in active:
            e.job.stop()
            e.sync_state()
        return len(active)

    def _prune(self) -> None:
        finished = [e for e in self.jobs.values() if not e.active]
        for e in sorted(finished, key=lambda e: e.created)[:-FINISHED_KEEP]:
            del self.jobs[e.id]


def sweep_orphans(work: Path) -> int:
    """Kill process groups left by an earlier agent, but only those whose
    command line or environment still names their job directory."""
    killed = 0
    for pgids in work.glob("*/pgids"):
        marker = str(pgids.parent).encode()
        for token in pgids.read_text().split():
            pid = int(token)
            try:
                proc = Path(f"/proc/{pid}")
                owned = (
                    marker in (proc / "cmdline").read_bytes()
                    or marker in (proc / "environ").read_bytes()
                )
            except OSError:
                continue
            if owned:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(pid, signal.SIGKILL)
                    killed += 1
        pgids.unlink()
    return killed


def _sse(item: dict) -> str:
    return f"id: {item['seq']}\nevent: {item['kind']}\ndata: {json.dumps(item['data'])}\n\n"


def create_app(cfg: Config, *, token: str, work: Path, cache: Path, nice: int = 0) -> FastAPI:
    if not token:
        raise ValueError("the runner needs a token (MTT_RUNNER_TOKEN)")
    registry = Registry(cfg, work, cache, nice)
    expected = f"Bearer {token}".encode()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        work.mkdir(parents=True, exist_ok=True)
        sweep_orphans(work)
        yield
        registry.stop_all()

    app = FastAPI(title="mtt runner", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.registry = registry

    def auth(request: Request) -> None:
        given = request.headers.get("authorization", "").encode()
        if not hmac.compare_digest(given, expected):
            raise HTTPException(401, "bad runner token")

    def reject(e: Rejected) -> HTTPException:
        return HTTPException(e.status, {"errors": e.errors})

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True, "active": len(registry.active())}

    @app.post("/jobs", status_code=201, dependencies=[Depends(auth)])
    async def submit(body: JobIn) -> dict:
        try:
            entry, created = await registry.submit(body)
        except Rejected as e:
            raise reject(e) from None
        return {**entry.summary(), "joined": not created}

    @app.post("/check", dependencies=[Depends(auth)])
    async def check(body: JobIn) -> dict:
        try:
            return await registry.check(body)
        except Rejected as e:
            raise reject(e) from None

    @app.get("/jobs", dependencies=[Depends(auth)])
    async def list_jobs(active: bool = False) -> list[dict]:
        entries = registry.active() if active else list(registry.jobs.values())
        return [e.summary() for e in sorted(entries, key=lambda e: e.created, reverse=True)]

    @app.get("/jobs/{job_id}", dependencies=[Depends(auth)])
    async def get_job(job_id: str) -> dict:
        entry = registry.jobs.get(job_id)
        if entry is None:
            raise HTTPException(404, {"errors": [f"no job {job_id}"]})
        return entry.summary()

    @app.delete("/jobs/{job_id}", dependencies=[Depends(auth)])
    async def stop_job(job_id: str, force: bool = False) -> dict:
        try:
            return registry.stop(job_id, force).summary()
        except Rejected as e:
            raise reject(e) from None

    @app.post("/stop-all", dependencies=[Depends(auth)])
    async def stop_all() -> dict:
        return {"stopped": registry.stop_all()}

    @app.get("/jobs/{job_id}/events", dependencies=[Depends(auth)])
    async def events(job_id: str, request: Request, since: int = 0) -> StreamingResponse:
        entry = registry.jobs.get(job_id)
        if entry is None:
            raise HTTPException(404, {"errors": [f"no job {job_id}"]})
        queue: asyncio.Queue = asyncio.Queue(LISTENER_QUEUE)
        entry.listeners.add(queue)

        async def stream():
            sent = since
            try:
                for item in list(entry.backlog):
                    if item["seq"] > sent:
                        sent = item["seq"]
                        yield _sse(item)
                while entry.active or not queue.empty():
                    try:
                        item = await asyncio.wait_for(queue.get(), HEARTBEAT_S)
                    except TimeoutError:
                        yield ": ping\n\n"
                        if await request.is_disconnected():
                            return
                        continue
                    if item["seq"] > sent:
                        sent = item["seq"]
                        yield _sse(item)
            finally:
                entry.listeners.discard(queue)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    return app
