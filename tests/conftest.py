# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# Hypothesis profiles (HYPOTHESIS_PROFILE=thorough on main), and a Markdown
# results summary appended to $GITHUB_STEP_SUMMARY when it is set.

import os
import time

try:
    from hypothesis import settings
except ImportError:  # the e2e run uses the service image, which has no hypothesis
    settings = None

if settings is not None:
    settings.register_profile("thorough", max_examples=2000, deadline=None)
    settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))

OUTCOMES = ("passed", "failed", "error", "skipped", "xfailed", "xpassed")
SLOWEST = 10
_started = time.monotonic()


def _cell(text: str, limit: int = 200) -> str:
    text = " ".join(str(text).split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text.replace("|", "\\|")


def _message(report) -> str:
    crash = getattr(report.longrepr, "reprcrash", None)
    if crash is not None:
        return crash.message
    if isinstance(report.longrepr, tuple):  # a skip: (path, line, reason)
        return report.longrepr[2]
    return str(report.longrepr).splitlines()[-1] if report.longrepr else ""


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    stats = terminalreporter.stats
    counts = {o: len(stats.get(o, [])) for o in OUTCOMES}
    title = os.environ.get("MTT_SUMMARY_TITLE", "Tests")
    mark = "✅" if exitstatus == 0 else "❌"
    lines = [
        f"### {mark} {title}",
        "",
        "| " + " | ".join(o.capitalize() for o in OUTCOMES) + " | Time |",
        "|" + "---|" * (len(OUTCOMES) + 1),
        "| "
        + " | ".join(str(counts[o]) for o in OUTCOMES)
        + f" | {time.monotonic() - _started:.1f} s |",
        "",
    ]
    failed = stats.get("failed", []) + stats.get("error", [])
    if failed:
        lines += ["**Failures**", "", "| Test | Message |", "|---|---|"]
        lines += [f"| `{r.nodeid}` | {_cell(_message(r))} |" for r in failed]
        lines.append("")
    calls = [r for o in ("passed", "failed") for r in stats.get(o, []) if r.when == "call"]
    detailed = [r for r in calls if r.user_properties]
    if detailed:
        keys = list(dict.fromkeys(k for r in detailed for k, _ in r.user_properties))
        lines += ["| Test | " + " | ".join(keys) + " |", "|" + "---|" * (len(keys) + 1)]
        for r in detailed:
            props = dict(r.user_properties)
            lines.append(
                f"| `{r.nodeid.split('::')[-1]}` | "
                + " | ".join(_cell(props.get(k, "")) for k in keys)
                + " |"
            )
        lines.append("")
    skipped = stats.get("skipped", [])
    if skipped:
        reasons: dict[str, int] = {}
        for r in skipped:
            reasons[_message(r)] = reasons.get(_message(r), 0) + 1
        lines += ["<details><summary>Skipped</summary>", ""]
        lines += [f"- {n} × {_cell(reason)}" for reason, n in reasons.items()]
        lines += ["", "</details>", ""]
    slow = sorted(calls, key=lambda r: r.duration, reverse=True)[:SLOWEST]
    if slow:
        lines += [
            f"<details><summary>Slowest {len(slow)}</summary>",
            "",
            "| Test | Time |",
            "|---|---|",
        ]
        lines += [f"| `{r.nodeid}` | {r.duration:.2f} s |" for r in slow]
        lines += ["", "</details>", ""]
    with open(path, "a") as f:
        f.write("\n".join(lines) + "\n")
