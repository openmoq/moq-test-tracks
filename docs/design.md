# moq-test-tracks — design and plan

Status: planning. This is the working plan. Reference docs split out as
they settle: [tool-contract.md](tool-contract.md) is the first.

## Goal

A hosted service at `moq-test.ci.openmoq.org` that publishes MoQT test track
sets to a chosen relay on request. The track sets are:

- media track sets per
  [draft-marzot-moq-test-tracks](https://gmarzot.github.io/draft-marzot-moq-test-tracks/draft-marzot-moq-test-tracks.html);
- synthetic moq-test-00 tracks per draft-afrind-moq-test.

The namespace fully describes each track set, so the same name means the same
shape whichever tool emitted it.

Non-goals for now:

- conformance scoring of publishers (that is moq-contribution-interop-runner);
- relay benchmarking;
- ABR ladders.

## Three specs

| Spec | Audience | Defines |
|------|----------|---------|
| The draft | anyone building or subscribing to test tracks | namespace fields, track names, media shape, generator behavior |
| [tool-contract.md](tool-contract.md) | tool and adapter authors | registration, `request.json`, what to publish where, source media, status, exit codes |
| API (to be written in phase 3) | users and scripts | HTTP endpoints, query parameters, presets, auth |

## Request model

A request has two halves:

| Half | Carried by | Examples |
|------|-----------|----------|
| Track characteristics | the namespace | catalog, packaging, codec, height, fps, audio datagrams, content |
| Run parameters | the API request only | tool, relay, mode, MoQT draft, transport, TTL |

- **No defaults on the wire.** The namespace always carries all 17 fields
  explicitly, so each shape has exactly one name. Convenience lives in the
  API: presets (`preset=720p30`) and per-parameter fallbacks fill in
  whatever the caller leaves out. The API returns the full namespace it
  built.
- **Prefix:** `openmoq/mtt/<tool_id>`, plus `/<clip_id>` when the content is
  `clip`. The same shape from two tools gives two comparable namespaces on
  one relay. An optional run field
  (`isolate=1` → `r_<job_id>`) keeps a job from being shared.
- **Dedupe:** a request for a (relay, namespace) that an active job already
  publishes joins that job instead of starting a second encoder.

## Topology

```
            HTTPS
 browser/curl ───► proxy (Caddy) ──► api ──internal──► runner ──► jobs (subprocesses)
                                      │                  │         adapter → tool ──► relay
                                   SQLite              source cache   ffmpeg ┘
```

docker compose runs three long-lived services, two of them from one image:

- **proxy.** Caddy: TLS (ACME DNS-01 via Route53), security headers, rate
  limiting. The only published ports.
- **api** (`mtt api`). Dashboard and API; auth, policy, job registry.
  Holds the SQLite database (keys, jobs, audit) and the session secret.
  Never runs a tool.
- **runner** (`mtt runner`). Hosts every tool, ffmpeg and the source cache.
  Each job is a subprocess tree (ffmpeg plus the adapter and tool) in its
  own process group, started and supervised per the tool contract. The
  runner enforces TTLs itself, so jobs end even if api is down. It is
  reachable only from api on the internal network, and has outbound
  internet for QUIC.

Why jobs are subprocesses and not per-job containers:

- Per-job containers would need the Docker socket in a container that is
  root-equivalent on the host.
- Subprocesses start in milliseconds and keep a tool as one Dockerfile stage
  plus an adapter directory.

Why api and runner are separate containers:

- Tools are MoQT clients talking to relays, and they parse whatever comes
  back. If one is compromised, it lands in a container with no keys, no
  session secret and no inbound port.
- The cost is one internal HTTP interface (start, stop, list, events),
  which the supervisor needs anyway.
- `mtt dev` runs api and runner in one process for local work.

If a future tool's runtime can't share the runner image, it gets a second
runner service built from its own image, with the same agent.
`config/tools.yaml` maps tool → runner. There are still no dynamic
containers.

## Stack

- Python 3.12
- FastAPI + uvicorn
- Pydantic v2 (namespace codec, API models, contract schemas)
- Jinja2 + htmx, vendored with no CDN and a strict CSP
- Server-Sent Events for live job status
- SQLite (stdlib)
- httpx between api and runner

The namespace codec is a pure module with no dependencies, so that tools and
probes can import it.

## API (sketch)

Calls that change state are POST or DELETE. A GET never changes state, so
crawlers, link previews and CSRF can't start jobs.

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/` | session | Dashboard; query parameters prefill the new-job form |
| POST | `/login`, `/logout` | — | Exchange an API key for a session cookie |
| GET | `/api/v1/namespace` | none | Parameters → full namespace and its §8.8 rendering |
| GET | `/api/v1/namespace/decode?ns=` | none | Namespace → fields, or the validation errors |
| GET | `/api/v1/tools`, `/api/v1/relays` | view | Registry and allowlist |
| POST | `/api/v1/jobs/check` | run | Dry run: validation, policy, the adapter's plan |
| POST | `/api/v1/jobs` | run | Start or join a job; JSON body or query parameters |
| GET | `/api/v1/jobs`, `/api/v1/jobs/{id}` | view | List and detail (own jobs; all for admin) |
| GET | `/api/v1/jobs/{id}/events` | view | SSE: state, stats, log lines |
| DELETE | `/api/v1/jobs/{id}` | run | Stop gracefully; `?force=1` kills |
| POST | `/api/v1/admin/stop-all` | admin | Kill switch |
| GET | `/healthz` | none | Liveness |

Query parameters:

| Param | Draft field | | Param | Draft field |
|-------|-------|-|-------|-------|
| `cat` | 1 catalog | | `vfwd` | 9 video forwarding |
| `pkg` | 2 packaging | | `ac` | 10 audio codec |
| `init` | 3 init data | | `ch` | 11 channels |
| `vc` | 4 video codec | | `abr` | 12 audio kbps |
| `h` | 5 height | | `afpg` | 13 audio frames/group |
| `fps` | 6 frame rate | | `afwd` | 14 audio forwarding |
| `vbr` | 7 video kbps | | `content` | 15 content |
| `gop` | 8 group ms | | `dur` | 16 duration s |

- Alternatives to field parameters: `preset=<name>`, or `ns=` with a full
  namespace.
- Run parameters: `tool`, `relay`, `mode` (`namespace` | `publish`), `draft`,
  `transport` (`moqt` | `webtransport`), `ttl`, `isolate`. FORWARD is not a
  parameter: tools send 1 and follow the relay.
- moq-test-00 jobs take `proto=moq_test_00` plus that draft's 15 fields.

Example:

```
curl -X POST -H "Authorization: Bearer $MTT_KEY" \
  "https://moq-test.ci.openmoq.org/api/v1/jobs?preset=1080p60&ac=opus&afwd=3&tool=aiomoqt&relay=moqx_main"
```

## Tools

See [tool-contract.md](tool-contract.md) for the contract. The first tool is
aiomoqt pub_media ([adapters/aiomoqt](../adapters/aiomoqt/)), the reference
engine: the service is complete when aiomoqt covers the draft. Its gaps
become asks for the aiomoqt release session as we hit them. The second is
ffmpeg with the openmoq MoQ muxer ([adapters/ffmpeg_moq](../adapters/ffmpeg_moq/)),
which brings a second MoQ stack (libmoq) and encodes for itself. It is best
effort: the adapter maps what the muxer supports today, and closing its open
items is up to the FFmpeg muxer and libmoq contributors.

Candidates, from the survey of openmoq tools:

| Tool | Source | Serves |
|------|--------|--------|
| aiomoqt `pub_media` | `mpegts`, `mp4` | media: LOC / CMAF / LOCMAF, MSF / CMSF |
| ffmpeg, [openmoq/FFmpeg](https://github.com/openmoq/FFmpeg) `-f moq` (libmoq) | — (encodes itself) | media: LOC / CMAF, MSF / CMSF; H.264, HEVC, AV1, AAC, Opus |
| moxygen `moqtest_publisher` | — | moq-test-00 |
| moqxr `openmoq-publisher` | `fmp4` | media: CMAF + CMSF |
| moq5 `media_publish_loc` | `annexb` | media: LOC + MSF; H.264 only, and libmoq like ffmpeg, so ffmpeg covers it |
| moxygen `moqflvstreamerclient` | `flv` | media: MoqMi, no catalog (outside this draft; possibly a third protocol) |
| moq-playa node-publisher | prepared CMAF fixture | media: CMAF, WebTransport only |

The OBS plugin is excluded because it needs a GUI.

## Source stage

The runner image includes ffmpeg from Debian packages:

- libx264, libx265 and libsvtav1 for video;
- libopus and the native AAC encoder for audio;
- a DejaVu font for drawtext.

Filter graphs and encoder arguments are built only from validated field
values.

- **Content:**
  - `ts`: testsrc2 with drawtext overlays (wall clock, frame and group),
    plus a tone with a one-second beep and flash;
  - `bars`: smptehdbars with 1 kHz;
  - `noise`: a noise source and anoisesrc.
- **Shared recipes:** one module renders the ffmpeg inputs, filters and
  encoder arguments for each content value and field set. The source stage
  and adapters whose tool is ffmpeg use the same module, so a content value
  looks and compresses the same whichever tool emits it.
- **Encoding:** `-bf 0`, `-g` and `-keyint_min` equal to fps × gop,
  `-sc_threshold 0`, realtime presets, and `-b:v -maxrate -bufsize`.
- **Loop cache:** for `mp4` sources, a 60 s file (a whole number of groups)
  is rendered once per (video, audio, content) combination. It is stored as
  `/cache/<sha256>.mp4` with LRU eviction by size. This is much cheaper than
  live encoding.
- **Slots:** live encodes have their own pool, smaller than the job pool.

### Real media (`clip`)

Synthetic content stays the default. For `clip`:

- **Catalog of clips:** `media/sources.yaml` lists each clip: id
  (`[a-z0-9_]+`), title, license, attribution text, official URL, SHA-256,
  and native resolution and frame rate.
- **Not in git:** clips run to hundreds of MB and GitHub caps files at
  100 MB. `mtt media fetch <id>` downloads from the official URL, verifies
  the SHA-256 and writes a mezzanine file to the runner's cache volume. An
  image build can bake clips in.
- **Encoding:** jobs encode from the mezzanine like any other source, live
  or as cached loops. The clip id is the prefix field after the tool id:
  `openmoq/mtt/<tool_id>/<clip_id>/moq_test_tracks_00/…`.
- **Attribution:** the open films are Creative Commons Attribution, not
  public domain. The picture carries a small credit line, the dashboard
  lists credits, and each license is checked against the text in the
  download.
- **First clip:** Big Buck Bunny, preferring a native 30 or 60 fps release
  to avoid frame-rate conversion. Sintel and Tears of Steel (24 fps) can
  follow; Tears of Steel's license requires keeping the credit scroll, so it
  loops in full.

## Job lifecycle

`requested → checked → queued → starting → running → stopping → ended | failed | expired`

- Every job has a TTL, even when draft Field 16 is 0. The default and
  maximum come from policy.
- The runner owns TTLs, and api reconciles with the runner on restart.
- On start, the runner sweeps up orphaned process groups.
- Stats: the last N samples per job, shown as sparklines.
- The audit log records who started or stopped what, from which IP, and
  when.

## Dashboard

- **New job.** A form with presets. Options the chosen tool can't do are
  greyed out. It shows the live namespace (slash form and §8.8 form) with
  copy buttons, and the planned command line from check mode.
- **Active jobs.** Namespace, tool, relay, mode, owner, age and TTL, state,
  live stats, a log tail, player links (templated, with relay and namespace
  filled in), and Stop and Kill buttons.
- **Admin.** All jobs, stop-all, runner health.

## Security model

The service turns an HTTP request into CPU-heavy encoding and outbound UDP
traffic. The threats are resource abuse, traffic aimed at third parties,
and code execution through the launch path.

| Threat | Controls |
|--------|----------|
| Unauthenticated use | An API key is required for everything except `/healthz` and the namespace codec. Anonymous jobs are impossible. |
| Key leakage | Keys look like `mtt_<id>_<secret>`. Only the SHA-256 of the secret is stored. Keys have scopes (`view`, `run`, `admin`), expiry and revocation. A key goes in the `Authorization` header, or is exchanged once for an HttpOnly, Secure, SameSite=Strict cookie. Query strings never carry a key. Keys are issued by a CLI inside the api container, never over HTTP. |
| CSRF / drive-by | GET never changes state. SameSite=Strict cookies, plus an Origin check on POST and DELETE. |
| Traffic aimed at third parties | Relays come from the allowlist ([config/relays.yaml](../config/relays.yaml)), by id. A custom URL needs the admin scope, must be `moqt://` or `https://`, and must resolve to a public address (not private, loopback, link-local, multicast or CGNAT). The runner checks again. |
| Resource exhaustion | Concurrency caps (global, live-encode and per key), caps on height, fps and bitrate (admin keys may exceed the per-key caps, never the global ones), a mandatory TTL, dedupe, and rate limits in Caddy and the api. The runner container has CPU, memory and pids limits. |
| Command injection | argv lists only, never a shell. Fields are validated against closed sets and ranges. Namespace fields are `[a-z0-9_]` and tool ids `[a-z0-9_]+`. Adapters get a reset environment with no secrets. |
| Lateral movement | Containers run as non-root with a read-only root filesystem, tmpfs work directories, `cap_drop: [ALL]` and `no-new-privileges`, and no Docker socket. The api/runner split described in [Topology](#topology). |
| Runaway jobs | The runner owns TTLs. Jobs are killed by process group, orphans are swept at start, and admins have a kill switch. |
| Supply chain | Pinned base images and a lock file, Dependabot, and images built in CI and deployed by digest. |

## Deployment

Everything is in `deploy/compose.yaml`; the host only needs Docker.

- **DNS:** `moq-test.ci.openmoq.org`.
- **Interim host:** moqarena (OCI) until the Linode is ready.
  - moqarena's TCP 80 and 443 are held by the existing g5-web nginx, so the
    compose there publishes HTTPS on another port, set by
    `MTT_HTTPS_PORT` (for example 8443).
  - That port has to be opened in the OCI security list and in ufw.
- **Certificates:** DNS-01 through Route53 works without ports 80 and 443,
  so the same compose runs unchanged on both hosts.
  - The proxy image is Caddy built with `caddy-dns/route53`.
  - It needs an IAM key scoped to the `ci.openmoq.org` zone, kept in
    `deploy/.env`, which is not committed.
- **Linode:** `MTT_HTTPS_PORT=443`, plus a host egress policy that limits
  the runner's UDP to the allowlisted relays.
- **Default relay:** moqx-main.
  - Raw QUIC: `moqt://moqx-main.ci.openmoq.org:4433`.
  - WebTransport: `https://moqx-main.ci.openmoq.org:4433/moq-relay`.
  - It speaks drafts 16 and 18 and negotiates 16 when offered both, so jobs
    send `draft: 18` unless the request asks otherwise.

## Prior art reused

| Source | What we take |
|--------|-------------|
| draft-afrind-moq-test, moxygen `moqtest_server` | namespace-encoded parameters, version field, on-demand serving of a prefix |
| moq-contribution-interop-runner | adapter contract shape: request file, process group, signal teardown, exit 64 = refuse |
| moq-interop-runner `implementations.json` | static registry with per-implementation draft lists |
| DASH-IF livesim2 | on-demand synthetic live streams whose options are in the request path |
| aiomoqt `tools/load_sim.py` | command and event queues for per-process supervision |
| moqx stats stack | `/healthz` open, resource limits, log rotation |

## Phases

| # | Deliverable | Done when |
|---|-------------|-----------|
| 0 | Repo scaffolding, draft -00, tool contract, this plan | reviewed |
| 1 | Namespace codec (moq_test_tracks_00 + moq-test-00) and the `mtt ns` CLI | property tests round-trip; the draft's examples parse |
| 2 | Runner and supervisor, ffmpeg source stage, aiomoqt adapter, `mtt run` | a subscriber plays a track from moqx-main |
| 3 | api: auth, policy, SQLite, dashboard, compose with Caddy; deploy to moq-test.ci.openmoq.org | a keyed user starts, watches and stops a job in the browser |
| 4 | Probe subscriber (shape checks, fps, kbps, latency), `mtt tools verify`, player links | the dashboard shows the received shape per job, and true end-to-end latency (the probe shares the generator's clock) |
| 5 | moq-test-00 via moxygen `moqtest_publisher` | moxygen's conformance client passes against a service job |
| 6 | On-demand responder for `openmoq/mtt/aiomoqt/moq_test_tracks_00` | a plain SUBSCRIBE starts media |
| 7 | More tools: ffmpeg (libmoq), moqxr | each passes `mtt tools verify` |

## Open decisions

1. moqarena interim: publish on an alternate HTTPS port (as planned), or
   move g5-web behind our Caddy?
2. Should a read-only list of active namespaces be public, or should
   everything sit behind a key?

## Draft open issues

To raise with reviewers; these become GitHub issues once the repo is public.

1. **Profile vs separate draft.** Should moq-test-tracks be a profile or
   section of a future moq-test revision, rather than its own draft?
2. **Empty fields in moq-test.** MoQT forbids empty Track Namespace fields
   ("MUST contain at least one byte"), so moq-test-00's blank defaults
   break on current drafts. How should moq-test-01 handle this?
3. **MSF stream mapping.** MSF §6 says each Object goes on a new stream;
   most implementations use one subgroup (stream) per group. Fixes under
   discussion: [#148](https://github.com/moq-wg/msf/issues/148),
   [#169](https://github.com/moq-wg/msf/pull/169) (subgroup 0) and
   [#211](https://github.com/moq-wg/msf/pull/211) (catalog `streamMapping`).
   Tuple fields 9 and 14 exercise both mappings; if `streamMapping` lands,
   generators set it to match.
4. **ABR.** How should a namespace express ladders and multiple renditions?
5. **Profile and level.** Should tuple field 4 accept codec strings as well
   as short names? Codec strings contain `.`, which the §8.8 representation
   escapes.
6. **MSF with CMAF.** CMSF extends MSF with `cmaf` packaging and has no
   catalog marker of its own, so catalog `msf` covers both and the
   packaging field selects the CMSF extensions. Is that enough for players?
7. **Timestamp mapping.** The timing relies on draft-frindell-moq-timestamp,
   which players don't implement yet (G5 player will). Is it the right base?
8. **Timed metadata and captions.** Should there be a field for them?
