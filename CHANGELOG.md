<!-- Version headings are setext h1, sections ATX h2; MD003 has no style for that. -->
<!-- markdownlint-disable-file MD003 MD041 -->
<!-- markdownlint-configure-file { "no-duplicate-heading": { "siblings_only": true } } -->

All notable changes to moq-test-tracks are recorded here.
The format follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).
How to add an entry: see [CONTRIBUTING.md](/CONTRIBUTING.md#changelog).

[Unreleased]
============

## Added

- Namespace codec for `moq_test_tracks_00` and `moq-test-00`, with the MoQT name representation, and the `mtt ns encode|decode|render` command.
- `mtt run`: runs one job in the foreground against an allowlisted relay. It covers the content recipes (`ts`, `bars`, `noise`), live MPEG-TS and cached mp4-loop sources, the aiomoqt adapter, supervision (TTL, stop, exit codes) and `@mtt` status events.
- `mtt runner`: the internal runner API (`/jobs` start, list, detail, stop or force-kill, event stream; `/stop-all`; `/healthz`) behind a bearer token. It re-validates every job, joins requests for an active relay and namespace, enforces the `global.max_jobs` and `max_live_encodes` policy caps, runs jobs at nice `MTT_JOB_NICE` (default 10), and on start kills process groups an earlier runner left behind.
- `mtt api`: the public API and dashboard. Callers authenticate with an API key (`Authorization: Bearer mtt_…`) or a session cookie from `/login`. Roles are `view`, `run` and `admin`. Endpoints: `/api/v1/namespace`, `/namespace/decode`, `/presets`, `/tools`, `/relays`, `/jobs` (check, start or join, list, detail, stop, events) and `/admin/stop-all`. The runner gains `POST /check`.
- `mtt keys create|list|revoke`: API keys, stored as the SHA-256 of their secret.
- Presets and per-parameter fallbacks for job requests (`preset=1080p60`, or only the fields that differ). Namespaces must use the `openmoq/mtt/<tool>` prefix.
- Container image (`docker/Dockerfile`: ffmpeg with x264, x265, SVT-AV1 and Opus; aiomoqt; non-root), a hardened compose project (`deploy/compose.yaml`: Caddy with automatic HTTPS, api, runner on an internal network) and `deploy/deploy.sh <ssh-host>` for manual deploys.
