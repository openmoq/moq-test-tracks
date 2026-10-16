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
| `mode` | `publish`: default · `publish_namespace`: `--pub-ns` · `both`: `--pub-both` |
| `ttl_s`, `duration_s` | `-t` = `duration_s` if > 0, else `ttl_s` |
| packaging `loc` | source `mpegts` → `--ts <fifo>` |
| packaging `loc`, audio `pcm` | source `annexb` → `--h264 <video fifo> --tone` (`audio_path` unused) |
| packaging `cmaf`, `locmaf` | source `mp4` → `--mp4 <file> --loop --packaging cmaf\|locmaf` |
| audio forwarding 3 | `-D` |
| audio codec `none` | `--no-audio` |

Status: the adapter turns pub_media's `[pub M:SS.sss] …` stats lines into
`@mtt` events.

## Gaps

Refused in check mode until aiomoqt supports them:

| Gap | Ask for aiomoqt |
|-----|-----------------|
| catalog `none` | `--no-catalog` |
| `pcm` comes from `--tone` (no one-second beep for content `ts`) | PCM input from a pipe |
| `opus` on the TS / mp4 paths | Opus in the `--ts` and `--mp4` sources |
| `h265`, `vp9`; `av1` via `--ts` | codec coverage per source path |
| video forwarding 1 | expose the PER_OBJECT subgroup mapping |
| audio frames per group > 1 (LOC) | an audio grouping option |
| datagram audio over WebTransport | WT datagram send |
| datagram size not checked (`delivery.py:195`) | check against the max datagram size; exit, never fall back |
| SIGTERM kills abruptly | handle SIGTERM like SIGINT |
| human-readable stats only | `--stats-json`, or `@mtt` lines natively |
| `request.json` | optional native contract support (no adapter needed) |
| on-demand prefix serving | `--serve-prefix` (draft §On demand) |
