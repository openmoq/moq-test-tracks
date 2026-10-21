# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# moq-test-00 tuple fields (draft-afrind-moq-test), always explicit: MoQT
# forbids the empty fields that moq-test uses for defaults.

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass

from .fields import FieldError, NamespaceError

VERSION = "moq-test-00"
MAX_GROUP = 2**62 - 1

_UNSIGNED = re.compile(r"0|[1-9][0-9]*")
_EXTENSION = re.compile(r"-1|0|[1-9][0-9]*")

FIELD_NAMES = (
    "forwarding_preference",
    "start_group",
    "start_object",
    "last_group",
    "last_object",
    "objects_per_group",
    "size_object_0",
    "size_objects",
    "object_frequency_ms",
    "group_increment",
    "object_increment",
    "end_of_group_markers",
    "integer_extension",
    "variable_extension",
    "delivery_timeout_ms",
)
_EXTENSION_FIELDS = ("integer_extension", "variable_extension")


@dataclass(frozen=True)
class MoqTestParams:
    """Defaults are the draft's; last_object None means its maximum."""

    forwarding_preference: int = 0
    start_group: int = 0
    start_object: int = 0
    last_group: int = MAX_GROUP
    last_object: int | None = None
    objects_per_group: int = 10
    size_object_0: int = 1024
    size_objects: int = 100
    object_frequency_ms: int = 1000
    group_increment: int = 1
    object_increment: int = 1
    end_of_group_markers: int = 0
    integer_extension: int = -1
    variable_extension: int = -1
    delivery_timeout_ms: int = 0

    @property
    def max_last_object(self) -> int:
        return (self.objects_per_group + self.end_of_group_markers) * self.object_increment

    def resolved(self) -> MoqTestParams:
        if self.last_object is not None:
            return self
        return dataclasses.replace(self, last_object=self.max_last_object)


def check(params: MoqTestParams) -> list[FieldError]:
    p = params.resolved()
    errors: list[FieldError] = []

    def err(name: str, message: str) -> None:
        errors.append(FieldError(FIELD_NAMES.index(name) + 1, name, message))

    for name in FIELD_NAMES:
        value = getattr(p, name)
        floor = -1 if name in _EXTENSION_FIELDS else 0
        if not isinstance(value, int) or value < floor:
            err(name, f"{value!r} is not an integer >= {floor}")
    if errors:
        return errors
    if p.forwarding_preference > 3:
        err("forwarding_preference", f"{p.forwarding_preference} is not 0-3")
    if p.end_of_group_markers > 1:
        err("end_of_group_markers", f"{p.end_of_group_markers} is not 0 or 1")
    if p.last_group > MAX_GROUP:
        err("last_group", "exceeds 2^62-1")
    if p.start_group > p.last_group:
        err("start_group", "exceeds last_group")
    if p.start_object > p.last_object:
        err("start_object", "exceeds last_object")
    if p.last_object > p.max_last_object:
        err("last_object", f"exceeds the maximum {p.max_last_object}")
    if p.group_increment == 0:
        err("group_increment", "cannot be 0")
    if p.object_increment == 0:
        err("object_increment", "cannot be 0")
    return errors


def parse_fields(tokens: list[str]) -> MoqTestParams:
    errors: list[FieldError] = []
    values: dict[str, int] = {}
    for i, (name, token) in enumerate(zip(FIELD_NAMES, tokens, strict=True), start=1):
        pattern = _EXTENSION if name in _EXTENSION_FIELDS else _UNSIGNED
        if not pattern.fullmatch(token):
            errors.append(FieldError(i, name, f"{token!r} is not a valid integer"))
            continue
        values[name] = int(token)
    if errors:
        raise NamespaceError(errors)
    params = MoqTestParams(**values)
    errors = check(params)
    if errors:
        raise NamespaceError(errors)
    return params


def format_fields(params: MoqTestParams) -> list[str]:
    errors = check(params)
    if errors:
        raise NamespaceError(errors)
    p = params.resolved()
    return [str(getattr(p, name)) for name in FIELD_NAMES]
