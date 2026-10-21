# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# mtt command line.

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mtt", allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)

    ns = sub.add_parser("ns", help="test namespace codec")
    ns_sub = ns.add_subparsers(dest="ns_command", required=True)

    enc = ns_sub.add_parser("encode", help="fields to a namespace", allow_abbrev=False)
    enc.add_argument("--proto", choices=(fields.VERSION, moq_test.VERSION), default=fields.VERSION)
    enc.add_argument("--prefix", help="prefix fields in slash notation, e.g. openmoq/mtt/aiomoqt")
    for opt, name in MEDIA_OPTIONS.items():
        enc.add_argument(f"--{opt}", dest=opt, metavar="VALUE", help=name)
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
