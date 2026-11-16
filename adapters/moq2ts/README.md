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

Status: moq2ts-cli writes a `[stats] objects= bytes= groups=` line about once
a second and a `[final]` line at exit. The adapter reports `ready` on the
first source bytes it hands over, and every 2 s a `stats` event with those
counters for `program-1` plus the input rate (`input_kbps`). Other output
goes to the job log.

Exit codes: moq2ts-cli exits 1 when the session ends or never opens; the
adapter exits 69 for it, so the runner restarts the run with backoff. 2
(invalid arguments) becomes 70.

## Gaps

| Gap | Ask for moq2ts |
|-----|----------------|
| v1.3.0 has no `--quic-idle-timeout` and no in-process reconnect (both on main): a lost relay ends the run, and the runner restarts it with a new source | a release with both |
| v1.3.0 waits 120 s for a first subscriber, then ends the session | `main` waits indefinitely |
| `[stats]` counts the Objects the pipeline hands over, not what was sent; `ready` is not tied to the relay accepting the namespace | a ready line and sent counters per interval ([moq2ts#60](https://github.com/openmoq/moq2ts/issues/60)) |
| moqx relays route raw QUIC sessions by path (`/moq-relay`), which the relay allowlist's `moqt` URLs omit | none: aiomoqt-relay first; moqx needs a URL with the path |
| the release AppImage targets Ubuntu 24.04 (glibc 2.38) and leaves out fontconfig, harfbuzz, ALSA, GL and others | a build for older glibc, or a CLI-only bundle |
