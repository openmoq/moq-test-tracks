# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# Runs one job per the tool contract: check mode, the source stage, the
# adapter in its own process group with a reset environment, @mtt events,
# TTL, and signal-then-SIGKILL teardown.

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import signal
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..config import CONTRACT_VERSION, Tool
from ..content import recipes
from ..spec.fields import TrackSpec

EXIT_OK = 0
EXIT_REFUSED = 64
EXIT_UNAVAILABLE = 69
EXIT_INTERNAL = 70

CHECK_TIMEOUT_S = 30
SOURCE_GRACE_S = 2
EVENT_PREFIX = "@mtt "

# One render per cache key at a time; jobs that need the same loop wait for it.
_RENDERS: dict[str, asyncio.Lock] = {}


class JobError(RuntimeError):
    pass


@dataclass
class CheckResult:
    accept: bool
    source: str | None = None
    argv: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    reasons: list[dict] = field(default_factory=list)


def _killpg(pid: int, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, sig)


def mp4_complete(path: Path) -> bool:
    """True if the top-level boxes span the file exactly and include ftyp and moov."""
    try:
        size = path.stat().st_size
        seen, pos = set(), 0
        with path.open("rb") as f:
            while pos < size:
                f.seek(pos)
                head = f.read(16)
                if len(head) < 8:
                    return False
                box = int.from_bytes(head[:4], "big")
                if box == 1:
                    if len(head) < 16:
                        return False
                    box = int.from_bytes(head[8:16], "big")
                elif box == 0:
                    box = size - pos
                if box < 8:
                    return False
                seen.add(head[4:8])
                pos += box
        return pos == size and {b"ftyp", b"moov"} <= seen
    except OSError:
        return False


class Job:
    def __init__(
        self,
        tool: Tool,
        request: dict,
        spec: TrackSpec,
        work_dir: Path,
        cache_dir: Path,
        nice: int = 0,
    ):
        self.tool = tool
        self.nice = nice
        self.request = request
        self.spec = spec
        self.dir = work_dir / request["job_id"]
        self.cache_dir = cache_dir
        self.state = "requested"
        self.expired = False
        self._adapter: asyncio.subprocess.Process | None = None
        self._source: asyncio.subprocess.Process | None = None
        self._stopping = False
        self.dir.mkdir(parents=True, exist_ok=True)

    @property
    def request_file(self) -> Path:
        return self.dir / "request.json"

    def env(self, mode: str) -> dict[str, str]:
        return {
            "MTT_CONTRACT_VERSION": str(CONTRACT_VERSION),
            "MTT_MODE": mode,
            "MTT_REQUEST_FILE": str(self.request_file),
            "MTT_JOB_DIR": str(self.dir),
            "PATH": f"{Path(sys.executable).parent}:/usr/local/bin:/usr/bin:/bin",
            "HOME": str(self.dir),
            "LANG": "C.UTF-8",
        }

    def _preexec(self) -> None:
        if self.nice:
            os.nice(self.nice)

    def _record(self, proc: asyncio.subprocess.Process) -> None:
        with (self.dir / "pgids").open("a") as f:
            f.write(f"{proc.pid}\n")

    def _write_request(self) -> None:
        self.request_file.write_text(json.dumps(self.request, indent=2))

    async def check(self) -> CheckResult:
        self.request["source"] = None
        self._write_request()
        proc = await asyncio.create_subprocess_exec(
            str(self.tool.adapter),
            cwd=self.dir,
            env=self.env("check"),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            preexec_fn=self._preexec,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), CHECK_TIMEOUT_S)
        except TimeoutError:
            _killpg(proc.pid, signal.SIGKILL)
            raise JobError("adapter check timed out") from None
        if proc.returncode not in (EXIT_OK, EXIT_REFUSED):
            raise JobError(f"adapter check exited {proc.returncode}: {err.decode().strip()}")
        try:
            plan = json.loads(out)
        except json.JSONDecodeError:
            raise JobError(f"adapter check printed no JSON: {out.decode()[:200]}") from None
        result = CheckResult(
            accept=proc.returncode == EXIT_OK and bool(plan.get("accept")),
            source=plan.get("source"),
            argv=plan.get("argv", []),
            notes=plan.get("notes", []),
            reasons=plan.get("reasons", []),
        )
        if result.accept and result.source not in (None, *self.tool.sources):
            raise JobError(f"adapter asked for source {result.source}, not in its manifest")
        self.state = "checked"
        return result

    async def _prepare_source(self, fmt: str | None) -> None:
        if fmt is None:
            self.request["source"] = None
            return
        if fmt == "mpegts":
            fifo = self.dir / "source.ts"
            with contextlib.suppress(FileNotFoundError):
                fifo.unlink()
            os.mkfifo(fifo)
            argv = recipes.source_command(self.spec, "mpegts", fifo)
            log = (self.dir / "ffmpeg.log").open("ab")
            self._source = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=log,
                start_new_session=True,
                preexec_fn=self._preexec,
            )
            self._record(self._source)
            self.request["source"] = {"format": fmt, "path": str(fifo), "fifo": True, "loop": False}
            return
        if fmt == "mp4":
            key = hashlib.sha256(
                json.dumps(recipes.source_command(self.spec, "mp4", "OUT")).encode()
            ).hexdigest()[:24]
            path = self.cache_dir / f"{key}.mp4"
            async with _RENDERS.setdefault(key, asyncio.Lock()):
                if not mp4_complete(path):
                    await self._render_mp4(path)
            self.request["source"] = {"format": fmt, "path": str(path), "fifo": False, "loop": True}
            return
        raise JobError(f"no source stage for format {fmt}")

    async def _render_mp4(self, path: Path) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.stem}.{self.request['job_id']}.part.mp4")
        try:
            proc = await asyncio.create_subprocess_exec(
                *recipes.source_command(self.spec, "mp4", tmp),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                preexec_fn=self._preexec,
            )
            _, err = await proc.communicate()
            if proc.returncode != 0:
                raise JobError(f"rendering the mp4 loop failed: {err.decode().strip()[-300:]}")
            if not mp4_complete(tmp):
                raise JobError("rendering the mp4 loop produced an incomplete file")
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)

    async def _stop_source(self) -> None:
        if self._source is None or self._source.returncode is not None:
            return
        _killpg(self._source.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(self._source.wait(), SOURCE_GRACE_S)
        except TimeoutError:
            _killpg(self._source.pid, signal.SIGKILL)
            await self._source.wait()

    def stop(self) -> None:
        """Stop signal to the adapter's process group, SIGKILL after the grace period."""
        proc = self._adapter
        if proc is None or proc.returncode is not None or self._stopping:
            return
        self._stopping = True
        self.state = "stopping"
        _killpg(proc.pid, self.tool.stop_signal)
        asyncio.get_running_loop().call_later(self.tool.grace_s, self._kill)

    def kill(self) -> None:
        """SIGKILL the adapter's process group now."""
        self._stopping = True
        self._kill()

    def _kill(self) -> None:
        if self._adapter is not None and self._adapter.returncode is None:
            _killpg(self._adapter.pid, signal.SIGKILL)

    def _expire(self) -> None:
        self.expired = True
        self.stop()

    async def run(
        self,
        check: CheckResult,
        on_event: Callable[[dict], None],
        on_log: Callable[[str], None],
    ) -> int:
        self.state = "starting"
        await self._prepare_source(check.source)
        self._write_request()
        self._adapter = await asyncio.create_subprocess_exec(
            str(self.tool.adapter),
            cwd=self.dir,
            env=self.env("run"),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            preexec_fn=self._preexec,
        )
        self._record(self._adapter)
        ttl = asyncio.get_running_loop().call_later(self.request["ttl_s"], self._expire)
        log = (self.dir / "log").open("a")

        def logline(line: str) -> None:
            log.write(line + "\n")
            on_log(line)

        adapter = self._adapter

        async def read_stdout() -> None:
            async for raw in adapter.stdout:
                line = raw.decode(errors="replace").rstrip("\n")
                if not line.startswith(EVENT_PREFIX):
                    logline(line)
                    continue
                try:
                    event = json.loads(line[len(EVENT_PREFIX) :])
                except json.JSONDecodeError:
                    logline(line)
                    continue
                if event.get("event") == "ready" and self.state == "starting":
                    self.state = "running"
                on_event(event)

        async def read_stderr() -> None:
            async for raw in adapter.stderr:
                logline(raw.decode(errors="replace").rstrip("\n"))

        async def watch_source() -> None:
            if self._source is None:
                return
            rc = await self._source.wait()
            # The tool closing its input at the end of a run also ends the source.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(adapter.wait(), SOURCE_GRACE_S)
                return
            if not self._stopping:
                logline(f"source stage exited {rc}; stopping")
                self.stop()

        watcher = asyncio.create_task(watch_source())
        try:
            await asyncio.gather(read_stdout(), read_stderr())
            rc = await adapter.wait()
        finally:
            ttl.cancel()
            watcher.cancel()
            await self._stop_source()
            log.close()
        return self._finish(rc)

    def _finish(self, rc: int) -> int:
        killed = self._stopping and rc == -signal.SIGKILL
        if rc < 0:
            rc = 0 if self._stopping and -rc == self.tool.stop_signal else 128 - rc
        if killed:
            self.state = "killed"
        elif self.expired and rc == EXIT_OK:
            self.state = "expired"
        else:
            self.state = "ended" if rc == EXIT_OK else "failed"
        return rc
