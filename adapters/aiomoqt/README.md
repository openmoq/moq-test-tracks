# aiomoqt adapter

Drives `python -u -m aiomoqt.tools.pub_media`. pub_media packages and
publishes but does not encode, so the service supplies the source media.

## Mapping

| Request | pub_media |
|---------|-----------|
| `endpoints[].url` | positional URLs (one process fans out) |
| `endpoints[].draft` | `--draft N` |
| `tls.verify: false` | `-k` |
| `namespace` | `-N` joined with `/` (fields contain no `/`) |
| `mode` | `namespace`: `--pub-ns` · `publish`: `--pub-both` |
| FORWARD | `--forward 1` (pub_media defaults to 0) |
| `ttl_s`, `duration_s` | `-t` = `duration_s` if > 0, else `ttl_s` |
| packaging `loc` | source `mpegts` → `--ts <fifo>` |
| packaging `cmaf`, `locmaf` | source `mp4` → `--mp4 <file> --loop --packaging cmaf\|locmaf` |
| audio forwarding 3 | `-D` |
| audio codec `none` | `--no-audio` |

Status: the adapter turns pub_media's `[pub M:SS.sss] …` stats lines into
`@mtt` events.

## Gaps

Refused in check mode, or not yet conformant, until aiomoqt supports them:

| Gap | Ask for aiomoqt |
|-----|-----------------|
| no timestamp mapping | emit TIMESCALE and TIMESTAMP_MAPPING Track Properties, and OBJECT_TIMESTAMP corrections off schedule (draft-frindell-moq-timestamp) |
| LOC Timestamp taken at read time (`loc.py:225`) | stamp the scheduled capture time: start + n × frame duration |
| LOC Timescale sent per Object in self-describing mode (`loc.py:183`) | never send LOC Timescale with wall-clock timestamps |
| no `prft` for `cmaf` / `locmaf` | `prft` with the capture time in the first Object of each Group |
| FORWARD defaults to 0 | default to 1 |
| catalog `none` | `--no-catalog` |
| init data `track` / `object`; with `catalog`, VIDEO_CONFIG / AUDIO_CONFIG also ride each group's first object | select init carriage: catalog only, Track Property only, or group-start Object Property only |
| `opus` on the TS / mp4 paths | Opus in the `--ts` and `--mp4` sources |
| `h265`; `av1` via `--ts` | codec coverage per source path |
| video forwarding 1 | expose the PER_OBJECT subgroup mapping |
| audio frames per group > 1 (LOC) | an audio grouping option |
| datagram audio over WebTransport | WT datagram send |
| datagram size not checked (`delivery.py:195`) | check against the max datagram size; exit, never fall back |
| SIGTERM kills abruptly | handle SIGTERM like SIGINT |
| human-readable stats only | `--stats-json`, or `@mtt` lines natively |
| `request.json` | optional native contract support (no adapter needed) |
| on-demand prefix serving | `--serve-prefix` (draft §Publisher Behavior, on demand) |
