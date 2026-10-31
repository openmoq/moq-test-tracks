# Contributing to moq-test-tracks

This document describes current guidelines for contributing to `moq-test-tracks`, and related OpenMOQ repos. Openness and community participation are foundational principles of the OpenMOQ Consortium. All are welcome and encouraged to use the software here freely, and contribute to testing, fixing, and enhancing the code when possible.

The following describes the general philosophy and approach for handling contributions. The process remains flexible and subject to review, and may change in the future. The ultimate goal is to facilitate the production of high-quality, professional-grade software.

## Pull request scope and content

**Each PR should address a single and logically cohesive thesis.**

This makes reviews more approachable and more likely to occur. Avoid bundling various unrelated changes and ad hoc changes.

The following is a list of ideals for PR content — developer discretion and sound judgement is expected:

- The title should clearly reflect the functional impact of the PR (in most cases this becomes the commit message on the merge commit).
- The description should contain additional technical details and solution rationale.
- If the PR addresses an existing issue, reference it in the description — `Fixes #N` to auto-close on merge, or `Refs #N` for partial or related work.
- Tests or other evaluation criteria should be included for independent pass/fail evaluation (including unit tests where possible).
- Relevant logs, developer test output, or other supporting material may be attached to support the review process.

## PR state

PRs with all checks passing may be merged by maintainers at unpredictable times based on availability and relative priority. The author can signal merge intent in the following ways:

- **Draft** — not ready for review; CI still runs.
- **`WIP:` prefix** — ready for review and CI, not ready for merge.
- **Ready** (non-draft, no `WIP:` prefix) — merge when all checks pass (manual for now).

## How to contribute

`moq-test-tracks` is a public repo — fork-based PRs are welcome.

- Outside contributors: fork, branch, PR against `main`.
- Org members: branch directly on this repo, PR against `main`.

PRs run CI with no secrets. Image publish and deploy run only on `push: main`
after merge.

## Reviews

At least one approving review is required. Changes under `auth/`, `jobs/`,
`adapters/`, `config/`, `deploy/`, `docker/` and `.github/` also need a code
owner ([CODEOWNERS](.github/CODEOWNERS)): they are the service's security
boundary.

Gemini reviews a PR when it is opened or marked ready for review. To ask
for another review, add the `gemini-review` label; it is removed when the
review starts.

## Branches

- `main` — rolling head; the hosted instance deploys from here.
- `feature/*`, `fix/*`, `devops/*` — convention only, no enforcement.

## Merge

PRs are squash-merged; the PR title becomes the commit message on `main`.

## Changelog

[CHANGELOG.md](CHANGELOG.md) follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/). Versions follow [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

- A PR adds its user-visible changes to the `[Unreleased]` section, in the same PR.
- An entry covers what an operator, API client, or subscriber of the service can observe.
- Everything goes under the matching subheading: `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed` or `Security`. Create the subheading if it is missing.
- Name the API endpoint, config key, tuple field, or tool adapter that changed.
- A breaking change starts with `**Breaking:**`. A change to the namespace encoding is breaking unless it bumps the version tuple.

## Local development

Before submitting:

1. `ruff check . && ruff format --check .`
2. `pytest`
3. Add tests; bug fixes include a regression test. Namespace codec changes
   include encode/decode round-trip cases.

## CI tiers

Each run writes a results summary (counts, failures, slowest tests) to its
Actions page.

| Workflow | When | Runs |
|----------|------|------|
| `ci pr` | every pull request | ruff, unit tests |
| `ci main` | push to main, nightly, by hand; pull requests that change `docker/`, `tests/e2e/` or the workflow | ruff; unit tests with `HYPOTHESIS_PROFILE=thorough` and coverage; the service image, its pins against the manifest, and end-to-end tests inside it |

The end-to-end tests (`tests/e2e`, marker `e2e`, deselected by default) run
real `mtt run` jobs through a local aiomoqt-relay and check each track with a
subscriber: group sizes, datagram audio, the catalog, End of Track and
PUBLISH_DONE. They need aiomoqt, ffmpeg and openssl, and opt in with
`MTT_E2E=1`, which also trusts the relay's self-signed cert by appending it
to certifi's bundle for the run. The easiest place to run them is the image:

```sh
docker build -f docker/Dockerfile -t mtt:ci .
docker run --rm --user 0 --entrypoint sh -e MTT_E2E=1 -v "$PWD:/src:ro" mtt:ci \
  -c 'pip install -q pytest && cd /src && python -m pytest -q -p no:cacheprovider -m e2e tests/e2e'
```

## The draft

The namespace encoding is specified by draft-marzot-moq-test-tracks, kept
in [gmarzot/draft-marzot-moq-test-tracks](https://github.com/gmarzot/draft-marzot-moq-test-tracks).
The codec in `src/moq_test_tracks/spec/` implements it. An encoding change
lands in the draft first; the codec PR links to it.

## Issues

GitHub Issues track bugs and features. Include version, the request (namespace
or API call), tool, relay, and logs.

## Security & License

Report security issues via [SECURITY.md](SECURITY.md) — not public
issues. Contributions are licensed under [LICENSE](LICENSE).

Every source file starts with the OpenMOQ header in its comment syntax:

```python
# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.
```

Put a file description in its own comment below the header.
