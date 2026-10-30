# ffmpeg_moq adapter

Drives one `ffmpeg` process built from [openmoq/FFmpeg](https://github.com/openmoq/FFmpeg)
with the MoQ muxer (`libavformat/moqenc.c`, on libmoq from
[openmoq/moq5](https://github.com/openmoq/moq5)). ffmpeg generates, encodes
and publishes, so the adapter requests no source (`sources: []`). Inputs,
filters and encoder arguments come from the shared content recipes, so
content matches what the source stage produces for other tools.

Best effort. The adapter maps what the muxer supports today; closing the
open items below is up to the FFmpeg muxer and libmoq contributors. Not
enabled in `config/tools.yaml` until they are settled.

## Mapping

| Request | ffmpeg |
|---------|--------|
| `fields` (content, video, audio) | inputs, filters and encoder arguments from the shared recipes |
| `endpoints[0].url` | output URL (`moqt://…` or `https://…`), `-f moq` |
| `endpoints[0].draft` | `-moq_draft 16\|18` |
| `tls.verify: false` / `tls.ca_file` | `-moq_insecure 1` / `-moq_ca_file` |
| `namespace` | `-moq_namespace` joined with `-` (fields contain no `-`) |
| `tracks` | `-moq_video_track video -moq_audio_track audio` (the defaults) |
| packaging | `-moq_packaging loc\|cmaf` |
| audio delivery `N` (frames per group) | `-moq_audio_fragment_frames N` (always passed; the default is one second) |
| `ttl_s`, `duration_s` | `-t` = `duration_s` if > 0, else `ttl_s` |
| status | `-progress pipe:1`, translated to `@mtt` events |

## Open items

| Item | Where | Owner |
|------|-------|-------|
| `namespace` mode: libmoq's sender can announce without PUBLISH, but the muxer sets `publish_tracks = 1` | muxer option | moqenc (Santiago Ferreiro, Joaquin Bartaburu) |
| Capture time falls back to `av_gettime()` at write time when packets carry no `prft` side data | muxer | moqenc |
| LOC properties are the LOC-01 profile; G5 reads LOC-04 Timestamp `0x10` | libmoq | moq5 (Raymond Lucke) |
| No datagram path in the media sender (audio delivery `dgram`) | libmoq | moq5 |
| Confirm one subgroup per group, and that PUBLISH carries FORWARD 1 (or omits it) and honours the relay's REQUEST_UPDATE | libmoq | moq5 |
| No timestamp mapping (TIMESCALE, TIMESTAMP_MAPPING) | libmoq | moq5 |
| No catalog-less mode (`none`) | libmoq | moq5 |
