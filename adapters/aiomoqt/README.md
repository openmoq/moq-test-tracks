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
| Timing (draft §Timing) | `--timestamp-mapping` |
| packaging `loc`, video `h264` / `h264cb` | source `mpegts` → `--ts <fifo> --ts-cadence fps=N[/D],gop=M --catalog-bitrate <video bps>` |
| other `loc` video; `cmaf`, `locmaf`; content `clip` | source `mp4` → `--mp4 <file> --loop --packaging loc\|cmaf\|locmaf` |
| audio delivery `N` | `--audio-frames-per-group N` |
| audio delivery `dgram` | `--audio-frames-per-group 1 -D` (LOC only) |
| audio codec `none` | `--no-audio` |
| catalog `none` | `--no-catalog` |
| catalog `msf` | `--target-latency 250` |
| init data, `loc` | `--init-data catalog\|track\|object` |
| relay lost mid-run, or a GOAWAY | `--reconnect` (redials at once on GOAWAY) |
| FETCH (draft §Publisher Behavior) | `--fetch-history <bytes>`: two groups of the larger track at its bitrate, at least 64 KiB; a paused track keeps its open group |

Status: pub_media writes `@mtt` events itself (`--stats-json`); the adapter
passes them through and sends the rest of its output to the job log.

## Gaps

Refused in check mode, not yet conformant, or limiting the service, until
aiomoqt supports them:

| Gap | Ask for aiomoqt |
|-----|-----------------|
| catalog `none`: decoder config rides VIDEO_CONFIG / AUDIO_CONFIG on each group's first object | parameter sets in the media with `--no-catalog` |
| `av1` via `--ts` | AV1 in the TS source |
| `request.json` | optional native contract support (no adapter needed) |
| on-demand prefix serving | `--serve-prefix` (draft §Publisher Behavior, on demand) |
| a GOAWAY's New Session URI is followed to any host, off the relay allowlist | an allowlist for GOAWAY URIs (aiomoqt 0.15.0 or later) |
| on a GOAWAY to the same address the old session stays open until the redial succeeds, so the relay drains for its full timeout | close the old session on a same-address GOAWAY |
| video tracks `altN` (ladders) and `viewN` (views) | publish several video tracks in one namespace and catalog (draft field 13) |
| datagram audio with `cmaf` packaging | `-D` with CMAF |
