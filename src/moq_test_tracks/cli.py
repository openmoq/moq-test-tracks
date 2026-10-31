# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# mtt command line.

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from .config import MODES, job_errors
from .config import load as load_config
from .content import media
from .runner.request import build as build_request
from .runner.supervisor import EXIT_REFUSED, Job, JobError
from .spec import fields, moq_test
from .spec.fields import FIELDS, NA, FieldError, NamespaceError, TrackSpec, applies
from .spec.namespace import decode, encode, from_slash, from_text, render, to_slash

# Option names match the API's query parameters (docs/design.md).
MEDIA_OPTIONS = {
    "content": "content",
    "cat": "catalog",
    "pkg": "packaging",
    "vc": "video_codec",
    "h": "video_height",
    "fps": "frame_rate",
    "ac": "audio_codec",
    "ch": "audio_channels",
    "init": "init_data",
    "gop": "group_ms",
    "adel": "audio_delivery",
    "dur": "duration_s",
    "vt": "video_tracks",
}


def _fail(errors: Sequence[FieldError | str]) -> int:
    for e in errors:
        print(f"error: {e}", file=sys.stderr)
    return 2


def _media_tokens(args: argparse.Namespace) -> list[str]:
    given = {name: getattr(args, opt) for opt, name in MEDIA_OPTIONS.items()}
    has = {
        "catalog": given["catalog"] != "none",
        "video": given["video_codec"] != "none",
        "audio": given["audio_codec"] != "none",
    }
    opt_of = {name: opt for opt, name in MEDIA_OPTIONS.items()}
    errors, tokens = [], []
    for field in FIELDS:
        value = given[field.name]
        if not applies(field, **has):
            if value is not None:
                errors.append(
                    FieldError(field.index, field.name, f"--{opt_of[field.name]} does not apply")
                )
            tokens.append(NA)
        elif value is not None:
            tokens.append(value)
        elif field.omitted is not None:
            tokens.append(str(fields.omitted_value(field, given)))
        else:
            errors.append(
                FieldError(field.index, field.name, f"--{opt_of[field.name]} is required")
            )
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
                "kbps": spec.video_kbps,
                "frames_per_group": int(spec.frames_per_group),
                "timing": vt.__dict__ if vt else None,
            }
            if spec.is_ladder:
                out["video"]["ladder"] = [
                    {"track": t, "height": h, "kbps": spec.kbps_at(h)}
                    for t, h in zip(spec.video_track_names, spec.ladder(), strict=True)
                ]
        if spec.has_audio:
            at = spec.audio_timing()
            out["audio"] = {
                "sample_rate": fields.AUDIO_SAMPLE_RATE,
                "kbps": spec.audio_kbps,
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
    prefix = from_slash(args.prefix) if args.prefix else cfg.prefix(tool.id, args.clip)
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
    cache = cfg.root / "var" / "cache"
    clip = None
    if decoded.spec.content == "clip":
        cid = decoded.prefix[-1].decode() if decoded.prefix else ""
        mezz = media.mezzanine(cache, cid) if cid in cfg.clips else None
        if mezz is None:
            return _fail([f"clip {cid} is not configured and fetched (mtt media fetch)"])
        c = cfg.clips[cid]
        clip = {"id": cid, "path": str(mezz.path), "attribution": c.attribution, "loop_s": c.loop_s}
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
        transport=args.transport, draft=draft, mode=args.mode, ttl_s=ttl, clip=clip,
    )  # fmt: skip
    print(f"namespace: {to_slash(namespace)}")
    work = Path(args.work) if args.work else cfg.root / "var" / "jobs"
    job = Job(tool, req, decoded.spec, work, cache)
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
    var = _var(args, cfg)
    app = create_app(
        cfg,
        token=token,
        work=var / "jobs",
        cache=var / "cache",
        nice=int(os.environ.get("MTT_JOB_NICE", "10")),
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def _var(args: argparse.Namespace, cfg) -> Path:
    if getattr(args, "var", None):
        return Path(args.var)
    if env := os.environ.get("MTT_VAR"):
        return Path(env)
    return cfg.root / "var"


def cmd_media(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(Path(args.root) if args.root else None)
    except (FileNotFoundError, ValueError) as e:
        return _fail([str(e)])
    cache = _var(args, cfg) / "cache"
    if args.media_command == "list":
        for c in cfg.clips.values():
            mezz = media.mezzanine(cache, c.id)
            state = (
                f"fetched {mezz.captured_at}  {mezz.duration_s:.0f} s  "
                f"{mezz.video.get('width')}x{mezz.video.get('height')} {mezz.video.get('codec')}"
                if mezz
                else ("not imported" if c.video else "not fetched")
            )
            print(f"{c.id:18} {state}  {c.title}")
        return 0
    clip = cfg.clips.get(args.clip)
    if clip is None:
        return _fail([f"unknown clip {args.clip}; see media/sources.yaml"])
    try:
        if args.media_command == "import":
            media_dir = _var(args, cfg) / "media"
            print(f"importing {clip.id} from {media_dir}", file=sys.stderr)
            mezz = media.import_files(clip, cache, media_dir)
        else:
            print(f"recording {clip.capture_s} s of {clip.id} from {clip.url}", file=sys.stderr)
            mezz = media.fetch(clip, cache)
    except (media.MediaError, subprocess.TimeoutExpired) as e:
        return _fail([str(e)])
    print(json.dumps({"clip": clip.id, "path": str(mezz.path), **mezz.summary()}, indent=2))
    return 0


def _db_path(args: argparse.Namespace) -> Path:
    if getattr(args, "db", None):
        return Path(args.db)
    if env := os.environ.get("MTT_DB"):
        return Path(env)
    return Path("var") / "api.sqlite"


def cmd_api(args: argparse.Namespace) -> int:
    import uvicorn

    from .api.app import Settings, create_app

    env = os.environ
    missing = [
        v
        for v in (
            "MTT_RUNNER_TOKEN",
            "MTT_TOKEN_SECRET",
            "MTT_SESSION_SECRET",
            "MTT_PUBLIC_ORIGINS",
        )
        if not env.get(v)
    ]
    if missing:
        return _fail([f"{', '.join(missing)} not set"])
    try:
        cfg = load_config(Path(args.root) if args.root else None)
    except FileNotFoundError as e:
        return _fail([str(e)])
    settings = Settings(
        runner_url=env.get("MTT_RUNNER_URL", "http://127.0.0.1:8090"),
        runner_token=env["MTT_RUNNER_TOKEN"],
        token_secret=env["MTT_TOKEN_SECRET"],
        session_secret=env["MTT_SESSION_SECRET"],
        db_path=_db_path(args),
        public_origins=tuple(o.strip() for o in env["MTT_PUBLIC_ORIGINS"].split(",") if o.strip()),
        trust_proxy=env.get("MTT_TRUST_PROXY", "1") == "1",
        secure_cookies=env.get("MTT_INSECURE_COOKIES", "0") != "1",
    )
    uvicorn.run(
        create_app(cfg, settings),
        host=args.host,
        port=args.port,
        log_level="info",
        proxy_headers=settings.trust_proxy,
        forwarded_allow_ips="*" if settings.trust_proxy else None,
    )
    return 0


def _utc(t: float) -> str:
    return time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(t))


def _write_private(path: Path, text: str) -> None:
    """A new file only its owner can read; never overwrites."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text + "\n")


def cmd_tokens(args: argparse.Namespace) -> int:
    from .api import auth
    from .api.db import Db

    db = Db(_db_path(args))
    command = args.tokens_command
    if command in ("create", "show"):
        try:
            signer = auth.Signer(os.environ.get("MTT_TOKEN_SECRET", ""))
        except ValueError:
            return _fail(["MTT_TOKEN_SECRET is not set or shorter than 32 characters"])
    if command == "create":
        try:
            cfg = load_config(Path(args.root) if args.root else None)
        except FileNotFoundError as e:
            return _fail([str(e)])
        unknown = [r for r in args.relay if r not in cfg.relays]
        if unknown:
            return _fail(
                [f"unknown relay {r}; configured: {', '.join(cfg.relays)}" for r in unknown]
            )
        if args.role != "view" and not args.relay:
            return _fail(["run and admin tokens need at least one --relay"])
        try:
            token, p = auth.mint(
                signer,
                args.name,
                args.role,
                relays=args.relay,
                tier=args.tier,
                expires_days=args.expires_days,
            )
        except ValueError as e:
            return _fail([str(e)])
        if args.out:
            try:
                _write_private(Path(args.out), token)
            except FileExistsError:
                return _fail([f"{args.out} exists"])
        else:
            print(token)
        auth.record(db, p)
        relays = ",".join(p.relays) or "-"
        print(
            f"{p.token_id}  {p.role} {p.tier}  relays {relays}  expires {_utc(p.expires)}  {p.name}",
            file=sys.stderr,
        )
        if not args.out:
            print("Store this token now; it cannot be shown again.", file=sys.stderr)
    elif command == "list":
        for t in auth.issued(db):
            relays = ",".join(t["relays"]) or "-"
            print(
                f"{t['token_id']}  {t['role']:5}  {t['tier']:12}  {t['status']:7}  "
                f"{_utc(t['expires'])}  {relays}  {t['name']}"
            )
    elif command == "revoke":
        if not re.fullmatch(r"[0-9a-f]{16}", args.token_id):
            return _fail(["a token id is 16 hex digits (mtt tokens list)"])
        if not auth.revoke(db, args.token_id, args.reason):
            return _fail([f"{args.token_id} is already revoked"])
        db.audit("token_revoked", args.token_id, None, args.reason)
        known = any(t["token_id"] == args.token_id for t in auth.issued(db))
        print(f"revoked {args.token_id}" + ("" if known else " (not in this issue log)"))
    elif command == "show":
        token = (Path(args.file).read_text() if args.file else sys.stdin.read()).strip()
        claims = signer.open(token)
        p = auth.principal(claims) if claims is not None else None
        if p is None:
            return _fail(["not a token signed with this MTT_TOKEN_SECRET"])
        if auth.is_revoked(db, p.token_id):
            status = "revoked"
        else:
            status = "expired" if p.expires <= time.time() else "active"
        print(json.dumps({**p.info(), "issued": claims[auth.IAT], "status": status}, indent=2))
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
    run.add_argument("--clip", help="clip id for --content clip (adds it to the prefix)")
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

    api = sub.add_parser("api", help="serve the public API and dashboard")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8080)
    api.add_argument("--root", help="repository root holding config/ and adapters/")
    api.add_argument("--db", help="SQLite path (default: MTT_DB or var/api.sqlite)")
    api.set_defaults(func=cmd_api)

    med = sub.add_parser("media", help="clip mezzanines (media/sources.yaml)")
    med.add_argument("--root", help="repository root holding config/ and media/")
    med.add_argument("--var", help="state directory (default: MTT_VAR or <root>/var)")
    med_sub = med.add_subparsers(dest="media_command", required=True)
    fetch = med_sub.add_parser("fetch", help="record a clip from its URL into the cache")
    fetch.add_argument("clip", help="clip id")
    imp = med_sub.add_parser("import", help="take a clip's local files from <var>/media")
    imp.add_argument("clip", help="clip id")
    med_sub.add_parser("list", help="configured clips and what has been fetched")
    med.set_defaults(func=cmd_media)

    tok = sub.add_parser("tokens", help="issue, list, revoke and inspect tokens (docs/admin.md)")
    tok.add_argument("--db", help="SQLite path (default: MTT_DB or var/api.sqlite)")
    tok_sub = tok.add_subparsers(dest="tokens_command", required=True)
    create = tok_sub.add_parser("create", help="issue a token and print it once")
    create.add_argument("--name", required=True, help="who holds it")
    create.add_argument("--role", choices=("view", "run", "admin"), default="run")
    create.add_argument(
        "--tier",
        choices=("standard", "unrestricted"),
        default="standard",
        help="unrestricted: past the per-token limits up to the global caps; expires within 30 days",
    )
    create.add_argument(
        "--relay",
        action="append",
        default=[],
        metavar="ID",
        help="a relay id from config/relays.yaml it may publish to; repeat for more",
    )
    create.add_argument("--expires-days", type=float, default=30, help="default 30, at most 365")
    create.add_argument("--out", metavar="FILE", help="write it to a new file only you can read")
    create.add_argument("--root", help="repository root holding config/")
    tok_sub.add_parser("list", help="issued tokens and their status")
    rev = tok_sub.add_parser("revoke", help="revoke a token by id")
    rev.add_argument("token_id")
    rev.add_argument("--reason", default="")
    show = tok_sub.add_parser("show", help="verify a token from stdin and print its claims")
    show.add_argument("--file", help="read the token from this file instead")
    tok.set_defaults(func=cmd_tokens)

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
