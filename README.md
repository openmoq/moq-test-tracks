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
openmoq/mtt/aiomoqt/moq_test_tracks_00/msf/loc/catalog/h264/720/30/2500/2000/0/aac/2/128/1/0/clock/0
└──── prefix ─────┘ └──── version ───┘ └─────────────── characteristic fields 1–16 ────────────────┘
```

> **Status: in testing** at <https://moq-test.ci.openmoq.org>. Access is by
> API key; see [Getting started](#getting-started). The architecture, security
> model and phases are in [docs/design.md](docs/design.md).

## Pieces

- **The encoding.** [draft-marzot-moq-test-tracks](https://gmarzot.github.io/draft-marzot-moq-test-tracks/draft-marzot-moq-test-tracks.html)
  ([repo](https://github.com/gmarzot/draft-marzot-moq-test-tracks)) specifies the namespace fields, track names, media shape and generator
  behavior. It extends the approach of
  [draft-afrind-moq-test](https://datatracker.ietf.org/doc/draft-afrind-moq-test/)
  from synthetic objects to real media. Serving moq-test-00 tracks is
  planned.
- **The service.** A web dashboard and API at `moq-test.ci.openmoq.org` to
  start, watch and stop generator jobs. Access is by API key.
- **Tools.** Publisher tools plug in through the
  [tool contract](docs/tool-contract.md): a manifest, an adapter that turns
  the standard request into the tool's command line, and an enable line in
  [config/tools.yaml](config/tools.yaml). aiomoqt `pub_media` is first.
- **Relays.** Jobs publish to an allowlisted relay
  ([config/relays.yaml](config/relays.yaml)). The default is the OpenMOQ CI
  relay, moqx-main.

## Getting started

1. **Get a key.** The service operators issue keys; open an issue in this
   repository to ask for one. A `run` key can start and stop its own jobs.
2. **Start a job.** Log in to the dashboard with the key, pick a shape, and
   start it. Or use the [API](docs/api.md):
   ```sh
   curl -s -X POST -H "Authorization: Bearer $MTT_KEY" \
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
| [docs/](docs/) | [API](docs/api.md), [tool contract](docs/tool-contract.md), [design](docs/design.md) |
| [adapters/](adapters/) | One directory per tool: manifest, adapter, notes |
| [config/](config/) | Enabled tools, relay allowlist, policy limits |
| [media/](media/) | Clip sources (`sources.yaml`) |
| [src/moq_test_tracks/](src/moq_test_tracks/) | Service, runner, namespace codec, adapters |

## License

Apache 2.0, see [/LICENSE](/LICENSE). Contributions: [CONTRIBUTING.md](CONTRIBUTING.md).
Security reports: [SECURITY.md](SECURITY.md).
