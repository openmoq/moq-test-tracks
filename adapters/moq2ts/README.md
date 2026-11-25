# moq2ts adapter

Drives `moq2ts-cli` from [openmoq/moq2ts](https://github.com/openmoq/moq2ts),
which publishes an MPEG-TS stream as one MSFTS track
(draft-gregoire-moq-msfts) with unmodified carriage. It packages and
publishes but does not encode: the service supplies a paced, null-padded CBR
TS (packaging `msfts`).

## Mapping

| Request | moq2ts-cli |
|---------|------------|
| `endpoints[0].url` | `--endpoint` (one relay) |
| `endpoints[0].draft` | `--draft N` |
| `tls.verify: false` | `--insecure` |
| `namespace` | `--namespace`, joined with `/` |
| packaging `msfts` | `--transparent`: every source packet, nulls included, on track `program-1`, with PAT and PMT in the catalog's `initDataList`, and the timeline track `program-1.timeline` |
| `ts_packets` | `--segment-bytes` = 188 × `ts_packets` |
| `mode` | `namespace` only: moq2ts-cli sends Objects once the relay forwards a SUBSCRIBE, and no PUBLISH |
| source `mpegts` | the adapter copies the FIFO to `--video /dev/stdin` |

Status: moq2ts-cli prints `[ready] namespace accepted` once the relay accepts
the namespace, a `[stats]` line about once a second and a `[final]` line at
exit. The adapter reports `ready` on the `[ready]` line, and every 2 s a
`stats` event for `program-1` from the `[stats]` sent counters (`sent_objects`,
`sent_bytes`), plus the input rate (`input_kbps`). Other output goes to the
job log.

Relay loss: moq2ts-cli reconnects in-process (after 2 s, then 2 s longer per
failed attempt, up to 60 s) and resumes at the next Group. It exits 1 when 10
attempts fail before any connection has worked; the adapter exits 69 for it,
so the runner restarts the run with backoff. 2 (invalid arguments) becomes 70.

## Gaps

| Gap | Ask for moq2ts |
|-----|----------------|
| reconnects within moq2ts-cli are not reported, so the job stays `running` while it reconnects | a line per connection state on stdout |
| the release AppImage targets Ubuntu 24.04 and leaves out fontconfig, harfbuzz, ALSA, GL and others | a build for older glibc, or a CLI-only bundle |
