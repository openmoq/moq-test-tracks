# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# Test namespace encode/decode, version dispatch, and the MoQT name
# representation (draft-ietf-moq-transport §8.8).

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from . import fields, moq_test
from .fields import FIELDS, NA, FieldError, NamespaceError, TrackSpec
from .moq_test import MoqTestParams

Namespace = tuple[bytes, ...]

MAX_NAMESPACE_FIELDS = 32
MAX_FULL_NAME_BYTES = 4096

_FIELD_COUNTS = {fields.VERSION: len(FIELDS), moq_test.VERSION: len(moq_test.FIELD_NAMES)}
_VERSIONS = {v.encode(): v for v in _FIELD_COUNTS}
_SAFE = frozenset(b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
_HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class Decoded:
    prefix: Namespace
    protocol: str  # the version field
    spec: TrackSpec | MoqTestParams


def _as_bytes(field: bytes | str) -> bytes:
    return field.encode() if isinstance(field, str) else bytes(field)


def _check_limits(namespace: Namespace) -> list[FieldError]:
    errors = []
    if len(namespace) > MAX_NAMESPACE_FIELDS:
        errors.append(
            FieldError(None, "namespace", f"{len(namespace)} fields exceed {MAX_NAMESPACE_FIELDS}")
        )
    if any(len(f) == 0 for f in namespace):
        errors.append(FieldError(None, "namespace", "MoQT forbids empty fields"))
    if sum(len(f) for f in namespace) > MAX_FULL_NAME_BYTES:
        errors.append(FieldError(None, "namespace", f"exceeds {MAX_FULL_NAME_BYTES} bytes"))
    return errors


def _check_prefix(prefix: Namespace) -> list[FieldError]:
    return [
        FieldError(None, "prefix", f"field {i} equals a version field")
        for i, f in enumerate(prefix)
        if f in _VERSIONS
    ]


def encode(spec: TrackSpec | MoqTestParams, prefix: Sequence[bytes | str] = ()) -> Namespace:
    pre = tuple(_as_bytes(f) for f in prefix)
    if isinstance(spec, TrackSpec):
        version, tokens = fields.VERSION, format_media(spec)
    else:
        version, tokens = moq_test.VERSION, moq_test.format_fields(spec)
    namespace = (*pre, version.encode(), *(t.encode() for t in tokens))
    errors = _check_prefix(pre) + _check_limits(namespace)
    if errors:
        raise NamespaceError(errors)
    return namespace


def decode(namespace: Sequence[bytes | str]) -> Decoded:
    ns = tuple(_as_bytes(f) for f in namespace)
    errors = _check_limits(ns)
    if errors:
        raise NamespaceError(errors)
    at = next((i for i, f in enumerate(ns) if f in _VERSIONS), None)
    if at is None:
        raise NamespaceError([FieldError(None, "namespace", "no known version field")])
    version = _VERSIONS[ns[at]]
    rest = ns[at + 1 :]
    if len(rest) != _FIELD_COUNTS[version]:
        raise NamespaceError(
            [
                FieldError(
                    None,
                    "namespace",
                    f"{version} needs {_FIELD_COUNTS[version]} fields after the version, "
                    f"got {len(rest)}",
                )
            ]
        )
    try:
        tokens = [f.decode("ascii") for f in rest]
    except UnicodeDecodeError:
        raise NamespaceError([FieldError(None, "namespace", "fields are not ASCII")]) from None
    if version == fields.VERSION:
        spec: TrackSpec | MoqTestParams = parse_media(tokens)
    else:
        spec = moq_test.parse_fields(tokens)
    return Decoded(ns[:at], version, spec)


def parse_media(tokens: list[str]) -> TrackSpec:
    """Characteristic fields 1-15 of moq_test_tracks_00 to a validated spec."""
    errors: list[FieldError] = []
    values: dict[str, object] = {}
    for field, token in zip(FIELDS, tokens, strict=True):
        if token == NA:
            values[field.name] = None
            continue
        try:
            values[field.name] = field.parse(token)
        except ValueError as e:
            errors.append(FieldError(field.index, field.name, str(e)))
            values[field.name] = None
    if errors:
        raise NamespaceError(errors)
    spec = TrackSpec(**values)  # type: ignore[arg-type]
    errors = fields.check(spec)
    if errors:
        raise NamespaceError(errors)
    return spec


def format_media(spec: TrackSpec) -> list[str]:
    errors = fields.check(spec)
    if errors:
        raise NamespaceError(errors)
    out = []
    for field in FIELDS:
        value = getattr(spec, field.name)
        out.append(NA if value is None else str(value))
    return out


def to_slash(namespace: Sequence[bytes | str]) -> str:
    """Fields joined by "/", the notation of the draft's examples."""
    out = []
    for f in namespace:
        b = _as_bytes(f)
        if b"/" in b or not b.isascii() or not b.decode().isprintable():
            raise ValueError(f"field {b!r} has no slash notation; use render()")
        out.append(b.decode())
    return "/".join(out)


def from_slash(text: str) -> Namespace:
    parts = text.split("/")
    if any(p == "" for p in parts):
        raise ValueError("empty field in slash notation")
    return tuple(p.encode() for p in parts)


def render(namespace: Sequence[bytes | str], track: bytes | str | None = None) -> str:
    """MoQT name representation: fields joined by "-", track after "--"."""
    out = "-".join(_render_field(_as_bytes(f)) for f in namespace)
    if track is not None:
        out += "--" + _render_field(_as_bytes(track))
    return out


def _render_field(b: bytes) -> str:
    return "".join(chr(c) if c in _SAFE else f".{c:02x}" for c in b)


def parse_rendered(text: str) -> tuple[Namespace, bytes | None]:
    """Inverse of render(); rejects any non-canonical spelling."""
    ns_text, sep, track_text = text.partition("--")
    if sep and "-" in track_text:
        raise ValueError("track name contains an unescaped '-'")
    namespace = tuple(_parse_field(f) for f in ns_text.split("-")) if ns_text else ()
    track = _parse_field(track_text, allow_empty=True) if sep else None
    return namespace, track


def _parse_field(text: str, allow_empty: bool = False) -> bytes:
    if not text and not allow_empty:
        raise ValueError("empty field")
    out = bytearray()
    i = 0
    while i < len(text):
        c = text[i]
        if c == ".":
            hexpair = text[i + 1 : i + 3]
            if len(hexpair) != 2 or not set(hexpair) <= _HEX:
                raise ValueError(f"'.' at {i} is not followed by two lowercase hex digits")
            byte = int(hexpair, 16)
            if byte in _SAFE:
                raise ValueError(f".{hexpair} encodes {chr(byte)!r}, which is written literally")
            out.append(byte)
            i += 3
            continue
        if ord(c) > 127 or ord(c) not in _SAFE:
            raise ValueError(f"{c!r} at {i} must be escaped")
        out.append(ord(c))
        i += 1
    return bytes(out)


def from_text(text: str) -> Namespace:
    """Slash notation if the text contains "/", else the MoQT name representation."""
    if "/" in text:
        return from_slash(text)
    namespace, track = parse_rendered(text)
    if track is not None:
        raise ValueError("expected a namespace, got a full track name")
    return namespace
