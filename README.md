<div align="center">

[![ci pr](https://github.com/openmoq/moq-test-tracks/actions/workflows/ci-pr.yml/badge.svg)](https://github.com/openmoq/moq-test-tracks/actions/workflows/ci-pr.yml)
[![License](https://img.shields.io/github/license/openmoq/moq-test-tracks)](/LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/openmoq/moq-test-tracks)](https://github.com/openmoq/moq-test-tracks/commits/main)
[![Open issues](https://img.shields.io/github/issues/openmoq/moq-test-tracks)](https://github.com/openmoq/moq-test-tracks/issues)
[![MOQT](https://img.shields.io/badge/MOQT-draft--18-blue)](https://datatracker.ietf.org/doc/draft-ietf-moq-transport/)

</div>

# moq-test-tracks

On-demand MoQT media test tracks. The track namespace names the media shape
(catalog, packaging, codecs, resolution, frame rate, audio over datagrams,
content). The service encodes and publishes matching media to a relay with
the publisher tool of your choice.

```
openmoq/mtt/aiomoqt/moq_test_tracks_00/clock/msf/loc/h264/720/30/aac/2
└──── prefix ─────┘ └──── version ───┘ └─── required fields 1–8 ──────┘
```

Optional fields 9 to 13 (init data, group duration, audio delivery, duration,
video tracks) follow when they differ from the values the draft gives them.

> **Status: in testing** at <https://moq-test.ci.openmoq.org>. Access is by
> token; see [Getting started](#getting-started). The architecture, security
> model and phases are in [docs/design.md](docs/design.md).

## Pieces

- **The encoding.** [draft-marzot-moq-test-tracks](https://gmarzot.github.io/draft-marzot-moq-test-tracks/draft-marzot-moq-test-tracks.html)
  ([repo](https://github.com/gmarzot/draft-marzot-moq-test-tracks)) specifies the namespace fields, track names, media shape and generator
  behavior. It extends the approach of
  [draft-afrind-moq-test](https://datatracker.ietf.org/doc/draft-afrind-moq-test/)
  from synthetic objects to real media. Serving moq-test-00 tracks is
  planned.
- **The service.** A web dashboard and API at `moq-test.ci.openmoq.org` to
  start, watch and stop generator jobs. Access is by token.
- **Tools.** Publisher tools plug in through the
  [tool contract](docs/tool-contract.md): a manifest, an adapter that turns
  the standard request into the tool's command line, and an enable line in
  [config/tools.yaml](config/tools.yaml). aiomoqt `pub_media` is first.
- **Relays.** Jobs publish to a relay from the catalog in
  [config/relays.yaml](config/relays.yaml) that the caller's token grants.
  The default is the OpenMOQ CI relay, moqx-main.

## Getting started

1. **Get a token.** The service operators issue tokens; open an issue in this
   repository to ask for one. A `run` token can start and stop its own jobs
   on the relays it names. The [user guide](docs/user-guide.md) covers using
   it; operators see the [admin guide](docs/admin.md).
2. **Start a job.** Log in to the dashboard with the token, pick a shape, and
   start it. Or use the [API](docs/api.md):
   ```sh
   curl -s -X POST -H "Authorization: Bearer $MTT_TOKEN" \
     "https://moq-test.ci.openmoq.org/api/v1/jobs?preset=720p30&ttl=600"
   ```
   The response carries the job's namespace and relay. Jobs end at their TTL,
   or with `DELETE /api/v1/jobs/<id>`.
3. **Subscribe.** Point any MoQT player or client at the job's relay and
   namespace; the track names are `catalog`, `video` and `audio`. On
   moqx-main, subscribe with draft 18 (see [docs/api.md](docs/api.md#subscribing)).

The service names its namespaces `openmoq/mtt/<tool>/moq_test_tracks_00/…`,
plus a clip id after the tool for clips. The draft's examples use the prefix
`openmoq/mtt`; the fields from `moq_test_tracks_00` on are the same.

## Layout

| Path | Contents |
|------|----------|
| [docs/](docs/) | [User guide](docs/user-guide.md), [admin guide](docs/admin.md), [API](docs/api.md), [tool contract](docs/tool-contract.md), [design](docs/design.md) |
| [adapters/](adapters/) | One directory per tool: manifest, adapter, notes |
| [config/](config/) | Enabled tools, relay catalog, policy limits |
| [media/](media/) | Clip sources (`sources.yaml`) |
| [src/moq_test_tracks/](src/moq_test_tracks/) | Service, runner, namespace codec, adapters |

## License

Apache 2.0, see [/LICENSE](/LICENSE). Contributions: [CONTRIBUTING.md](CONTRIBUTING.md).
Security reports: [SECURITY.md](SECURITY.md).
