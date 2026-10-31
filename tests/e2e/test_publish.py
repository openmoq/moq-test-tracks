# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# End to end: real `mtt run` jobs (adapter, ffmpeg, pub_media) through a local
# aiomoqt-relay, checked by a subscriber. Opt in with MTT_E2E=1 where aiomoqt,
# ffmpeg and openssl are installed; ci-main.yml runs it in the service image.
# The relay's self-signed cert is trusted by appending it to certifi's bundle
# for the run, which is restored afterwards.

import asyncio
import dataclasses
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from moq_test_tracks import config
from moq_test_tracks.spec.fields import VERSION
from moq_test_tracks.spec.namespace import decode, encode, from_slash, to_slash

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("MTT_E2E") != "1", reason="end to end needs MTT_E2E=1"),
]

DRAFT = 22
DURATION_S = 10
TRACK_ENDED = 2
PREFIX = "openmoq/mtt/aiomoqt"
SHAPES = {
    "loc": "clock/msf/loc/h264/360/30/aac/2",
    "cmaf": "clock/msf/cmaf/h264/360/30/aac/2",
    "locmaf": "clock/msf/locmaf/h264/360/30/aac/2",
    "h265_opus": "clock/msf/loc/h265/360/30/opus/2",
    "dgram_audio": "clock/msf/loc/h264/360/30/aac/2/catalog/2000/dgram",
    "audio_4_per_group": "clock/msf/loc/h264/360/30/aac/2/catalog/2000/4",
    "no_catalog": "clock/none/loc/h264/360/30/aac/2",
}


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        pytest.skip(f"needs {name}")
    return path


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def relay(tmp_path_factory):
    import certifi

    d = tmp_path_factory.mktemp("relay")
    cert, key = d / "cert.pem", d / "key.pem"
    subprocess.run(
        [_tool("openssl"), "req", "-x509", "-newkey", "ec", "-pkeyopt",
         "ec_paramgen_curve:prime256v1", "-nodes", "-keyout", key, "-out", cert, "-days", "1",
         "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost"],
        check=True, capture_output=True,
    )  # fmt: skip
    bundle = Path(certifi.where())
    original = bundle.read_bytes()
    bundle.write_bytes(original + b"\n" + cert.read_bytes())
    port = _free_udp_port()
    log = d / "relay.log"
    with open(log, "w") as out:
        proc = subprocess.Popen(
            [_tool("aiomoqt-relay"), "--bind", "127.0.0.1", "--port", str(port), "--quic",
             "--cert", cert, "--key", key],
            stdout=out, stderr=subprocess.STDOUT,
        )  # fmt: skip
    try:
        deadline = time.monotonic() + 15
        while "Starting MOQT server" not in log.read_text():
            assert proc.poll() is None and time.monotonic() < deadline, log.read_text()
            time.sleep(0.2)
        yield f"moqt://localhost:{port}"
    finally:
        proc.terminate()
        proc.wait(10)
        bundle.write_bytes(original)


@pytest.fixture(scope="module")
def root(tmp_path_factory, relay):
    """A config root whose only relay is the local one."""
    base = config.find_root()
    r = tmp_path_factory.mktemp("root")
    shutil.copytree(base / "config", r / "config")
    (r / "media").mkdir()
    shutil.copy(base / "media" / "sources.yaml", r / "media")
    (r / "adapters").symlink_to(base / "adapters")
    (r / "config" / "relays.yaml").write_text(
        f"default: local\nrelays:\n  local:\n    name: local test relay\n"
        f"    endpoints:\n      moqt: {relay}\n    drafts: [{DRAFT}]\n    default_draft: {DRAFT}\n"
    )
    return r


class Job:
    """`mtt run` in the background, its output collected line by line."""

    def __init__(self, root: Path, namespace: str, work: Path):
        cli = "import sys; from moq_test_tracks.cli import main; sys.exit(main())"
        self.proc = subprocess.Popen(
            [sys.executable, "-c", cli, "run", "--root", str(root), "--ns", namespace,
             "--ttl", "60", "--work", str(work)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )  # fmt: skip
        self.lines: list[str] = []
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        for line in self.proc.stdout:
            self.lines.append(line.rstrip())

    def wait_ready(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while not any(line.startswith("ready:") for line in self.lines):
            assert self.proc.poll() is None, self.output()
            assert time.monotonic() < deadline, f"no ready event\n{self.output()}"
            time.sleep(0.1)

    def finish(self, timeout: float) -> int:
        rc = self.proc.wait(timeout)
        self._reader.join(5)
        return rc

    def output(self) -> str:
        return "\n".join(self.lines[-30:])


@pytest.mark.parametrize("shape", SHAPES)
def test_publish(shape, root, relay, tmp_path, record_property):
    from subscriber import subscribe  # needs aiomoqt, which only e2e runs install

    decoded = decode(from_slash(f"{PREFIX}/{VERSION}/{SHAPES[shape]}"))
    spec = dataclasses.replace(decoded.spec, duration_s=DURATION_S)
    namespace = to_slash(encode(spec, decoded.prefix))
    names = ["video", "audio"] + (["catalog"] if spec.catalog != "none" else [])

    job = Job(root, namespace, tmp_path / "jobs")
    try:
        job.wait_ready(60)
        tracks = asyncio.run(
            subscribe(relay, namespace, names, draft=DRAFT, timeout=DURATION_S + 20)
        )
        rc = job.finish(30)
    finally:
        if job.proc.poll() is None:
            job.proc.kill()
    video, audio = tracks["video"], tracks["audio"]
    ended = {t.name: "✓" if t.end_of_track else "-" for t in tracks.values()}
    record_property("namespace", namespace.removeprefix(PREFIX + "/"))
    record_property("video", f"{video.objects} obj, groups {video.complete_groups()}")
    record_property("audio", f"{audio.objects} obj ({audio.datagrams} dgram)")
    record_property("End of Track", " ".join(f"{n} {e}" for n, e in ended.items()))

    assert rc == 0, job.output()
    for t in tracks.values():
        assert t.reply == "SubscribeOk", t.name
        assert t.objects > 0, t.name
        assert t.done_code == TRACK_ENDED, t.name
        assert t.after_done == 0, t.name
        if t.name == "audio" and spec.audio_datagram:
            continue  # a datagram End of Track may be lost; PUBLISH_DONE carries the end
        assert t.end_of_track is not None, f"{t.name}: no End of Track"
        assert t.end_of_track[0] in (max(t.groups), max(t.groups) + 1), (t.name, t.end_of_track)
    complete = video.complete_groups()
    assert complete and set(complete) == {spec.frames_per_group}, complete
    if spec.audio_datagram:
        assert audio.datagrams == audio.objects
    else:
        assert audio.datagrams == 0
        assert set(audio.complete_groups()) == {spec.audio_frames_per_group}
    if "catalog" in tracks:
        catalog = json.loads(tracks["catalog"].first_payload)
        assert {"video", "audio"} <= {t["name"] for t in catalog["tracks"]}, catalog
    if spec.packaging == "cmaf":
        assert b"moof" in video.first_payload[:512]
