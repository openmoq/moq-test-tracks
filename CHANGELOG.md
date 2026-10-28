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
- High-fidelity clips: `bbb_4k30` (Akamai-hosted Big Buck Bunny DASH, 4K30 about 12 Mbps), `bbb_4k60` (Big Buck Bunny 4K60) and `akamai_cmaf` (Akamai live CMAF, 720p29.97). Clips take `start_s` to begin the recording past a file's opening, and credits may use `|` and `©`.
- Monitoring: node-exporter, Prometheus (30 days) and Grafana at `/grafana/`, with Platform (CPU, load, memory, disk, network) and Service (jobs against the caps, per-job bitrate, relay-loss restarts) dashboards. The runner serves `/metrics`. Grafana trusts only identity from Caddy, which asks the api (`/auth/grafana`) to accept the session or key first; it has no login, sign-up or anonymous access of its own. The dashboard links to it.
- Clips from local files and the Artemis II launch set. A clip can name a `video` file and a separate `audio` file under `<var>/media` instead of a URL; `mtt media import <id>` turns them into the clip's mezzanine, and `loop_s` sets the loop length. `media/sources.yaml` adds nine synced Artemis II launch views (NASA footage, from openmoq/FFmpeg's multi-view demo set), each with the NASA TV audio and a 150 s loop.
- Jobs survive relay restarts: when the tool loses every relay (exit 69), the runner starts it again on the same namespace after 1, 2, 4… s (capped at 30 s) until the TTL ends, at most 20 failed attempts in a row. The job shows `reconnecting` meanwhile, `restarts` counts restarts, and a `restart` event marks each one. A stop or the TTL ends a reconnecting job.
- `docs/api.md`: the API reference (keys and roles, endpoints, job parameters, modes, prefixes, job states, events, errors, subscribing). The README gains a getting-started section, and the dashboard links to it, the API, the draft and the source, and says how to get a key.
- Unrestricted keys: `mtt keys create --unrestricted` makes a key exempt from the per-key limits (height, frame rate, bitrate, TTL, job count) up to the global caps, without admin rights. It requires `--expires-days` of 30 or less. `mtt keys list` shows each key's limits, and key creation is audited. The runner API's `admin` flag is now `unrestricted`.

## Changed

- Job caps: 16 jobs in total, per key and for aiomoqt (were 6, 2 and 6). Live encodes stay capped at 2.
- **Breaking:** relay ids are `moqx-main` (was `moqx_main`) and `aiomoqt-relay`, which replaces `moqarena`; both match their hostnames. `aiomoqt-relay` tracks aiomoqt dev builds.
- **Breaking:** content `ts` (tuple field 15) is renamed `clock`, following the draft, which now names content classes by what they measure and requires the capture time in the picture. Namespaces, presets, the dashboard and the manifests use `clock`.
- **Breaking:** `moq_test_tracks_00` follows the revised draft. New tuple field 3, init data (`catalog`, `track`, `object`; `_` with no catalog); later fields move up one, so duration is field 16. Catalog `cmsf` is gone: `msf` with `cmaf` or `locmaf` packaging uses the CMSF extensions. Video codec `h264` is H.264 High, and the new `h264cb` is Constrained Baseline. The API, CLI (`--init`) and dashboard take the `init` parameter, defaulting to `catalog`. The aiomoqt adapter refuses `track` and `object` init data.
- The runner image runs aiomoqt's 0.14.0 development branch (gmarzot-0.14.0 @ 3d920db) until 0.14.0 is released. pub_media there declares audio and catalog at priority 64 and video at 128, which relays use to send audio ahead of video; it keeps the flags the adapter uses.
- The runner image moves to aiomoqt 0.13.3: wire fixes for FETCH object status at d16/d18, end-of-range markers and FILL_PARAMETERS encoding. pub_media's flags and output are unchanged.
- The runner image and the aiomoqt adapter move to aiomoqt 0.13.2. Every job now sends draft-frindell-moq-timestamp timing (`--timestamp-mapping`: TIMESCALE and TIMESTAMP_MAPPING Track Properties, `prft` in CMAF and LOCMAF), and job events come from pub_media's own `@mtt` output. The adapter newly accepts video codec `h265`, audio codec `opus`, video and audio forwarding 1 (not with `locmaf`), and any audio frames per group. Publish mode delivers media again.
- Admin keys may exceed the per-key limits on height, frame rate, video bitrate and TTL (up to `global.max_ttl_s`); the global caps still apply. The runner API takes an `admin` flag, which the api service sets from the caller's role. `config/tools.yaml`: aiomoqt may run 6 jobs.

## Fixed

- A stop or kill that arrives while a job's tool is starting or restarting is no longer lost; before, the job kept publishing until its TTL, or past it after a kill.
- Jobs that need the same mp4 loop source no longer render it concurrently into one temporary file, which failed all but one job and could leave a corrupt file in the cache. One render runs per cache entry; each render writes its own temporary file and is checked before it replaces the entry, and an incomplete cached file is rendered again.
- Opus audio runs at the requested bitrate. libopus's default VBR produced about 96 kbps for `abr` 64 on the `clock` tone; the recipe now uses CBR (`-vbr off`).
- The per-key job limit (`per_key_defaults.max_jobs`) is enforced for keys below admin; joining an active job does not count.
- A job's log no longer reports `source stage exited` when the tool ends its run normally.
