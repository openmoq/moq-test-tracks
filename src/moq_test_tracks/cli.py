# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# mtt command line.

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import signal
import sys
from collections.abc import Sequence
from pathlib import Path

from .config import MODES, job_errors
from .config import load as load_config
from .runner.request import build as build_request
from .runner.supervisor import EXIT_REFUSED, Job, JobError
from .spec import fields, moq_test
from .spec.fields import FIELDS, NA, FieldError, NamespaceError, TrackSpec
from .spec.namespace import decode, encode, from_slash, from_text, render, to_slash

# Option names match the API's query parameters (docs/design.md).
MEDIA_OPTIONS = {
    "cat": "catalog",
    "pkg": "packaging",
    "vc": "video_codec",
    "h": "video_height",
    "fps": "frame_rate",
    "vbr": "video_kbps",
    "gop": "group_ms",
    "vfwd": "video_forwarding",
    "ac": "audio_codec",
    "ch": "audio_channels",
    "abr": "audio_kbps",
    "afpg": "audio_frames_per_group",
    "afwd": "audio_forwarding",
    "content": "content",
    "dur": "duration_s",
}


def _fail(errors: Sequence[FieldError | str]) -> int:
    for e in errors:
        print(f"error: {e}", file=sys.stderr)
    return 2


def _media_tokens(args: argparse.Namespace) -> list[str]:
    given = {name: getattr(args, opt) for opt, name in MEDIA_OPTIONS.items()}
    applies = {"video": given["video_codec"] != "none", "audio": given["audio_codec"] != "none"}
    opt_of = {name: opt for opt, name in MEDIA_OPTIONS.items()}
    errors, tokens = [], []
    for field in FIELDS:
        value = given[field.name]
        if field.media is not None and not applies[field.media]:
            if value is not None:
                errors.append(
                    FieldError(field.index, field.name, f"--{opt_of[field.name]} does not apply")
                )
            tokens.append(NA)
        elif value is None:
            errors.append(
                FieldError(field.index, field.name, f"--{opt_of[field.name]} is required")
            )
        else:
            tokens.append(value)
    if errors:
        raise NamespaceError(errors)
    return tokens


def _moq_test_params(pairs: list[str]) -> moq_test.MoqTestParams:
    values: dict[str, int] = {}
    for pair in pairs:
        name, sep, value = pair.partition("=")
        if not sep or name not in moq_test.FIELD_NAMES:
            raise ValueError(f"expected NAME=VALUE with NAME in {', '.join(moq_test.FIELD_NAMES)}")
        values[name] = int(value)
    return moq_test.MoqTestParams(**values)


def _describe(spec: TrackSpec | moq_test.MoqTestParams) -> dict:
    out: dict = {"fields": {k: getattr(spec, k) for k in spec.__dataclass_fields__}}
    if isinstance(spec, TrackSpec):
        out["tracks"] = list(spec.tracks)
        if spec.has_video:
            vt = spec.video_timing()
            out["video"] = {
                "width": spec.video_width,
                "frames_per_group": int(spec.frames_per_group),
                "timing": vt.__dict__ if vt else None,
            }
        if spec.has_audio:
            at = spec.audio_timing()
            out["audio"] = {
                "sample_rate": fields.AUDIO_SAMPLE_RATE,
                "timing": at.__dict__ if at else None,
            }
    return out


def cmd_encode(args: argparse.Namespace) -> int:
    prefix = from_slash(args.prefix) if args.prefix else ()
    try:
        if args.proto == moq_test.VERSION:
            namespace = encode(_moq_test_params(args.pairs), prefix)
        else:
            if args.pairs:
                return _fail(["NAME=VALUE pairs are for --proto moq-test-00"])
            tokens = _media_tokens(args)
            namespace = (*prefix, fields.VERSION.encode(), *(t.encode() for t in tokens))
            namespace = encode(decode(namespace).spec, prefix)
    except NamespaceError as e:
        return _fail(e.errors)
    except ValueError as e:
        return _fail([str(e)])
    print(render(namespace, args.track) if args.render else to_slash(namespace))
    return 0


def cmd_decode(args: argparse.Namespace) -> int:
    try:
        decoded = decode(from_text(args.namespace))
    except NamespaceError as e:
        return _fail(e.errors)
    except ValueError as e:
        return _fail([str(e)])
    out = {"protocol": decoded.protocol, "prefix": [f.decode("latin-1") for f in decoded.prefix]}
    out.update(_describe(decoded.spec))
    print(json.dumps(out, indent=2))
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    try:
        namespace = from_text(args.namespace)
    except ValueError as e:
        return _fail([str(e)])
    print(render(namespace, args.track))
    return 0


def _format_event(event: dict) -> str:
    kind = event.get("event")
    if kind == "stats":
        parts = []
        for name, t in event.get("tracks", {}).items():
            rate = t.get("objects_per_s", 0)
            part = f"{name} {rate:.0f} obj/s {t.get('kbps', 0):.0f} kbps"
            if t.get("dropped"):
                part += f" dropped {t['dropped']}"
            parts.append(part)
        return f"[{event.get('t', 0):7.1f}s] " + " | ".join(parts)
    rest = {k: v for k, v in event.items() if k != "event"}
    return f"{kind}: {json.dumps(rest)}"


async def _run_job(job: Job, dry_run: bool, verbose: bool) -> int:
    try:
        check = await job.check()
    except JobError as e:
        return _fail([str(e)])
    if not check.accept:
        _fail([f"{r.get('field')}: {r.get('message')}" for r in check.reasons] or ["refused"])
        return EXIT_REFUSED
    print("plan: " + " ".join(check.argv))
    if dry_run:
        return 0
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, job.stop)

    def on_log(line: str) -> None:
        if verbose:
            print(f"  | {line}", file=sys.stderr)

    try:
        rc = await job.run(
            check, on_event=lambda e: print(_format_event(e), flush=True), on_log=on_log
        )
    except JobError as e:
        return _fail([str(e)])
    print(f"job {job.request['job_id']} {job.state} (exit {rc}); log: {job.dir / 'log'}")
    return rc


def cmd_run(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(Path(args.root) if args.root else None)
    except FileNotFoundError as e:
        return _fail([str(e)])
    tool = cfg.tools.get(args.tool)
    if tool is None:
        return _fail([f"unknown or disabled tool {args.tool}"])
    relay = cfg.relays.get(args.relay or cfg.default_relay)
    if relay is None:
        return _fail([f"relay {args.relay} is not in the allowlist"])
    prefix = from_slash(args.prefix) if args.prefix else (*cfg.policy.namespace_prefix, tool.id)
    try:
        if args.ns:
            namespace = from_text(args.ns)
        else:
            prefix = tuple(p.encode() if isinstance(p, str) else p for p in prefix)
            namespace = (
                *prefix,
                fields.VERSION.encode(),
                *(t.encode() for t in _media_tokens(args)),
            )
        decoded = decode(namespace)
    except NamespaceError as e:
        return _fail(e.errors)
    except ValueError as e:
        return _fail([str(e)])
    if not isinstance(decoded.spec, TrackSpec):
        return _fail([f"{decoded.protocol} jobs are not supported by mtt run"])
    draft = args.draft or relay.default_draft
    ttl = args.ttl or cfg.policy.default_ttl_s
    errors = job_errors(
        tool, relay, decoded.spec, mode=args.mode, transport=args.transport, draft=draft,
        ttl_s=ttl, policy=cfg.policy,
    )  # fmt: skip
    if errors:
        return _fail(errors)
    req = build_request(
        f"j_{secrets.token_hex(4)}", tuple(namespace), decoded.spec, relay,
        transport=args.transport, draft=draft, mode=args.mode, ttl_s=ttl,
    )  # fmt: skip
    print(f"namespace: {to_slash(namespace)}")
    work = Path(args.work) if args.work else cfg.root / "var" / "jobs"
    job = Job(tool, req, decoded.spec, work, cfg.root / "var" / "cache")
    return asyncio.run(_run_job(job, args.dry_run, args.verbose))


def cmd_runner(args: argparse.Namespace) -> int:
    import uvicorn

    from .runner.agent import create_app

    token = os.environ.get("MTT_RUNNER_TOKEN", "")
    if not token:
        return _fail(["MTT_RUNNER_TOKEN is not set"])
    try:
        cfg = load_config(Path(args.root) if args.root else None)
    except FileNotFoundError as e:
        return _fail([str(e)])
    var = Path(args.var) if args.var else cfg.root / "var"
    app = create_app(
        cfg,
        token=token,
        work=var / "jobs",
        cache=var / "cache",
        nice=int(os.environ.get("MTT_JOB_NICE", "10")),
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def _add_media_options(p: argparse.ArgumentParser) -> None:
    for opt, name in MEDIA_OPTIONS.items():
        p.add_argument(f"--{opt}", dest=opt, metavar="VALUE", help=name)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mtt", allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run one job in the foreground", allow_abbrev=False)
    run.add_argument("--tool", default="aiomoqt")
    run.add_argument("--relay", help="relay id from config/relays.yaml (default: its default)")
    run.add_argument("--transport", choices=("moqt", "webtransport"), default="moqt")
    run.add_argument("--draft", type=int, help="MoQT draft (default: the relay's default)")
    run.add_argument("--mode", choices=MODES, default="publish")
    run.add_argument("--ttl", type=int, help="seconds (default: policy default_ttl_s)")
    run.add_argument("--prefix", help="namespace prefix in slash notation (default: policy + tool)")
    run.add_argument("--ns", help="full namespace instead of field options")
    _add_media_options(run)
    run.add_argument("--work", help="job directory root (default: var/jobs)")
    run.add_argument("--root", help="repository root holding config/ and adapters/")
    run.add_argument("--dry-run", action="store_true", help="check only; print the plan")
    run.add_argument("-v", "--verbose", action="store_true", help="show the tool's log")
    run.set_defaults(func=cmd_run)

    agent = sub.add_parser("runner", help="serve the internal runner API")
    agent.add_argument("--host", default="127.0.0.1")
    agent.add_argument("--port", type=int, default=8090)
    agent.add_argument("--root", help="repository root holding config/ and adapters/")
    agent.add_argument("--var", help="state directory for jobs and the source cache")
    agent.set_defaults(func=cmd_runner)

    ns = sub.add_parser("ns", help="test namespace codec")
    ns_sub = ns.add_subparsers(dest="ns_command", required=True)

    enc = ns_sub.add_parser("encode", help="fields to a namespace", allow_abbrev=False)
    enc.add_argument("--proto", choices=(fields.VERSION, moq_test.VERSION), default=fields.VERSION)
    enc.add_argument("--prefix", help="prefix fields in slash notation, e.g. openmoq/mtt/aiomoqt")
    _add_media_options(enc)
    enc.add_argument("pairs", nargs="*", metavar="NAME=VALUE", help="moq-test-00 fields")
    enc.add_argument("--render", action="store_true", help="print the MoQT name representation")
    enc.add_argument("--track", help="track name for --render")
    enc.set_defaults(func=cmd_encode)

    dec = ns_sub.add_parser("decode", help="namespace to fields, as JSON")
    dec.add_argument("namespace", help="slash notation or MoQT name representation")
    dec.set_defaults(func=cmd_decode)

    ren = ns_sub.add_parser("render", help="namespace to the MoQT name representation")
    ren.add_argument("namespace", help="slash notation or MoQT name representation")
    ren.add_argument("--track", help="track name")
    ren.set_defaults(func=cmd_render)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
