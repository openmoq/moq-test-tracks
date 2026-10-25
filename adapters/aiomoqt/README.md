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
| packaging `loc`, video `h264` / `h264cb` | source `mpegts` → `--ts <fifo>` |
| other `loc` video; `cmaf`, `locmaf` | source `mp4` → `--mp4 <file> --loop --packaging loc\|cmaf\|locmaf` |
| video forwarding 1 | `--video-subgroups object` |
| audio frames per group | `--audio-frames-per-group N` |
| audio forwarding 1 | `--audio-subgroups object` |
| audio forwarding 3 | `-D` |
| audio codec `none` | `--no-audio` |

Status: pub_media writes `@mtt` events itself (`--stats-json`); the adapter
passes them through and sends the rest of its output to the job log.

## Gaps

Refused in check mode, or not yet conformant, until aiomoqt supports them:

| Gap | Ask for aiomoqt |
|-----|-----------------|
| catalog `none` | `--no-catalog` |
| init data `track` / `object`; with `catalog`, VIDEO_CONFIG / AUDIO_CONFIG also ride each group's first object | `--init-data catalog\|track\|object` |
| exit 1 for an unreachable relay and for internal errors | exit 69 and 70 |
| `ready` sent when no endpoint is up | `ready` only once an endpoint is up |
| `av1` via `--ts` | AV1 in the TS source |
| datagram audio over WebTransport | WT datagram send |
| `request.json` | optional native contract support (no adapter needed) |
| on-demand prefix serving | `--serve-prefix` (draft §Publisher Behavior, on demand) |
