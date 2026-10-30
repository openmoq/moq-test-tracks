# API

The service runs at `https://moq-test.ci.openmoq.org`. Everything except
`/healthz` and the namespace codec (presets, namespace, decode) needs an API
key.

## Keys

Keys look like `mtt_<id>_<secret>`. The service operators issue them; open an
issue in this repository to ask for one. A key has a role and an expiry:

| Role | Can |
|------|-----|
| `view` | read jobs, tools, relays and clips; follow job events |
| `run` | `view`, plus check, start and stop its own jobs |
| `admin` | `run`, plus stop any job and stop all jobs |

Every key gets the per-key limits in `config/policy.yaml` (today: height up to
1080, up to 60 fps, video up to 8000 kbps, TTL up to 1 h, 16 jobs). A key
created as unrestricted, or an admin key, may exceed them up to the global
caps (16 jobs in total, 2 live encodes, TTL up to 24 h).

Send the key in a header:

```
Authorization: Bearer mtt_<id>_<secret>
```

The dashboard exchanges a key for a session cookie at `POST /login`. Query
strings never carry a key.

## Endpoints

| Method | Path | Role | Purpose |
|--------|------|------|---------|
| GET | `/healthz` | none | Liveness, and whether the runner answers |
| GET | `/api/v1/me` | view | The calling key: id, name, role |
| GET | `/api/v1/presets` | none | Presets, fallbacks and parameter names |
| GET | `/api/v1/namespace?…` | none | Build a namespace from parameters, without starting anything |
| GET | `/api/v1/namespace/decode?ns=…` | none | Decode a namespace into its fields |
| GET | `/api/v1/tools` | view | Enabled publisher tools and their capabilities |
| GET | `/api/v1/relays` | view | The relay allowlist and the default relay |
| GET | `/api/v1/clips` | view | Clips, with what has been recorded |
| POST | `/api/v1/jobs/check` | run | Validate a job and return the tool's plan, without starting it |
| POST | `/api/v1/jobs` | run | Start a job, or join the running job for the same relay and namespace |
| GET | `/api/v1/jobs?active=true` | view | List jobs |
| GET | `/api/v1/jobs/{id}` | view | One job |
| GET | `/api/v1/jobs/{id}/events` | view | Server-sent events for a job |
| DELETE | `/api/v1/jobs/{id}` | run | Stop the job (owner or admin); `?force=1` kills it |
| POST | `/api/v1/admin/stop-all` | admin | Stop every job |

## Describing a job

`POST /api/v1/jobs` and `/jobs/check` take a JSON body or the same names as
query parameters. Give either a full namespace or parameters:

```json
{"namespace": "openmoq/mtt/aiomoqt/moq_test_tracks_00/clock/msf/loc/h264/720/30/aac/2",
 "relay": "moqx-main", "draft": 18, "mode": "publish", "ttl_s": 600}
```

```json
{"preset": "720p30", "params": {"pkg": "cmaf"}, "relay": "aiomoqt-relay", "draft": 22}
```

**Track parameters.** Each one sets a tuple field of the draft. Required
fields you leave out take the preset's or the service's fallback values, and
optional ones (9 to 13) take the value the draft gives them when omitted. The
response always carries the namespace in its shortest form, the one the
service publishes; a namespace you pass in is rewritten to it.

| Param | Field | Param | Field |
|-------|-------|-------|-------|
| `content` | 1 content | `ch` | 8 channels |
| `cat` | 2 catalog | `init` | 9 init data (`catalog`) |
| `pkg` | 3 packaging | `gop` | 10 group ms (2000, or 2002 at 29_97) |
| `vc` | 4 video codec | `adel` | 11 audio delivery: `1`–`100` frames per group, or `dgram` (1) |
| `h` | 5 height | `dur` | 12 duration s (0) |
| `fps` | 6 frame rate | `vt` | 13 video tracks: `1`, `alt2`–`alt7`, `view2`–`view16` (1) |
| `ac` | 7 audio codec | | |

Bitrates are not parameters: the draft derives the video bitrate from height
and frame rate, and the audio bitrate from codec and channels.
`/api/v1/namespace/decode` reports them. The parameters `vbr`, `abr`, `vfwd`,
`afwd` and `afpg` are refused with what replaced them.

- `preset`: a name from `/api/v1/presets` (`720p30`, `1080p60`, `audio`,
  `cmaf`, …).
- `clip`: a clip id from `/api/v1/clips`. It sets `content` to `clip` and puts
  the clip id in the namespace prefix.

**Run parameters.** These are not part of the namespace.

| Param | Values | Default |
|-------|--------|---------|
| `tool` | `/api/v1/tools` | `aiomoqt` |
| `relay` | `/api/v1/relays` | `moqx-main` |
| `draft` | a draft the relay speaks | the relay's default |
| `transport` | `moqt` (raw QUIC) or `webtransport` | `moqt` |
| `mode` | `publish` or `namespace`, see below | `publish` |
| `ttl_s` (or `ttl`) | seconds | 1800 |

**Mode.** `publish` sends PUBLISH_NAMESPACE, then PUBLISH for each track
(FORWARD 1); the relay starts forwarding when someone subscribes. `namespace`
sends only PUBLISH_NAMESPACE for the job's one namespace, and the relay
forwards each SUBSCRIBE to the publisher. Neither is the draft's on-demand
serving of a whole prefix, which the service does not offer yet: a job always
publishes one fixed set of tracks.

**Prefix.** The service publishes under `openmoq/mtt/<tool>`, plus
`/<clip id>` for clips. The draft's examples use the prefix `openmoq/mtt`;
the fields from `moq_test_tracks_00` on are the same.

## Jobs

A job response carries `id`, `state`, `namespace`, `namespace_safe`, `tracks`,
`tool`, `relay`, `url`, `transport`, `draft`, `mode`, `ttl_s`, `created`,
`expires`, `owner`, `owner_name`, `plan` (the tool's command line), `source`,
`exit_code`, `error`, `last_stats` and `restarts`. `POST /api/v1/jobs` adds `joined`: true
when the same relay and namespace were already running, and you share that
job.

States: `checked`, `starting`, `running`, `stopping`, then `ended`, `expired`
(TTL reached), `killed` or `failed`. A job is publishing from `running`.

If the tool loses every relay (for example when a relay restarts), the job
is `reconnecting` until one is back. aiomoqt redials the relay itself, 1, 2,
4… s apart (at most 30 s), and its media timeline runs on meanwhile. A tool
that exits instead is started again on the same namespace after the same
backoff, until the TTL ends; `restarts` counts these, and after 20 failed
attempts in a row the job ends `failed`. The
first job of a clip shape renders a loop first, which can take up to a minute
at 2160p.

`/api/v1/jobs/{id}/events` is a server-sent event stream:

| Event | Data |
|-------|------|
| `state` | `{"state": …, "exit_code": …}` |
| `event` | the tool's status: `ready`, `stats` (per-track objects, groups, kbps), `endpoint_lost`, `endpoint_up`, `done`; and the service's `restart` (`attempt`, `delay_s`, `exit_code`) |
| `log` | one line of the tool's log |

A restart of the service itself still ends running jobs, and their records
are lost.

## Errors

Errors return `{"detail": {"errors": ["…"]}}`. A namespace error names the
tuple field.

| Status | Meaning |
|--------|---------|
| 401 | no key, or an unknown, expired or revoked one |
| 403 | the role is too low, a stop by someone other than the owner, or a cross-origin cookie request |
| 422 | invalid parameters or namespace, a relay or draft not allowed, or the tool cannot produce it |
| 429 | a cap is reached (jobs, live encodes, per-key jobs); retry later |
| 503 | the runner is unreachable |

## Subscribing

Subscribe with any MoQT client to the job's namespace on the job's relay. Track
names are `catalog`, `video` and `audio`; ladders and views add `video_1`,
`video_2` and so on.

- **moqx-main:** subscribe with draft 18. moqx negotiates draft 16 with a
  client that offers both 16 and 18, and the draft-18 timestamp properties
  then reach that client mis-encoded.
- **Example:**
  `python -m aiomoqt.tools.sub_media moqt://moqx-main.ci.openmoq.org:4433 -N <namespace> --draft 18 --analyze`

## Example

```sh
read -rs MTT_KEY
U=https://moq-test.ci.openmoq.org
H="Authorization: Bearer $MTT_KEY"
curl -s -H "$H" "$U/api/v1/namespace?preset=720p30&pkg=cmaf"
job=$(curl -s -X POST -H "$H" "$U/api/v1/jobs?preset=720p30&ttl=600")
id=$(echo "$job" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
curl -s -N -H "$H" "$U/api/v1/jobs/$id/events"
curl -s -X DELETE -H "$H" "$U/api/v1/jobs/$id"
```
