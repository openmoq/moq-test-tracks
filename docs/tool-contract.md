# Tool contract (v1)

How a publisher tool plugs into moq-test-tracks. The namespace encoding and
media shape are defined by
[draft-marzot-moq-test-tracks](https://gmarzot.github.io/draft-marzot-moq-test-tracks/draft-marzot-moq-test-tracks.html);
this document defines how the service asks a tool to produce them, and what
the tool must do. Nothing here is specific to one tool.

## Roles

| Role | Owns |
|------|------|
| **Service** (mtt runner) | Validates the request and builds the namespace. Chooses the relay. Produces the source media. Starts, watches and stops the job. |
| **Adapter** | One executable per tool. Translates `request.json` into the tool's own command line and starts the tool. It refuses anything the tool can't do. |
| **Tool** | Connects to the relay and publishes the track set. |

An adapter is usually a short script. It may also be the tool itself, if the
tool reads `request.json` natively.

## Adding a tool

1. **Install the tool** into the runner image: add a stage to
   [docker/Dockerfile](../docker/Dockerfile).
2. **Create `adapters/<tool_id>/`** with:
   - `manifest.yaml` — identity, capabilities, source formats, stop
     behavior ([below](#manifest)).
   - `run` — the adapter executable ([below](#launch)).
   - `README.md` — the mapping from fields to tool options, and known gaps.
3. **Enable it** in [config/tools.yaml](../config/tools.yaml).
4. **Run `mtt tools verify <tool_id>`.** This runs the smoke matrix against a
   local relay and checks every result with the probe subscriber
   ([Verification](#verification)).
5. **Open a PR.**

`tool_id` matches `[a-z0-9_]+`. It becomes a namespace field
(`openmoq/mtt/<tool_id>/…`), so it must render verbatim in MoQT names.

## Manifest

`adapters/<tool_id>/manifest.yaml`:

```yaml
contract_version: 1
id: aiomoqt
name: aiomoqt pub_media
repository: https://github.com/gmarzot/aiomoqt
version: 0.12.0                    # tool version installed in the image
protocols: [moq_test_tracks_00]    # version fields this tool can produce
sources: [mpegts, mp4]             # formats check mode may request
stop:
  signal: SIGINT                   # first signal sent on stop
  grace_s: 3                       # then SIGKILL
capabilities:                      # coarse; used to grey out UI options
  catalog: [msf]
  init_data: [catalog]
  packaging: [loc, cmaf, locmaf]
  video_codec: [h264, h264cb, av1, none]
  audio_codec: [aac, none]
  video_forwarding: [0]
  audio_forwarding: [0, 3]
  audio_frames_per_group: [1]
  content: [ts, bars, noise, clip]
  transport: [moqt, webtransport]
  datagram_transport: [moqt]
  draft: [14, 16, 18, 20, 22]
  mode: [namespace, publish]
  max_endpoints: 8                 # >1 = one process publishes to several relays
```

- **Capability keys** are draft field names. A key the manifest omits means
  the tool can do any value of that field.
- **Combination rules** (for example, "datagram audio only with LOC") belong
  in the adapter's check mode, not in the manifest.
- **`protocols`** lists the version fields the tool can produce. A tool that
  serves moq-test lists `moq-test-00`.
- **`sources`** lists the formats ([Source media](#source-media)) that check
  mode may ask for. `[]` means the tool encodes for itself.

## Launch

The service writes `request.json` to the job directory. It then runs
`adapters/<tool_id>/run`:

- in a new process group;
- as an unprivileged user;
- with the job directory as the working directory;
- with stdin set to `/dev/null`.

The environment is reset to only these variables:

| Variable | Value |
|----------|-------|
| `MTT_CONTRACT_VERSION` | `1` |
| `MTT_MODE` | `check` or `run` |
| `MTT_REQUEST_FILE` | absolute path of `request.json` |
| `MTT_JOB_DIR` | writable job directory; logs and scratch files go here |
| `PATH`, `HOME`, `LANG` | sanitized |

No secrets are ever passed to a tool.

### request.json

```json
{
  "contract_version": 1,
  "job_id": "j_7f3a9c",
  "protocol": "moq_test_tracks_00",
  "namespace": ["openmoq", "mtt", "aiomoqt", "moq_test_tracks_00",
                "msf", "loc", "catalog", "h264", "720", "30", "2500", "2000", "0",
                "aac", "2", "128", "1", "0", "ts", "0"],
  "namespace_safe": "openmoq-mtt-aiomoqt-moq_test_tracks_00-msf-loc-catalog-h264-720-30-2500-2000-0-aac-2-128-1-0-ts-0",
  "tracks": ["catalog", "video", "audio"],
  "fields": {
    "catalog": "msf", "packaging": "loc", "init_data": "catalog",
    "video_codec": "h264", "video_height": 720, "video_width": 1280,
    "frame_rate": "30", "video_kbps": 2500, "group_ms": 2000, "video_forwarding": 0,
    "audio_codec": "aac", "audio_channels": 2,
    "audio_kbps": 128, "audio_frames_per_group": 1, "audio_forwarding": 0,
    "content": "ts", "duration_s": 0
  },
  "endpoints": [
    {"url": "moqt://moqx-main.ci.openmoq.org:4433", "transport": "moqt", "draft": 18}
  ],
  "mode": "namespace",
  "ttl_s": 1800,
  "source": {"format": "mpegts", "path": "/work/j_7f3a9c/source.ts", "fifo": true, "loop": false},
  "clip": null,
  "tls": {"verify": true, "ca_file": null}
}
```

- **`namespace`** holds the exact tuple to publish, as UTF-8 strings.
  `namespace_safe` is its MoQT §8.8 rendering, for log and file names.
- **`fields`** are the decoded characteristic fields. Fields that don't apply
  are `null`. `frame_rate` stays a string, so that `29_97` is unambiguous.
- **`endpoints`** has one entry unless the manifest's `max_endpoints` is
  more than 1.
- **`clip`** is set when `content` is `clip`:
  `{"id": "bbb", "path": "/cache/clips/bbb.mp4", "attribution": "…"}`. A
  tool that encodes for itself reads the clip from `path` and shows
  `attribution` in the picture, as the shared recipes do.
- **`source`** is the media the service prepared, in the format check mode
  asked for ([Source media](#source-media)). It is `null` when check mode
  asked for none. In check mode itself, `source` is always `null`.

## What the tool must publish

These rules hold for every tool. They restate the draft operationally.

**Connection**
- Connect to every entry in `endpoints`, using exactly the given transport
  and MoQT draft. Never fall back to another draft or transport.
- Verify TLS unless `tls.verify` is false. Use `tls.ca_file` when it is set.

**Namespace and tracks**
- Publish exactly `namespace`, byte for byte. Never add, drop or rewrite a
  field.
- Publish exactly the track names in `tracks` (`catalog`, `video`, `audio`),
  and no others.

**Mode**

| `mode` | Messages |
|--------|----------|
| `namespace` | PUBLISH_NAMESPACE for `namespace`, then serve SUBSCRIBE and FETCH for its tracks |
| `publish` | PUBLISH_NAMESPACE for `namespace`, then PUBLISH for each track, `catalog` first; also serve SUBSCRIBE and FETCH |

- PUBLISH carries FORWARD 1, or omits it (the default is 1). After that,
  follow the relay's REQUEST_UPDATE: stop sending while paused, resume
  when told.

**Catalog** (when `catalog` is `msf`)
- Publish the catalog before any media Object.
- The catalog lists exactly the published media tracks, with codec strings
  and parameters that match `fields`. With `cmaf` or `locmaf` packaging it
  uses the CMSF extensions.
- Carry decoder initialization data only where `init_data` says:
  - `catalog`: inline in the catalog's initialization data list;
  - `track`: in the VIDEO_CONFIG or AUDIO_CONFIG Track Property;
  - `object`: in the VIDEO_CONFIG or AUDIO_CONFIG Object Property on the
    first Object of each Group.
- The tool may republish the catalog as a new Group, unchanged or updated.

**Objects and Groups**
- Video:
  - One Object per frame. With `cmaf`, an Object is one CMAF chunk holding
    one frame.
  - One Group per key-frame interval, starting with the key frame. A Group
    is a whole number of frames.
- Audio: one Object per codec frame, and `audio_frames_per_group` Objects
  per Group.
- Subgroups follow the forwarding fields: 0 = one Subgroup per Group,
  1 = one Subgroup per Object.
- Audio forwarding 3 sends every audio Object as a datagram. If an Object
  doesn't fit, the tool exits with 64 rather than falling back to a stream.

**Timing** (draft §Timing)
- Capture time is the scheduled wall-clock time of each frame: the start
  time of the track plus the frame's position on the frame (or audio frame)
  cadence. It is not the time the tool happens to read or send the frame.
- Every media track carries the TIMESCALE and TIMESTAMP_MAPPING Track
  Properties (draft-frindell-moq-timestamp), without CLOCK_ID:
  - video: timescale 1000 × fps (24000, 30000 or 60000 for `23_976`,
    `29_97` and `59_94`), Object Multiplier 1000 (1001 for those rates);
  - audio: timescale 48000, Object Multiplier = samples per frame (1024 for
    AAC, 960 for Opus);
  - Group Multiplier = Objects per Group × Object Multiplier; Base Group =
    first Group ID; Base Timestamp = capture time of its first Object.
- An Object whose capture time differs from its mapped timestamp carries
  OBJECT_TIMESTAMP with the correction. On schedule, no per-Object timing
  is sent.
- `loc`: the LOC Timestamp property (0x10) is the capture time in µs since
  the Unix epoch. Never send the LOC Timescale property (0x08).
- `cmaf`, `locmaf`: the first Object of each Group contains a `prft` box
  with the capture time.

**Media**
- Publish the source media as given: package it, don't transcode it. The
  source already matches `fields`.
- A tool that asks for no source encodes for itself, and is then
  responsible for matching `fields`.

**Duration**
- When `duration_s` is greater than 0, end each track after that many seconds
  using the MoQT end-of-track signaling, then exit 0.
- Exit at `ttl_s` regardless. The service also enforces the TTL with signals.

**Fan-out**
- With several endpoints, publish the same media to each.
- If one relay is lost, carry on with the rest and report it. Exit 69 only
  when none remain.

## Source media

The service renders source media with ffmpeg, using only validated field
values. Every source format guarantees:

- codec, resolution, frame rate, bitrate target and channels exactly as in
  `fields`, with audio at 48 kHz;
- a key frame exactly at each group boundary, no B-frames, and in-band
  parameter sets on every key frame;
- content as specified by the draft's `content` field; for `clip`, the
  clip's mezzanine with its credit line in the picture.

| `format` | Delivery | Notes |
|----------|----------|-------|
| `mpegts` | FIFO, live | H.264/H.265 with AUDs, AAC in ADTS, or Opus |
| `annexb` | two FIFOs, live: `video_path` (Annex B) and `audio_path` (ADTS AAC or Ogg Opus) | For tools that take elementary streams. A path is `null` when the namespace has no video or no audio. |
| `fmp4` | FIFO, live | An init segment, then one moof+mdat per frame (CMAF chunks) |
| `flv` | FIFO, live | H.264 + AAC only |
| `mp4` | file, `loop: true` | Non-fragmented and not interleaved. Its length is a whole number of groups, so the tool can loop it seamlessly. Cached and shared between jobs. |

For a live source, the service starts ffmpeg writing to the FIFO before
starting the adapter. The FIFO only produces data while the tool reads it.

## Check mode

The service runs `MTT_MODE=check` before every job. The adapter must not
connect to anything or start the tool. It exits 0 or 64 and prints one JSON
object on stdout.

To accept:

```json
{"accept": true, "source": "mpegts", "argv": ["python", "-u", "-m", "aiomoqt.tools.pub_media", "…"], "notes": []}
```

`source` names the format the service must prepare. It is one of the
manifest's `sources`, or `null` for none.

To refuse:

```json
{"accept": false, "reasons": [{"field": "audio_codec", "message": "opus is not supported with loc packaging"}]}
```

`argv` is shown in the dashboard as the planned command. The API passes
`reasons` through to the client.

## Run mode

- **Status.** A stdout line starting with `@mtt ` followed by JSON is a
  status report. Every other line on stdout or stderr is log output, which
  the service keeps in a ring buffer and in `$MTT_JOB_DIR/log`.
  ```
  @mtt {"event":"ready","endpoints":[{"url":"moqt://…","draft":18,"state":"up"}]}
  @mtt {"event":"stats","t":12.0,"tracks":{"video":{"objects":360,"groups":6,"kbps":2480,"dropped":0},"audio":{"objects":562,"groups":562,"kbps":128,"dropped":0}},"subscribers":1}
  @mtt {"event":"endpoint_lost","url":"moqt://…","reason":"…"}
  @mtt {"event":"done","reason":"duration"}
  ```
  - The job is `running` from the `ready` event.
  - Send `stats` every 1–5 s.
  - An adapter whose tool can't produce these lines translates the tool's
    own output.
- **Stop.** The service sends the manifest's `stop.signal` to the process
  group, then SIGKILL after `stop.grace_s`. It does the same on TTL expiry.
- **Exit codes:**

  | Code | Meaning |
  |------|---------|
  | 0 | ended normally (duration, TTL or stop signal) |
  | 64 | request refused |
  | 69 | no endpoint reachable, or all endpoints lost |
  | 70 | internal error |

  Any other code is a failure.

## Verification

`mtt tools verify <tool_id>`:

- runs every manifest capability value at least once, combined with the
  others at their most common values;
- runs each check for 20 s against a local relay;
- has the probe subscriber check every run against the draft's
  *Subscriber Checks*.

A tool is listed in the dashboard only for combinations it has passed.

## Versioning

`contract_version` increments on any change an existing adapter would
notice. The service runs adapters of the versions it supports and reports
the rest as disabled.
