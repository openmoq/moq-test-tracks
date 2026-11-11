# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# ffmpeg command lines that render a TrackSpec's content and encode it to the
# spec's codecs, shared by the source stage and ffmpeg-based adapters.

from __future__ import annotations

import math
from fractions import Fraction
from pathlib import Path

from ..spec.fields import AUDIO_FRAME_SAMPLES, AUDIO_SAMPLE_RATE, TrackSpec, VideoTrack

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
LOOP_TARGET_S = 60
LOOP_MAX_S = 300
# Rate-control buffer in frames: caps a key frame near this many mean frames.
VBV_FRAMES = 5

FORMATS = ("mpegts", "mp4")


class RecipeError(ValueError):
    pass


def _rate(fps: Fraction) -> str:
    return f"{fps.numerator}/{fps.denominator}"


def _video_input(spec: TrackSpec, start_us: int | None, track: VideoTrack | None) -> list[str]:
    """The content's picture; track is one track of a set of several."""
    size = f"{spec.video_width}x{spec.video_height}"
    rate = _rate(spec.fps)
    if spec.content == "clock":
        frame_s = float(1 / spec.fps)
        fontsize = max(12, spec.video_height // 18)
        if start_us is None:
            # A set's loops show the loop position, the same in every track.
            clock = "%{gmtime\\:%T}" if track is None else "%{pts\\:hms}"
            phase = 0
        else:
            # Frame time from the stream start: the capture time the timestamps carry.
            day_s = start_us % 86_400_000_000 / 1_000_000
            clock, phase = f"%{{pts\\:hms\\:{day_s:.6f}\\:24HH}}", start_us % 1_000_000 / 1_000_000
        graph = (
            f"testsrc2=size={size}:rate={rate},"
            f"drawtext=fontfile={FONT}:fontsize={fontsize}:fontcolor=white:box=1:boxcolor=black@0.6"
            f":x=(w-tw)/2:y=h-2*th:text='{clock} f %{{n}}',"
            f"drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill"
            f":enable='lt(mod(t+{phase:.6f},1),{frame_s:.6f})'"
        )
    elif spec.content == "bars":
        graph = f"smptehdbars=size={size}:rate={rate}"
    elif spec.content == "noise":
        graph = f"color=c=gray:size={size}:rate={rate},noise=alls=100:allf=t+u"
    else:
        raise RecipeError(f"no recipe for content {spec.content}")
    if track is not None:
        graph += _set_member(spec, track)
    return ["-f", "lavfi", "-i", graph]


def _set_member(spec: TrackSpec, track: VideoTrack) -> str:
    """Filters that make the picture one track of a set: a ladder rung is the same picture
    scaled, a view a picture of its own; each shows its label."""
    index = spec.video_track_names.index(track.track)
    out = ""
    if spec.content == "noise":
        out += f":all_seed={index if spec.is_views else 0}"
    elif spec.is_views:
        out += f",hue=h={360 * index // len(spec.video_track_names)}"
    if track.height != spec.video_height:
        out += f",scale={track.width}:{track.height}"
    return out + (
        f",drawtext=fontfile={FONT}:fontsize={max(12, track.height // 18)}:fontcolor=white"
        f":box=1:boxcolor=black@0.6:x=w-tw-th/2:y=th/2:text='{track.label}'"
    )


def _audio_input(spec: TrackSpec, start_us: int | None) -> list[str]:
    layout = "mono" if spec.audio_channels == 1 else "stereo"
    rate = AUDIO_SAMPLE_RATE
    if spec.content == "clock":
        phase = 0 if start_us is None else start_us % 1_000_000 / 1_000_000
        beat = f"mod(t+{phase:.6f}\\,1)"
        expr = f"if(lt({beat}\\,0.1)\\,0.5*sin(2*PI*1000*t)\\,0.3*sin(2*PI*440*t))"
        graph = f"aevalsrc=exprs={expr}:s={rate}:c={layout}"
    elif spec.content == "bars":
        graph = f"sine=frequency=1000:sample_rate={rate},aformat=channel_layouts={layout}"
    elif spec.content == "noise":
        graph = f"anoisesrc=sample_rate={rate}:color=white:amplitude=0.5,aformat=channel_layouts={layout}"
    else:
        raise RecipeError(f"no recipe for content {spec.content}")
    return ["-f", "lavfi", "-i", graph]


def _clip_video_filter(spec: TrackSpec, attribution: str, track: VideoTrack | None) -> list[str]:
    """Clip picture scaled and padded to the track's size and the spec's rate, with its credit
    line."""
    w, h = (track.width, track.height) if track else (spec.video_width, spec.video_height)
    fontsize = max(10, h // 36)
    graph = (
        f"fps={_rate(spec.fps)},"
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
        f"drawtext=fontfile={FONT}:fontsize={fontsize}:fontcolor=white@0.8:box=1:boxcolor=black@0.4"
        f":x=8:y=h-th-8:text='{attribution}'"
    )
    return ["-filter:v", graph]


def _video_encoder(
    spec: TrackSpec, fmt: str, track: VideoTrack | None = None, stream: int | None = None
) -> list[str]:
    """Encoder options; with stream, scoped to that output video stream."""
    opts = _video_codec_opts(spec, fmt, track.kbps if track else spec.video_kbps)
    if stream is None:
        return opts
    return [f"{o.split(':')[0]}:v:{stream}" if i % 2 == 0 else o for i, o in enumerate(opts)]


def _video_codec_opts(spec: TrackSpec, fmt: str, video_kbps: int) -> list[str]:
    gop = int(spec.frames_per_group)
    kbps = f"{video_kbps}k"
    vbv = f"{math.ceil(video_kbps * VBV_FRAMES / spec.fps)}k"
    rate = ["-b:v", kbps, "-maxrate", kbps, "-bufsize", vbv]
    common = ["-r", _rate(spec.fps), "-pix_fmt", "yuv420p", "-g", str(gop), "-keyint_min", str(gop)]
    if spec.video_codec in ("h264", "h264cb"):
        profile = "high" if spec.video_codec == "h264" else "baseline"
        return [
            "-c:v", "libx264", "-profile:v", profile, "-preset", "veryfast",
            "-tune", "zerolatency", "-bf", "0", "-sc_threshold", "0",
            "-x264-params", "repeat-headers=1", *common, *rate,
        ]  # fmt: skip
    if spec.video_codec == "h265":
        params = f"keyint={gop}:min-keyint={gop}:scenecut=0:bframes=0:repeat-headers=1"
        tag = ["-tag:v", "hvc1"] if fmt == "mp4" else []
        return [
            "-c:v", "libx265", "-preset", "veryfast", "-tune", "zerolatency",
            "-x265-params", params, *tag, *common, *rate,
        ]  # fmt: skip
    if spec.video_codec == "av1":
        return [
            "-c:v", "libsvtav1", "-preset", "10",
            "-svtav1-params", f"keyint={gop}:scd=0:pred-struct=1", *common, *rate,
        ]  # fmt: skip
    raise RecipeError(f"no encoder for video codec {spec.video_codec}")


def _audio_encoder(spec: TrackSpec) -> list[str]:
    common = ["-ar", str(AUDIO_SAMPLE_RATE), "-ac", str(spec.audio_channels)]
    if spec.audio_codec == "aac":
        return ["-c:a", "aac", "-b:a", f"{spec.audio_kbps}k", *common]
    if spec.audio_codec == "opus":
        return [
            "-c:a", "libopus", "-b:a", f"{spec.audio_kbps}k", "-vbr", "off", "-frame_duration", "20",
            "-application", "lowdelay", *common,
        ]  # fmt: skip
    raise RecipeError(f"no encoder for audio codec {spec.audio_codec}")


def _lcm(a: Fraction, b: Fraction) -> Fraction:
    return Fraction(
        math.lcm(a.numerator * b.denominator, b.numerator * a.denominator),
        a.denominator * b.denominator,
    )


def loop_seconds(spec: TrackSpec, target: int | None = None) -> Fraction:
    """Loop length of whole video groups and whole audio frames, so both tracks stay on their
    frame grid across the seam: the longest within target (default LOOP_TARGET_S, which a clip
    sets to its window), else one unit. When that unit exceeds LOOP_MAX_S, whole video
    groups only."""
    target = Fraction(target or LOOP_TARGET_S)
    units = []
    if spec.group_ms is not None:
        units.append(Fraction(spec.group_ms, 1000))
    if spec.has_audio:
        units.append(Fraction(AUDIO_FRAME_SAMPLES[spec.audio_codec], AUDIO_SAMPLE_RATE))
    if not units:
        return target
    unit = units[0]
    for u in units[1:]:
        unit = _lcm(unit, u)
    if unit > LOOP_MAX_S:
        unit = units[0]
    return unit * max(1, math.floor(target / unit))


def source_command(
    spec: TrackSpec,
    fmt: str,
    output: str | Path,
    clip: dict | None = None,
    start_us: int | None = None,
    track: VideoTrack | None = None,
) -> list[str]:
    """ffmpeg argv producing the spec's media: a live MPEG-TS stream, or a
    loopable non-fragmented mp4 of loop_seconds() length. Clip content reads
    the request's clip mezzanine ({"path", "attribution"}). start_us, µs since
    the Unix epoch of the first frame, sets the clock content's time.

    With several video tracks, the stream carries them all, in Track Name order
    before the audio; an mp4 holds one, track, with the audio in the first."""
    if fmt not in FORMATS:
        raise RecipeError(f"unknown source format {fmt}")
    argv = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "warning", "-y"]
    live = fmt == "mpegts"
    tracks = spec.video_set()
    if len(tracks) > 1 and not live and track is None:
        raise RecipeError("each video track of a set has its own mp4")
    if len(tracks) > 1 and live:
        video = list(enumerate(tracks))  # (output stream, track)
    else:
        video = [(None, track)] if spec.has_video else []
    with_audio = spec.has_audio and (track is None or track == tracks[0])
    inputs, maps, encoders = [], [], []
    if spec.content == "clip":
        if not clip:
            raise RecipeError("clip content needs a fetched clip")
        if len(video) > 1:
            raise RecipeError("a clip's video tracks are mp4 loops, one per track")
        inputs += (["-re"] if live else []) + ["-stream_loop", "-1", "-i", str(clip["path"])]
        if spec.has_video:
            maps += ["-map", "0:v:0"]
            encoders += _clip_video_filter(spec, clip["attribution"], track)
            encoders += _video_encoder(spec, fmt, track)
        if with_audio:
            maps += ["-map", "0:a:0"]
            encoders += _audio_encoder(spec)
    else:
        for stream, t in video:
            set_track = t if len(tracks) > 1 else None
            inputs += (["-re"] if live else []) + _video_input(spec, start_us, set_track)
            encoders += _video_encoder(spec, fmt, t, stream)
        if with_audio:
            inputs += (["-re"] if live else []) + _audio_input(spec, start_us)
            encoders += _audio_encoder(spec)
        for i in range(len(video) + int(with_audio)):
            maps += ["-map", str(i)]
    argv += inputs + maps + encoders
    if live:
        argv += ["-f", "mpegts", "-muxdelay", "0", "-muxpreload", "0", str(output)]
        return argv
    seconds = loop_seconds(spec, (clip or {}).get("loop_s"))
    if spec.has_video:
        argv += ["-video_track_timescale", str(spec.video_timing().timescale)]
    if with_audio:
        # Stamped by sample count, as a mezzanine's millisecond timestamps leave
        # uneven frame durations.
        argv += ["-filter:a", "asetpts=N/SR/TB"]
    argv += ["-t", f"{float(seconds):.3f}", "-movflags", "+faststart", "-f", "mp4", str(output)]
    return argv
