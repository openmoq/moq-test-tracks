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
- `mtt run`: runs one job in the foreground against an allowlisted relay. It covers the content recipes (`clock`, `bars`, `noise`), live MPEG-TS and cached mp4-loop sources, the aiomoqt adapter, supervision (TTL, stop, exit codes) and `@mtt` status events.
- `mtt runner`: the internal runner API (`/jobs` start, list, detail, stop or force-kill, event stream; `/stop-all`; `/healthz`) behind a bearer token. It re-validates every job, joins requests for an active relay and namespace, enforces the `global.max_jobs` and `max_live_encodes` policy caps, runs jobs at nice `MTT_JOB_NICE` (default 10), and on start kills process groups an earlier runner left behind.
- `mtt api`: the public API and dashboard. Callers authenticate with an API key (`Authorization: Bearer mtt_…`) or a session cookie from `/login`. Roles are `view`, `run` and `admin`. Endpoints: `/api/v1/namespace`, `/namespace/decode`, `/presets`, `/tools`, `/relays`, `/jobs` (check, start or join, list, detail, stop, events) and `/admin/stop-all`. The runner gains `POST /check`.
- `mtt keys create|list|revoke`: API keys, stored as the SHA-256 of their secret.
- Presets and per-parameter fallbacks for job requests (`preset=1080p60`, or only the fields that differ). Namespaces must use the `openmoq/mtt/<tool>` prefix.
- Container image (`docker/Dockerfile`: ffmpeg with x264, x265, SVT-AV1 and Opus; aiomoqt; non-root), a hardened compose project (`deploy/compose.yaml`: Caddy with automatic HTTPS, api, runner on an internal network) and `deploy/deploy.sh <ssh-host>` for manual deploys.
- Clip content. `media/sources.yaml` lists the deployment's clips, starting with two Akamai-hosted live channels (`akamai_test`, `nasa_tv`). `mtt media fetch <id>` records a fixed excerpt into the runner cache (HTTPS only), and `mtt media list` shows what is fetched. Clip namespaces carry the clip id after the tool id (`openmoq/mtt/<tool>/<clip>/moq_test_tracks_00/…`); the API, the dashboard and `mtt run --clip` take `clip=<id>`, and `GET /api/v1/clips` lists the clips. Jobs encode the recording to the namespace's shape with the credit line in the picture; the aiomoqt adapter accepts `clip`.

## Changed

- Job caps: 16 jobs in total, per key and for aiomoqt (were 6, 2 and 6). Live encodes stay capped at 2.
- **Breaking:** relay ids are `moqx-main` (was `moqx_main`) and `aiomoqt-relay`, which replaces `moqarena`; both match their hostnames. `aiomoqt-relay` tracks aiomoqt dev builds.
- **Breaking:** content `ts` (tuple field 15) is renamed `clock`, following the draft, which now names content classes by what they measure and requires the capture time in the picture. Namespaces, presets, the dashboard and the manifests use `clock`.
- **Breaking:** `moq_test_tracks_00` follows the revised draft. New tuple field 3, init data (`catalog`, `track`, `object`; `_` with no catalog); later fields move up one, so duration is field 16. Catalog `cmsf` is gone: `msf` with `cmaf` or `locmaf` packaging uses the CMSF extensions. Video codec `h264` is H.264 High, and the new `h264cb` is Constrained Baseline. The API, CLI (`--init`) and dashboard take the `init` parameter, defaulting to `catalog`. The aiomoqt adapter refuses `track` and `object` init data.
- The runner image and the aiomoqt adapter move to aiomoqt 0.13.2. Every job now sends draft-frindell-moq-timestamp timing (`--timestamp-mapping`: TIMESCALE and TIMESTAMP_MAPPING Track Properties, `prft` in CMAF and LOCMAF), and job events come from pub_media's own `@mtt` output. The adapter newly accepts video codec `h265`, audio codec `opus`, video and audio forwarding 1 (not with `locmaf`), and any audio frames per group. Publish mode delivers media again.
- Admin keys may exceed the per-key limits on height, frame rate, video bitrate and TTL (up to `global.max_ttl_s`); the global caps still apply. The runner API takes an `admin` flag, which the api service sets from the caller's role. `config/tools.yaml`: aiomoqt may run 6 jobs.

## Fixed

- Jobs that need the same mp4 loop source no longer render it concurrently into one temporary file, which failed all but one job and could leave a corrupt file in the cache. One render runs per cache entry; each render writes its own temporary file and is checked before it replaces the entry, and an incomplete cached file is rendered again.
- The per-key job limit (`per_key_defaults.max_jobs`) is enforced for keys below admin; joining an active job does not count.
- A job's log no longer reports `source stage exited` when the tool ends its run normally.
