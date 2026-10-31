# Using moq-test-tracks

moq-test-tracks publishes MoQT test tracks on request
([draft-marzot-moq-test-tracks](https://gmarzot.github.io/draft-marzot-moq-test-tracks/draft-marzot-moq-test-tracks.html)).
You choose a shape (codec, resolution, packaging, catalog, audio delivery) and
a relay; the service publishes that track there, and your player or relay
subscribes to it. This guide is for holders of a token. The full endpoint
list is in the [API reference](api.md).

## Your token

The operators send you a token: one line starting `mtt1_`, about 150–200
characters. It says who you are, what you may do, which relays you may
publish to, and when it expires.

Treat it like a password. Anyone who has it can start tracks as you. Keep it
out of issues, chats, scripts you commit and shell history. If it leaks, or
you need other relays or limits, ask the operators: they revoke the old one
and issue a new one. A token cannot be extended or changed.

In a shell, read it without echoing it:

```sh
read -rs MTT_TOKEN && export MTT_TOKEN   # paste, then Enter
U=https://moq-test.ci.openmoq.org
H="Authorization: Bearer $MTT_TOKEN"
```

See what it allows:

```sh
curl -s -H "$H" $U/api/v1/token
```

```json
{"token_id": "2fb4139e28322b7d", "name": "peter-chave", "role": "run",
 "tier": "standard", "relays": ["us-ord-3"], "expires": 1801232230.0}
```

| Field | Meaning |
|-------|---------|
| `role` | `view`: watch jobs. `run`: also start and stop your own. `admin`: also stop anyone's |
| `tier` | `standard`: height up to 1080, up to 60 fps, TTL up to 1 h. `unrestricted`: the service's caps only (TTL up to 24 h) |
| `relays` | the relays you may publish to |
| `expires` | Unix time; after it every request gets 401 |

## The dashboard

[moq-test.ci.openmoq.org](https://moq-test.ci.openmoq.org) takes the token
once and keeps you logged in for up to 12 hours. It lists every job, builds
a namespace from a form, and starts, checks and stops jobs. The relay list
shows only your relays.

## Your first track

```sh
curl -s -X POST -H "$H" "$U/api/v1/jobs?preset=720p30&ttl=600"
```

The reply describes the job:

```json
{"id": "j_5b4790bcdb62", "state": "starting",
 "namespace": "openmoq/mtt/aiomoqt/moq_test_tracks_00/clock/msf/loc/h264/720/30/aac/2",
 "tracks": ["catalog", "video", "audio"], "relay": "moqx-main",
 "url": "moqt://moqx-main.ci.openmoq.org:4433", "draft": 18, "ttl_s": 600, ...}
```

It publishes from `running`, a second or two later (up to a minute for the
first job of a new clip shape), until its TTL ends. Follow it, then stop it:

```sh
id=j_5b4790bcdb62
curl -s -N -H "$H" "$U/api/v1/jobs/$id/events"   # state, stats and log lines
curl -s -X DELETE -H "$H" "$U/api/v1/jobs/$id"
```

A stopped track ends gracefully: End of Track after its last object, then
PUBLISH_DONE, then PUBLISH_NAMESPACE_DONE.

If someone already runs the same namespace on the same relay, you join their
job (`"joined": true`) instead of starting a second one.

## Choosing the tracks

The namespace is the track description. Build it from a preset and the
parameters that differ; anything you leave out takes the preset's or the
draft's value.

| Want | Parameters |
|------|------------|
| 1080p60 H.264, CMAF | `preset=1080p60&pkg=cmaf` |
| H.265 in LOC | `vc=h265` |
| Opus audio in datagrams | `ac=opus&adel=dgram` |
| Audio only | `preset=audio` |
| 4 audio frames per group | `adel=4` |
| No catalog, decoder config in the media | `cat=none` |
| Big Buck Bunny at 1080p30, CMAF | `clip=bbb_4k30&h=1080&pkg=cmaf` |
| Ten minutes, then end | `dur=600` |

Preview the namespace without starting anything (no token needed):

```sh
curl -s "$U/api/v1/namespace?preset=1080p60&pkg=cmaf"
```

Ask whether the tool can publish it, and see its exact plan, without
starting it:

```sh
curl -s -X POST -H "$H" "$U/api/v1/jobs/check?preset=1080p60&pkg=cmaf"
```

Or pass a namespace you already have, in a JSON body:

```sh
curl -s -X POST -H "$H" -H "Content-Type: application/json" "$U/api/v1/jobs" -d \
  '{"namespace": "openmoq/mtt/aiomoqt/moq_test_tracks_00/clock/msf/cmaf/h264/1080/30/aac/2",
    "relay": "us-ord-3", "draft": 18, "ttl_s": 900}'
```

The service always replies with the namespace in its shortest form and
publishes that one. `GET /api/v1/presets` lists presets and parameters,
`GET /api/v1/clips` the clips, and `GET /api/v1/tools` what each tool can
publish. Every parameter is in the [API reference](api.md#describing-a-job).

## Choosing the relay

`relay=<id>` picks one of your relays (`GET /api/v1/relays` lists them with
their URLs and drafts); without it the service picks `moqx-main` if you have
it, else your first relay. `draft=<n>` picks a draft the relay speaks,
`transport=webtransport` WebTransport instead of raw QUIC.

```sh
curl -s -X POST -H "$H" "$U/api/v1/jobs?preset=720p30&relay=us-ord-3&draft=18&ttl=600"
```

To publish to a relay that is not offered to you, ask the operators.

## Playing it

Subscribe with any MoQT player or client to the job's `namespace` at its
`url`, using the job's `draft`. The tracks are `catalog`, `video` and
`audio`. For example, with aiomoqt:

```sh
python -m aiomoqt.tools.sub_media moqt://moqx-main.ci.openmoq.org:4433 \
  -N openmoq/mtt/aiomoqt/moq_test_tracks_00/clock/msf/loc/h264/720/30/aac/2 --draft 18 --analyze
```

On moqx relays, subscribe with draft 18: offered both 16 and 18, moqx picks
16, and the timestamp properties then arrive mis-encoded.

The `clock` content shows the capture time, frame and group in the picture,
so you can read latency and gaps off the screen.

## When something is refused

Errors come back as `{"detail": {"errors": ["…"]}}`, saying what to change.

| Status | Usually |
|--------|---------|
| 401 | no token, or it expired or was revoked: ask the operators for a new one |
| 403 | your role is too low, the relay is not one of yours, or you tried to stop someone else's job |
| 422 | a parameter or namespace the service or tool cannot produce; the message names the field |
| 429 | a limit is reached (your jobs, all jobs, live encodes); retry later or stop a job |

Every token holder can see every job, with its namespace, relay and holder
name.
