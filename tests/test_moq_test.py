# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# moq-test-00 codec.

import dataclasses

import pytest

from moq_test_tracks.cli import main
from moq_test_tracks.spec import MoqTestParams, NamespaceError, decode, encode, from_slash, to_slash
from moq_test_tracks.spec.moq_test import MAX_GROUP, VERSION

# Canonical case from moq-interop-runner docs/tests/data-plane/data-subgroup-basic.md.
CANONICAL = "moq-test/data-subgroup-basic/r1/moq-test-00/0/0/0/2/4/5/64/32/2/1/1/0/-1/-1/0"


def test_canonical_round_trip():
    decoded = decode(from_slash(CANONICAL))
    assert decoded.protocol == VERSION
    assert decoded.prefix == (b"moq-test", b"data-subgroup-basic", b"r1")
    p = decoded.spec
    assert (p.last_group, p.last_object, p.objects_per_group) == (2, 4, 5)
    assert (p.integer_extension, p.variable_extension) == (-1, -1)
    assert to_slash(encode(p, decoded.prefix)) == CANONICAL


def test_defaults_are_explicit():
    fields = to_slash(encode(MoqTestParams())).split("/")
    assert fields[0] == VERSION
    assert fields[4] == str(MAX_GROUP)
    assert fields[5] == "10"  # last_object resolved to its maximum
    assert all(fields)


@pytest.mark.parametrize(
    "changes, name",
    [
        ({"forwarding_preference": 4}, "forwarding_preference"),
        ({"start_group": 3, "last_group": 2}, "start_group"),
        ({"start_object": 5, "last_object": 4}, "start_object"),
        ({"last_object": 11}, "last_object"),
        ({"group_increment": 0}, "group_increment"),
        ({"object_increment": 0}, "object_increment"),
        ({"end_of_group_markers": 2}, "end_of_group_markers"),
        ({"last_group": MAX_GROUP + 1}, "last_group"),
        ({"size_objects": -1}, "size_objects"),
        ({"integer_extension": -2}, "integer_extension"),
    ],
)
def test_invalid_params(changes, name):
    with pytest.raises(NamespaceError) as e:
        encode(dataclasses.replace(MoqTestParams(), **changes))
    assert name in [err.name for err in e.value.errors]


@pytest.mark.parametrize("token", ["02", "+2", "-1", "x", ""])
def test_invalid_tokens(token):
    parts = CANONICAL.split("/")
    parts[3 + 4] = token  # last_group
    with pytest.raises(NamespaceError):
        decode(tuple(p.encode() for p in parts))


def test_cli_encode_moq_test(capsys):
    args = ["ns", "encode", "--proto", VERSION, "--prefix", "moq-test/data-subgroup-basic/r1"]
    pairs = [
        "last_group=2",
        "last_object=4",
        "objects_per_group=5",
        "size_object_0=64",
        "size_objects=32",
        "object_frequency_ms=2",
    ]
    assert main([*args, *pairs]) == 0
    assert capsys.readouterr().out.strip() == CANONICAL
