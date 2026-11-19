# Running tracks locally

`mtt run` publishes any test track from your own machine to any relay. It
needs no token and no service: it runs the same content recipe and tool
adapter as the service, with ffmpeg making the media and aiomoqt `pub_media`
publishing it.

## Requirements

- Linux. The picture's text uses `/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf`.
- Python 3.12 or later.
- ffmpeg on `PATH` with libx264, `drawtext` and the native `aac` encoder;
  libx265, libsvtav1 or libopus for `h265`, `av1` or `opus` tracks. On Debian
  or Ubuntu: `sudo apt install ffmpeg fonts-dejavu-core`.

## Install

```sh
git clone https://github.com/openmoq/moq-test-tracks
cd moq-test-tracks
python3 -m venv .venv && . .venv/bin/activate
pip install -e . "aiomoqt==0.15.0rc1"
```

Run `mtt` inside the clone; it reads `config/` and `adapters/` there.

## Publish a track

Use a prefix of your own: publishers that share a relay need distinct
prefixes, and `openmoq/…` is the service's.

```sh
mtt run --relay moqt://aiomoqt-relay.marzresearch.net:4443 \
  --ns alice/demo/mtt_00/clock/loc/h264/720/30/aac/2 --ttl 3600
```

It publishes that namespace until the TTL or Ctrl-C. Subscribe to its tracks
`video`, `audio` and `catalog`. The
[user guide](user-guide.md#choosing-the-tracks) lists the fields after
`mtt_00`.

- `--relay` takes a `moqt://` URL (raw QUIC), an `https://` URL (WebTransport),
  or a relay id from [config/relays.yaml](../config/relays.yaml).
- `pub_media` checks the relay's certificate. For a local relay with a
  self-signed one, add it to the venv's bundle:
  `cat cert.pem >> "$(python -m certifi)"`.
- `--draft` sets the MoQT draft: 18 by default for a URL, else the relay's
  default.
- The field options (`--prefix alice/demo --content clock --pkg loc …`) work
  in place of `--ns`, but publish the namespace with every optional field
  written out, as printed on the first line; subscribe to that one.
- `--ttl` is in seconds, up to 86400; the default is 1800.
- `--dry-run` prints the `pub_media` command without starting it; `-v` shows
  the tool's log.

## Clips

A clip id in field 1 needs the clip recorded first:

```sh
mtt media fetch bbb_4k30
mtt run --relay aiomoqt-relay --ns alice/demo/mtt_00/bbb_4k30/cmaf/h264/1080/30/aac/2
```

`mtt media list` shows the clips in [media/sources.yaml](../media/sources.yaml).
Clips without a URL, such as the Artemis II views, take their files from
`var/media` with `mtt media import <id>`. The first run of a clip shape renders
its loop, which can take a minute at 2160p.
