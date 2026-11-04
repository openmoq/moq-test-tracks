# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

import dataclasses
from pathlib import Path

import pytest

from moq_test_tracks import config
from moq_test_tracks.cli import _url_relay, main

ROOT = Path(__file__).resolve().parents[1]
NS = "alice/demo/mtt_00/clock/loc/h264/720/30/aac/2"


def run(*args: str) -> int:
    return main(["run", "--root", str(ROOT), "--ns", NS, "--dry-run", *args])


@pytest.mark.parametrize("url", ["moqt://127.0.0.1:4443", "https://relay.example:4443/moq"])
def test_run_relay_url(url, capsys):
    assert run("--relay", url) == 0
    plan = capsys.readouterr().out.split("plan: ", 1)[1]
    assert f" {url} " in plan and "--draft 18" in plan and f"-N {NS}" in plan


def test_run_relay_url_draft(capsys):
    assert run("--relay", "moqt://127.0.0.1:4443", "--draft", "22") == 0
    assert "--draft 22" in capsys.readouterr().out
    assert run("--relay", "moqt://127.0.0.1:4443", "--draft", "17") == 2
    assert "does not speak draft 17" in capsys.readouterr().err


def test_url_relay_tool_without_drafts():
    tool = config.load(ROOT).tools["aiomoqt"]
    caps = {k: v for k, v in tool.capabilities.items() if k != "draft"}
    any_draft = dataclasses.replace(tool, capabilities=caps)
    assert _url_relay("moqt://h:1", any_draft, None).drafts == (18,)
    assert _url_relay("moqt://h:1", any_draft, 17).default_draft == 17


def test_run_relay_url_scheme(capsys):
    assert run("--relay", "http://127.0.0.1:4443") == 2
    assert "must start moqt:// or https://" in capsys.readouterr().err


def test_run_ttl_up_to_global_cap(capsys):
    assert run("--relay", "aiomoqt-relay", "--ttl", "86400") == 0
    assert run("--relay", "aiomoqt-relay", "--ttl", "86401") == 2
    assert "outside 1-86400" in capsys.readouterr().err
